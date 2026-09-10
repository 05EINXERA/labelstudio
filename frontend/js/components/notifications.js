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

export class NotificationManager {
  constructor(bellId, dropdownId, listId, badgeId) {
    this.bell = document.getElementById(bellId);
    this.dropdown = document.getElementById(dropdownId);
    this.list = document.getElementById(listId);
    this.badge = document.getElementById(badgeId);

    this.notifications = [];
    this.pollTimer = null;

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

  toggleDropdown() {
    this.dropdown.classList.toggle("is-active");
    this.bell.setAttribute(
      "aria-expanded", this.dropdown.classList.contains("is-active") ? "true" : "false",
    );
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

    this.list.innerHTML =
      `<div class="notification-actions">
         <button type="button" class="cell-link" data-action="mark-all">Mark all read</button>
       </div>` +
      this.notifications.map((n) => {
        // created_at is serialized as timezone-aware UTC by the API, so it is
        // parsed as-is; appending "Z" (as this used to) double-stamped the
        // offset and shifted every timestamp.
        const when = new Date(n.created_at);
        const stamp = Number.isNaN(when.getTime()) ? "" : when.toLocaleString();
        return `<div class="notification-item" data-id="${n.id}" role="button" tabindex="0">
            <div class="notification-message">${escapeHTML(n.message)}</div>
            <div class="notification-time">${escapeHTML(stamp)}</div>
          </div>`;
      }).join("");

    this.list.querySelector('[data-action="mark-all"]')
      ?.addEventListener("click", () => this.markAllRead());

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
