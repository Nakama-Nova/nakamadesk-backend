"""
Day 2 Integration Tests — Inventory Movement Engine

Tests the full E2E workflows for:
  Test 1: Purchase → confirm → raw material stock increases
  Test 2: Create job → start → raw material stock decreases
  Test 3: Complete job → finished goods (item stock) increases
  Test 4: Insufficient stock → job start fails with 400

Uses the same auth_client / db / TestingSessionLocal fixture pattern
from conftest.py. All assertions verify DB state directly via the `db`
fixture, not just API response fields.
"""

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.inventory_movement import InventoryMovement
from app.models.purchase import Purchase
from app.models.purchase_item import PurchaseItem
from app.models.raw_material import RawMaterial

# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _create_raw_material(
    auth_client: TestClient, name: str, stock: float = 0.0
) -> dict:
    """Create a raw material with optional initial stock."""
    r = auth_client.post(
        "/raw-materials/",
        json={
            "name": f"{name}-{uuid.uuid4().hex[:6]}",
            "unit": "CFT",
            "current_price": 850.0,
            "stock": stock,
        },
    )
    assert r.status_code in (200, 201), r.json()
    return r.json()


def _create_item(auth_client: TestClient, stock: int = 0) -> dict:
    """Create a catalogue item."""
    r = auth_client.post(
        "/items/",
        json={
            "sku": f"SKU-{uuid.uuid4().hex[:8]}",
            "name": f"Teak Chair {uuid.uuid4().hex[:4]}",
            "selling_price": 12000.0,
            "current_stock": stock,
            "gst_percent": 12.0,
            "hsn_code": "9403",
        },
    )
    assert r.status_code in (200, 201), r.json()
    return r.json()


def _create_bom_entry(
    auth_client: TestClient, item_id: str, material_id: str, qty: float
) -> dict:
    """Add a BOM entry linking item → raw material."""
    r = auth_client.post(
        "/bom/",
        json={
            "item_id": item_id,
            "material_id": material_id,
            "required_qty": qty,
            "wastage_pct": 0.0,
        },
    )
    assert r.status_code in (200, 201), r.json()
    return r.json()


def _create_purchase_with_raw_material(
    db: Session,
    material_id: str,
    quantity: float,
    unit_price: float = 850.0,
) -> str:
    """Directly insert a purchase + purchase_item into DB (bypasses missing POST /purchases endpoint)."""
    purchase = Purchase(
        invoice_number=f"INV-{uuid.uuid4().hex[:8]}",
        total_amount=quantity * unit_price,
        tax_total=0.0,
        status="pending",
        purchase_type="tax_invoice",
        is_itc_eligible=True,
    )
    db.add(purchase)
    db.flush()

    item = PurchaseItem(
        purchase_id=purchase.id,
        raw_material_id=uuid.UUID(material_id),
        item_id=None,
        quantity=Decimal(str(quantity)),
        unit_price=Decimal(str(unit_price)),
        gst_percent=Decimal("12.0"),
        line_total=Decimal(str(quantity * unit_price)),
    )
    db.add(item)
    db.commit()
    return str(purchase.id)


def _create_production_job(auth_client: TestClient, item_id: str, qty: int = 1) -> str:
    """Create a production job for the given item."""
    r = auth_client.post(
        "/production/jobs",
        json={
            "item_id": item_id,
            "target_quantity": qty,
            "custom_desc": None,
        },
    )
    assert r.status_code == 201, r.json()
    return r.json()["id"]


# ---------------------------------------------------------------------------
# Test 1: Purchase confirm increases raw material stock
# ---------------------------------------------------------------------------


def test_purchase_confirm_increases_stock(auth_client: TestClient, db: Session):
    """
    Confirming a purchase must:
      - Increment raw_material.stock by the purchased quantity.
      - Create an inventory_movement record with movement_type='raw_in'.
    """
    material = _create_raw_material(auth_client, "Teak", stock=10.0)
    material_id = material["id"]

    # Verify initial stock
    mat_db: RawMaterial = (
        db.query(RawMaterial).filter(RawMaterial.id == uuid.UUID(material_id)).first()
    )
    initial_stock = float(mat_db.stock)

    # Create purchase directly in DB (no POST /purchases endpoint yet)
    purchase_id = _create_purchase_with_raw_material(
        db, material_id, quantity=50.0, unit_price=850.0
    )

    # Confirm via API
    r = auth_client.patch(f"/purchases/{purchase_id}/confirm")
    assert r.status_code == 200, r.json()
    assert r.json()["status"] == "confirmed"

    # DB assertions
    db.expire_all()
    mat_db = (
        db.query(RawMaterial).filter(RawMaterial.id == uuid.UUID(material_id)).first()
    )
    assert float(mat_db.stock) == pytest.approx(initial_stock + 50.0)

    movement = (
        db.query(InventoryMovement)
        .filter(
            InventoryMovement.reference_id == uuid.UUID(purchase_id),
            InventoryMovement.movement_type == "raw_in",
        )
        .first()
    )
    assert movement is not None
    assert float(movement.quantity) == pytest.approx(50.0)
    assert movement.raw_material_id == uuid.UUID(material_id)


# ---------------------------------------------------------------------------
# Test 2: Start job decreases raw material stock
# ---------------------------------------------------------------------------


