from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.deps import check_role, get_current_user, get_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.registers import (
    IssueIn,
    IssueOut,
    ReturnIn,
    ToolIn,
    ToolOut,
    ToolUpdate,
)
from app.services import tool_service as service

router = APIRouter(prefix="/tools", tags=["Tools"])

MANAGE = [UserRole.OWNER, UserRole.MANAGER]
VIEW = [UserRole.OWNER, UserRole.MANAGER, UserRole.ACHARI]


@router.post("/", response_model=ToolOut, status_code=201)
def create_tool(
    body: ToolIn,
    _: User = Depends(check_role(MANAGE)),
    db: Session = Depends(get_db),
):
    """Add a tool to the register. Owner and manager."""
    return service.create(db, body)


@router.get("/", response_model=List[ToolOut])
def list_tools(
    status: Optional[str] = Query(
        None, pattern="^(available|issued|lost|broken|retired)$"
    ),
    location_id: Optional[UUID] = None,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(check_role(VIEW)),
    db: Session = Depends(get_db),
):
    return service.list_tools(db, status, location_id, limit, offset)


@router.get("/mine", response_model=List[IssueOut])
def my_tools(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """The tools you have been handed and not yet returned. Any signed-in staff."""
    return service.held_by(db, current_user.id)


@router.get("/{tool_id}", response_model=ToolOut)
def get_tool(
    tool_id: UUID,
    _: User = Depends(check_role(VIEW)),
    db: Session = Depends(get_db),
):
    return service.get(db, tool_id)


@router.put("/{tool_id}", response_model=ToolOut)
def update_tool(
    tool_id: UUID,
    body: ToolUpdate,
    _: User = Depends(check_role(MANAGE)),
    db: Session = Depends(get_db),
):
    """Change a tool's details; retire it, or make a found or repaired one available."""
    return service.update(db, tool_id, body)


@router.post("/{tool_id}/issue", response_model=IssueOut, status_code=201)
def issue_tool(
    tool_id: UUID,
    body: IssueIn,
    current_user: User = Depends(check_role(MANAGE)),
    db: Session = Depends(get_db),
):
    """Hand the tool to a person. 409 if it is already out or not available."""
    return service.issue(db, tool_id, body, current_user.id)


@router.post("/{tool_id}/return", response_model=IssueOut)
def return_tool(
    tool_id: UUID,
    body: ReturnIn,
    _: User = Depends(check_role(MANAGE)),
    db: Session = Depends(get_db),
):
    """Take the tool back: fine, damaged (becomes broken) or lost."""
    return service.give_back(db, tool_id, body)


@router.get("/{tool_id}/history", response_model=List[IssueOut])
def tool_history(
    tool_id: UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: User = Depends(check_role(MANAGE)),
    db: Session = Depends(get_db),
):
    """Everyone who has had this tool, newest first."""
    return service.history(db, tool_id, limit, offset)
