from typing import List

from fastapi import HTTPException
from passlib import exc
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.models.enums import UserRole
from app.models.user import User
from app.schemas.user import UserCreate, UserUpdate

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Checked against when the username does not exist, so the answer takes as long.
DUMMY_HASH = pwd_context.hash("not-a-real-password")


def hash_password(password: str) -> str:
    """
    Hash a plain-text password using the default crypt algorithm.

    Args:
        password (str): The plain-text password to hash.

    Returns:
        str: The hashed password string.
    """
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plain-text password against a stored hash.

    Args:
        plain_password (str): The raw password to check.
        hashed_password (str): The hashed password to compare against.

    Returns:
        bool: True if password matches, False otherwise.
    """
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except (ValueError, TypeError, exc.UnknownHashError):
        # a damaged or unknown stored hash is a failed sign-in, never a 500
        return False


def register_user(db: Session, user: UserCreate) -> User:
    """
    Register a new user in the system.

    Checks for existing username, hashes the password, and persists the user record.

    Args:
        db (Session): Database session.
        user (UserCreate): Payload containing user details and plain-text password.

    Returns:
        User: The newly created user object.

    Raises:
        HTTPException: If the username is already taken.
    """
    existing_user = db.query(User).filter(User.username == user.username).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Username already exists")
    if db.query(User).filter(User.email == user.email).first():
        raise HTTPException(status_code=400, detail="Email already in use")

    hashed_pw = hash_password(user.password)

    new_user = User(
        username=user.username,
        email=user.email,
        password_hash=hashed_pw,
        full_name=user.full_name,
        phone=user.phone,
        role=user.role,
        status=user.status,
        is_active=user.is_active,
        base_daily_wage=user.base_daily_wage,
        overtime_rate_per_hour=user.overtime_rate_per_hour,
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


def bootstrap_owner(db: Session, username: str, email: str, password: str) -> User:
    """
    Create the first owner account. Refuses if an owner already exists.

    Registration needs an owner token, so this is the only way in on a fresh
    database. Called from `python -m app.cli create-owner`.

    Raises:
        HTTPException: 400 if an owner already exists (or the username is taken).
    """
    if db.query(User).filter(User.role == UserRole.OWNER).first():
        raise HTTPException(status_code=400, detail="An owner already exists")
    return register_user(
        db,
        UserCreate(
            username=username, email=email, password=password, role=UserRole.OWNER
        ),
    )


def list_users(db: Session) -> List[User]:
    """
    Retrieve a list of all registered users.

    Args:
        db (Session): Database session.

    Returns:
        List[User]: List of User model instances.
    """
    return db.query(User).all()


def _other_active_owner(db: Session, user_id) -> bool:
    return (
        db.query(User)
        .filter(
            User.role == UserRole.OWNER, User.is_active.is_(True), User.id != user_id
        )
        .first()
        is not None
    )


def update_user(db: Session, actor: User, user_id, data: UserUpdate) -> User:
    """
    Edit a staff account (owner only, enforced by the route).

    Guards: you cannot change your own role or switch yourself off, and the
    last active owner can never be demoted or deactivated (nobody could log in
    to fix it).
    """
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    changes = data.model_dump(exclude_unset=True)

    if user.id == actor.id and (
        changes.get("role", user.role) != user.role or changes.get("is_active") is False
    ):
        raise HTTPException(
            status_code=400,
            detail="You cannot change your own role or switch yourself off",
        )
    losing_owner = user.role == UserRole.OWNER and (
        changes.get("role", user.role) != UserRole.OWNER
        or changes.get("is_active") is False
    )
    if losing_owner and not _other_active_owner(db, user.id):
        raise HTTPException(
            status_code=400, detail="There must be at least one active owner"
        )
    if "email" in changes and changes["email"] != user.email:
        if db.query(User).filter(User.email == changes["email"]).first():
            raise HTTPException(status_code=400, detail="Email already in use")
    for key, value in changes.items():
        setattr(user, key, value)
    if "is_active" in changes:
        user.status = "active" if user.is_active else "inactive"
    db.commit()
    db.refresh(user)
    return user


def set_password(db: Session, user: User, new_password: str) -> None:
    user.password_hash = hash_password(new_password)
    db.commit()
