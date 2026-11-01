"""
The tool register: what tools there are, who has each one, and what state it came back in.

A tool is `available` until it is issued to someone (`issued`), and goes back to
`available` when returned fine, `broken` when returned damaged, or `lost`. Only one
open issue per tool, enforced by locking the tool row while issuing.
"""

from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.tool import Tool, ToolIssue
from app.models.user import User
from app.schemas.registers import (
    IssueIn,
    IssueOut,
    ReturnIn,
    ToolIn,
    ToolOut,
    ToolUpdate,
)
from app.services import stock_location_service


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _open_issue(session: Session, tool_id: UUID) -> Optional[ToolIssue]:
    return (
        session.query(ToolIssue)
        .filter(ToolIssue.tool_id == tool_id, ToolIssue.returned_at.is_(None))
        .first()
    )


def _to_out(session: Session, tool: Tool) -> ToolOut:
    out = ToolOut.model_validate(tool)
    if tool.status == "issued":
        issue = _open_issue(session, tool.id)
        out.held_by = issue.user_id if issue else None
    return out


def _issue_out(session: Session, issue: ToolIssue) -> IssueOut:
    tool = session.get(Tool, issue.tool_id)
    user = session.get(User, issue.user_id)
    return IssueOut(
        id=issue.id,
        tool_id=issue.tool_id,
        tool_tag=tool.tag if tool else None,
        tool_name=tool.name if tool else None,
        user_id=issue.user_id,
        user_name=user.username if user else None,
        issued_at=issue.issued_at,
        condition_out=issue.condition_out,
        returned_at=issue.returned_at,
        outcome=issue.outcome,
        condition_in=issue.condition_in,
        notes=issue.notes,
    )


def _get(session: Session, tool_id: UUID, lock: bool = False) -> Tool:
    q = session.query(Tool).filter(Tool.id == tool_id)
    if lock:
        q = q.with_for_update()
    tool = q.first()
    if tool is None:
        raise HTTPException(status_code=404, detail="Tool not found")
    return tool


def _save(session: Session, tool: Tool) -> None:
    try:
        session.add(tool)
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=409, detail="A tool with that tag already exists"
        ) from None


def create(session: Session, data: ToolIn) -> ToolOut:
    if data.location_id is not None:
        stock_location_service.get_location(session, data.location_id)
    tool = Tool(**data.model_dump())
    _save(session, tool)
    session.refresh(tool)
    return _to_out(session, tool)


def update(session: Session, tool_id: UUID, data: ToolUpdate) -> ToolOut:
    tool = _get(session, tool_id, lock=True)
    if data.location_id is not None:
        stock_location_service.get_location(session, data.location_id)
    for key, value in data.model_dump(exclude={"status"}).items():
        setattr(tool, key, value)
    if data.status is not None and data.status != tool.status:
        if tool.status == "issued":
            raise HTTPException(status_code=409, detail="Return the tool first")
        if data.status == "available" and tool.status in ("lost", "broken", "retired"):
            tool.status = "available"  # found it, fixed it, or un-retired it
        elif data.status == "retired":
            tool.status = "retired"
    _save(session, tool)
    session.refresh(tool)
    return _to_out(session, tool)


def get(session: Session, tool_id: UUID) -> ToolOut:
    return _to_out(session, _get(session, tool_id))


def list_tools(
    session: Session,
    status: Optional[str],
    location_id: Optional[UUID],
    limit: int,
    offset: int,
) -> List[ToolOut]:
    q = session.query(Tool)
    if status:
        q = q.filter(Tool.status == status)
    if location_id:
        q = q.filter(Tool.location_id == location_id)
    rows = q.order_by(Tool.tag).limit(limit).offset(offset).all()
    return [_to_out(session, t) for t in rows]


def issue(session: Session, tool_id: UUID, data: IssueIn, issued_by: UUID) -> IssueOut:
    tool = _get(session, tool_id, lock=True)
    if tool.status != "available":
        if tool.status == "issued":
            open_issue = _open_issue(session, tool.id)
            holder = session.get(User, open_issue.user_id) if open_issue else None
            raise HTTPException(
                status_code=409,
                detail=f"Already issued to {holder.username if holder else 'someone'}",
            )
        raise HTTPException(status_code=409, detail=f"This tool is {tool.status}")
    user = session.get(User, data.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=404, detail="Person not found")
    row = ToolIssue(
        tool_id=tool.id,
        user_id=user.id,
        issued_by=issued_by,
        condition_out=tool.condition,
        notes=data.notes,
    )
    tool.status = "issued"
    session.add(row)
    session.commit()
    session.refresh(row)
    return _issue_out(session, row)


def give_back(session: Session, tool_id: UUID, data: ReturnIn) -> IssueOut:
    tool = _get(session, tool_id, lock=True)
    row = _open_issue(session, tool.id)
    if row is None:
        raise HTTPException(status_code=409, detail="This tool is not issued")
    row.returned_at = _now()
    row.outcome = data.outcome
    row.notes = data.notes or row.notes
    if data.outcome == "lost":
        tool.status = "lost"
        row.condition_in = None
    elif data.outcome == "damaged":
        tool.status = "broken"
        tool.condition = data.condition_in or "damaged"
        row.condition_in = tool.condition
    else:
        tool.status = "available"
        tool.condition = data.condition_in or tool.condition
        row.condition_in = tool.condition
    session.commit()
    session.refresh(row)
    return _issue_out(session, row)


def history(session: Session, tool_id: UUID, limit: int, offset: int) -> List[IssueOut]:
    _get(session, tool_id)
    rows = (
        session.query(ToolIssue)
        .filter(ToolIssue.tool_id == tool_id)
        .order_by(ToolIssue.issued_at.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    return [_issue_out(session, r) for r in rows]


def held_by(session: Session, user_id: UUID) -> List[IssueOut]:
    """The tools a person has right now."""
    rows = (
        session.query(ToolIssue)
        .filter(ToolIssue.user_id == user_id, ToolIssue.returned_at.is_(None))
        .order_by(ToolIssue.issued_at)
        .all()
    )
    return [_issue_out(session, r) for r in rows]
