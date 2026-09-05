"""The annotation-count memo on the save path.

One save counts the same annotation blob several times over — `objects_prev`
for the service log, the history row's two counters, the response. Each count
was a full `json.loads`. That was invisible while blobs were ~1 MB and became
the dominant cost of a save once production blobs reached 15.6 MB, where a
single parse is ~185 ms of GIL-held CPU that stalls every other request in the
process.

See .devnotes/server-issue-diagnosis/evidence/06_ROOT_CAUSE_CONFIRMED.md.

These tests pin the two properties that matter: the memo must not change any
answer, and it must not leak an answer across requests (it is keyed on string
identity, and CPython reuses addresses).
"""
import json

from api.routers.tasks import (
    _COUNT_CACHE,
    _count_annotations,
    _reset_count_cache,
)


def _blob(n, label="defect"):
    return json.dumps(
        [
            {
                "id": f"o{i}",
                "type": "polygon",
                "label": label,
                "points": [[i * 1.5, i * 2.5], [i * 3.0, i * 4.0]],
            }
            for i in range(n)
        ]
    )


def test_memo_agrees_with_a_full_parse():
    _reset_count_cache()
    for n in (0, 1, 2, 37, 500):
        blob = _blob(n)
        assert _count_annotations(blob) == n
        # And again, now served from the memo.
        assert _count_annotations(blob) == n


def test_repeated_counts_of_one_blob_parse_once(monkeypatch):
    """The whole point: five counts of one blob must not be five parses."""
    _reset_count_cache()
    blob = _blob(120)

    calls = []
    real_loads = json.loads

    def counting_loads(s, *a, **kw):
        calls.append(len(s))
        return real_loads(s, *a, **kw)

    monkeypatch.setattr("api.routers.tasks.json.loads", counting_loads)

    results = [_count_annotations(blob) for _ in range(5)]

    assert results == [120] * 5
    assert len(calls) == 1, f"expected one parse, got {len(calls)}"


def test_empty_and_malformed_blobs_are_unchanged():
    _reset_count_cache()
    for empty in (None, "", "   ", "[]", "null"):
        assert _count_annotations(empty) == 0
    # A malformed or non-array blob degrades to the -1 sentinel rather than
    # raising: a save must never fail because its *count* could not be taken.
    _reset_count_cache()
    assert _count_annotations('{"not": "an array"}') == -1
    _reset_count_cache()
    assert _count_annotations("[1, 2") == -1


def test_reset_clears_the_memo():
    """Entries are keyed on `id()`, so they must not outlive the request.

    Without the reset, a freed blob's address can be reused by a different
    string of the same length and the memo would answer with the wrong count.
    """
    _reset_count_cache()
    _count_annotations(_blob(3))
    assert _COUNT_CACHE
    _reset_count_cache()
    assert not _COUNT_CACHE


def test_distinct_blobs_of_equal_length_do_not_collide():
    _reset_count_cache()
    # Two blobs of identical length but different element counts. Both are
    # held live for the duration, so their ids cannot be recycled into one
    # another — what is being pinned here is that equal length alone never
    # makes two blobs share a memo entry.
    a = '[{"id":"aa"},{"id":"bb"}]'
    b = '[{"identifier":"cccc"}]  '
    assert len(a) == len(b), "test needs two equal-length blobs"
    assert _count_annotations(a) == 2
    assert _count_annotations(b) == 1
    # And the first answer is still intact after the second was cached.
    assert _count_annotations(a) == 2


def test_parse_memo_shares_one_parse_across_consumers(monkeypatch):
    """The save path's several consumers must parse a blob once between them.

    `_count_annotations`, the append check and the history counters all go
    through `_parsed`. Before this was shared, one save parsed the stored blob
    twice and the incoming blob three times — ~60 ms of GIL-held CPU each on a
    5 MB blob, which is what stalled unrelated requests.
    """
    from api.routers.tasks import _parsed

    _reset_count_cache()
    blob = _blob(80)

    calls = []
    real_loads = json.loads

    def counting_loads(s, *a, **kw):
        calls.append(len(s))
        return real_loads(s, *a, **kw)

    monkeypatch.setattr("api.routers.tasks.json.loads", counting_loads)

    # Every consumer of the blob in one request.
    first = _parsed(blob)
    again = _parsed(blob)
    count = _count_annotations(blob)

    assert count == 80
    assert first is again, "the parsed list must be shared, not re-created"
    assert len(calls) == 1, f"expected one parse, got {len(calls)}"


def test_parse_memo_is_cleared_with_the_count_memo():
    from api.routers.tasks import _PARSE_CACHE, _parsed

    _reset_count_cache()
    _parsed(_blob(3))
    assert _PARSE_CACHE
    _reset_count_cache()
    assert not _PARSE_CACHE


def test_parsed_returns_none_for_unusable_blobs():
    """None is the "unusable" signal the `_parsed` diff variants expect."""
    from api.routers.tasks import _parsed

    for bad in (None, "", "   ", '{"not": "a list"}', "42", "[1,2", "nope"):
        _reset_count_cache()
        assert _parsed(bad) is None, f"expected None for {bad!r}"
