"""Roles and policies, edited by people holding iam:* rights (managers can, within their own rights)."""

import re
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.deps import get_current_user, get_db
from app.iam import evaluator
from app.iam import service as iam
from app.iam.actions import ACTIONS
from app.models.iam import IamPolicy, IamRole, IamRolePolicy, IamUserRole
from app.models.user import User
from app.services import audit

router = APIRouter(prefix="/iam", tags=["Roles & permissions"])


class PolicyIn(BaseModel):
    name: str
    document: dict


class RoleIn(BaseModel):
    label: str
    description: Optional[str] = None
    policy_ids: List[UUID] = []


class Assignment(BaseModel):
    role_key: str
    plant_unit_id: Optional[UUID] = None


class Simulate(BaseModel):
    user_id: UUID
    action: str
    resource: str = "*"
    plant_unit_id: Optional[UUID] = None


def _check_doc(db, actor, doc):
    try:
        evaluator.validate_document(doc)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    over = evaluator.can_grant(iam.statements_for(db, actor), doc)
    if over:
        raise HTTPException(
            status_code=403, detail=f"You cannot grant what you do not hold: {over}"
        )


def _policy_out(p):
    return {
        "id": p.id,
        "name": p.name,
        "document": p.document,
        "is_system": p.is_system,
    }


def _role_out(db, r):
    pols = db.query(IamRolePolicy.policy_id).filter(IamRolePolicy.role_id == r.id).all()
    return {
        "id": r.id,
        "key": r.key,
        "label": r.label,
        "description": r.description,
        "is_system": r.is_system,
        "policy_ids": [p[0] for p in pols],
    }


def _role_or_404(db, role_id):
    r = db.get(IamRole, role_id)
    if not r:
        raise HTTPException(status_code=404, detail="Role not found")
    return r


def _set_policies(db, actor, role, policy_ids):
    for pid in policy_ids:
        p = db.get(IamPolicy, pid)
        if not p:
            raise HTTPException(status_code=404, detail=f"Policy {pid} not found")
        _check_doc(db, actor, p.document)
    db.query(IamRolePolicy).filter(IamRolePolicy.role_id == role.id).delete()
    for pid in set(policy_ids):
        db.add(IamRolePolicy(role_id=role.id, policy_id=pid))


@router.get("/actions")
def list_actions(_: User = Depends(iam.require("iam:role:read"))):
    return [{"action": a, "description": d} for a, d in ACTIONS.items()]


