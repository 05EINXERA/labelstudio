"""Re-inject CLAUDE.md into the agent's context when it changes mid-session.

Why this exists
---------------
Claude Code snapshots CLAUDE.md into the system prompt once, when the session
starts, and never refreshes it. Anything that rewrites the file afterwards --
a `git merge`, a `git checkout`, a branch switch between worktrees, or someone
editing the file in another window -- leaves the agent working from a copy that
no longer matches disk, with no signal that it happened.

That is not hypothetical. On 2026-10-06 the `dev`/`dev-stage` telemetry rule was
added to the Workflow section and merged into `dev-stage` *while a session was
running*. The agent kept quoting the pre-merge Workflow section ("Branch from
`main`") for the rest of the session and had to be corrected by hand.

What it does
------------
Records the file's contents the first time it runs in a session, then on every
later run compares disk against that record. When they differ it prints a
unified diff, which is what the agent actually needs: the precise delta between
the stale copy in its system prompt and the truth on disk.

Silent when nothing changed -- which is almost always -- so it costs a hash
comparison per prompt and nothing in context.

Wired to two events (see .claude/settings.json):
  UserPromptSubmit    -- catches edits made between turns.
  PostToolUse, Bash   -- catches a change the agent itself caused (a merge, a
                         checkout, a sed -i) in the same turn rather than one
                         prompt later.

The Bash hook deliberately carries no `if: Bash(git *)` filter. Two reasons: the
filter was measured not to apply in this build (the hook fired on a `tail`), and
git is not the only way the file moves -- a `cp`, a `sed -i`, or an editor in
another window all count. Running on every Bash call costs one sha-free string
compare of two small files, which is not worth optimising away.

Contract: this must never break a session. Every failure path exits 0 silently.
A hook that crashes the agent to report a docs change is worse than the staleness.
"""

import hashlib
import json
import os
import sys
import tempfile
from difflib import unified_diff

# Files whose mid-session drift matters. Relative to the repo root.
WATCHED = ("CLAUDE.md", "AGENTS.md")

# A diff bigger than this is summarised instead of printed in full: past this
# size it is a rewrite, and the agent should re-read the file itself rather than
# have the whole thing replayed into context.
#
# Both limits are needed. A line cap alone let a 17.5 KB diff through during
# development -- 60 changed lines, but prose lines of 200+ characters each --
# which is exactly the context flood the cap exists to prevent. Characters are
# the dimension that actually costs anything.
MAX_DIFF_LINES = 200
MAX_DIFF_CHARS = 4000


def repo_root() -> str:
    # This file is at <root>/.claude/hooks/, so the root is two levels up.
    # Derived from __file__ rather than cwd: hooks do not reliably run from the
    # project directory, and a worktree has its own root.
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def state_path(session_id: str, root: str) -> str:
    # Keyed by session AND root, so two worktrees of this repo open in two
    # sessions never read each other's baseline.
    key = hashlib.sha256(f"{session_id}::{root}".encode("utf-8")).hexdigest()[:16]
    return os.path.join(tempfile.gettempdir(), f"claude-md-freshness-{key}.json")


def read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        # A missing or unreadable file is recorded as empty rather than erroring:
        # if it appears later, that shows up as a diff, which is correct.
        return ""


def main() -> int:
    # CLAUDE.md is full of en-dashes and section signs, and this hook's stdout is
    # cp1252 on Windows by default -- which turned every one of them into "?" in
    # the diff. Force UTF-8 so the injected text matches the file.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}

    session_id = str(payload.get("session_id") or "no-session")
    root = repo_root()
    store = state_path(session_id, root)

    current = {name: read(os.path.join(root, name)) for name in WATCHED}

    try:
        with open(store, "r", encoding="utf-8") as handle:
            baseline = json.load(handle)
    except (OSError, json.JSONDecodeError, ValueError):
        baseline = None

    if baseline is None:
        # First run of this session. What is on disk now is what the system
        # prompt was built from, so there is nothing to report -- just record it.
        try:
            with open(store, "w", encoding="utf-8") as handle:
                json.dump(current, handle)
        except OSError:
            pass
        return 0

    reports = []
    for name in WATCHED:
        before = baseline.get(name, "")
        after = current[name]
        if before == after:
            continue
        diff = list(unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=f"{name} (copy in your system prompt)",
            tofile=f"{name} (on disk now)",
            lineterm="",
        ))
        body = "\n".join(diff)
        if len(diff) > MAX_DIFF_LINES or len(body) > MAX_DIFF_CHARS:
            # Name the changed section headings rather than the whole diff, so
            # the agent knows where to look without paying for the full text.
            headings = [ln[1:].strip() for ln in diff
                        if ln[:1] in "+-" and ln[1:].lstrip().startswith("#")]
            where = ("; ".join(dict.fromkeys(headings))[:400] or "multiple sections")
            reports.append(
                f"{name} changed substantially since this session started "
                f"({len(diff)} diff lines, {len(body)} chars). Affected: {where}. "
                f"Re-read {name} before relying on it."
            )
        else:
            reports.append(f"{name} changed since this session started:\n" + body)

    if not reports:
        return 0

    try:
        with open(store, "w", encoding="utf-8") as handle:
            json.dump(current, handle)
    except OSError:
        pass

    message = (
        "[project-instructions-changed] The copy of the project instructions in "
        "your system prompt is STALE. It was captured when this session started; "
        "the file has changed since. The diff below is authoritative -- prefer it "
        "over what your system prompt says.\n\n" + "\n\n".join(reports)
    )

    # Both events are fed through `additionalContext` rather than bare stdout.
    # UserPromptSubmit would accept plain stdout, but PostToolUse does not inject
    # it into context, so the uniform JSON shape is what makes the git-merge
    # trigger actually work. The event name has to match the firing event, which
    # is why settings.json passes it as argv[1].
    event = sys.argv[1] if len(sys.argv) > 1 else "UserPromptSubmit"
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": message,
        },
        "systemMessage": "Project instructions changed on disk - reloaded into context.",
    }))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 - a hook must never break the session
        sys.exit(0)
