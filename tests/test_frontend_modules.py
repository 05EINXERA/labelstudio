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

    # The clamp is deliberately one-sided. The floor must stay strictly below
    # the base, or it is silently dictating handle size instead of the
    # configured radius. The ceiling instead sits exactly AT the base: a handle
    # may scale down, never up, because a handle drawn larger than normal hides
    # the very pixels the annotator zoomed in to inspect.
    assert data["floor"] < data["base"], (
        f"Base handle radius {data['base']}px is not above the "
        f"{data['floor']}px floor."
    )
    assert data["ceiling"] == data["base"], (
        f"Ceiling {data['ceiling']}px does not equal the base "
        f"{data['base']}px: a vertex could be drawn larger than normal at "
        f"some zoom. vertexMaxRadiusBaseRatio must stay at 1.0."
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

    # The floor may not bind while the size is constant -- if it did, it would
    # be silently dictating handle size instead of vertexHandleRadius. The
    # ceiling sits at the base on purpose (see above), so it is excluded.
    assert data["floor"] < data["base"], (
        f"Base radius {data['base']}px is not strictly above the "
        f"{data['floor']}px floor, so the floor is overriding the configured "
        f"size."
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


def test_vertex_handle_never_exceeds_the_base_radius():
    """A vertex may shrink with zoom, but must never be drawn larger.

    `vertexHandleRadius` is the size that reads correctly against a 3px
    outline without covering the pixels being judged. A handle bigger than
    that hides detail exactly when the annotator has zoomed in to inspect it,
    so there is no zoom at which exceeding the base is an improvement.

    This is enforced at the clamp rather than by the current
    `vertexZoomScale: 0`. That zero makes the whole question moot today, but
    it is documented as a one-number change away from being re-enabled, and a
    POSITIVE exponent grows handles as you zoom in -- unbounded, 512px at 64x.
    So the test drives zoomScaledRadius with scaling deliberately switched on,
    in both directions, and asserts the ceiling still holds. Scaling DOWN
    stays available: the clamp is one-sided by design.
    """
    if not _node_available():
        pytest.skip("node is not available on PATH")

    import json
    import subprocess

    flags_url = (
        "file:///"
        + os.path.join(FRONTEND_JS_DIR, "feature-flags.js").replace("\\", "/")
    )
    # Exercise both signs of the exponent across the full zoom range. The
    # positive case is the one the ceiling exists for.
    script = (
        f"import('{flags_url}').then(m => {{"
        "  const base = m.annotationSettings.vertexHandleRadius;"
        "  const zooms = [0.1, 0.25, 0.5, 1, 2, 4, 8, 64, 500];"
        "  const out = {};"
        "  for (const exp of [1, 0.5, -0.5, -1]) {"
        "    m.annotationSettings.vertexZoomScale = exp;"
        "    out[String(exp)] = zooms.map(z => m.zoomScaledRadius(base, z));"
        "  }"
        "  console.log(JSON.stringify({"
        "    base,"
        "    ratio: m.annotationSettings.vertexMaxRadiusBaseRatio,"
        "    zooms,"
        "    byExponent: out,"
        "  }));"
        "});"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"node failed: {result.stderr}"
    data = json.loads(result.stdout.strip())

    # The ratio is the setting that encodes "never bigger than normal".
    assert data["ratio"] == 1.0, (
        f"vertexMaxRadiusBaseRatio is {data['ratio']}, which permits a handle "
        f"{data['base'] * data['ratio']}px against a {data['base']}px base. "
        f"It must be 1.0 so a vertex can never be drawn larger than normal."
    )

    # The invariant itself, under every exponent and zoom.
    base = data["base"]
    for exponent, radii in data["byExponent"].items():
        for zoom, radius in zip(data["zooms"], radii):
            assert radius <= base, (
                f"With vertexZoomScale={exponent} at zoom {zoom}x the handle "
                f"is {radius}px, larger than the {base}px base. The ceiling "
                f"is not holding."
            )

    # And the clamp must be one-sided: shrinking is still reachable, or the
    # ceiling has been implemented by freezing the size outright.
    shrunk = [r for radii in data["byExponent"].values() for r in radii
              if r < base]
    assert shrunk, (
        "No exponent/zoom combination produced a handle smaller than the "
        "base, so scaling down is unreachable. The ceiling must bound growth "
        "only, not pin the radius."
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

    # The rule must be OR-ed into the spacing gate, so a corner can commit
    # inside the spacing threshold. It is NOT unconditional: `cornerOpen`
    # carries its own, smaller separation floor, because a corner that commits
    # at any distance fuses its handle with the previous one. See
    # test_freehand_corner_floor_respects_the_grab_radius.
    assert "travelled > threshold || cornerOpen" in source, (
        "the corner test must be OR-ed with the spacing gate, so a corner "
        "can commit inside the spacing threshold"
    )

    # A near-zero-length step has no meaningful heading; committing on one
    # stacks a cluster of points at the moment of the turn.
    assert "freehandCornerMinTravel" in source, (
        "a minimum-travel floor is required, or cursor jitter at the corner "
        "commits a burst of points"
    )


def test_freehand_corner_floor_respects_the_grab_radius():
    """No two freehand vertices may land with overlapping grab areas.

    This is the "two vertices overlapped at the corner" report. The corner
    rule deliberately bypasses `freehandPointSpacing` -- that gate is what
    rounds corners off -- but bypassing it without its own separation floor
    lets a corner commit a vertex a few pixels from its neighbour. The two
    handles fuse on screen and neither can be grabbed reliably.

    Two things have to hold, and the first fix only got the first one:

    1. The floor derives from the grab radius, so it follows the handle
       across zoom instead of assuming a fixed pixel gap.
    2. It is TWICE that radius, and it is measured from the last COMMITTED
       point. Each vertex carries its own grab area, so centres must be
       radius+radius apart for the areas to be disjoint. Gating only the
       outgoing mouse step (`outMag`) does not do this: that step is the
       pending sample, not the gap actually left behind.
    """
    interactions = os.path.join(FRONTEND_JS_DIR, "canvas", "interactions.js")
    with open(interactions, "r", encoding="utf-8") as f:
        source = f.read()

    match = re.search(
        r"function freehandCornerMinTravel\s*\(\s*\)\s*\{(.*?)\n\}",
        source, re.S)
    assert match, "freehandCornerMinTravel() is not defined"
    body = match.group(1)

    # (1) Tied to the grab radius, not a hardcoded pixel gap.
    assert "vertexGrabScreenRadius" in body, (
        "the corner floor must derive from vertexGrabScreenRadius, so it "
        "follows the handle the annotator sees as zoom changes"
    )

    # (2a) Doubled -- one radius for each of the two handles.
    assert re.search(r"2\s*\*\s*vertexGrabScreenRadius", body), (
        "the corner floor must be TWICE the grab radius: each vertex has its "
        "own grab area, so centres closer than radius+radius overlap"
    )

    # (2b) Applied to the distance from the last committed point, at the emit
    # site. Gating outMag alone leaves the real neighbour gap unchecked.
    assert re.search(
        r"travelled\s*>\s*cornerFloor\s*&&\s*isFreehandCorner\(pts,\s*end\)",
        source), (
        "the corner must be gated on `travelled` (distance from the last "
        "committed point), not only on the pending step inside "
        "isFreehandCorner"
    )

    # The separation the corner path guarantees must be at least what the
    # spacing gate guarantees, or corners reintroduce the overlap that
    # freehandPointSpacing exists to prevent.
    if not _node_available():
        pytest.skip("node is not available for the numeric half")

    import json
    import subprocess

    flags_url = (
        "file:///"
        + os.path.join(FRONTEND_JS_DIR, "feature-flags.js").replace("\\", "/")
    )
    script = (
        f"import('{flags_url}').then(m => {{"
        "  console.log(JSON.stringify({"
        "    floor: 2 * m.vertexGrabScreenRadius("
        "      m.annotationSettings.vertexGrabRadius, 1, 1),"
        "    spacing: m.annotationSettings.freehandPointSpacing,"
        "  }));"
        "});"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"node failed: {result.stderr}"
    data = json.loads(result.stdout.strip())

    assert data["floor"] >= data["spacing"], (
        f"The corner floor is {data['floor']}px but freehandPointSpacing is "
        f"{data['spacing']}px. A corner could commit a vertex closer than the "
        f"spacing gate allows, reintroducing fused handles. Either raise the "
        f"spacing or lower vertexGrabRadius."
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


def test_hidden_objects_indicator_is_wired():
    """The Objects pane header must show a closed-eye marker while anything is
    hidden.

    Hiding is otherwise invisible from the header: the count keeps reporting
    every object, so an annotator who pressed H and scrolled away has no sign
    that the canvas is showing less than the full list. The per-row eye icons
    do not cover this -- the list is virtualized, so rows that are not on
    screen are not in the DOM at all.

    Checked structurally rather than by running the module: workspace.js
    touches the DOM at import time and cannot be loaded under node.
    """
    root = os.path.dirname(os.path.dirname(__file__))
    html_path = os.path.join(root, "frontend", "app.html")
    dom_path = os.path.join(FRONTEND_JS_DIR, "dom.js")
    workspace_path = os.path.join(FRONTEND_JS_DIR, "components", "workspace.js")
    css_path = os.path.join(root, "frontend", "styles.css")

    with open(html_path, encoding="utf-8") as fh:
        html = fh.read()
    with open(dom_path, encoding="utf-8") as fh:
        dom = fh.read()
    with open(workspace_path, encoding="utf-8") as fh:
        workspace = fh.read()
    with open(css_path, encoding="utf-8") as fh:
        css = fh.read()

    # The element exists in the Objects header, and starts hidden so an empty
    # or fully-visible task shows no marker.
    assert 'id="hiddenObjectsIndicator"' in html, (
        "No hiddenObjectsIndicator element in app.html"
    )
    indicator_tag = re.search(r"<span[^>]*id=\"hiddenObjectsIndicator\"[^>]*>", html)
    assert indicator_tag and "hidden" in indicator_tag.group(0), (
        "hiddenObjectsIndicator must start hidden, or every task opens showing "
        "a closed-eye marker with nothing actually hidden."
    )

    assert "hiddenObjectsIndicator" in dom, "Indicator not exported from dom.js"
    assert "renderHiddenIndicator" in workspace, (
        "workspace.js has no renderHiddenIndicator()"
    )

    # It must run on BOTH renderAnnotations() paths -- the empty-task early
    # return as well as the normal one -- or the marker survives deleting the
    # last annotation.
    assert workspace.count("renderHiddenIndicator();") >= 2, (
        "renderHiddenIndicator() must be called on both renderAnnotations() "
        "paths, including the early return for a task with no annotations."
    )

    # Both ways of hiding must count, matching what the rows treat as hidden.
    indicator_body = workspace[workspace.index("function renderHiddenIndicator"):]
    indicator_body = indicator_body[:indicator_body.index("\nfunction ", 1)]
    assert "hiddenAnnotationIds" in indicator_body, (
        "Indicator ignores hiddenAnnotationIds (the H key / per-row eye)."
    )
    assert "hiddenLabelIds" in indicator_body, (
        "Indicator ignores hiddenLabelIds, so hiding a whole class would leave "
        "the header claiming nothing is hidden while rows show otherwise."
    )

    # display:grid beats the browser's default [hidden] rule, so the attribute
    # needs an explicit override or the marker can never hide.
    assert ".hidden-objects-indicator[hidden]" in css, (
        "Missing a [hidden] override for .hidden-objects-indicator: its "
        "display rule would beat the browser default and pin it visible."
    )

    # The marker sits immediately LEFT of the object count, on one row. It only
    # lands there if .header-actions is a flex row: the sole rule for it used
    # to be scoped to .accordion-header, and the Objects pane is not an
    # accordion, so its children stacked and the eye appeared ABOVE the count.
    assert re.search(r"^\.header-actions\s*\{[^}]*display:\s*flex", css, re.M), (
        "No unscoped .header-actions flex rule: a plain .pane-header would "
        "stack its actions vertically, putting the hidden marker above the "
        "object count instead of beside it."
    )

    # Left-of-count is DOM order under a flex row, so the span must come first.
    actions = re.search(
        r"<div class=\"header-actions\">(.*?)</div>", html, re.S
    )
    assert actions, "Objects pane header has no .header-actions block"
    body = actions.group(1)
    assert body.index("hiddenObjectsIndicator") < body.index("annotationCount"), (
        "The hidden marker must precede the count in the DOM, or it renders to "
        "the right of the number."
    )


def test_freehand_spacing_is_uniform_regardless_of_cursor_speed():
    """Freehand vertices land at even intervals however fast the cursor moves.

    This is the "vertices should be uniform" report. The spacing threshold is a
    floor and nothing bounds it from above, so committing the cursor's current
    position the moment it passes that floor makes the real gap depend on where
    pointer events happen to land -- a function of cursor speed and event rate,
    not of any setting. A slow trace samples every few pixels and commits near
    the threshold; a fast sweep delivers one event forty pixels along and
    commits there. One stroke then carries tight clusters through the slow
    curves and long bare runs through the fast ones.

    `appendEvenlySpacedPoints` subdivides the step instead, so the gap follows
    from `freehandPointSpacing` alone. Exercised by running the real module
    against a synthetic event stream, because the property under test is
    numeric and a source-text assertion cannot see it.
    """
    if not _node_available():
        pytest.skip("node is not available on PATH")

    import json
    import subprocess

    geometry_url = (
        "file:///"
        + os.path.join(FRONTEND_JS_DIR, "canvas", "geometry.js").replace("\\", "/")
    )
    script = (
        f"import('{geometry_url}').then(m => {{"
        "  const emit = m.appendEvenlySpacedPoints;"
        "  const SP = 10;"
        "  const gaps = (p) => p.slice(1).map((q, i) =>"
        "    Math.hypot(q.x - p[i].x, q.y - p[i].y));"
        # A cursor that crawls, then sweeps, then crawls again -- the stroke
        # that produced the report. Every x is one pointer event.
        "  let mixed = [{x: 0, y: 0}];"
        "  for (const x of [3, 6, 9, 12, 60, 63, 66, 110])"
        "    mixed = emit(mixed, {x, y: 0}, SP);"
        # One event that jumps far past the threshold: the old code committed a
        # single vertex at the far end of this.
        "  let sweep = [{x: 0, y: 0}];"
        "  sweep = emit(sweep, {x: 47, y: 0}, SP);"
        # Sub-threshold movement must still commit nothing.
        "  const short = emit([{x: 0, y: 0}], {x: 4, y: 0}, SP);"
        "  console.log(JSON.stringify({"
        "    mixedGaps: gaps(mixed),"
        "    sweepGaps: gaps(sweep),"
        "    shortCount: short.length,"
        # A degenerate spacing must not attempt an infinite subdivision.
        "    zeroSpacing: emit([{x: 0, y: 0}], {x: 5, y: 0}, 0).length,"
        "    nanSpacing: emit([{x: 0, y: 0}], {x: 5, y: 0}, NaN).length,"
        "    capped: emit([{x: 0, y: 0}], {x: 99999, y: 0}, SP, 512).length - 1,"
        "  }));"
        "});"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"node failed: {result.stderr}"
    data = json.loads(result.stdout.strip())

    # The point of the whole exercise: one tolerance, every gap, both speeds.
    # Rounding to whole image pixels is what the 1.5 allows for.
    for label in ("mixedGaps", "sweepGaps"):
        gaps = data[label]
        assert gaps, f"{label}: no vertices were emitted at all"
        uneven = [g for g in gaps if abs(g - 10) > 1.5]
        assert not uneven, (
            f"{label}: gaps {uneven} deviate from the 10px spacing. Vertex "
            f"spacing is tracking cursor speed again, which means a fast "
            f"stroke is committing one point at the end of the step instead "
            f"of subdividing it."
        )

    # A fast sweep must yield MANY vertices, not one at the far end. Without
    # this the test above passes trivially on a single-vertex result.
    assert len(data["sweepGaps"]) == 4, (
        f"A 47px step emitted {len(data['sweepGaps'])} gaps; expected 4 whole "
        f"10px intervals with the remainder carried to the next event."
    )

    # Movement under the threshold still commits nothing, or slow tracing would
    # emit a vertex per pointer event.
    assert data["shortCount"] == 1, (
        "A sub-threshold step committed a vertex; the spacing floor is gone."
    )

    # Guards: a spacing of 0 or NaN would make the interval count infinite.
    assert data["zeroSpacing"] == 2 and data["nanSpacing"] == 2, (
        "A degenerate spacing must fall back to appending the single point, "
        "not attempt an unbounded subdivision."
    )
    assert data["capped"] == 512, (
        "The per-event vertex cap is not holding; a pathological jump could "
        "emit unbounded points inside one event handler and stall the drag."
    )


def test_edit_task_modal_sections_are_collapsible():
    """The tall optional sections of the Edit task modal collapse.

    This is the "Save button is off the bottom of the screen" report. Capping
    the heights of the preview and the two panes -- the previous fix, still
    present under a max-height media query -- only slows the overflow down:
    the modal's height is the sum of its sections, so on a short viewport
    enough sections together still exceed the cap however small each is made.
    Collapsing removes a section from the height budget outright.

    Asserted structurally rather than by rendering, since there is no browser
    in the test environment: what can be checked here is that the sections are
    real <details> disclosures, that each carries a summary the owner can read
    while it is shut, and that the JS toggling visibility does not reintroduce
    the `display:grid` that breaks a <details> element's own layout.
    """
    tasks_js = os.path.join(FRONTEND_JS_DIR, "pages", "project", "tasks.js")
    with open(tasks_js, "r", encoding="utf-8") as f:
        source = f.read()

    edit_modal = re.search(
        r'<div class="modal-overlay" id="editModal">(.*?)\n    </div>',
        source, re.S)
    assert edit_modal, "the Edit task modal markup could not be located"
    markup = edit_modal.group(1)

    # Both tall sections are disclosures, not always-open blocks.
    for section_id in ("editAssigneeSection", "editHistoryWrap"):
        assert re.search(
            rf'<details[^>]*id="{section_id}"', markup), (
            f"{section_id} must be a <details> element so it can be collapsed; "
            f"an always-open block puts its full height back into the modal."
        )

    assert markup.count("<summary>") == 2, (
        "each collapsible section needs a <summary>, or it cannot be opened"
    )

    # A collapsed section has to say what is inside it, or collapsing costs a
    # click rather than saving one.
    for hint_id in ("editAssigneeHint", "editHistoryHint"):
        assert f'id="{hint_id}"' in markup, (
            f"{hint_id} is missing: a shut section with no summary of its "
            f"contents has to be opened to be read."
        )

    # `display:grid` on a <details> makes the summary and the disclosed content
    # grid items and breaks the collapse, so the show path must not use it.
    wrap_shows = re.findall(r'wrap\.style\.display\s*=\s*"(\w+)"', source)
    assert wrap_shows, "the history section's visibility toggle disappeared"
    assert "grid" not in wrap_shows, (
        f"the history section is shown with display:{wrap_shows}; a <details> "
        f"must be block-level or its open/closed state stops working."
    )

    # The hint must track the live selection, not just the value the modal
    # opened with, or it reads as stale as soon as the section is shut again.
    assert re.search(
        r'el\("editAssignee"\)\.addEventListener\("change",\s*syncAssigneeHint\)',
        source), (
        "the assignee hint must be refreshed on change, or the collapsed "
        "summary contradicts the selection inside."
    )


def test_collapsible_modal_section_styles_exist():
    """The disclosure styling the Edit task modal depends on is defined."""
    css_path = os.path.join(
        os.path.dirname(FRONTEND_JS_DIR), "styles.css")
    with open(css_path, "r", encoding="utf-8") as f:
        css = f.read()

    assert ".modal-section" in css, (
        "no .modal-section rules: the collapsible sections would fall back to "
        "unstyled <details> markup"
    )

    # The default triangle is replaced by a caret drawn on ::before. Both the
    # standard property and the WebKit pseudo-element are needed -- hiding only
    # one leaves a stray marker in the other engine.
    assert re.search(r"\.modal-section\s*>\s*summary\s*\{[^}]*list-style:\s*none",
                     css, re.S), (
        "summary list-style is not cleared, so the default marker sits "
        "alongside the custom caret"
    )
    assert "::-webkit-details-marker" in css, (
        "the WebKit marker pseudo-element is not hidden, so Chrome and Safari "
        "still draw the default triangle next to the custom caret"
    )

    # Keyboard users need to see which section has focus.
    assert re.search(r"\.modal-section\s*>\s*summary:focus-visible", css), (
        "no focus-visible style on the summary: the section is keyboard "
        "operable, so its focus state has to be visible"
    )
