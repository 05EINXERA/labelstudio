/**
 * Modals Controller
 * 
 * Manages modal dialogs across the workspace:
 * - Settings modal & AI parameter controls
 * - Help & shortcuts modal
 * - Team validation / annotator identity prompt
 * - Task completed modal and next-task progression
 * 
 * In accordance with repository UI rules, all modal visibility transitions
 * strictly use classList.add('is-active') / classList.remove('is-active').
 */
import { setStatus } from "./workspace.js?v=6";
import { state } from "../state.js?v=2";
import { drainTaskTime } from "./timer.js?v=2";

/**
 * Initializes the Settings modal and AI configuration controls.
 */
export function initSettingsModal() {
  const openSettingsBtn = document.getElementById("openSettingsBtn");
  const settingsModal = document.getElementById("settingsModal");
  const settingsClose = document.getElementById("settingsClose");
  const settingsUsernameInput = document.getElementById("settingsUsernameInput");
  const saveUsernameBtn = document.getElementById("saveUsernameBtn");
  const exportDataBtn = document.getElementById("exportDataBtn");
  const importDataInput = document.getElementById("importDataInput");
  const clearDataBtn = document.getElementById("clearDataBtn");

  const aiModelSize = document.getElementById("settingsAiModelSize");
  const aiSamModel = document.getElementById("settingsAiSamModel");

  const dropdownAiConf = document.getElementById("dropdownAiConf");
  const dropdownAiConfVal = document.getElementById("dropdownAiConfVal");
  const dropdownAiNms = document.getElementById("dropdownAiNms");
  const dropdownAiNmsVal = document.getElementById("dropdownAiNmsVal");
  const dropdownSaveAiSettingsBtn = document.getElementById("dropdownSaveAiSettingsBtn");
  const aiSettingsMenuButton = document.querySelector("#aiSettingsMenuButton");
  const aiSettingsDropdownContainer = document.querySelector("#aiSettingsDropdownContainer");

  // Populate initial values
  if (dropdownAiConf) {
    dropdownAiConf.value = localStorage.getItem("ai_conf") || "0.35";
    if (dropdownAiConfVal) dropdownAiConfVal.textContent = dropdownAiConf.value;
    dropdownAiConf.addEventListener("input", (e) => {
      if (dropdownAiConfVal) dropdownAiConfVal.textContent = e.target.value;
    });
  }

  if (dropdownAiNms) {
    dropdownAiNms.value = localStorage.getItem("ai_nms") || "0.45";
    if (dropdownAiNmsVal) dropdownAiNmsVal.textContent = dropdownAiNms.value;
    dropdownAiNms.addEventListener("input", (e) => {
      if (dropdownAiNmsVal) dropdownAiNmsVal.textContent = e.target.value;
    });
  }

  if (aiModelSize) {
    aiModelSize.value = localStorage.getItem("ai_model_size") || "n";
    aiModelSize.addEventListener("change", (e) => {
      localStorage.setItem("ai_model_size", e.target.value);
      setStatus("Detection Model Size Changed");
    });
  }

  if (aiSamModel) {
    aiSamModel.value = localStorage.getItem("ai_sam_model") || "mobile_sam.pt";
    aiSamModel.addEventListener("change", (e) => {
      localStorage.setItem("ai_sam_model", e.target.value);
      setStatus("Magic Wand Model Changed");
    });
  }

  if (openSettingsBtn && settingsModal) {
    openSettingsBtn.addEventListener("click", () => {
      if (settingsUsernameInput) {
        settingsUsernameInput.value = localStorage.getItem("dataset_username") || "";
      }
      settingsModal.classList.add("is-active");
    });
  }

  if (dropdownSaveAiSettingsBtn) {
    dropdownSaveAiSettingsBtn.addEventListener("click", () => {
      if (aiModelSize) localStorage.setItem("ai_model_size", aiModelSize.value);
      if (aiSamModel) localStorage.setItem("ai_sam_model", aiSamModel.value);
      if (dropdownAiConf) localStorage.setItem("ai_conf", dropdownAiConf.value);
      if (dropdownAiNms) localStorage.setItem("ai_nms", dropdownAiNms.value);

      setStatus("AI Settings Applied");
    });
  }

  if (settingsClose && settingsModal) {
    settingsClose.addEventListener("click", () => {
      settingsModal.classList.remove("is-active");
    });
  }

  if (settingsModal) {
    settingsModal.addEventListener("click", (e) => {
      if (e.target === settingsModal) {
        settingsModal.classList.remove("is-active");
      }
    });
  }

  if (aiSettingsMenuButton && aiSettingsDropdownContainer) {
    aiSettingsMenuButton.addEventListener("click", (e) => {
      e.stopPropagation();
      aiSettingsDropdownContainer.classList.toggle("show");
    });
    document.addEventListener("click", (e) => {
      if (aiSettingsDropdownContainer && !aiSettingsDropdownContainer.contains(e.target)) {
        aiSettingsDropdownContainer.classList.remove("show");
      }
    });
  }

  if (saveUsernameBtn && settingsUsernameInput) {
    saveUsernameBtn.addEventListener("click", () => {
      const newName = settingsUsernameInput.value.trim();
      if (newName) {
        localStorage.setItem("dataset_username", newName);
        const displayUsername = document.getElementById("displayUsername");
        if (displayUsername) displayUsername.textContent = newName;
        setStatus("Username updated");
      }
    });
  }

  if (exportDataBtn) {
    exportDataBtn.addEventListener("click", () => {
      const backup = {
        workspace: localStorage.getItem("image-annotation-mvp-v1"),
        team: localStorage.getItem("dataset_team"),
        tasks: localStorage.getItem("dataset_tasks"),
        username: localStorage.getItem("dataset_username")
      };
      const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(backup));
      const downloadAnchor = document.createElement("a");
      downloadAnchor.setAttribute("href", dataStr);
      downloadAnchor.setAttribute("download", "workspace_backup.json");
      document.body.appendChild(downloadAnchor);
      downloadAnchor.click();
      downloadAnchor.remove();
      setStatus("Data exported");
    });
  }

  if (importDataInput) {
    importDataInput.addEventListener("change", (e) => {
      const file = e.target.files[0];
      if (!file) return;

      const reader = new FileReader();
      reader.onload = (event) => {
        try {
          const backup = JSON.parse(event.target.result);
          if (backup.workspace) localStorage.setItem("image-annotation-mvp-v1", backup.workspace);
          if (backup.team) localStorage.setItem("dataset_team", backup.team);
          if (backup.tasks) localStorage.setItem("dataset_tasks", backup.tasks);
          if (backup.username) localStorage.setItem("dataset_username", backup.username);

          alert("Workspace imported successfully! The page will now reload.");
          window.location.reload();
        } catch (err) {
          alert("Invalid backup file.");
          console.error("Failed to parse backup:", err);
        }
      };
      reader.readAsText(file);
    });
  }

  if (clearDataBtn) {
    clearDataBtn.addEventListener("click", () => {
      if (confirm("WARNING: This will permanently delete all your local annotations, tasks, and settings! Are you absolutely sure?")) {
        localStorage.clear();
        window.location.href = "index.html";
      }
    });
  }
}

