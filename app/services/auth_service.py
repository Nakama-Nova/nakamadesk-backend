from typing import List

from fastapi import HTTPException
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.models.user import User
from app.schemas.user import UserCreate

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


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
    return pwd_context.verify(plain_password, hashed_password)


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
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


def list_users(db: Session) -> List[User]:
    """
    Retrieve a list of all registered users.

    Args:
        db (Session): Database session.

    Returns:
        List[User]: List of User model instances.
    """
    return db.query(User).all()
