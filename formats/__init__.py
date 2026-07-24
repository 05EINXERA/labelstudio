"""Annotation import/export format implementations.

This package holds the format logic that used to live inline in
`api/routers/exports.py` and `api/routers/imports.py`. Routers stay HTTP-shaped
(validate, dispatch, job plumbing); everything that knows what a COCO file or a
YOLO label file looks like lives here.

Layout (see .devnotes/data-refactor/01_PLAN.md § 0):

    common.py            shared helpers: geometry, value derivation, status
                         maps, image dimensions, annotation type inference
    coco.py              COCO JSON                (two-way)
    annotations_json.py  interop task JSON        (two-way)
    yolo.py              YOLO segmentation        (two-way)
    masks.py             rasterized masks         (EXPORT ONLY - see below)
    csv_flat.py          flat CSV                 (export only)

Export builders share one contract so `_build_zip` can combine them:

    builder(ctx: ExportContext) -> List[Tuple[str, bytes]]

returning (arcname, content) pairs relative to that format's own folder. A
builder never constructs a ZIP itself.

Masks are deliberately export-only. Contour-tracing a raster back to polygons
is not a faithful inverse, and the raster carries no trustworthy class
identity. This is a settled product decision, not a gap — see
.devnotes/data-refactor/00_FORMAT_ANALYSIS.md § 8 before adding a parser.
"""
