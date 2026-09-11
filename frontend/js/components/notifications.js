/**
 * Notification bell for the management pages.
 *
 * Polls the same 30s cadence as the presence heartbeat (api.js
 * initPresenceHeartbeat) rather than holding a WebSocket or SSE stream open:
 * the app runs as a single uvicorn worker with a tuned threadpool/DB-pool
 * budget (config.THREADPOOL_CAP), and ~25 idle long-lived connections are a
 * cost this feature does not need to pay for a badge that can lag 30s.
 *
 * Visibility is toggled with the `is-active` class, never `style.display`
 * (CLAUDE.md rule 15) — the CSS transition is keyed on the class.
 */
import { apiFetch } from "../api.js?v=3";
import { escapeHTML } from "../utils.js?v=2";

const POLL_INTERVAL_MS = 30000;

// Per-viewer preference, so people sharing an office can silence their own bell.
// localStorage is right for this: a stale value is harmless and it never needs
// to reach the server (CONVENTIONS.md § 5).
const SOUND_PREF_KEY = "notif_sound";

/** Two-tone chime, synthesised rather than loaded from a file.
 *
 * No audio asset is committed (repo rule 19) and the artifact CSP blocks media
 * from every external host, so a few oscillator nodes are both the smallest and
 * the only dependency-free option.
 *
 * Browsers refuse to start audio until the page has been interacted with, so
 * the context can be born `suspended` — a tab left sitting on the projects list
 * may never have been clicked. Every failure path here is swallowed: a silent
 * chime is a much smaller problem than a poll that throws.
 */
function playChime(ctx) {
  if (!ctx || ctx.state !== "running") return;
  try {
    const now = ctx.currentTime;
    // A rising fifth (G5 -> D6). Short, quiet, and distinct from OS sounds.
    [
      { freq: 784.0, at: 0, dur: 0.16 },
      { freq: 1174.7, at: 0.13, dur: 0.22 },
    ].forEach(({ freq, at, dur }) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      // Eased in and out: a raw start/stop on a sine clicks audibly.
      gain.gain.setValueAtTime(0.0001, now + at);
      gain.gain.exponentialRampToValueAtTime(0.14, now + at + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + at + dur);
      osc.connect(gain).connect(ctx.destination);
      osc.start(now + at);
      osc.stop(now + at + dur + 0.02);
    });
  } catch (err) {
    console.debug("Notification chime failed", err);
  }
}

export class NotificationManager {
  constructor(bellId, dropdownId, listId, badgeId) {
    this.bell = document.getElementById(bellId);
    this.dropdown = document.getElementById(dropdownId);
    this.list = document.getElementById(listId);
    this.badge = document.getElementById(badgeId);

    this.notifications = [];
    this.pollTimer = null;

    // Ids already seen by this page, so the chime fires on genuinely new
    // notices rather than on a count change. Counting alone is wrong: marking
    // one read while another arrives leaves the count flat, and that arrival
    // still deserves a sound.
    this.seenIds = new Set();
    // The first fetch seeds `seenIds` silently. Without this, every navigation
    // between the three management pages would replay a chime for a backlog
    // the user has already seen.
    this.primed = false;
    this.audioCtx = null;

    if (!this.bell || !this.dropdown || !this.list || !this.badge) {
      // A page without the bell markup is a normal case (only the management
      // pages carry it), so this is a debug note, not a warning.
      return;
    }

    this.init();
  }

