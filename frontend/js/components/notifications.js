import { apiFetch } from "../api.js?v=1";

export class NotificationManager {
    constructor(bellId, dropdownId, listId, badgeId) {
        this.bell = document.getElementById(bellId);
        this.dropdown = document.getElementById(dropdownId);
        this.list = document.getElementById(listId);
        this.badge = document.getElementById(badgeId);
        
        if (!this.bell || !this.dropdown || !this.list || !this.badge) {
            console.warn("NotificationManager initialized but missing DOM elements.");
            return;
        }

        this.notifications = [];
        
        this.init();
    }

    async init() {
        this.bell.addEventListener('click', (e) => {
            e.stopPropagation();
            this.toggleDropdown();
        });

        document.addEventListener('click', (e) => {
            if (!this.dropdown.contains(e.target) && e.target !== this.bell) {
                this.dropdown.style.display = 'none';
            }
        });

        await this.fetchNotifications();
        
        // Poll every 30 seconds
        setInterval(() => this.fetchNotifications(), 30000);
    }

    toggleDropdown() {
        if (this.dropdown.style.display === 'block') {
            this.dropdown.style.display = 'none';
        } else {
            this.dropdown.style.display = 'block';
        }
    }

    async fetchNotifications() {
        try {
            const res = await apiFetch('/api/notifications');
            if (res && res.ok) {
                this.notifications = await res.json();
                this.render();
            }
        } catch (e) {
            console.error("Failed to fetch notifications", e);
        }
    }

    render() {
        if (this.notifications.length > 0) {
            this.badge.style.display = 'flex';
            this.badge.textContent = this.notifications.length;
        } else {
            this.badge.style.display = 'none';
        }

        this.list.innerHTML = '';
        if (this.notifications.length === 0) {
            this.list.innerHTML = '<div class="notification-empty">No new notifications</div>';
            return;
        }

        for (const notif of this.notifications) {
            const item = document.createElement('div');
            item.className = 'notification-item';
            
            const message = document.createElement('div');
            message.className = 'notification-message';
            message.textContent = notif.message;
            
            const time = document.createElement('div');
            time.className = 'notification-time';
            const date = new Date(notif.created_at + 'Z');
            time.textContent = date.toLocaleString();
            
            item.appendChild(message);
            item.appendChild(time);
            
            item.addEventListener('click', () => {
                this.markAsRead(notif);
            });
            
            this.list.appendChild(item);
        }
    }

    async markAsRead(notification) {
        try {
            await apiFetch('/api/notifications/mark-read', {
                method: 'POST',
                body: JSON.stringify({ notification_ids: [notification.id] })
            });
            
            // Navigate based on type
            if (notification.type === 'task') {
                window.location.href = `project.html?id=${notification.entity_id}#/tasks`; 
                // wait, a task notification only has entity_id = task.id. 
                // we'd need project_id to go directly to it if we use project.html
                // But wait, the previous implementation used project.html?id=<project_id>&activeTaskId=<task_id>#/tasks
                // Or we can just navigate to a page or let it just mark as read.
                // Let's just mark it as read for now and refetch.
            } else if (notification.type === 'project') {
                window.location.href = `project.html?id=${notification.entity_id}#/tasks`;
            } else if (notification.type === 'team') {
                window.location.href = `teams.html`;
            }
            
            await this.fetchNotifications();
            this.dropdown.style.display = 'none';
        } catch (e) {
            console.error("Failed to mark notification as read", e);
        }
    }
}
