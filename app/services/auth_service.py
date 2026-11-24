import secrets
from datetime import date
from typing import List

from fastapi import HTTPException
from passlib import exc
from passlib.context import CryptContext
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.iam import service as iam
from app.models.enums import UserRole
from app.models.hr import UserWage
from app.models.user import User
from app.schemas.user import UserCreate, UserUpdate
from app.services import audit, team_service, wage_service

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Checked against when the username does not exist, so the answer takes as long.
DUMMY_HASH = pwd_context.hash("not-a-real-password")


def find_by_username(db: Session, username: str) -> User | None:
    """The account for a typed username. An exact match wins; otherwise the name matches whatever
    its capitals (a phone capitalises the first letter, a desk does not), unless two accounts
    differ only by case, in which case nobody is guessed."""
    exact = db.query(User).filter(User.username == username).first()
    if exact:
        return exact
    loose = (
        db.query(User)
        .filter(func.lower(User.username) == username.strip().lower())
        .limit(2)
        .all()
    )
    return loose[0] if len(loose) == 1 else None


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


def register_user(db: Session, user: UserCreate, actor_id=None) -> User:
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
    existing_user = (
        db.query(User)
        .filter(func.lower(User.username) == user.username.strip().lower())
        .first()
    )  # "Swapnac" and "swapnac" are one name: people type either
    if existing_user:
        raise HTTPException(status_code=400, detail="Username already exists")
    if db.query(User).filter(User.email == user.email).first():
        raise HTTPException(status_code=400, detail="Email already in use")

    team_service.check_manager(db, None, user.role, user.reports_to_id)

    # No password given: a random one nobody knows, until the person sets their own
    # from the emailed code (staff_code_service).
    hashed_pw = hash_password(user.password or secrets.token_urlsafe(32))

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
        reports_to_id=user.reports_to_id,
        employment=user.employment or "full_time",
        joining_date=user.joining_date,
        last_working_date=user.last_working_date,
        address=user.address,
        emergency_contact_name=user.emergency_contact_name,
        emergency_contact_phone=user.emergency_contact_phone,
        pay_upi=user.pay_upi,
        id_proof_type=user.id_proof_type,
        id_proof_last4=user.id_proof_last4,
    )
    pay_type = user.pay_type or "daily"
    if user.pay_amount is not None:
        amount = user.pay_amount
    elif user.base_daily_wage is not None:
        amount = wage_service.amount_from_daily(
            pay_type, user.base_daily_wage, date.today()
        )
    else:
        amount = None
    if amount is not None:
        wage_service.set_user_rate(new_user, pay_type, amount, date.today())
    else:
        new_user.pay_type = pay_type

    db.add(new_user)
    db.flush()
    if amount is not None:  # the first rate in the wage history
        db.add(
            UserWage(
                user_id=new_user.id,
                pay_type=pay_type,
                amount=wage_service.q(amount),
                effective_from=user.joining_date or date.today(),
                note="Starting rate",
                created_by=actor_id,
            )
        )
    audit.record(
        db,
        actor_id,
        "users",
        new_user.id,
        "INSERT",
        new={"username": new_user.username, "role": new_user.role},
    )
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
    # you may not edit someone who holds more than you do, nor give a role above your own
    iam.check_may_grant_role(db, actor, user.role, strict=False)
    if "role" in changes:
        iam.check_may_grant_role(db, actor, changes["role"])

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
    new_role = changes.get("role", user.role)
    if "reports_to_id" in changes:
        team_service.check_manager(db, user.id, new_role, changes["reports_to_id"])
    elif new_role == UserRole.OWNER and user.reports_to_id:
        changes["reports_to_id"] = None  # promoted to owner: now at the top
    for key in ("pay_type", "employment"):  # never null
        if key in changes and changes[key] is None:
            del changes[key]
    old = {k: getattr(user, k) for k in changes}
    old_pay_type = user.pay_type
    rate_changes = {
        k: changes.pop(k)
        for k in ("base_daily_wage", "pay_type", "pay_amount")
        if k in changes and changes[k] is not None
    }
    for k in ("base_daily_wage", "pay_type", "pay_amount"):
        changes.pop(k, None)
    for key, value in changes.items():
        setattr(user, key, value)
    wage_service.record_rate_edit(db, actor, user, rate_changes, old_pay_type)
    changes.update(rate_changes)
    if "is_active" in changes:
        user.status = "active" if user.is_active else "inactive"
    # a changed role or a switch-off ends this person's current sessions
    if changes.get("role", user.role) != old.get("role", user.role) or (
        "is_active" in changes and changes["is_active"] != old["is_active"]
    ):
        user.token_version = (user.token_version or 0) + 1
    audit.record(db, actor.id, "users", user.id, "UPDATE", old=old, new=changes)
    db.commit()
    db.refresh(user)
    return user


def set_password(
    db: Session, user: User, new_password: str, actor_id=None, action="PASSWORD_CHANGE"
) -> None:
    """New password; every session issued before it stops working."""
    user.password_hash = hash_password(new_password)
    user.token_version = (user.token_version or 0) + 1
    audit.record(db, actor_id, "users", user.id, action)
    db.commit()
