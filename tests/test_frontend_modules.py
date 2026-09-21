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


def test_vertex_handle_radius_is_constant_across_zoom():
    """Vertex handles must be the same on-screen size at every zoom level.

    Zoom-dependent sizing has been tried in both directions and annotators
    objected to both: shrinking on zoom-in was reported as vertices "becoming
    smaller", and growing on zoom-in drew discs large enough to cover the work.
    A constant size is the one behaviour nobody can report as the handles
    changing under them, and it is what this test pins.

    The trade-off is accepted deliberately: a constant handle covers more image
    detail the further in the annotator zooms. It is kept small enough (a 10px
    dot) that this stays tolerable.

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
        "  const zooms = [0.25, 0.5, 1, 2, 4, 8, 64];"
        "  console.log(JSON.stringify({"
        "    floor: m.minVertexRadius(),"
        "    edgeWidth: m.annotationSettings.selectedEdgeWidth,"
        "    base: m.annotationSettings.vertexHandleRadius,"
        "    ceiling: m.annotationSettings.vertexHandleRadius"
        "      * m.annotationSettings.vertexMaxRadiusBaseRatio,"
        "    zooms,"
        "    radii: zooms.map(z => m.zoomScaledRadius("
        "      m.annotationSettings.vertexHandleRadius, z)),"
        "    atOne: m.zoomScaledRadius("
        "      m.annotationSettings.vertexHandleRadius, 1),"
        "    degenerate: [0, -1, null].map(z => m.zoomScaledRadius("
        "      m.annotationSettings.vertexHandleRadius, z === null ? NaN : z)),"
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

    # The base must sit strictly between the clamps, or one of them is silently
    # dictating handle size instead of the configured radius.
    assert data["floor"] < data["base"] < data["ceiling"], (
        f"Base handle radius {data['base']}px is not between the "
        f"{data['floor']}px floor and {data['ceiling']}px ceiling."
    )

    # At fit-to-window the annotator gets exactly the configured radius.
    assert data["atOne"] == data["base"]

    # The invariant: one size at every zoom, from far out to deep in. This is
    # what makes "the vertices change size when I zoom" un-reportable.
    assert all(r == data["base"] for r in data["radii"]), (
        f"Handle size varies with zoom: "
        f"{list(zip(data['zooms'], data['radii']))} (expected "
        f"{data['base']}px throughout)"
    )

    # Neither clamp may bind while the size is constant -- if one did, it would
    # be silently dictating handle size instead of vertexHandleRadius.
    assert data["floor"] < data["base"] < data["ceiling"], (
        f"Base radius {data['base']}px is not strictly between the "
        f"{data['floor']}px floor and {data['ceiling']}px ceiling, so a clamp "
        f"is overriding the configured size."
    )

    # A constant handle covers more of the image the further in the annotator
    # zooms; that is the accepted cost of not changing size. Keep it bounded so
    # the cost stays tolerable: at 8x a 10px dot spans ~1.25 image px.
    at_eight = dict(zip(data["zooms"], data["radii"])).get(8)
    if at_eight is not None:
        covered = (at_eight * 2) / 8
        assert covered < 2.0, (
            f"At 8x zoom the handle covers {covered:.2f} image pixels, enough "
            f"to hide the detail being annotated. Lower vertexHandleRadius."
        )

    # A zero, negative or non-finite zoom is not a meaningful view; scaling by
    # one yields 0 or NaN, so the base radius is returned instead.
    assert all(r == data["base"] for r in data["degenerate"]), (
        f"Degenerate zooms did not fall back to the base radius: "
        f"{data['degenerate']}"
    )


def test_grab_radii_do_not_create_phantom_snapping():
    """The vertex grab radius must stay small enough, relative to the spacing
    freehand tracing lays points down at, that adjacent grab areas cannot
    overlap.

    This is the invariant behind the "vertices snap to each other like a
    magnet" report: there is no snapping code anywhere: the effect is entirely
    a hit test claiming ground it should not. When `vertexGrabRadius` exceeds
    half of `freehandPointSpacing`, a freehand-traced outline has overlapping
    grab zones by construction, so clicking near one corner grabs whichever
    neighbour wins the distance check.

    Evaluated by running the module, so the invariant holds against the real
    values rather than against the source text.
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
        "  console.log(JSON.stringify({"
        "    grab: m.annotationSettings.vertexGrabRadius,"
        "    edge: m.annotationSettings.edgeGrabRadius,"
        "    spacing: m.annotationSettings.freehandPointSpacing,"
        "    imageCap: m.annotationSettings.maxGrabRadiusImagePx,"
        # The real hit-test path: screen radius at a range of zooms, divided
        # back into image space the way hitTestPoint() does. imageBox.scale and
        # viewZoom move together, so scale == zoom for this purpose.
        "    imageSpaceReach: [1, 2, 4, 8, 16, 64].map(z =>"
        "      m.vertexGrabScreenRadius("
        "        m.annotationSettings.vertexGrabRadius, z, z) / z),"
        "    screenReach: [0.25, 0.5, 1, 2, 4, 8, 64].map(z =>"
        "      m.vertexGrabScreenRadius("
        "        m.annotationSettings.vertexGrabRadius, z, z)),"
        "    uncappedScale: m.vertexGrabScreenRadius("
        "      m.annotationSettings.vertexGrabRadius, 4, NaN),"
        "  }));"
        "});"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"node failed: {result.stderr}"
    data = json.loads(result.stdout.strip())

    # Freehand traces must stay dense enough to follow a curve faithfully. This
    # is the user-facing cost of a larger grab radius, and the reason the pair
    # cannot simply be scaled up together: the invariant below is satisfiable
    # by raising the spacing, but annotators then report the distance between
    # vertices growing and traced outlines looking coarse. Cap the spacing so
    # that escape route is closed.
    assert data["spacing"] <= 10, (
        f"freehandPointSpacing is {data['spacing']}px: freehand traces lay "
        f"points down that far apart, which annotators see as the gap between "
        f"vertices growing and outlines becoming coarse. If a larger grab "
        f"radius forced this up, lower the grab radius instead."
    )

    assert data["grab"] * 2 <= data["spacing"], (
        f"vertexGrabRadius {data['grab']}px exceeds half of "
        f"freehandPointSpacing {data['spacing']}px, so neighbouring vertices "
        f"on a freehand trace have overlapping grab areas and the wrong one "
        f"can win a click."
    )

    # The edge test is only safe at or below the vertex radius: it runs second
    # and a corner wins a tie, but an edge radius LARGER than the vertex one
    # would claim ground outside any handle the annotator can see.
    assert data["edge"] <= data["grab"], (
        f"edgeGrabRadius {data['edge']}px exceeds vertexGrabRadius "
        f"{data['grab']}px, so edges claim ground outside the drawn handles."
    )

    # Radii now scale WITH the image, so the grab area would cover a constant
    # patch of image at every zoom and zooming in to place a vertex precisely
    # would not help. vertexGrabScreenRadius() caps its reach in image space to
    # restore that; this asserts the cap actually binds on the real hit-test
    # path, at every zoom an annotator might use.
    # The cap is floored at the base radius so it does not shrink the click
    # target at ordinary zoom, so it binds from the zoom where the scaled
    # radius overtakes it (4x with the current numbers) upward. That is the
    # range where precise placement actually matters.
    zooms = [1, 2, 4, 8, 16, 64]
    for zoom, reach in zip(zooms, data["imageSpaceReach"]):
        if zoom < 4:
            continue
        assert reach <= data["imageCap"] + 1e-9, (
            f"At {zoom}x zoom a vertex grab reaches {reach:.2f}px into the "
            f"image, past the {data['imageCap']}px cap: a vertex placed that "
            f"close to an existing one gets swallowed instead of placed."
        )

    # The property that matters: zooming in must keep making placement finer.
    # If reach in image space ever stopped decreasing, zooming in to separate
    # two close vertices would stop working -- the magnet report.
    for (z_small, r_small), (z_large, r_large) in zip(
        list(zip(zooms, data["imageSpaceReach"])),
        list(zip(zooms, data["imageSpaceReach"]))[1:],
    ):
        assert r_large <= r_small + 1e-9, (
            f"Grab reach in image space grew from {r_small:.2f}px at "
            f"{z_small}x to {r_large:.2f}px at {z_large}x zoom, so zooming in "
            f"makes precise placement harder rather than easier."
        )

    # And the grab target must never fall below the comfortable base radius on
    # screen, or vertices are drawn larger than the area that can catch a click.
    assert min(data["screenReach"]) >= data["grab"] - 1e-9, (
        f"Grab radius drops to {min(data['screenReach'])}px on screen, below "
        f"the {data['grab']}px base: handles would look grabbable without "
        f"being grabbable."
    )

    # A missing/degenerate image scale must not silently collapse the grab area
    # to nothing -- the cap is skipped rather than computed from a bad scale.
    assert data["uncappedScale"] > 0, (
        "A non-finite imageScale collapsed the grab radius; the cap should be "
        "skipped when the scale is unusable, not guessed."
    )