  init() {
    this.bell.addEventListener("click", (e) => {
      e.stopPropagation();
      this.toggleDropdown();
    });

    document.addEventListener("click", (e) => {
      if (!this.dropdown.contains(e.target) && !this.bell.contains(e.target)) {
        this.close();
      }
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") this.close();
    });

    // A fixed dropdown does not follow its anchor, so a resize would leave it
    // stranded away from the bell. Re-anchoring is cheap and keeps it attached;
    // this is a no-op wherever the dropdown is not fixed.
    window.addEventListener("resize", () => {
      if (this.dropdown.classList.contains("is-active")) this.positionForSidebar();
    });

    // The AudioContext can only start once the page has been interacted with,
    // so it is created lazily on the first real interaction and resumed if the
    // browser parked it. Passive + once: this costs nothing after the first.
    const unlockAudio = () => {
      try {
        if (!this.audioCtx) {
          const Ctx = window.AudioContext || window.webkitAudioContext;
          if (Ctx) this.audioCtx = new Ctx();
        }
        if (this.audioCtx?.state === "suspended") this.audioCtx.resume();
      } catch (err) {
        console.debug("Audio unavailable", err);
      }
    };
    ["pointerdown", "keydown"].forEach((evt) => {
      document.addEventListener(evt, unlockAudio, { once: true, passive: true });
    });

    this.fetchNotifications();
    this.pollTimer = setInterval(() => this.fetchNotifications(), POLL_INTERVAL_MS);

    // A tab returning to the foreground should not wait out the rest of its
    // interval to show what arrived while it was hidden.
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") this.fetchNotifications();
    });
  }

  /** Stop polling. Called if a page ever tears the bell down. */
  unmount() {
    if (this.pollTimer) clearInterval(this.pollTimer);
    this.pollTimer = null;
  }

  /** Sound is on unless the viewer turned it off. Storage may be unavailable
   *  (private mode, blocked site data), in which case the default stands. */
  soundEnabled() {
    try {
      return localStorage.getItem(SOUND_PREF_KEY) !== "off";
    } catch {
      return true;
    }
  }

  setSoundEnabled(on) {
    try {
      localStorage.setItem(SOUND_PREF_KEY, on ? "on" : "off");
    } catch {
      // A viewer with storage blocked keeps the setting for this page only.
    }
    // Turning sound on is itself an interaction, so it is a good moment to
    // start the audio context the browser would not let us create earlier.
    if (on) {
      try {
        if (!this.audioCtx) {
          const Ctx = window.AudioContext || window.webkitAudioContext;
          if (Ctx) this.audioCtx = new Ctx();
        }
        if (this.audioCtx?.state === "suspended") this.audioCtx.resume();
      } catch (err) {
        console.debug("Audio unavailable", err);
      }
    }
  }

  /**
   * Anchors the dropdown to the bell when it hangs in the workspace sidebar.
   *
   * There it is `position: fixed` (styles.css) to escape the sidebar's
   * `overflow: hidden`, which means it has no useful static position and must
   * be told where to go. It opens upward, because the bell sits in the user
   * footer pinned to the bottom of the column.
   *
   * A no-op on the management pages, where the dropdown is absolutely
   * positioned inside a header that does not clip it, and on the narrow-width
   * drawer, where it renders inline — both are detected by asking for the
   * computed position rather than re-testing the breakpoint in JS.
   */
  positionForSidebar() {
    if (getComputedStyle(this.dropdown).position !== "fixed") return;
    const r = this.bell.getBoundingClientRect();
    // Clamped so the 320px panel cannot run off the right edge of a narrow
    // sidebar, nor off the left of the viewport.
    const width = this.dropdown.offsetWidth || 320;
    const left = Math.max(8, Math.min(r.left, window.innerWidth - width - 8));
    this.dropdown.style.left = `${left}px`;
    this.dropdown.style.bottom = `${window.innerHeight - r.top + 8}px`;
  }

  toggleDropdown() {
    const opening = !this.dropdown.classList.contains("is-active");
    // Position before revealing: measuring a hidden-but-laid-out element is
    // fine, and setting coordinates after the transition starts would slide it
    // in from the wrong place.
    if (opening) this.positionForSidebar();
    this.dropdown.classList.toggle("is-active");
    this.bell.setAttribute("aria-expanded", opening ? "true" : "false");
  }

  close() {
    this.dropdown.classList.remove("is-active");
    this.bell.setAttribute("aria-expanded", "false");
  }

  async fetchNotifications() {
    try {
      const res = await apiFetch("/api/notifications");
      if (!res || !res.ok) return; // apiFetch handles 401 by redirecting
      this.notifications = await res.json();

      const arrived = this.notifications.filter((n) => !this.seenIds.has(n.id));
      this.notifications.forEach((n) => this.seenIds.add(n.id));
      // A tab stays open all shift, so this set would otherwise grow without
      // bound. Ids only ever increase, so dropping the oldest is safe: a
      // re-appearing old id cannot happen.
      if (this.seenIds.size > 500) {
        this.seenIds = new Set([...this.seenIds].slice(-250));
      }
      if (this.primed && arrived.length && this.soundEnabled()) {
        playChime(this.audioCtx);
      }
      this.primed = true;

      this.render();
    } catch (err) {
      // Never surface a failed poll as a user-facing error: the bell is
      // ambient, and the next tick retries.
      console.error("Failed to fetch notifications", err);
    }
  }

  render() {
    const count = this.notifications.length;
    this.badge.textContent = count > 9 ? "9+" : String(count);
    this.badge.classList.toggle("is-active", count > 0);
    this.bell.setAttribute(
      "aria-label", count ? `Notifications (${count} unread)` : "Notifications",
    );

    if (count === 0) {
      this.list.innerHTML = `<div class="notification-empty">No new notifications</div>`;
      return;
    }

    const soundOn = this.soundEnabled();
    this.list.innerHTML =
      `<div class="notification-actions">
         <span class="notification-sound">
           <span class="notification-sound-label" id="notifSoundLabel">Sound</span>
           <button type="button" class="switch${soundOn ? " is-on" : ""}"
                   role="switch" aria-checked="${soundOn ? "true" : "false"}"
                   aria-labelledby="notifSoundLabel"
                   data-action="toggle-sound"><span class="switch-knob"></span></button>
         </span>
         <button type="button" class="cell-link" data-action="mark-all">Mark all read</button>
       </div>` +
      this.notifications.map((n) => {
        // created_at is serialized as timezone-aware UTC by the API, so it is
        // parsed as-is; appending "Z" (as this used to) double-stamped the
        // offset and shifted every timestamp.
        const when = new Date(n.created_at);
        const stamp = Number.isNaN(when.getTime()) ? "" : when.toLocaleString();

        // A direct link to the annotation canvas for the task, which is where
        // the recipient actually wants to land. The row click still goes to the
        // project's task list; this is the shortcut past it. Omitted when the
        // task or its project has since been deleted, so the bell never renders
        // a dead link.
        const canOpen = n.type === "task" && n.entity_id && n.project_id;
        const openLink = canOpen
          ? `<a class="notification-link" data-role="open-task"
                href="app.html?projectId=${encodeURIComponent(n.project_id)}&taskId=${encodeURIComponent(n.entity_id)}"
                title="Open this task in the annotation workspace">Open task</a>`
          : "";
        const project = n.project_name
          ? `<div class="notification-project">${escapeHTML(n.project_name)}</div>`
          : "";

        return `<div class="notification-item" data-id="${n.id}" role="button" tabindex="0">
            <div class="notification-message">${escapeHTML(n.message)}</div>
            ${project}
            <div class="notification-meta">
              <span class="notification-time">${escapeHTML(stamp)}</span>
              ${openLink}
            </div>
          </div>`;
      }).join("");

    this.list.querySelector('[data-action="mark-all"]')
      ?.addEventListener("click", () => this.markAllRead());

    // Toggling sound must not close the dropdown or mark anything read, so it
    // stops propagation and re-renders in place.
    this.list.querySelector('[data-action="toggle-sound"]')
      ?.addEventListener("click", (e) => {
        e.stopPropagation();
        const next = !this.soundEnabled();
        this.setSoundEnabled(next);
        if (next) playChime(this.audioCtx); // confirm audibly that it is on
        this.render();
      });

    // The "Open task" anchor sits inside the clickable row, so its click must
    // not also run the row handler — that would navigate to the task list and
    // the canvas at once. The anchor still marks the notice read first, so
    // acting on it clears the badge rather than leaving it unread.
    this.list.querySelectorAll('[data-role="open-task"]').forEach((link) => {
      link.addEventListener("click", (e) => {
        e.stopPropagation();
        const id = Number(link.closest(".notification-item")?.dataset.id);
        if (id) this.markRead([id]);
      });
    });

    this.list.querySelectorAll(".notification-item").forEach((el) => {
      const id = Number(el.dataset.id);
      const notif = this.notifications.find((n) => n.id === id);
      if (!notif) return;
      el.addEventListener("click", () => this.open(notif));
      el.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          this.open(notif);
        }
      });
    });
  }

  /** Mark one notice read, then navigate to whatever it refers to. */
  async open(notification) {
    await this.markRead([notification.id]);

    // project_id is resolved server-side for task notices; without it (the task
    // was deleted since) there is nowhere to go, so just close.
    if (notification.project_id) {
      const params = new URLSearchParams({ id: String(notification.project_id) });
      if (notification.type === "task" && notification.entity_id) {
        params.set("activeTaskId", String(notification.entity_id));
      }
      window.location.href = `project.html?${params.toString()}#/tasks`;
      return;
    }
    this.close();
  }

  async markAllRead() {
    // An empty id list means "all" to the API.
    await this.markRead([]);
    this.close();
  }

  async markRead(ids) {
    try {
      const res = await apiFetch("/api/notifications/mark-read", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ notification_ids: ids }),
      });
      if (!res || !res.ok) return;
      await this.fetchNotifications();
    } catch (err) {
      console.error("Failed to mark notifications read", err);
    }
  }
}
