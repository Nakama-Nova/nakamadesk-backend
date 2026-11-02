"""
Who reports to whom.

Each person has at most one manager (users.reports_to_id), set by the owner, so the
staff form a tree: the owner at the top, then whoever the owner chooses. A manager sees
the people under them (their reports, and theirs in turn); the owner sees everyone.
The shop is small, so the tree is built in memory from one query.
"""

from typing import Dict, List, Optional, Set
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.enums import UserRole
from app.models.user import User


def check_manager(
    db: Session, user_id: Optional[UUID], role: str, manager_id: Optional[UUID]
) -> None:
    """
    Raise 400 unless `manager_id` may be this person's manager. None (no manager) is
    always fine. `user_id` is None for a person not created yet.
    """
    if manager_id is None:
        return
    if role == UserRole.OWNER:
        raise HTTPException(
            status_code=400, detail="The owner is at the top and reports to no one"
        )
    if manager_id == user_id:
        raise HTTPException(status_code=400, detail="Nobody can report to themselves")
    boss = db.get(User, manager_id)
    if not boss or not boss.is_active:
        raise HTTPException(
            status_code=400, detail="Choose an active person as the manager"
        )
    # walking up from the new manager must never come back to this person
    up = {u.id: u.reports_to_id for u in db.query(User.id, User.reports_to_id)}
    seen, cur = set(), manager_id
    while cur is not None and cur not in seen:
        if cur == user_id:
            raise HTTPException(
                status_code=400,
                detail="That would make a loop: they already manage this person",
            )
        seen.add(cur)
        cur = up.get(cur)


def report_ids(db: Session, user: User) -> Set[UUID]:
    """Everyone under this person, at any depth, who is still active. The owner: all."""
    people = db.query(User.id, User.reports_to_id, User.is_active).all()
    if user.role == UserRole.OWNER:
        return {p.id for p in people if p.is_active and p.id != user.id}
    under: Dict[Optional[UUID], List[UUID]] = {}
    for p in people:
        under.setdefault(p.reports_to_id, []).append(p.id)
    active = {p.id for p in people if p.is_active}
    out: Set[UUID] = set()
    todo = list(under.get(user.id, []))
    while todo:
        uid = todo.pop()
        if uid in out or uid == user.id:
            continue
        out.add(uid)
        todo.extend(under.get(uid, []))
    return out & active


def _node(u: User) -> dict:
    return {
        "id": u.id,
        "full_name": u.full_name,
        "username": u.username,
        "role": u.role,
        "reports_to_id": u.reports_to_id,
        "reports": [],
    }


def tree(db: Session) -> List[dict]:
    """
    The active staff as a tree, owner first. A person whose manager is switched off
    shows at the top rather than vanishing.
    """
    users = db.query(User).filter(User.is_active.is_(True)).all()
    nodes = {u.id: _node(u) for u in users}
    roots = []
    for u in sorted(users, key=lambda u: (u.full_name or u.username).lower()):
        parent = nodes.get(u.reports_to_id)
        (parent["reports"] if parent else roots).append(nodes[u.id])
    roots.sort(key=lambda n: n["role"] != UserRole.OWNER.value)  # owners first
    return roots
