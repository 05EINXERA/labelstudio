/**
 * Soft task lock — T2.1 / T2.2
 *
 * Sends claim / heartbeat / release calls against the backend advisory lock.
 * The lock is purely informational: it warns the second annotator before they
 * invest time on a task someone else is already editing.  It does NOT prevent
 * saving — the conflict detection in the save path is the real safety net.
 *
 * Single-worker constraint: the backend _TASK_LOCKS dict is in-process, so
 * this only works while the app runs as one uvicorn worker (CLAUDE.md rule 9).
 *
 * Usage:
 *   import { claimTask, heartbeatTask, releaseTask } from './task-lock.js?v=1';
 *
 *   // When a task is opened:
 *   const result = await claimTask(taskId, clientId);
 *   if (result.status === 'locked') { warn the user }
 *
 *   // On a timer tick (~30 s):
 *   await heartbeatTask(taskId, clientId);
 *
 *   // On switch / pagehide:
 *   releaseTask(taskId, clientId);         // fire-and-forget is fine
 */
import { apiFetch } from './api.js?v=1';

/**
 * Claim a task for editing.
 * @returns {Promise<{status:'ok'}|{status:'locked', locked_by:string, seconds_remaining:number}>}
 */
export async function claimTask(taskId, clientId) {
  if (!taskId || !clientId) return { status: 'ok' };
  try {
    const res = await apiFetch(
      `/api/tasks/${taskId}/claim?client_id=${encodeURIComponent(clientId)}`,
      { method: 'POST' }
    );
    if (!res) return { status: 'ok' };
    return await res.json();
  } catch (e) {
    // Lock calls are best-effort; a network error must never block annotation.
    console.warn('[task-lock] claim failed:', e);
    return { status: 'ok' };
  }
}

/**
 * Refresh the TTL on the current claim.  Call every ~30 s while the task is open.
 * @returns {Promise<{status:'ok'|'lost'}>}
 */
export async function heartbeatTask(taskId, clientId) {
  if (!taskId || !clientId) return { status: 'ok' };
  try {
    const res = await apiFetch(
      `/api/tasks/${taskId}/heartbeat?client_id=${encodeURIComponent(clientId)}`,
      { method: 'POST' }
    );
    if (!res) return { status: 'ok' };
    return await res.json();
  } catch (e) {
    console.warn('[task-lock] heartbeat failed:', e);
    return { status: 'ok' };
  }
}

/**
 * Release the claim.  Safe to call without awaiting (fire-and-forget).
 * Uses sendBeacon on unload so the release survives tab close.
 */
export function releaseTask(taskId, clientId, { useBeacon = false } = {}) {
  if (!taskId || !clientId) return;
  const url = `/api/tasks/${taskId}/claim?client_id=${encodeURIComponent(clientId)}`;
  if (useBeacon) {
    apiFetch(`/api/tasks/${taskId}/release-beacon?client_id=${encodeURIComponent(clientId)}`, {
      method: 'POST',
      keepalive: true
    }).catch(() => {});
    return;
  }
  apiFetch(url, { method: 'DELETE' }).catch(e =>
    console.warn('[task-lock] release failed:', e)
  );
}
