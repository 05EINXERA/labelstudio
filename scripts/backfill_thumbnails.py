"""Pre-generate list-view thumbnails for every existing upload.

    start /belownormal python scripts\\backfill_thumbnails.py
    python scripts/backfill_thumbnails.py --dry-run

`GET /thumbs/{name}` makes a thumbnail on first request, so this is not
required for correctness. It exists so the first Tasks page after deploy is
not the one that pays: without it, every annotator's first page generates up
to 10 thumbnails at ~150 ms each on a 2-slot pool.

Run it once, while the office is empty, at below-normal priority —
`start /belownormal` on Windows, where `os.nice` does nothing. Measured on the
dev data: ~150 ms and ~1-2 KB per image, so ~2 minutes per 1,000 uploads.

Idempotent and resumable: existing thumbnails are skipped, so an interrupted
run just picks up where it stopped. Uses the same function as the server
(api/thumbnails.py), so the files are identical whichever made them.
Output goes to DATA_DIR/thumbs/, a disposable cache (not backed up).
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from api.thumbnails import (  # noqa: E402
    UPLOAD_NAME,
    ThumbnailBusy,
    ThumbnailUnavailable,
    ensure_thumbnail,
    thumb_path,
    uploads_dir,
)


def backfill(dry_run: bool = False, out=sys.stdout) -> dict:
    counts = {"made": 0, "skipped": 0, "failed": 0, "ignored": 0}
    source_dir = uploads_dir()
    try:
        names = sorted(os.listdir(source_dir))
    except FileNotFoundError:
        print(f"No uploads folder at {source_dir}; nothing to do.", file=out)
        return counts

    started = time.monotonic()
    for i, name in enumerate(names, 1):
        if not UPLOAD_NAME.match(name):
            counts["ignored"] += 1
            continue
        if os.path.isfile(thumb_path(name)):
            counts["skipped"] += 1
            continue
        if dry_run:
            counts["made"] += 1
            continue
        try:
            # No competing requests to wait for here; a long wait is harmless.
            ensure_thumbnail(name, wait_seconds=60)
            counts["made"] += 1
        except (ThumbnailUnavailable, ThumbnailBusy) as exc:
            counts["failed"] += 1
            print(f"  failed: {name} ({type(exc).__name__})", file=out)
        if i % 100 == 0:
            print(f"  {i}/{len(names)} ...", file=out)

    verb = "would make" if dry_run else "made"
    print(
        f"{verb} {counts['made']}, already present {counts['skipped']}, "
        f"failed {counts['failed']}, not uploads {counts['ignored']} "
        f"in {time.monotonic() - started:.1f}s",
        file=out,
    )
    return counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="count what would be generated")
    args = ap.parse_args(argv)
    counts = backfill(dry_run=args.dry_run)
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
