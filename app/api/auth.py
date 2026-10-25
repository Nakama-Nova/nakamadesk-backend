import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.core.security import create_access_token
from app.db.deps import check_role, get_current_user, get_db
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.user import UserCreate, UserResponse
from app.services import auth_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/register", response_model=UserResponse)
def register(
    user: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(check_role([UserRole.OWNER])),
):
    """
    Create a new staff account.

    RBAC: OWNER only. The very first owner is created from the command line
    (`python -m app.cli create-owner`), since nobody can be logged in yet.

    Args:
        user (UserCreate): User registration data.
        db (Session): Database session.
        current_user (User): The authenticated owner.

    Returns:
        UserResponse: The newly created user.
    """
    return auth_service.register_user(db, user)


@router.post("/login")
def login(
    form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)
):
    """
    Authenticate a user and return an access token.

    Args:
        form_data (OAuth2PasswordRequestForm): Login credentials (username, password).
        db (Session): Database session.

    Returns:
        dict: Access token and user information.

    Raises:
        HTTPException: If credentials are invalid.
    """
    user = db.query(User).filter(User.username == form_data.username).first()

    if not user:
        logger.warning(f"Authentication failure: user '{form_data.username}' not found")
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not auth_service.verify_password(form_data.password, user.password_hash):
        logger.warning(
            f"Authentication failure: invalid password for user '{form_data.username}'"
        )
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Store user_id (UUID) in the token instead of just username
    token = create_access_token({"sub": str(user.id), "username": user.username})

    return {
        "access_token": token,
        "token_type": "bearer",
        "username": user.username,
        "role": user.role,
    }


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    """
    Get the profile of the currently authenticated user.

    Args:
        current_user (User): The authenticated user.

    Returns:
        User: The current user object.
    """
    return current_user


@router.get("/", response_model=List[UserResponse])
def list_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(
        check_role([UserRole.OWNER, UserRole.MANAGER, UserRole.SALES])
    ),
):
    """
    List all users in the system.

    RBAC: Restricted to OWNER, MANAGER, and SALES roles.

    Args:
        db (Session): Database session.
        current_user (User): Authenticated user performing the request.

    Returns:
        List[UserResponse]: A list of all registered users.
    """
    return auth_service.list_users(db)
