const urlParams = new URLSearchParams(window.location.search);
    const projectId = urlParams.get('id');

    // --- State ---
    let allTasks = [];
    let sortCol = 'id';
    let sortDesc = false;
    let currentPage = 1;
    let rowsPerPage = 10;
    let searchQuery = '';
    let statusFilter = 'All';
    let selectedTaskIds = new Set();
    let teamMembers = [];

    if (!projectId) {
      document.getElementById('projectName').textContent = "Project Not Found";
    } else {
      loadProjectDetails();
      loadTeam().then(() => {
        loadProjectTasks();
      });
    }

    async function loadTeam() {
      try {
        const res = await fetch('/api/team');
        if (res.ok) {
          const data = await res.json();
          teamMembers = data.map(t => t.name);
          
          const editTaskAssignee = document.getElementById('editTaskAssignee');
          if (editTaskAssignee) {
            teamMembers.forEach(member => {
              const opt = document.createElement('option');
              opt.value = member;
              opt.textContent = member;
              editTaskAssignee.appendChild(opt);
            });
          }
        }
      } catch (err) {
        console.error("Failed to load team", err);
      }
    }

    document.getElementById('startAnnotatingBtn').addEventListener('click', () => {
      window.location.href = `app.html?projectId=${projectId}`;
    });

    document.getElementById('imageUploadInput').addEventListener('change', async (e) => {
      const files = e.target.files;
      if (files.length === 0) return;

      const formData = new FormData();
      for (let i = 0; i < files.length; i++) {
        formData.append('file', files[i]);
      }

      try {
        const assignee = encodeURIComponent(localStorage.getItem('dataset_username') || 'Unknown');
        const res = await fetch(`/api/projects/${projectId}/upload?assignee=${assignee}`, {
          method: 'POST',
          body: formData
        });
        if (res.ok) {
          e.target.value = '';
          loadProjectTasks();
        } else {
          alert('Upload failed');
        }
      } catch (err) {
        console.error(err);
        alert('Upload failed');
      }
    });

    async function loadProjectDetails() {
      try {
        const username = localStorage.getItem('dataset_username') || '';
        const res = await fetch(`/api/projects?creator=${encodeURIComponent(username)}`);
        if (res.ok) {
          const projects = await res.json();
          const project = projects.find(p => p.id == projectId);
          if (project) {
            document.getElementById('projectName').textContent = project.name;
          } else {
            document.getElementById('projectName').textContent = "Project Not Found";
          }
        }
      } catch (e) {
        console.error(e);
      }
    }

    async function loadProjectTasks() {
      try {
        const res = await fetch(`/api/tasks?projectId=${projectId}`);
        if (res.ok) {
          allTasks = await res.json();
          renderTable();
        }
      } catch (e) {
        console.error(e);
      }
    }

    function renderTable() {
      const tbody = document.getElementById('taskTableBody');
      tbody.innerHTML = '';

      // 1. Filter
      let filtered = allTasks.filter(t => {
        const matchesSearch = t.description && t.description.toLowerCase().includes(searchQuery.toLowerCase());
        const matchesStatus = statusFilter === 'All' || t.status === statusFilter;
        return matchesSearch && matchesStatus;
      });

      // 2. Sort
      filtered.sort((a, b) => {
        let valA = a[sortCol];
        let valB = b[sortCol];
        
        // Handle nulls and different types safely
        if (valA == null) valA = '';
        if (valB == null) valB = '';
        if (typeof valA === 'string') valA = valA.toLowerCase();
        if (typeof valB === 'string') valB = valB.toLowerCase();

        if (valA < valB) return sortDesc ? 1 : -1;
        if (valA > valB) return sortDesc ? -1 : 1;
        return 0;
      });

      // 3. Paginate
      const totalPages = Math.ceil(filtered.length / rowsPerPage) || 1;
      if (currentPage > totalPages) currentPage = totalPages;
      const startIndex = (currentPage - 1) * rowsPerPage;
      const pageData = filtered.slice(startIndex, startIndex + rowsPerPage);

      // Update Pagination UI
      document.getElementById('pageInfo').textContent = `Showing ${pageData.length > 0 ? startIndex + 1 : 0} to ${Math.min(startIndex + rowsPerPage, filtered.length)} of ${filtered.length} entries`;
      document.getElementById('prevPageBtn').disabled = currentPage === 1;
      document.getElementById('nextPageBtn').disabled = currentPage === totalPages;

      // Update Select All Checkbox state
      const selectAllCb = document.getElementById('selectAllCheckbox');
      selectAllCb.checked = pageData.length > 0 && pageData.every(t => selectedTaskIds.has(t.id));

      if (filtered.length === 0) {
        tbody.innerHTML = '<tr><td colspan="11" style="text-align: center; color: var(--muted);">No tasks match your filters.</td></tr>';
        updateBulkUI();
        return;
      }

      // 4. Render Rows
      pageData.forEach(task => {
        // Calculate a 1-based index for this project based on its original position
        const displayId = allTasks.findIndex(t => t.id === task.id) + 1;

        const tr = document.createElement('tr');
        const badgeClass = (task.status || 'new').toLowerCase().replace(/\s+/g, '-');
        const imgUrl = '/' + task.image_path.replace(/\\\\/g, '/');
        
        // Time Formatting
        const secs = task.time_spent || 0;
        const h = Math.floor(secs / 3600).toString().padStart(2, '0');
        const m = Math.floor((secs % 3600) / 60).toString().padStart(2, '0');
        const s = (secs % 60).toString().padStart(2, '0');
        const formattedTime = secs > 0 ? `${h}:${m}:${s}` : '-';

        // Date Formatting
        let formattedDate = '-';
        if (task.updated_at) {
          const d = new Date(task.updated_at + 'Z'); // UTC
          if (!isNaN(d.getTime())) {
            formattedDate = d.toLocaleString();
          } else {
            formattedDate = task.updated_at;
          }
        }

        const isChecked = selectedTaskIds.has(task.id) ? 'checked' : '';

        let commentCount = 0;
        let classCount = 0;
        if (task.annotations) {
          let anns = [];
          if (Array.isArray(task.annotations)) {
            anns = task.annotations;
          } else if (typeof task.annotations === 'string') {
            try {
              anns = JSON.parse(task.annotations);
            } catch (e) {
              console.error("Failed to parse annotations for task", task.id);
            }
          }
          if (Array.isArray(anns)) {
            commentCount = anns.filter(a => a.type === "comment").length;
            const uniqueClasses = new Set();
            anns.forEach(a => {
              if (a.labelId) uniqueClasses.add(a.labelId);
            });
            classCount = uniqueClasses.size;
          }
        }

        tr.innerHTML = `
          <td style="text-align: center;"><input type="checkbox" class="row-checkbox" data-id="${task.id}" ${isChecked}></td>
          <td>${displayId}</td>
          <td><img src="${imgUrl}" style="height: 40px; border-radius: 4px; border: 1px solid var(--line);"></td>
          <td style="max-width: 400px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">
            <a href="app.html?projectId=${projectId}&taskId=${task.id}" style="color: #0066cc; text-decoration: underline;" onmouseover="this.style.textDecoration='none'" onmouseout="this.style.textDecoration='underline'">
              ${task.description}
            </a>
          </td>
          <td style="font-family: monospace; font-size: 0.95rem;">${formattedTime}</td>
          <td><span class="status-badge ${badgeClass}">${task.status}</span></td>
          <td style="font-size: 0.85rem; color: var(--muted);">${formattedDate}</td>
          <td style="text-align: center; color: var(--muted);">${classCount}</td>
          <td style="text-align: center; color: var(--muted);">💬 ${commentCount}</td>
          <td style="text-align: center; white-space: nowrap;">
            <button type="button" class="icon-button edit-task-btn" 
              data-id="${task.id}" 
              data-description="${task.description}" 
              data-assignee="${task.assignee || ''}" 
              data-status="${task.status}" 
              data-image="${imgUrl}"
              style="padding: 6px; border-radius: 4px; display: inline-flex; align-items: center; justify-content: center;" title="Edit Task">
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/><path d="m15 5 4 4"/></svg>
            </button>
            <button type="button" class="icon-button delete-task-btn" 
              data-id="${task.id}" 
              style="padding: 6px; border-radius: 4px; color: #ff6b6b; display: inline-flex; align-items: center; justify-content: center;" title="Delete Task">
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><line x1="10" x2="10" y1="11" y2="17"/><line x1="14" x2="14" y1="11" y2="17"/></svg>
            </button>
          </td>
        `;
        tbody.appendChild(tr);
      });

      // Attach row listeners
      document.querySelectorAll('.row-checkbox').forEach(cb => {
        cb.addEventListener('change', (e) => {
          const id = parseInt(e.target.dataset.id, 10);
          if (e.target.checked) selectedTaskIds.add(id);
          else selectedTaskIds.delete(id);
          updateBulkUI();
          
          // Update select all checkbox logic based on new selection
          const currentVisibleIds = pageData.map(t => t.id);
          selectAllCb.checked = currentVisibleIds.every(id => selectedTaskIds.has(id));
        });
      });

      document.querySelectorAll('.edit-task-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
          const b = e.currentTarget;
          openEditTaskModal(b.dataset.id, b.dataset.description, b.dataset.assignee, b.dataset.status, b.dataset.image);
        });
      });

      document.querySelectorAll('.delete-task-btn').forEach(btn => {
        btn.addEventListener('click', async (e) => {
          const taskId = e.currentTarget.dataset.id;
          if (confirm('Are you sure you want to delete this task? This action cannot be undone.')) {
            try {
              const res = await fetch(`/api/tasks/${taskId}`, { method: 'DELETE' });
              if (res.ok) {
                selectedTaskIds.delete(parseInt(taskId, 10));
                loadProjectTasks();
              } else {
                alert('Failed to delete task.');
              }
            } catch (err) {
              console.error(err);
              alert('Failed to delete task.');
            }
          }
        });
      });



      updateBulkUI();
    }

    // --- Toolbar & Table Event Listeners ---
    document.addEventListener('DOMContentLoaded', () => {
      document.getElementById('searchFilter').addEventListener('input', (e) => {
        searchQuery = e.target.value;
        currentPage = 1;
        renderTable();
      });

      document.getElementById('statusFilter').addEventListener('change', (e) => {
        statusFilter = e.target.value;
        currentPage = 1;
        renderTable();
      });

      document.getElementById('prevPageBtn').addEventListener('click', () => {
        if (currentPage > 1) {
          currentPage--;
          renderTable();
        }
      });

      document.getElementById('nextPageBtn').addEventListener('click', () => {
        currentPage++;
        renderTable();
      });

      // Sorting
      document.querySelectorAll('th[data-sort]').forEach(th => {
        th.addEventListener('click', (e) => {
          const col = e.currentTarget.dataset.sort;
          if (sortCol === col) {
            sortDesc = !sortDesc;
          } else {
            sortCol = col;
            sortDesc = false;
          }
          
          // Update header arrows
          document.querySelectorAll('th[data-sort]').forEach(el => {
            el.innerHTML = el.innerHTML.replace(/ [↑↓↕]/, ' ↕');
          });
          e.currentTarget.innerHTML = e.currentTarget.innerHTML.replace(' ↕', sortDesc ? ' ↓' : ' ↑');
          
          renderTable();
        });
      });

      // Select All
      document.getElementById('selectAllCheckbox').addEventListener('change', (e) => {
        const isChecked = e.target.checked;
        const visibleCheckboxes = document.querySelectorAll('.row-checkbox');
        visibleCheckboxes.forEach(cb => {
          cb.checked = isChecked;
          const id = parseInt(cb.dataset.id, 10);
          if (isChecked) selectedTaskIds.add(id);
          else selectedTaskIds.delete(id);
        });
        updateBulkUI();
      });
    });

    // --- Bulk Action Logic ---
    function updateBulkUI() {
      const container = document.getElementById('bulkActions');
      const countLabel = document.getElementById('selectedCount');
      if (selectedTaskIds.size > 0) {
        container.style.display = 'flex';
        countLabel.textContent = `${selectedTaskIds.size} selected`;
      } else {
        container.style.display = 'none';
      }
    }

    document.getElementById('bulkDeleteBtn').addEventListener('click', async () => {
      if (selectedTaskIds.size === 0) return;
      if (confirm(`Are you sure you want to permanently delete ${selectedTaskIds.size} tasks?`)) {
        try {
          const res = await fetch('/api/tasks/bulk-delete', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ ids: Array.from(selectedTaskIds) })
          });
          if (res.ok) {
            selectedTaskIds.clear();
            loadProjectTasks();
          } else {
            alert('Failed to bulk delete tasks.');
          }
        } catch(e) {
          console.error(e);
          alert('Failed to bulk delete tasks.');
        }
      }
    });

    document.getElementById('bulkAssignBtn').addEventListener('click', async () => {
      if (selectedTaskIds.size === 0) return;
      const newAssignee = prompt("Enter the new assignee's username for the selected tasks:");
      if (newAssignee === null) return; // cancelled
      
      try {
        const res = await fetch('/api/tasks/bulk-update', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ ids: Array.from(selectedTaskIds), assignee: newAssignee.trim() })
        });
        if (res.ok) {
          selectedTaskIds.clear();
          loadProjectTasks();
        } else {
          alert('Failed to bulk assign tasks.');
        }
      } catch(e) {
        console.error(e);
        alert('Failed to bulk assign tasks.');
      }
    });

    // --- Edit Task Modal Logic ---
    function openEditTaskModal(id, description, assignee, status, imageUrl) {
      document.getElementById('editTaskId').value = id;
      document.getElementById('editTaskDescription').value = description;
      document.getElementById('editTaskAssignee').value = assignee;
      document.getElementById('editTaskStatus').value = status;
      
      const imgPreview = document.getElementById('editTaskImagePreview');
      if (imageUrl) {
        imgPreview.src = imageUrl;
        imgPreview.style.display = 'inline-block';
      } else {
        imgPreview.style.display = 'none';
      }
      
      document.getElementById('editTaskModal').classList.add('is-active');
    }

    function closeEditTaskModal() {
      document.getElementById('editTaskModal').classList.remove('is-active');
    }

    document.addEventListener('DOMContentLoaded', () => {
      document.getElementById('editTaskClose').addEventListener('click', closeEditTaskModal);
      document.getElementById('editTaskCancelBtn').addEventListener('click', closeEditTaskModal);

      document.getElementById('editTaskStartAnnotatingBtn').addEventListener('click', () => {
        const taskId = document.getElementById('editTaskId').value;
        if (taskId && projectId) {
          window.location.href = `app.html?projectId=${projectId}&taskId=${taskId}`;
        }
      });

      document.getElementById('editTaskForm').addEventListener('submit', async (e) => {
        e.preventDefault();
        const id = document.getElementById('editTaskId').value;
        const description = document.getElementById('editTaskDescription').value;
        const assignee = document.getElementById('editTaskAssignee').value;
        const status = document.getElementById('editTaskStatus').value;

        try {
          const res = await fetch('/api/tasks', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ id, description, assignee, status })
          });
          if (res.ok) {
            closeEditTaskModal();
            loadProjectTasks();
          } else {
            alert('Failed to update task.');
          }
        } catch (err) {
          console.error(err);
          alert('Failed to update task.');
        }
      });
    });