from typing import List, Optional

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.deps import get_current_user, get_db
from app.iam import defaults, evaluator
from app.iam.actions import ACTIONS
from app.models.iam import IamPolicy, IamRole, IamRolePolicy, IamUserRole
from app.models.user import User


def _key(role) -> str:
    return getattr(role, "value", role)


def role_statements(db: Session, role: IamRole) -> List[dict]:
    rows = (
        db.query(IamPolicy)
        .join(IamRolePolicy, IamRolePolicy.policy_id == IamPolicy.id)
        .filter(IamRolePolicy.role_id == role.id)
        .all()
    )
    return [s for p in rows for s in p.document["statements"]]


def statements_for(db: Session, user: User) -> List[evaluator.Scoped]:
    """Everything this person's roles say: their main role plus any extra assignments."""
    out: List[evaluator.Scoped] = []
    main = _key(user.role)
    role = db.query(IamRole).filter(IamRole.key == main).first()
    if role:
        out += [(s, None) for s in role_statements(db, role)]
    elif main in defaults.SYSTEM_ROLES:  # seed row missing: use the built-in
        out += [(s, None) for s in defaults.SYSTEM_ROLES[main][1]]
    extras = (
        db.query(IamUserRole, IamRole)
        .join(IamRole, IamRole.id == IamUserRole.role_id)
        .filter(IamUserRole.user_id == user.id)
        .all()
    )
    for ur, r in extras:
        scope = str(ur.plant_unit_id) if ur.plant_unit_id else None
        out += [(s, scope) for s in role_statements(db, r)]
    return out


def allowed(
    db: Session,
    user: User,
    action: str,
    resource: str = "*",
    ctx: Optional[dict] = None,
) -> bool:
    ctx = {"user_id": user.id, **(ctx or {})}
    return evaluator.decide(statements_for(db, user), action, resource, ctx)


def require(action: str):
    """Route guard, the replacement for check_role([...])."""
    if action not in ACTIONS:
        raise RuntimeError(
            f"unregistered action: {action}"
        )  # fails at import, not in prod

    def dep(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
        if not allowed(db, user, action):
            raise HTTPException(status_code=403, detail=f"Operation not permitted. Missing permission: {action}")
        return user

    return dep


def my_permissions(db: Session, user: User) -> dict:
    stmts = statements_for(db, user)
    scopes = {sc for _, sc in stmts if sc}
    ctx = {"user_id": user.id}
    return {
        "role": _key(user.role),
        "allowed": [a for a in ACTIONS if evaluator.decide(stmts, a, "*", ctx)],
        "by_plant_unit": {
            sc: [
                a
                for a in ACTIONS
                if evaluator.decide(stmts, a, "*", {**ctx, "plant_unit_id": sc})
            ]
            for sc in scopes
        },
    }


def ensure_system_roles(db: Session) -> None:
    """Create any missing built-in role (the migration does this in production)."""
    for key, (label, statements) in defaults.SYSTEM_ROLES.items():
        if db.query(IamRole).filter(IamRole.key == key).first():
            continue
        role = IamRole(key=key, label=label, is_system=True)
        pol = IamPolicy(
            name=f"{key}-default",
            document=defaults.document(statements),
            is_system=True,
        )
        db.add_all([role, pol])
        db.flush()
        db.add(IamRolePolicy(role_id=role.id, policy_id=pol.id))
    db.commit()
