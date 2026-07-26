"""Create or reset an annotator account from the command line.

Self-registration is disabled on the LAN deployment (ALLOW_REGISTRATION=0), so
this is how the operator provisions the ~30 annotator accounts and resets a
forgotten password.

    python scripts/create_user.py alice
    python scripts/create_user.py alice --reset

The password is prompted for, never taken as an argument — an argument would
land in the shell history and the process list.
"""
import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import models  # noqa: E402
from api.auth import get_password_hash  # noqa: E402
from config import MIN_PASSWORD_LENGTH  # noqa: E402
from database import SessionLocal  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or reset an annotator account.")
    parser.add_argument("username")
    parser.add_argument(
        "--reset", action="store_true",
        help="Set a new password for an account that already exists.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        existing = db.query(models.User).filter(
            models.User.username == args.username
        ).first()

        if existing and not args.reset:
            print(f"User {args.username!r} already exists. Use --reset to change the password.")
            return 1
        if not existing and args.reset:
            print(f"User {args.username!r} does not exist.")
            return 1

        password = getpass.getpass("Password: ")
        if len(password) < MIN_PASSWORD_LENGTH:
            print(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
            return 1
        if password != getpass.getpass("Confirm password: "):
            print("Passwords do not match.")
            return 1

        if existing:
            existing.hashed_password = get_password_hash(password)
            action = "reset"
        else:
            db.add(models.User(
                username=args.username,
                hashed_password=get_password_hash(password),
            ))
            action = "created"

        db.commit()
        print(f"User {args.username!r} {action}.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
