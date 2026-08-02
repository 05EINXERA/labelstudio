"""A minimal in-process rate limiter.

There is no rate-limit infrastructure in this app, and the one endpoint that
needs it (`POST /api/teams/{id}/members`, which discloses whether a username
exists) does not justify adding Redis. This is the lightest thing that works.

**Single-worker constraint applies (CLAUDE.md rule 9 / D3):** the counters live
in a process-local dict, exactly like `JOBS`, `_models` and `_TASK_LOCKS`. With
`--workers N` each worker would keep its own window and the effective limit
would be N times the configured one. Do not add workers.

On restart the windows reset, so a caller who had exhausted their allowance gets
a fresh one. That is harmless: the limit exists to stop bulk enumeration, and a
restart is not something an attacker can trigger on demand.

If a reverse proxy is ever added (deferred item D1), move this to the edge.
"""
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Hashable, Tuple

# {bucket_name: {key: deque[timestamp]}} — one deque of hit times per caller.
# A deque rather than a counter so the window slides continuously instead of
# resetting on a fixed boundary, which would let a caller spend two full
# allowances back to back across the boundary.
_HITS: Dict[str, Dict[Hashable, Deque[float]]] = defaultdict(lambda: defaultdict(deque))


def check_rate_limit(
    bucket: str, key: Hashable, limit: int, window_seconds: int
) -> Tuple[bool, int]:
    """Record a hit and report whether it is allowed.

    Returns `(allowed, retry_after_seconds)`. `retry_after_seconds` is 0 when
    allowed, and otherwise how long until the oldest hit leaves the window.

    A rejected call is **not** recorded: counting refusals would extend the
    lockout every time a client retried, turning a rate limit into a ban.
    """
    if limit <= 0:
        return True, 0

    now = time.monotonic()
    hits = _HITS[bucket][key]

    cutoff = now - window_seconds
    while hits and hits[0] <= cutoff:
        hits.popleft()

    if len(hits) >= limit:
        retry_after = max(1, int(hits[0] + window_seconds - now) + 1)
        return False, retry_after

    hits.append(now)
    return True, 0


def reset_rate_limit(bucket: str = None) -> None:
    """Clear recorded hits. For tests, and for an operator unwedging a caller.

    Without this, one test exhausting a limit would leak into every later test
    in the same process — the counters are module state and the test suite does
    not restart between files.
    """
    if bucket is None:
        _HITS.clear()
    else:
        _HITS.pop(bucket, None)
