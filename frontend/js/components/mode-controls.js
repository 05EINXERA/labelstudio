/**
 * Mode Controls & Workspace Toolbar Actions
 * 
 * Handles interaction modes (Draw, Select, Box, Polygon, Comment, Magic Wand),
 * history actions (Undo, Redo), annotation mutations (Delete, Clear), manual save actions,
 * move objects lock/unlock toggle, and comment overlay keyboard handling.
 */
import { generateUUID, round } from "../utils.js?v=1";
import { state, snapshot } from "../state.js?v=1";
import { view } from "../canvas/view.js?v=1";
import { commentOverlayRefs } from "../comment-overlay.js?v=1";
import {
  drawMode, selectMode, boxMode, polygonMode, commentMode, magicWandMode,
  autoDetectButton, autoTagButton, undoButton, redoButton, deleteButton,
  clearButton, saveButton, stageWrap
} from "../dom.js?v=1";
import { setStatus, save, render, manualSaveWithUI } from "./workspace.js?v=1";
import { autoDetectObjects, autoTagObjects, preloadMagicWand } from "../ai/detect.js?v=1";
import { finalizePolygon, deleteSelected, undoAction, redoAction } from "../canvas/interactions.js?v=1";

/**
 * Initializes Move Objects toggle button and dropdown menu.
 */
export function initMoveObjectsToggle() {
  const moveObjectsDropdownContainer = document.querySelector("#moveObjectsDropdownContainer");
  const moveObjectsMenuButton = document.querySelector("#moveObjectsMenuButton");
  const moveObjectsToggle = document.querySelector("#moveObjectsToggle");

  function renderMoveObjectsUI() {
    const on = state.moveObjectsUnlocked;
    if (moveObjectsToggle) {
      moveObjectsToggle.classList.toggle("is-on", on);
      moveObjectsToggle.setAttribute("aria-checked", on ? "true" : "false");
    }
    if (moveObjectsMenuButton) {
      const icon = moveObjectsMenuButton.querySelector(".btn-icon");
      const label = moveObjectsMenuButton.querySelector(".btn-label");
      if (icon) icon.textContent = on ? "🔓" : "🔒";
      if (label) label.textContent = on ? "Unlocked" : "Locked";
      moveObjectsMenuButton.classList.toggle("is-active", on);
    }
  }

  if (moveObjectsMenuButton) {
    moveObjectsMenuButton.addEventListener("click", (e) => {
      e.stopPropagation();
      const open = moveObjectsDropdownContainer.classList.toggle("show");
      moveObjectsMenuButton.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  if (moveObjectsToggle) {
    moveObjectsToggle.addEventListener("click", (e) => {
      e.stopPropagation();
      state.moveObjectsUnlocked = !state.moveObjectsUnlocked;
      renderMoveObjectsUI();
      setStatus(state.moveObjectsUnlocked ? "Move Objects: On" : "Move Objects: Off");
    });
  }

  document.addEventListener("click", (e) => {
    if (moveObjectsDropdownContainer && !moveObjectsDropdownContainer.contains(e.target)) {
      moveObjectsDropdownContainer.classList.remove("show");
      if (moveObjectsMenuButton) moveObjectsMenuButton.setAttribute("aria-expanded", "false");
    }
  });

  renderMoveObjectsUI();
}

/**
 * Initializes comment popup input handlers (Enter to save, Escape to cancel).
 */
export function initCommentInput() {
  if (!commentOverlayRefs?.commentOverlayInput) return;

  commentOverlayRefs.commentOverlayInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      const text = commentOverlayRefs.commentOverlayInput.value;
      if (text && text.trim() !== "") {
        if (view.pendingCommentEditId) {
          const annotation = state.annotations.find(a => a.id === view.pendingCommentEditId);
          if (annotation) {
            snapshot();
            annotation.text = text.trim();
            render();
            save();
            setStatus("Comment updated");
          }
          view.pendingCommentEditId = null;
          commentOverlayRefs.commentOverlay.classList.add("is-hidden");
        } else if (view.pendingCommentPoint) {
          snapshot();
          const annotation = {
            id: generateUUID(),
            type: "comment",
            text: text.trim(),
            author: localStorage.getItem("dataset_username") || "Unknown",
            x: round(view.pendingCommentPoint.x),
            y: round(view.pendingCommentPoint.y),
            width: 20,
            height: 20,
            points: [
              { x: view.pendingCommentPoint.x - 10, y: view.pendingCommentPoint.y - 10 },
              { x: view.pendingCommentPoint.x + 10, y: view.pendingCommentPoint.y - 10 },
              { x: view.pendingCommentPoint.x + 10, y: view.pendingCommentPoint.y + 10 },
              { x: view.pendingCommentPoint.x - 10, y: view.pendingCommentPoint.y + 10 }
            ]
          };
          state.annotations.push(annotation);
          state.selectedId = annotation.id;
          view.pendingCommentPoint = null;
          commentOverlayRefs.commentOverlay.classList.add("is-hidden");
          render();
          save();
          setStatus("Comment added");
        }
      } else {
        view.pendingCommentPoint = null;
        view.pendingCommentEditId = null;
        commentOverlayRefs.commentOverlay.classList.add("is-hidden");
        render();
      }
    } else if (e.key === "Escape") {
      e.preventDefault();
      view.pendingCommentPoint = null;
      view.pendingCommentEditId = null;
      commentOverlayRefs.commentOverlay.classList.add("is-hidden");
      render();
    }
  });
}

