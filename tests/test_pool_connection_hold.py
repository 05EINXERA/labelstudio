"""Connection-pool holding regression tests.

The 2026-09-07 recurrence of 08_POOL_EXHAUSTION.md was not caused by too few
connections but by each request holding one for too long. `get_current_user`
runs on every /api/* request, queried, and never ended its transaction — so the
pooled connection stayed checked out from auth until the response, across all
the non-DB work in between. At ~25 concurrent annotators that idle holding
exhausted a 30+30 pool.

These tests pin the invariant that made it safe: a read-only auth dependency
returns its connection to the pool before the handler body runs.
"""
import config
from database import SessionLocal, engine

import api.auth as auth


def _checked_out():
    return engine.pool.checkedout()


def test_get_current_user_does_not_hold_a_connection(alice):
    """The read-only auth path must not pin a pooled connection.

    Regression: without the explicit rollback the session's transaction stays
    open and the connection is held for the whole request.
    """
    token = alice["Authorization"].split(" ", 1)[1]
    db = SessionLocal()
    try:
        before = _checked_out()
        user = auth.get_current_user(token=token, db=db)
        assert user is not None
        assert _checked_out() == before, (
            "get_current_user left a connection checked out; it must end its "
            "read-only transaction so the connection returns to the pool"
        )
    finally:
        db.close()


def test_returned_user_is_still_usable_after_release(alice):
    """Releasing the connection must not detach the ORM object.

    The rollback is only safe because `user` stays attached to the session and
    transparently re-acquires a connection on next access. If that stopped
    holding, every handler reading a user attribute would raise instead.
    """
    token = alice["Authorization"].split(" ", 1)[1]
    db = SessionLocal()
    try:
        user = auth.get_current_user(token=token, db=db)
        assert user.username  # re-acquires a connection lazily, must not raise
    finally:
        db.close()


def test_get_current_annotator_without_header_holds_nothing():
    """The no-header early return is the common case and must not query/hold."""
    class _Req:
        headers: dict = {}

    db = SessionLocal()
    try:
        before = _checked_out()
        assert auth.get_current_annotator(request=_Req(), db=db) is None
        assert _checked_out() == before
    finally:
        db.close()


def test_threadpool_cap_stays_below_pool_ceiling():
    """Cap == ceiling lets a burst take every connection and starve cheap
    endpoints (/api/team/ping timed out beside the saves on 2026-09-07). The
    margin keeps the thread queue the first bottleneck, not the pool."""
    ceiling = config.DB_POOL_SIZE + config.DB_MAX_OVERFLOW
    assert config.THREADPOOL_CAP < ceiling, (
        f"THREADPOOL_CAP ({config.THREADPOOL_CAP}) must stay below the pool "
        f"ceiling ({ceiling}) so requests queue on threads, not connections"
    )