def test_freehand_commits_a_point_at_a_corner():
    """Freehand drag-draw must lay a vertex down at a corner even when the
    cursor has not travelled the spacing distance.

    The spacing gate alone cannot see a corner, and loses it for a specific
    reason: tracing into a corner the annotator slows and pivots, so the cursor
    stays inside the spacing radius for the whole turn and no point is
    committed. The next one lands a full spacing distance down the NEW heading,
    so the outline cuts the corner and the vertex reads as pulled toward the
    adjacent side -- reported as vertices "snapping like a magnet" at corners
    on curved shapes.

    `isFreehandCorner` is module-private and interactions.js touches the DOM at
    import time, so the angle rule is re-implemented here and checked against
    the same constants the module uses. The wiring (that the rule is actually
    OR-ed into the spacing gate) is asserted separately below.
    """
    import math

    interactions = os.path.join(FRONTEND_JS_DIR, "canvas", "interactions.js")
    with open(interactions, "r", encoding="utf-8") as f:
        source = f.read()

    match = re.search(r"FREEHAND_CORNER_ANGLE_DEGREES\s*=\s*([\d.]+)", source)
    assert match, "FREEHAND_CORNER_ANGLE_DEGREES is not defined"
    corner_angle = float(match.group(1))

    def turn_degrees(prev, last, end):
        ix, iy = last[0] - prev[0], last[1] - prev[1]
        ox, oy = end[0] - last[0], end[1] - last[1]
        imag = math.hypot(ix, iy)
        omag = math.hypot(ox, oy)
        if imag == 0 or omag == 0:
            return 0.0
        cos = max(-1.0, min(1.0, (ix * ox + iy * oy) / (imag * omag)))
        return math.degrees(math.acos(cos))

    # A right-angle turn is unambiguously a corner and must commit.
    square_corner = turn_degrees((0, 0), (10, 0), (10, 10))
    assert square_corner > corner_angle, (
        f"A 90-degree corner measures {square_corner} and does not exceed the "
        f"{corner_angle}-degree threshold, so it would not be committed."
    )

    # A gentle arc must NOT trip the corner rule, or every traced curve gets a
    # point at every mouse sample and the outline becomes needlessly heavy.
    arc_step = turn_degrees((0, 0), (10, 0), (19.8, 2.0))
    assert arc_step < corner_angle, (
        f"A gentle arc step measures {arc_step} and exceeds the "
        f"{corner_angle}-degree threshold, so smooth curves would be "
        f"over-sampled."
    )

    # The rule must be OR-ed into the spacing gate -- a corner commits whether
    # or not the cursor has travelled far enough.
    assert "travelled > threshold || isFreehandCorner(pts, end)" in source, (
        "the corner test must be OR-ed with the spacing gate, so a corner "
        "commits a point regardless of distance travelled"
    )

    # A near-zero-length step has no meaningful heading; committing on one
    # stacks a cluster of points at the moment of the turn.
    assert "FREEHAND_CORNER_MIN_TRAVEL_PX" in source, (
        "a minimum-travel floor is required, or cursor jitter at the corner "
        "commits a burst of points"
    )


