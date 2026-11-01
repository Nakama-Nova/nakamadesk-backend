"""Who changed what on staff accounts. Never put password hashes or tokens in here."""

import json
from typing import Optional

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog


def record(
    db: Session,
    actor_id,
    table: str,
    row_id,
    action: str,
    old: Optional[dict] = None,
    new: Optional[dict] = None,
) -> None:
    """Add an audit row to the caller's transaction (the caller commits)."""

    def plain(d):  # Decimals, UUIDs and enums become text so JSONB accepts them
        return None if d is None else json.loads(json.dumps(d, default=str))

    db.add(
        AuditLog(
            user_id=actor_id,
            table_name=table,
            row_id=str(row_id),
            action=action,
            old_data=plain(old),
            new_data=plain(new),
        )
    )
