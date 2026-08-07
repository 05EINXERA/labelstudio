import os
import re
import pytest

FRONTEND_JS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "js")

def test_p4_modules_exist():
    """Verify that all P4 extracted modules exist in frontend/js/components/."""
    expected_files = [
        os.path.join(FRONTEND_JS_DIR, "components", "gallery.js"),
        os.path.join(FRONTEND_JS_DIR, "components", "modals.js"),
        os.path.join(FRONTEND_JS_DIR, "components", "mode-controls.js"),
        os.path.join(FRONTEND_JS_DIR, "components", "opacity-control.js"),
        os.path.join(FRONTEND_JS_DIR, "init.js"),
    ]
    for file_path in expected_files:
        assert os.path.isfile(file_path), f"Expected file not found: {file_path}"


def test_opacity_control_module():
    """Verify opacity-control.js exports, persistence keys, and shortcut handlers."""
    opacity_file = os.path.join(FRONTEND_JS_DIR, "components", "opacity-control.js")
    assert os.path.isfile(opacity_file)
    with open(opacity_file, "r", encoding="utf-8") as f:
        content = f.read()

    assert "export function setAnnotationOpacity" in content
    assert "export function initOpacityControl" in content
    assert "annotationOpacity" in content
    assert "drawAllLayers" in content
    assert "annotation_opacity_percent" in content



def test_js_import_paths_resolve():
    """Verify that all ES module import paths across frontend/js resolve to existing files on disk."""
    import_regex = re.compile(r'import\s+(?:(?:(?:\w+|\{[^}]+\}|\*\s+as\s+\w+)\s+from\s+)?[\'"]([^\'"]+)[\'"]|[\'"]([^\'"]+)[\'"])')

    for root, _, files in os.walk(FRONTEND_JS_DIR):
        for file in files:
            if not file.endswith(".js"):
                continue
            file_path = os.path.join(root, file)
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            for match in import_regex.finditer(content):
                import_spec = match.group(1) or match.group(2)
                # Strip query strings like ?v=1
                clean_path = import_spec.split("?")[0]
                if clean_path.startswith("."):
                    target_path = os.path.normpath(os.path.join(root, clean_path))
                    assert os.path.isfile(target_path), (
                        f"Import '{import_spec}' in {file_path} does not resolve to a file. "
                        f"Looked at: {target_path}"
                    )


def test_modal_manipulation_conventions():
    """Verify that modal visibility adheres to rule: classList.add/remove('is-active')."""
    modals_file = os.path.join(FRONTEND_JS_DIR, "components", "modals.js")
    with open(modals_file, "r", encoding="utf-8") as f:
        content = f.read()

    # Ensure is-active is used
    assert "classList.add(\"is-active\")" in content or "classList.add('is-active')" in content
    assert "classList.remove(\"is-active\")" in content or "classList.remove('is-active')" in content
    # Ensure style.display = 'flex' or 'none' is not used on modals
    assert "settingsModal.style.display" not in content
    assert "helpModal.style.display" not in content
    assert "tcModal.style.display" not in content
    assert "teamValidationModal.style.display" not in content


def test_undo_redo_and_workspace_reset_hygiene():
    """Verify that undo history is reset between tasks and labels are not corrupted by undo/redo."""
    state_file = os.path.join(FRONTEND_JS_DIR, "state.js")
    with open(state_file, "r", encoding="utf-8") as f:
        state_content = f.read()

    # resetWorkspaceForNewImage must reset history and redoHistory
    assert "state.history = [];" in state_content
    assert "state.redoHistory = [];" in state_content

    # snapshot() must not store labels
    assert "labels: state.labels" not in state_content

    gallery_file = os.path.join(FRONTEND_JS_DIR, "components", "gallery.js")
    with open(gallery_file, "r", encoding="utf-8") as f:
        gallery_content = f.read()

    # switchImage must not snapshot before resetting
    assert "snapshot()" not in gallery_content

    interactions_file = os.path.join(FRONTEND_JS_DIR, "canvas", "interactions.js")
    with open(interactions_file, "r", encoding="utf-8") as f:
        interactions_content = f.read()

    # undoAction and redoAction must not overwrite state.labels
    assert "state.labels = restored.labels" not in interactions_content

