"""
Admin commands that must work before anyone can log in.

    python -m app.cli create-owner
    python -m app.cli finance-sync      write the ledger from sales and order payments
"""

import getpass
import sys

from fastapi import HTTPException

from app.db.session import SessionLocal
from app.services import auth_service


def create_owner() -> int:
    username = input("Owner username: ").strip()
    email = input("Owner email: ").strip()
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Repeat password: "):
        print("Passwords do not match.")
        return 1

    db = SessionLocal()
    try:
        user = auth_service.bootstrap_owner(db, username, email, password)
    except HTTPException as e:
        print(f"Not created: {e.detail}")
        return 1
    finally:
        db.close()
    print(f"Owner '{user.username}' created.")
    return 0


def finance_sync() -> int:
    from app.finance import projector

    db = SessionLocal()
    try:
        r = projector.project(db)
    finally:
        db.close()
    print(
        f"posted {r.posted}, reversed {r.reversed}, unchanged {r.unchanged}, "
        f"nothing to post {r.nothing_to_post}, problems {len(r.problems)}"
    )
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["finance-sync"]:
        sys.exit(finance_sync())
    if sys.argv[1:] == ["create-owner"]:
        sys.exit(create_owner())
    print(__doc__)
    sys.exit(2)