export function updateModeControlsLockState() {
  const modeButtons = [drawMode, boxMode, polygonMode, commentMode, magicWandMode];
  const actionButtons = [undoButton, redoButton, deleteButton, clearButton, saveButton];
  const groupButtons = [
    document.querySelector("#groupButton"),
    document.querySelector("#ungroupButton")
  ];
  const allButtons = [...modeButtons, ...actionButtons, ...groupButtons];

  allButtons.forEach(btn => {
    if (btn) {
      btn.disabled = state.statusLocked;
      btn.style.opacity = state.statusLocked ? "0.5" : "1";
      btn.style.cursor = state.statusLocked ? "not-allowed" : "pointer";
    }
  });

  // Keep status dropdown enabled
  const statusDropdown = document.getElementById('taskStatusSelect');
  if (statusDropdown) {
    statusDropdown.disabled = false;
    statusDropdown.style.opacity = "1";
  }
}

/**
 * Initializes toolbar mode buttons, undo/redo, delete, clear, and save actions.
 */
export function initModeControls() {
  if (drawMode) {
    drawMode.addEventListener("click", () => {
      if (state.statusLocked) return;
      if (!state.activeLabelId) {
        setStatus("Pick a class first, then draw");
        render();
        return;
      }
      state.mode = "draw";
      state.selectedId = null;
      state.selectedIds.clear();
      render();
    });
  }

  if (selectMode) {
    selectMode.addEventListener("click", () => {
      if (state.statusLocked) return;
      if (view.drag?.type === "draw-polygon") {
        finalizePolygon();
      }
      state.mode = "select";
      render();
    });
  }

  if (boxMode) {
    boxMode.addEventListener("click", () => {
      if (state.statusLocked) return;
      if (view.drag?.type === "draw-polygon") {
        finalizePolygon();
      }
      state.mode = "draw";
      state.shape = "box";
      state.selectedId = null;
      state.selectedIds.clear();
      render();
    });
  }

  if (polygonMode) {
    polygonMode.addEventListener("click", () => {
      if (state.statusLocked) return;
      state.mode = "draw";
      state.shape = "polygon";
      state.selectedId = null;
      state.selectedIds.clear();
      render();
    });
  }

  if (commentMode) {
    commentMode.addEventListener("click", () => {
      if (state.statusLocked) return;
      if (view.drag?.type === "draw-polygon") {
        finalizePolygon();
      }
      state.mode = "draw";
      state.shape = "comment";
      state.selectedId = null;
      state.selectedIds.clear();
      render();
    });
  }

  if (magicWandMode) {
    magicWandMode.addEventListener("click", () => {
      if (state.statusLocked) return;
      if (view.drag?.type === "draw-polygon") {
        finalizePolygon();
      }
      state.mode = "draw";
      state.shape = "magicWand";
      state.selectedId = null;
      state.selectedIds.clear();
      preloadMagicWand();
      render();
    });
  }

  if (undoButton) {
    undoButton.addEventListener("click", () => undoAction());
  }

  if (redoButton) {
    redoButton.addEventListener("click", () => redoAction());
  }

  if (deleteButton) {
    deleteButton.addEventListener("click", () => {
      if (state.statusLocked) return;
      deleteSelected();
    });
  }

  if (clearButton) {
    clearButton.addEventListener("click", () => {
      if (state.statusLocked) return;
      state.annotations = [];
      state.selectedIds.clear();
      state.selectedId = null;
      view.drag = null;
      render();
      save();
      setStatus("Cleared all");
    });
  }

  if (autoDetectButton) {
    autoDetectButton.addEventListener("click", () => autoDetectObjects({ replace: true }));
  }

  if (autoTagButton) {
    autoTagButton.addEventListener("click", () => autoTagObjects());
  }

  if (saveButton) {
    saveButton.addEventListener("click", () => manualSaveWithUI());
  }

  const saveDropdownToggle = document.getElementById('saveDropdownToggle');
  const saveDropdownMenu = document.getElementById('saveDropdownMenu');

  if (saveDropdownToggle && saveDropdownMenu) {
    saveDropdownToggle.addEventListener('click', (e) => {
      e.stopPropagation();
      const isVisible = saveDropdownMenu.style.display === 'block';
      saveDropdownMenu.style.display = isVisible ? 'none' : 'block';
    });

    document.addEventListener('click', (e) => {
      if (!saveDropdownMenu.contains(e.target) && e.target !== saveDropdownToggle) {
        saveDropdownMenu.style.display = 'none';
      }
    });

    saveDropdownMenu.querySelectorAll('.dropdown-item').forEach(btn => {
      btn.addEventListener('click', (e) => {
        saveDropdownMenu.style.display = 'none';
        const targetStatus = e.currentTarget.dataset.status;
        const wasLocked = state.statusLocked;
        const lockingStatus = ["Completed", "Approved", "Verified"];
        state.statusLocked = lockingStatus.includes(targetStatus);
        if (wasLocked && !state.statusLocked) {
          updateModeControlsLockState();
        }
        manualSaveWithUI(targetStatus);
      });
    });
  }

  // Global Ctrl+S / Cmd+S shortcut for saving annotations
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "s") {
      e.preventDefault();
      if (saveButton) {
        manualSaveWithUI();
      }
    }
  });

  // Suppress browser default navigate-on-drop
  if (stageWrap) {
    stageWrap.addEventListener("dragover", (e) => e.preventDefault());
    stageWrap.addEventListener("drop", (e) => e.preventDefault());
  }

  initMoveObjectsToggle();
  initCommentInput();
}
