"""Guard: only known files may write the assignment columns.

CLAUDE.md rule 1d. A new writer of `assigned_team_id` / `assignee_user_id` that
does not record history silently opens a gap between the table and its log.
This does not prove the known files record correctly (test_task_assignment_history
does that); it makes adding a *new* writer a deliberate, reviewed act.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Files that write the columns and record history through api/assignment_history.
KNOWN_WRITERS = {
    "api/routers/tasks.py",
    "api/routers/teams.py",
    "api/routers/grants.py",
}

# `Task.assigned_team_id: ...` (an UPDATE key), `task.assigned_team_id = ...`,
# or an `assigned_team_id=...` keyword (ORM constructor).
WRITE = re.compile(
    r"(models\.Task\.(assigned_team_id|assignee_user_id)\s*:"
    r"|\.(assigned_team_id|assignee_user_id)\s*=(?!=)"
    r"|\b(assigned_team_id|assignee_user_id)\s*=(?!=))"
)


def _py_files():
    yield from (ROOT / "api").rglob("*.py")
    yield from (ROOT / "formats").rglob("*.py")
    for name in ("main.py", "database.py", "detector.py"):
        yield ROOT / name


def test_only_known_files_write_assignment_columns():
    writers = set()
    for path in _py_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel == "api/assignment_history.py":
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if WRITE.search(line.split("#", 1)[0]):
                writers.add(rel)
                break
    unknown = writers - KNOWN_WRITERS
    assert not unknown, (
        f"{sorted(unknown)} write Task.assigned_team_id / assignee_user_id. Route the "
        "change through api/assignment_history.record() and add the file to "
        "KNOWN_WRITERS (CLAUDE.md rule 1d)."
    )