@router.get("/me")
def my_permissions(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """What I may do - the app shows and hides things from this, not from my role name."""
    return iam.my_permissions(db, user)


@router.get("/policies")
def list_policies(
    _: User = Depends(iam.require("iam:role:read")), db: Session = Depends(get_db)
):
    return [_policy_out(p) for p in db.query(IamPolicy).order_by(IamPolicy.name)]


@router.post("/policies", status_code=201)
def create_policy(
    body: PolicyIn,
    actor: User = Depends(iam.require("iam:role:write")),
    db: Session = Depends(get_db),
):
    if db.query(IamPolicy).filter(IamPolicy.name == body.name).first():
        raise HTTPException(status_code=409, detail="A policy with this name exists")
    _check_doc(db, actor, body.document)
    p = IamPolicy(name=body.name, document=body.document, created_by=actor.id)
    db.add(p)
    db.flush()
    audit.record(db, actor.id, "iam_policies", p.id, "INSERT", new=_policy_out(p))
    db.commit()
    return _policy_out(p)


@router.put("/policies/{policy_id}")
def update_policy(
    policy_id: UUID,
    body: PolicyIn,
    actor: User = Depends(iam.require("iam:role:write")),
    db: Session = Depends(get_db),
):
    p = db.get(IamPolicy, policy_id)
    if not p:
        raise HTTPException(status_code=404, detail="Policy not found")
    _check_doc(db, actor, body.document)
    owner_policy = (
        db.query(IamPolicy)
        .join(IamRolePolicy, IamRolePolicy.policy_id == IamPolicy.id)
        .join(IamRole, IamRole.id == IamRolePolicy.role_id)
        .filter(IamRole.key == "owner", IamPolicy.id == p.id)
        .first()
    )
    if owner_policy:
        raise HTTPException(status_code=403, detail="The owner's policy cannot change")
    old = _policy_out(p)
    p.document = body.document
    if not p.is_system:
        p.name = body.name
    audit.record(
        db, actor.id, "iam_policies", p.id, "UPDATE", old=old, new=_policy_out(p)
    )
    db.commit()
    return _policy_out(p)


@router.delete("/policies/{policy_id}", status_code=204)
def delete_policy(
    policy_id: UUID,
    actor: User = Depends(iam.require("iam:role:write")),
    db: Session = Depends(get_db),
):
    p = db.get(IamPolicy, policy_id)
    if not p:
        raise HTTPException(status_code=404, detail="Policy not found")
    if p.is_system:
        raise HTTPException(
            status_code=403, detail="Built-in policies cannot be deleted"
        )
    audit.record(db, actor.id, "iam_policies", p.id, "DELETE", old=_policy_out(p))
    db.delete(p)
    db.commit()


@router.get("/roles")
def list_roles(
    _: User = Depends(iam.require("iam:role:read")), db: Session = Depends(get_db)
):
    return [_role_out(db, r) for r in db.query(IamRole).order_by(IamRole.key)]


@router.post("/roles", status_code=201)
def create_role(
    body: RoleIn,
    actor: User = Depends(iam.require("iam:role:write")),
    db: Session = Depends(get_db),
):
    key = re.sub(r"[^a-z0-9]+", "_", body.label.lower()).strip("_")
    if not key:
        raise HTTPException(status_code=422, detail="Give the role a name")
    if db.query(IamRole).filter(IamRole.key == key).first():
        raise HTTPException(status_code=409, detail="A role with this name exists")
    r = IamRole(key=key, label=body.label, description=body.description)
    db.add(r)
    db.flush()
    _set_policies(db, actor, r, body.policy_ids)
    audit.record(db, actor.id, "iam_roles", r.id, "INSERT", new=_role_out(db, r))
    db.commit()
    return _role_out(db, r)


@router.put("/roles/{role_id}")
def update_role(
    role_id: UUID,
    body: RoleIn,
    actor: User = Depends(iam.require("iam:role:write")),
    db: Session = Depends(get_db),
):
    r = _role_or_404(db, role_id)
    if r.key == "owner":
        raise HTTPException(status_code=403, detail="The owner role cannot change")
    old = _role_out(db, r)
    r.label, r.description = body.label, body.description
    _set_policies(db, actor, r, body.policy_ids)
    audit.record(
        db, actor.id, "iam_roles", r.id, "UPDATE", old=old, new=_role_out(db, r)
    )
    db.commit()
    return _role_out(db, r)


@router.delete("/roles/{role_id}", status_code=204)
def delete_role(
    role_id: UUID,
    actor: User = Depends(iam.require("iam:role:write")),
    db: Session = Depends(get_db),
):
    r = _role_or_404(db, role_id)
    if r.is_system:
        raise HTTPException(status_code=403, detail="Built-in roles cannot be deleted")
    in_use = (
        db.query(User).filter(User.role == r.key).count()
        or db.query(IamUserRole).filter(IamUserRole.role_id == r.id).count()
    )
    if in_use:
        raise HTTPException(status_code=409, detail="People still hold this role")
    audit.record(db, actor.id, "iam_roles", r.id, "DELETE", old=_role_out(db, r))
    db.delete(r)
    db.commit()


@router.get("/users/{user_id}/roles")
def get_user_roles(
    user_id: UUID,
    _: User = Depends(iam.require("iam:user_role:assign")),
    db: Session = Depends(get_db),
):
    """This person's EXTRA roles (their main role is on the staff screen), in the form the PUT
    below takes, so a screen can show them and save changes without wiping what is there."""
    if not db.get(User, user_id):
        raise HTTPException(status_code=404, detail="User not found")
    rows = (
        db.query(IamUserRole, IamRole)
        .join(IamRole, IamRole.id == IamUserRole.role_id)
        .filter(IamUserRole.user_id == user_id)
        .order_by(IamRole.key)
        .all()
    )
    return {
        "user_id": user_id,
        "roles": [{"role_key": role.key, "plant_unit_id": link.plant_unit_id} for link, role in rows],
    }


@router.put("/users/{user_id}/roles")
def set_user_roles(
    user_id: UUID,
    body: List[Assignment],
    actor: User = Depends(iam.require("iam:user_role:assign")),
    db: Session = Depends(get_db),
):
    """Replace this person's EXTRA roles (their main role is set on the staff screen)."""
    if user_id == actor.id:
        raise HTTPException(status_code=403, detail="You cannot change your own roles")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    rows = []
    for a in body:
        r = db.query(IamRole).filter(IamRole.key == a.role_key).first()
        if not r:
            raise HTTPException(status_code=404, detail=f"No role {a.role_key}")
        _check_doc(db, actor, {"version": 1, "statements": iam.role_statements(db, r)})
        rows.append(
            IamUserRole(user_id=user.id, role_id=r.id, plant_unit_id=a.plant_unit_id)
        )
    old = [
        {"role_id": str(x.role_id), "plant_unit_id": str(x.plant_unit_id)}
        for x in db.query(IamUserRole).filter(IamUserRole.user_id == user.id)
    ]
    db.query(IamUserRole).filter(IamUserRole.user_id == user.id).delete()
    db.add_all(rows)
    user.token_version = (
        user.token_version or 0
    ) + 1  # ends their sessions, like a role change
    new = [a.model_dump(mode="json") for a in body]
    audit.record(
        db,
        actor.id,
        "iam_user_roles",
        user.id,
        "UPDATE",
        old={"roles": old},
        new={"roles": new},
    )
    db.commit()
    return {"user_id": user.id, "roles": new}


@router.post("/simulate")
def simulate(
    body: Simulate,
    _: User = Depends(iam.require("iam:role:read")),
    db: Session = Depends(get_db),
):
    """Would this person be allowed? (IAM's policy simulator.)"""
    user = db.get(User, body.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    ctx = {"plant_unit_id": body.plant_unit_id} if body.plant_unit_id else {}
    return {"allowed": iam.allowed(db, user, body.action, body.resource, ctx)}
