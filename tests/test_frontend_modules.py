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


def test_active_task_table_prioritization():
    """Verify data-table priorityRowId support and tasks.js active task prioritization."""
    data_table_file = os.path.join(FRONTEND_JS_DIR, "components", "data-table.js")
    with open(data_table_file, "r", encoding="utf-8") as f:
        dt_content = f.read()

    assert "priorityRowId" in dt_content
    assert "setPriorityRowId" in dt_content
    assert "getPriorityRowId" in dt_content

    gallery_file = os.path.join(FRONTEND_JS_DIR, "components", "gallery.js")
    with open(gallery_file, "r", encoding="utf-8") as f:
        gallery_content = f.read()

    assert "last_active_task_" in gallery_content
    assert "activeTaskId" in gallery_content

    tasks_file = os.path.join(FRONTEND_JS_DIR, "pages", "project", "tasks.js")
    with open(tasks_file, "r", encoding="utf-8") as f:
        tasks_content = f.read()

    assert "activeTaskId" in tasks_content
    assert "priorityRowId: activeTaskId" in tasks_content
    assert "row-recent-task" in tasks_content



def test_failed_task_hydration_marks_task_not_fully_loaded():
    """Every path that leaves the canvas un-hydrated must clear isFullyLoaded.

    `isFullyLoaded` is the only gate preventing an autosave from sending an
    empty annotation list over real server data (see syncToBackend in
    components/workspace.js and the gate in components/timer.js). The .catch()
    on the detail fetch used to blank `item.annotations` without clearing the
    flag, so a task that had previously loaded kept a stale `true` and could
    save its empty canvas -- the project 54 / task 241 wipe of 2026-09-16.
    """
    gallery_file = os.path.join(FRONTEND_JS_DIR, "components", "gallery.js")
    with open(gallery_file, "r", encoding="utf-8") as f:
        content = f.read()

    # Isolate the hydration handler: from the detail fetch to the lock claim
    # that follows it, so unrelated assignments elsewhere can't satisfy this.
    start = content.index("const detailPromise")
    end = content.index("const lockPromise", start)
    hydration = content[start:end]

    # Each blanking of annotations in this block must be accompanied by
    # clearing isFullyLoaded -- including the .catch() path.
    assert hydration.count("item.annotations = []") >= 3, (
        "expected the 403, non-OK and catch branches to blank annotations"
    )
    assert hydration.count("item.isFullyLoaded = false") == hydration.count(
        "item.annotations = []"
    ), (
        "every branch that empties item.annotations must also set "
        "item.isFullyLoaded = false, or an autosave can wipe server data"
    )

    catch_block = hydration[hydration.index(".catch("):]
    assert "item.isFullyLoaded = false" in catch_block, (
        "the .catch() hydration-failure path must clear isFullyLoaded"
    )


def _node_available():
    import shutil
    return shutil.which("node") is not None


def test_vertex_handle_radius_shrinks_with_zoom():
    """Vertex handles must shrink as the annotator zooms in, but never below the
    floor at which they stop being distinguishable from the outline they sit on.

    Evaluated by actually running the module, so the invariant is enforced
    against the real curve rather than against the source text.
    """
    if not _node_available():
        pytest.skip("node is not available on PATH")

    import json
    import subprocess

    flags_url = (
        "file:///"
        + os.path.join(FRONTEND_JS_DIR, "feature-flags.js").replace("\\", "/")
    )
    script = (
        f"import('{flags_url}').then(m => {{"
        "  const zooms = [1, 2, 4, 8, 16, 64];"
        "  console.log(JSON.stringify({"
        "    floor: m.minVertexRadius(),"
        "    edgeWidth: m.annotationSettings.selectedEdgeWidth,"
        "    base: m.annotationSettings.vertexHandleRadius,"
        "    radii: zooms.map(z => m.zoomScaledRadius("
        "      m.annotationSettings.vertexHandleRadius, z)),"
        "    zoomedOut: m.zoomScaledRadius("
        "      m.annotationSettings.vertexHandleRadius, 0.5),"
        "  }));"
        "});"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"node failed: {result.stderr}"
    data = json.loads(result.stdout.strip())

    # The floor is what keeps a handle from merging into the edge: it must be
    # strictly greater than the selected outline's half-width, or the handle is
    # no wider than the line and reads as a bump in it.
    edge_half_width = data["edgeWidth"] / 2
    assert data["floor"] > edge_half_width, (
        f"Vertex floor {data['floor']}px does not clear the selected edge's "
        f"half-width {edge_half_width}px, so handles merge into the outline."
    )

    # Zooming in shrinks the handle...
    assert data["radii"][1] < data["radii"][0], "Handle does not shrink on zoom-in"

    # ...monotonically, and never past the floor.
    for smaller, larger in zip(data["radii"], data["radii"][1:]):
        assert larger <= smaller, f"Handle grew with zoom: {data['radii']}"
    for radius in data["radii"]:
        assert radius >= data["floor"], (
            f"Handle shrank to {radius}px, below the {data['floor']}px floor"
        )

    # Zooming OUT must not inflate handles over a small shape.
    assert data["zoomedOut"] == data["base"]
