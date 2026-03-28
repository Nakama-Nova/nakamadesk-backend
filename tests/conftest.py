import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql import text

# Force ENV to test before any other imports
os.environ["ENV"] = "test"

from app.core.config import settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.deps import get_current_user, get_db, get_uow  # noqa: E402
from app.main import app  # noqa: E402
from app.models.user import User  # noqa: E402
from app.repositories.sqlalchemy_repo import SQLAlchemyUnitOfWork  # noqa: E402

# Use the configured TEST_DATABASE_URL from settings
SQLALCHEMY_DATABASE_URL = settings.DATABASE_URL
engine = create_engine(SQLALCHEMY_DATABASE_URL)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    """Create a clean database for the test session."""
    # Re-create the schema to ensure we start fresh
    # Note: For Postgres, we can drop and create schema public
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    except Exception:
        Base.metadata.drop_all(bind=engine)

    Base.metadata.create_all(bind=engine)
    yield
    # No need to drop after session if we want to inspect or if we drop at start of next session


@pytest.fixture
def db():
    """Get a database session for a single test."""
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db):
    """Get a TestClient for testing endpoints."""

    def override_get_db():
        try:
            yield db
        finally:
            pass

    def override_get_uow():
        session = TestingSessionLocal()
        try:
            yield SQLAlchemyUnitOfWork(session)
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_uow] = override_get_uow
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def auth_client(client, db):
    """Get a TestClient with an authenticated 'owner' user."""
    # Create a test user with a unique username for each test
    unique_id = uuid.uuid4().hex[:8]
    test_user = User(
        username=f"admin_{unique_id}",
        email=f"admin_{unique_id}@example.com",
        password_hash="fakehash",
        role="owner",
        is_active=True,
    )
    db.add(test_user)
    db.commit()
    db.refresh(test_user)

    def override_get_current_user():
        return test_user

    app.dependency_overrides[get_current_user] = override_get_current_user
    yield client
    app.dependency_overrides.clear()


@pytest.fixture
def manager_client(client, db):
    """Get a TestClient with an authenticated 'manager' user."""
    unique_id = uuid.uuid4().hex[:8]
    test_user = User(
        username=f"manager_{unique_id}",
        email=f"manager_{unique_id}@example.com",
        password_hash="fakehash",
        role="manager",
        is_active=True,
    )
    db.add(test_user)
    db.commit()
    db.refresh(test_user)

    def override_get_current_user():
        return test_user

    app.dependency_overrides[get_current_user] = override_get_current_user
    yield client
    app.dependency_overrides.clear()


@pytest.fixture
def worker_client(client, db):
    """Get a TestClient with an authenticated 'worker' user."""
    unique_id = uuid.uuid4().hex[:8]
    test_user = User(
        username=f"worker_{unique_id}",
        email=f"worker_{unique_id}@example.com",
        password_hash="fakehash",
        role="worker",
        is_active=True,
    )
    db.add(test_user)
    db.commit()
    db.refresh(test_user)

    def override_get_current_user():
        return test_user

    app.dependency_overrides[get_current_user] = override_get_current_user
    yield client
    app.dependency_overrides.clear()
