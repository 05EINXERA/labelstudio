"""The Python and JS class-name helpers must agree, pairwise.

Two pairs: `clean_label_name`/`cleanClassName` (the stored display name, case
preserved) and `normalize_label_name`/`normalizeClassName` (the case-folded
matching key the unique index compares).

The two are a deliberate mirror — there is no build step, so the definition
cannot be shared — and the same arrangement as `permissions.py`/`permissions.js`
guarded by `tests/js/permissions_spec.mjs` (CLAUDE.md rule 18b's precedent).

The stakes here are higher than rendering: the Python forms are what get stored
and what the unique index on (project_id, name_key) is taken over. If the
browser folds "Rust_Area" to something the server does not, the server stores a
second row for a class the user believes already exists — exactly the
duplicate-class bug this suite exists to prevent
(.devnotes/fix-class-creation/01_AUDIT.md D6).

Keeping the two functions separate is itself load-bearing: an earlier version of
this fix lowercased on *storage*, which broke 19 interop tests because
`formats/coco.py` writes `label.name` into `categories[].name` and the FastLabel
export puts it in `title` — "AF Paint" came back as "af paint".

The fixture list is shared: the JS side is run through node over the same
strings and the outputs compared elementwise, so a change to either
implementation fails here rather than in production.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from formats.common import clean_label_name, normalize_label_name

REPO = Path(__file__).resolve().parent.parent
UTILS_JS = REPO / "frontend" / "js" / "utils.js"

# Every case that has actually mattered, plus the three the old JS got wrong.
CASES = [
    "object",
    "Object",
    "OBJECT",
    " object ",
    "Rust_Area",
    "rust area",
    "Rust Area",
    "RUST_AREA ",
    "rust  area",      # interior run: collapses
    "_object_",        # leading/trailing underscore: was " object "
    "a__b",            # double underscore: was "a  b"
    "A_B",
    "",                # falsy: falls back to "object"
    "   ",             # whitespace only: must not become a nameless class
    "_",               # underscore only: same
    "car",
    "Traffic Light",
    "traffic_light",
    "Crack (major)",   # punctuation is preserved — only value_from_name strips
    "Zone 1",
]


def test_clean_preserves_case_but_tidies():
    """The display name keeps casing — the COCO/FastLabel exports carry it."""
    assert clean_label_name("AF_Paint") == "AF Paint"
    assert clean_label_name("  Rust  Area  ") == "Rust Area"
    assert clean_label_name("   ") == "object"
    for case in CASES:
        out = clean_label_name(case)
        assert out == out.strip()
        assert "_" not in out
        assert "  " not in out
        assert out


def test_the_key_is_the_clean_name_lowercased():
    """One definition, so the two cannot disagree about anything but case."""
    for case in CASES:
        assert normalize_label_name(case) == clean_label_name(case).lower()


def test_python_normalization_is_stable():
    """The properties the index depends on, independent of the JS."""
    for case in CASES:
        out = normalize_label_name(case)
        assert out == out.strip(), f"{case!r} -> {out!r} is not trimmed"
        assert out == out.lower(), f"{case!r} -> {out!r} is not lowercased"
        assert "_" not in out, f"{case!r} -> {out!r} kept an underscore"
        assert "  " not in out, f"{case!r} -> {out!r} kept a double space"
        assert out, f"{case!r} normalised to an empty name"


def test_normalization_is_idempotent():
    """Storing an already-normalised name must not change it again.

    A second pass that differed would mean re-saving a class could move it out
    from under the unique index it was inserted against.
    """
    for case in CASES:
        once = normalize_label_name(case)
        assert normalize_label_name(once) == once, f"{case!r} is not idempotent"


def test_the_variants_that_caused_duplicates_collapse():
    """The concrete bug: these must all be one class, not several."""
    forms = ["object", "Object", "OBJECT", " object ", "_object_", "object_"]
    assert len({normalize_label_name(f) for f in forms}) == 1
    assert normalize_label_name("Rust_Area") == normalize_label_name("rust area")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_and_python_agree():
    """The mirror check. Fails on drift in either direction."""
    script = f"""
    import {{ normalizeClassName, cleanClassName }} from {json.dumps(UTILS_JS.as_uri())};
    const cases = {json.dumps(CASES)};
    console.log(JSON.stringify({{
      key: cases.map(normalizeClassName),
      clean: cases.map(cleanClassName),
    }}));
    """
    result = subprocess.run(
        [shutil.which("node"), "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"node failed:\n{result.stdout}\n{result.stderr}"

    js_out = json.loads(result.stdout.strip().splitlines()[-1])

    for label, py_fn, js_key in (
        ("normalize_label_name / normalizeClassName", normalize_label_name, "key"),
        ("clean_label_name / cleanClassName", clean_label_name, "clean"),
    ):
        py_out = [py_fn(c) for c in CASES]
        mismatches = [
            (case, py, js)
            for case, py, js in zip(CASES, py_out, js_out[js_key])
            if py != js
        ]
        assert not mismatches, (
            f"{label} have drifted:\n"
            + "\n".join(f"  {c!r}: python={p!r} js={j!r}" for c, p, j in mismatches)
        )
