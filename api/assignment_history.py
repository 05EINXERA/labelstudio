"""Recording task-assignment changes (.devnotes/features/task-assignment-history/).

`Task.assigned_team_id` / `assignee_user_id` are the authority on *current*
assignment. `TaskAssignmentEvent` is the append-only log of how they got there.

**Every code path that writes either column must go through here** (CLAUDE.md
rule 1d; `tests/test_assignment_history_writers.py` enforces the list of files).
The usage is always the same three steps, in one transaction:

    before = snapshot(db, models.Task.id.in_(ids))   # read the old values
    ...the existing UPDATE...                        # change the columns
    record(db, before, after, actor_id=..., source=...)
    commit_with_retry(db)                            # one commit covers both

`record` never commits. A change and its history land together or not at all,
because a log that disagrees with the table is worse than no log.

Imports only `models` — never a router.
"""
from typing import Dict, Iterable, Optional, Tuple, Union

from sqlalchemy.orm import Session

import models

State = Tuple[Optional[int], Optional[int]]  # (team_id, user_id)

# The vocabulary of TaskAssignmentEvent.source.
SOURCE_ASSIGN = "assign"
SOURCE_BULK_ASSIGN = "bulk_assign"
SOURCE_TEAM_DELETED = "team_deleted"
SOURCE_GRANT_REVOKED = "grant_revoked"
SOURCE_MEMBER_LEFT = "member_left"
SOURCE_MEMBER_REMOVED = "member_removed"


def snapshot(db: Session, *criteria) -> Dict[int, State]:
    """`{task_id: (team_id, user_id)}` for the tasks matching `criteria`.

    One SELECT of three columns. Call it *before* the UPDATE it describes.
    """
    rows = (
        db.query(
            models.Task.id,
            models.Task.assigned_team_id,
            models.Task.assignee_user_id,
        )
        .filter(*criteria)
        .all()
    )
    return {tid: (team, user) for tid, team, user in rows}


def _names(db: Session, model, name_col, ids: Iterable[Optional[int]]) -> Dict[int, str]:
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    return {
        i: n for i, n in db.query(model.id, name_col).filter(model.id.in_(wanted)).all()
    }


def record(
    db: Session,
    before: Dict[int, State],
    after: Union[State, Dict[int, State]],
    *,
    actor_id: Optional[int],
    source: str,
) -> int:
    """Add one event per task whose state actually changed; return how many.

    `after` is either one `(team, user)` applied to every task, or a per-task
    dict (a bulk assign that sends only the team leaves each task's person
    alone). A task whose state is unchanged gets no row — an Apply with nothing
    changed must not fill the history with duplicates.
    """
    changes = []
    for task_id, old in before.items():
        new = after[task_id] if isinstance(after, dict) else after
        if new != old:
            changes.append((task_id, old, new))
    if not changes:
        return 0

    team_ids = [state[0] for _, old, new in changes for state in (old, new)]
    user_ids = [state[1] for _, old, new in changes for state in (old, new)]
    team_names = _names(db, models.Team, models.Team.name, team_ids)
    user_names = _names(db, models.User, models.User.username, user_ids)

    rows = []
    for task_id, (t_from, u_from), (t_to, u_to) in changes:
        rows.append(
            {
                "task_id": task_id,
                "changed_by_id": actor_id,
                "source": source,
                "team_from_id": t_from,
                "team_to_id": t_to,
                "user_from_id": u_from,
                "user_to_id": u_to,
                "team_from_name": team_names.get(t_from),
                "team_to_name": team_names.get(t_to),
                "user_from_name": user_names.get(u_from),
                "user_to_name": user_names.get(u_to),
            }
        )
    db.bulk_insert_mappings(models.TaskAssignmentEvent, rows)
    return len(rows)
