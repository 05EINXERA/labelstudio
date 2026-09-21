"""Grant or revoke the instance-level admin flag.

**This script is the only writer of `users.is_admin` anywhere.** No API path
sets it, and that is deliberate rather than an omission: it means privilege
escalation over HTTP is impossible rather than merely unlikely. Running this
needs shell access on the deploy box, which is already an elevated operation
(the app runs as an elevated Scheduled Task).

    python scripts/grant_admin.py alice
    python scripts/grant_admin.py alice --revoke
    python scripts/grant_admin.py --list

Re-runnable, and usable for any number of admins at any time (Q13) — it is not
a one-shot bootstrap. Granting twice is idempotent.

Modelled on `scripts/create_user.py`, which provisions the annotator accounts.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import models  # noqa: E402
from database import SessionLocal, commit_with_retry  # noqa: E402
from logging_service import log_event  # noqa: E402


def list_admins(db) -> int:
    admins = (
        db.query(models.User)
        .filter(models.User.is_admin.is_(True))
        .order_by(models.User.username)
        .all()
    )
    if not admins:
        print("No admins. Grant one with: python scripts/grant_admin.py <username>")
        return 0
    print(f"{len(admins)} admin(s):")
    for user in admins:
        print(f"  {user.username}  (id={user.id})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Grant or revoke instance-level admin. The only writer of is_admin.",
    )
    parser.add_argument("username", nargs="?", help="The account to change.")
    parser.add_argument(
        "--revoke", action="store_true",
        help="Remove admin instead of granting it. An admin role that can only "
             "ever be granted is a one-way door.",
    )
    parser.add_argument(
        "--list", action="store_true", dest="list_only",
        help="Show who currently holds admin, and change nothing.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.list_only:
            return list_admins(db)

        if not args.username:
            parser.error("a username is required unless --list is given")

        user = (
            db.query(models.User)
            .filter(models.User.username == args.username)
            .first()
        )
        if user is None:
            print(f"User {args.username!r} does not exist.")
            print("Create it first: python scripts/create_user.py <username>")
            return 1

        wanted = not args.revoke
        if bool(user.is_admin) == wanted:
            # Idempotent, and says so rather than implying it did something.
            state = "already an admin" if wanted else "already not an admin"
            print(f"{user.username} is {state}. Nothing to do.")
            return 0

        if args.revoke:
            remaining = (
                db.query(models.User)
                .filter(models.User.is_admin.is_(True), models.User.id != user.id)
                .count()
            )
            if remaining == 0:
                # Not fatal — an operator may genuinely want zero admins, and
                # this script can always grant one back. But losing the last
                # admin by accident is worth a word, since nothing in the UI
                # can undo it.
                print(
                    "WARNING: this removes the last admin. The dashboard will be "
                    "reachable by nobody until you grant one again."
                )

        user.is_admin = wanted
        commit_with_retry(db)  # CLAUDE.md rule 10, never raw db.commit()

        verb = "Granted" if wanted else "Revoked"
        print(f"{verb} admin for {user.username} (id={user.id}).")
        # WARN, like the other consequential auth actions: this is what keeps
        # the grant in the `grep WARN` destructive-action trail.
        log_event(
            "attendance.admin_granted" if wanted else "attendance.admin_revoked",
            level="WARN",
            account=user.username,
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