def test_sticky_class_hover_selects_last_polygon():
    """Sticky class: the polygon just finalized stays hover-armed.

    Hovering inside its boundary selects it for editing; crossing the boundary
    releases it and re-arms drawing so the next click starts a new polygon.
    """
    interactions = os.path.join(FRONTEND_JS_DIR, "canvas", "interactions.js")
    with open(interactions, "r", encoding="utf-8") as f:
        source = f.read()

    view_file = os.path.join(FRONTEND_JS_DIR, "canvas", "view.js")
    with open(view_file, "r", encoding="utf-8") as f:
        view_source = f.read()

    # The arming lives on the shared view state, not a module-local variable,
    # so state.js and mode-controls.js can clear it on task change / toggle-off.
    assert "stickyHoverId" in view_source and "stickyHoverInside" in view_source, (
        "the hover arming must live on the shared view object so other modules "
        "can clear it"
    )

    assert "function armStickyHover" in source, (
        "finalizePolygon must arm the finished polygon for hover selection"
    )
    assert "export function clearStickyHover" in source, (
        "clearStickyHover must be exported for mode-controls.js to call when "
        "the sticky-class toggle goes off"
    )
    assert "function updateStickyHover" in source

    # Sticky finalize must arm rather than fall straight back to drawing.
    assert "armStickyHover(annotation.id)" in source, (
        "the sticky branch of finalizePolygon must hover-arm the new polygon"
    )

    # Boundary crossing is what flips the state, so it must be a real
    # point-in-polygon test against that polygon -- not hitTest, which returns
    # whichever shape is topmost and would arm on an overlapping neighbour.
    assert "pointInPolygon(imagePoint(point), annotationPoints(annotation))" in source, (
        "hover state must be decided by this polygon's own boundary, not by "
        "hitTest, which reports the topmost overlapping shape"
    )

    # Re-evaluated on every pointer move, before the cursor is chosen, so the
    # cursor agrees with the mode the move just produced.
    move_handler = source[source.index('canvas.addEventListener("pointermove"'):]
    assert move_handler.index("updateStickyHover(point)") < move_handler.index("updateCanvasCursor(point)"), (
        "the hover state must be updated before the cursor is derived from it"
    )

    # A drag owns the gesture: dragging a vertex outside the shape must not
    # deselect it mid-drag.
    assert re.search(r"if \(view\.drag\) return false;", source), (
        "an in-progress drag must suppress hover re-evaluation"
    )

    # Clicking inside the armed polygon edits it; only a click outside starts
    # the next shape.
    assert "if (view.stickyHoverId && view.stickyHoverInside) {" in source, (
        "a click inside the armed polygon must fall through to the editing "
        "blocks instead of starting a new polygon"
    )

    # Leaving the boundary must hand the canvas back to draw mode.
    assert 'state.mode = "draw";' in source

    # The arming must not outlive its polygon or its image.
    state_file = os.path.join(FRONTEND_JS_DIR, "state.js")
    with open(state_file, "r", encoding="utf-8") as f:
        state_source = f.read()
    reset = state_source[state_source.index("export function resetWorkspaceForNewImage"):]
    assert "view.stickyHoverId = null" in reset, (
        "a task change must clear the arming, or the id dangles into the next image"
    )
    assert "clearStickyHover()" in source[source.index("export function deleteSelected"):], (
        "deleting the armed polygon must clear the arming"
    )