def test_start_job_decreases_raw_material_stock(auth_client: TestClient, db: Session):
    """
    Starting a production job must:
      - Deduct BOM-required quantity (including wastage) from raw_material.stock.
      - Create an inventory_movement record with movement_type='raw_consumed'.
      - Set job status to 'in_progress'.
      - Create a ProductionMaterialAllocation record.
    """
    # Setup: item with BOM, raw material with enough stock
    material = _create_raw_material(auth_client, "Neem", stock=100.0)
    item = _create_item(auth_client, stock=0)
    _create_bom_entry(auth_client, item["id"], material["id"], qty=10.0)

    # Create and start the job
    job_id = _create_production_job(auth_client, item["id"], qty=2)

    db.expire_all()
    mat_before = float(
        db.query(RawMaterial)
        .filter(RawMaterial.id == uuid.UUID(material["id"]))
        .first()
        .stock
    )

    r = auth_client.patch(f"/production/jobs/{job_id}/start")
    assert r.status_code == 200, r.json()
    assert r.json()["status"] == "in_progress"
    assert r.json()["started_at"] is not None

    # Stock must have been deducted: 10.0 * 2 units = 20.0 CFT
    db.expire_all()
    mat_after = float(
        db.query(RawMaterial)
        .filter(RawMaterial.id == uuid.UUID(material["id"]))
        .first()
        .stock
    )
    assert mat_after == pytest.approx(mat_before - 20.0)

    # Inventory movement must exist
    movement = (
        db.query(InventoryMovement)
        .filter(
            InventoryMovement.reference_id == uuid.UUID(job_id),
            InventoryMovement.movement_type == "raw_consumed",
        )
        .first()
    )
    assert movement is not None
    assert float(movement.quantity) == pytest.approx(-20.0)  # negative = consumed


# ---------------------------------------------------------------------------
# Test 3: Complete job increases finished goods stock
# ---------------------------------------------------------------------------


def test_complete_job_increases_finished_goods(auth_client: TestClient, db: Session):
    """
    Completing a production job must:
      - Increment item.current_stock by produced_quantity.
      - Create an inventory_movement record with movement_type='finished_in'.
      - Set job status to 'completed'.
    """
    material = _create_raw_material(auth_client, "Rosewood", stock=200.0)
    item = _create_item(auth_client, stock=3)
    _create_bom_entry(auth_client, item["id"], material["id"], qty=5.0)

    job_id = _create_production_job(auth_client, item["id"], qty=2)

    # Start the job first
    r_start = auth_client.patch(f"/production/jobs/{job_id}/start")
    assert r_start.status_code == 200, r_start.json()

    # Check item stock before completion
    db.expire_all()
    from app.models.item import Item

    item_before = db.query(Item).filter(Item.id == uuid.UUID(item["id"])).first()
    stock_before = item_before.current_stock

    # Complete the job
    r_complete = auth_client.patch(
        f"/production/jobs/{job_id}/complete",
        json={"produced_quantity": 2},
    )
    assert r_complete.status_code == 200, r_complete.json()
    assert r_complete.json()["status"] == "completed"
    assert float(r_complete.json()["produced_quantity"]) == 2.0
    assert r_complete.json()["completed_at"] is not None

    # Stock must increase
    db.expire_all()
    item_after = db.query(Item).filter(Item.id == uuid.UUID(item["id"])).first()
    assert item_after.current_stock == stock_before + 2

    # Inventory movement must exist
    movement = (
        db.query(InventoryMovement)
        .filter(
            InventoryMovement.reference_id == uuid.UUID(job_id),
            InventoryMovement.movement_type == "finished_in",
        )
        .first()
    )
    assert movement is not None
    assert float(movement.quantity) == pytest.approx(2.0)
    assert movement.item_id == uuid.UUID(item["id"])


# ---------------------------------------------------------------------------
# Test 4: Insufficient stock prevents job start
# ---------------------------------------------------------------------------


def test_start_job_fails_on_insufficient_stock(auth_client: TestClient, db: Session):
    """
    Attempting to start a job when raw material stock is insufficient must:
      - Return HTTP 400.
      - NOT change any stock (atomic rollback).
      - NOT transition job status from 'pending'.
    """
    # Give material only 5 units but BOM requires 10 per unit × 2 = 20
    material = _create_raw_material(auth_client, "Ebony", stock=5.0)
    item = _create_item(auth_client, stock=0)
    _create_bom_entry(auth_client, item["id"], material["id"], qty=10.0)

    job_id = _create_production_job(auth_client, item["id"], qty=2)

    # Attempt to start — must fail
    r = auth_client.patch(f"/production/jobs/{job_id}/start")
    assert r.status_code == 400
    assert "Insufficient stock" in r.json()["detail"]

    # Stock must be unchanged
    db.expire_all()
    mat = (
        db.query(RawMaterial)
        .filter(RawMaterial.id == uuid.UUID(material["id"]))
        .first()
    )
    assert float(mat.stock) == pytest.approx(5.0)

    # Job must still be 'pending'
    r_job = auth_client.get(f"/production/jobs/{job_id}")
    assert r_job.status_code == 200
    assert r_job.json()["status"] == "pending"

    # No inventory movement should have been created
    movements = (
        db.query(InventoryMovement)
        .filter(
            InventoryMovement.reference_id == uuid.UUID(job_id),
            InventoryMovement.movement_type == "raw_consumed",
        )
        .all()
    )
    assert len(movements) == 0
