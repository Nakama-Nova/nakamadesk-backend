"""Shared fixtures for the 3D Lab tests (loaded as a pytest plugin from conftest)."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.models.user import User

CHAIR_SIZE = {"l": 450, "w": 450, "h": 900}
PRESETS = {
    "S": {"l": 400, "w": 400, "h": 800},
    "M": {"l": 450, "w": 450, "h": 900},
    "L": {"l": 500, "w": 500, "h": 1000},
}


def chair_parts():
    """A dining chair. Overall L (left-right) x W (front-back) x H (up), mm."""
    common = {"wood_type": "Teak", "t_expr": "40", "w_expr": "40", "axis": "y"}
    return [
        {"name": "Seat", "wood_type": "Teak", "l_expr": "L", "w_expr": "W", "t_expr": "30",
         "x_expr": "0", "y_expr": "H/2", "z_expr": "0", "assembly_step": 2},
        {"name": "Front leg", "l_expr": "H/2", "x_expr": "0", **common},
        {"name": "Front leg", "l_expr": "H/2", "x_expr": "L-40", **common},
        {"name": "Back leg", "l_expr": "H", "x_expr": "0", "z_expr": "W-40", **common},
        {"name": "Back leg", "l_expr": "H", "x_expr": "L-40", "z_expr": "W-40", **common},
        {"name": "Back rail", "wood_type": "Teak", "l_expr": "L-80", "w_expr": "60",
         "t_expr": "20", "x_expr": "40", "y_expr": "H-150", "z_expr": "W-30", "assembly_step": 3,
         "joinery_note": "Mortise and tenon into both back legs"},
    ]  # fmt: skip


@pytest.fixture
def people(db):
    """One real user per role, with a real login token (no dependency overrides)."""
    out = {}
    for role in ("owner", "manager", "sales", "achari", "worker"):
        u = User(
            username=f"{role}_{uuid.uuid4().hex[:6]}",
            email=f"{role}_{uuid.uuid4().hex[:6]}@example.com",
            password_hash="x",
            role=role,
            is_active=True,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        out[role] = {
            "id": str(u.id),
            "h": {"Authorization": f"Bearer {create_access_token({'sub': str(u.id)})}"},
        }
    return out


@pytest.fixture
def chair(client: TestClient, people):
    """A furniture item with presets and parts, set up by the owner."""
    h = people["owner"]["h"]
    item = client.post(
        "/items/",
        headers=h,
        json={"sku": f"CH-{uuid.uuid4().hex[:6]}", "name": "Dining Chair",
              "dimensions_mm": CHAIR_SIZE, "is_furniture": True},
    ).json()  # fmt: skip
    assert (
        client.put(
            f"/lab/items/{item['id']}/presets", headers=h, json=PRESETS
        ).status_code
        == 200
    )
    r = client.put(
        f"/lab/items/{item['id']}/parts", headers=h, json={"parts": chair_parts()}
    )
    assert r.status_code == 200, r.text
    return item
