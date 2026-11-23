"""Pure policy logic (no database). Same rules as AWS IAM:
nothing is allowed by default; any matching Deny wins; otherwise any matching Allow permits.

A statement is {"effect": "Allow"|"Deny", "actions": [...], "resources": [...],
"condition": {"self": true}}. Patterns use * (fnmatch). "self" means the resource's
owner is the person asking (ctx["owner_id"] == ctx["user_id"]).
"""

from fnmatch import fnmatchcase
from typing import Iterable, Optional, Tuple

from app.iam.actions import ACTIONS

# (statement, plant_unit_scope) - scope None = applies everywhere
Scoped = Tuple[dict, Optional[str]]


def validate_document(doc) -> None:
    if not isinstance(doc, dict) or doc.get("version") != 1:
        raise ValueError("document must be an object with version 1")
    stmts = doc.get("statements")
    if not isinstance(stmts, list):
        raise ValueError("statements must be a list")
    for s in stmts:
        if s.get("effect") not in ("Allow", "Deny"):
            raise ValueError("effect must be Allow or Deny")
        acts = s.get("actions")
        if not acts or not all(isinstance(a, str) for a in acts):
            raise ValueError("each statement needs a list of actions")
        for a in acts:
            if not expand(a):
                raise ValueError(f"action pattern matches nothing: {a}")
        res = s.get("resources", ["*"])
        if not res or not all(isinstance(r, str) for r in res):
            raise ValueError("resources must be a list of strings")
        cond = s.get("condition") or {}
        if set(cond) - {"self"}:
            raise ValueError("unknown condition (only 'self' is supported)")


def expand(pattern: str) -> list:
    """The known actions a pattern covers."""
    return [a for a in ACTIONS if fnmatchcase(a, pattern)]


def decide(
    statements: Iterable[Scoped],
    action: str,
    resource: str = "*",
    ctx: Optional[dict] = None,
) -> bool:
    ctx = ctx or {}
    allowed = False
    for s, scope in statements:
        if scope is not None and str(ctx.get("plant_unit_id")) != scope:
            continue
        if not any(fnmatchcase(action, p) for p in s["actions"]):
            continue
        if not any(fnmatchcase(resource, p) for p in s.get("resources", ["*"])):
            continue
        if (s.get("condition") or {}).get("self") and (
            ctx.get("owner_id") is None or ctx.get("owner_id") != ctx.get("user_id")
        ):
            continue
        if s["effect"] == "Deny":
            return False
        allowed = True
    return allowed


def can_grant(actor: Iterable[Scoped], doc: dict) -> list:
    """Actions in `doc` that the actor may not hand out (empty = fine).

    You can only give what you hold everywhere. ponytail: resources/conditions in the
    document are not compared, only actions; tighten if narrow grants become common.
    """
    mine = [(s, sc) for s, sc in actor if sc is None]
    missing = set()
    for s in doc["statements"]:
        if s["effect"] != "Allow":
            continue  # a Deny only ever narrows
        for p in s["actions"]:
            missing.update(a for a in expand(p) if not decide(mine, a))
    return sorted(missing)
