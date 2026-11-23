import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db.base import Base


def _id():
    return Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )


class IamPolicy(Base):
    """A JSON permission document: Allow/Deny statements over actions (see app/iam)."""

    __tablename__ = "iam_policies"
    id = _id()
    name = Column(String, unique=True, nullable=False)
    document = Column(JSONB, nullable=False)
    is_system = Column(Boolean, nullable=False, default=False, server_default="false")
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    updated_at = Column(
        DateTime, default=lambda: datetime.now(timezone.utc), onupdate=datetime.now
    )


class IamRole(Base):
    """A named bundle of policies. users.role holds the key of the person's main role."""

    __tablename__ = "iam_roles"
    id = _id()
    key = Column(String, unique=True, nullable=False, index=True)
    label = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    is_system = Column(Boolean, nullable=False, default=False, server_default="false")


class IamRolePolicy(Base):
    __tablename__ = "iam_role_policies"
    role_id = Column(
        UUID(as_uuid=True),
        ForeignKey("iam_roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    policy_id = Column(
        UUID(as_uuid=True),
        ForeignKey("iam_policies.id", ondelete="CASCADE"),
        primary_key=True,
    )


class IamUserRole(Base):
    """An extra role for a person; plant_unit_id limits it to one plant (NULL = everywhere)."""

    __tablename__ = "iam_user_roles"
    id = _id()
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    role_id = Column(
        UUID(as_uuid=True),
        ForeignKey("iam_roles.id", ondelete="CASCADE"),
        nullable=False,
    )
    plant_unit_id = Column(
        UUID(as_uuid=True),
        ForeignKey("plant_units.id", ondelete="CASCADE"),
        nullable=True,
    )