/**
 * Initializes the Help & Shortcuts modal dialog.
 */
export function initHelpModal() {
  const helpBtnApp = document.getElementById("helpBtnApp");
  const helpModal = document.getElementById("helpModal");
  const helpClose = document.getElementById("helpClose");

  if (helpBtnApp && helpModal) {
    helpBtnApp.addEventListener("click", () => {
      helpModal.classList.add("is-active");
    });
  }

  if (helpClose && helpModal) {
    helpClose.addEventListener("click", () => {
      helpModal.classList.remove("is-active");
    });
  }

  if (helpModal) {
    helpModal.addEventListener("click", (e) => {
      if (e.target === helpModal) {
        helpModal.classList.remove("is-active");
      }
    });
  }
}

/**
 * Initializes the session annotator validation modal.
 */
export function initTeamValidationModal() {
  const teamValidationForm = document.getElementById("teamValidationForm");
  const teamValidationModal = document.getElementById("teamValidationModal");
  const teamValidationName = document.getElementById("teamValidationName");

  if (teamValidationForm && teamValidationModal) {
    teamValidationForm.addEventListener("submit", (e) => {
      e.preventDefault();
      const nameInput = teamValidationName ? teamValidationName.value.trim() : "";
      if (!nameInput) return;

      localStorage.setItem("dataset_username", nameInput);

      const displayUser = document.getElementById("displayUsername");
      if (displayUser) displayUser.textContent = nameInput;

      teamValidationModal.classList.remove("is-active");
      const userPanel = document.getElementById("userPanel");
      if (userPanel) userPanel.style.display = "block";
    });
  }
}

/**
 * Initializes the Task Completed modal and complete button action.
 * @param {Function} onContinue Callback invoked when the user proceeds after completing a task.
 */
export function initTaskCompletedModal(onContinue) {
  const completeTaskBtn = document.getElementById("completeTaskBtn");
  const tcModal = document.getElementById("taskCompletedModal");
  const tcClose = document.getElementById("taskCompletedClose");
  const tcOk = document.getElementById("taskCompletedOkBtn");

  function closeModal() {
    if (tcModal) tcModal.classList.remove("is-active");
    if (typeof onContinue === "function") {
      onContinue();
    }
  }

  if (tcClose) tcClose.addEventListener("click", closeModal);
  if (tcOk) tcOk.addEventListener("click", closeModal);

  if (completeTaskBtn) {
    completeTaskBtn.addEventListener("click", async () => {
      if (!state.gallery || state.gallery.length === 0) {
        alert("No image to complete!");
        return;
      }
      const currentTask = state.gallery[state.galleryIndex];
      if (currentTask && currentTask.id) {
        try {
          await drainTaskTime(currentTask, {
            status: "Completed",
            annotations: state.annotations
          });
          currentTask.status = "Completed";
          if (tcModal) tcModal.classList.add("is-active");
        } catch (e) {
          console.error("Failed to complete task:", e);
          alert("Failed to mark task as completed.");
        }
      } else {
        if (tcModal) tcModal.classList.add("is-active");
      }
    });
  }
}

/**
 * Convenience initializer for all modals in the workspace.
 * @param {Object} options
 * @param {Function} options.onTaskCompleteContinue Callback when user clicks Continue on Task Completed dialog.
 */
export function initModals({ onTaskCompleteContinue } = {}) {
  initSettingsModal();
  initHelpModal();
  initTeamValidationModal();
  initTaskCompletedModal(onTaskCompleteContinue);
}
