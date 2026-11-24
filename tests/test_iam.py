import uuid

import pytest

from app.db.deps import get_current_user
from app.iam import defaults, evaluator
from app.iam.actions import ACTIONS
from app.main import app
from app.models.enums import UserRole
from app.models.user import User

# What finance/api.py allowed before the move to permissions.
LEGACY_FINANCE = {
    "finance:ledger:read": {"owner", "manager", "accountant"},
    "finance:balance_sheet:read": {"owner", "accountant"},
    "finance:ledger:write": {"owner"},
}


def stmts(role):
    return [(s, None) for s in defaults.SYSTEM_ROLES[role][1]]


# Old PAYMASTERS / attendance lists: payroll = owner+manager, attendance desk = +sales,
# edit and GPS marking = owner+manager, marking yourself = owner.
OM = {"owner", "manager"}
LEGACY_WORK = {
    **{
        a: OM
        for a in (
            "payroll:summary:read",
            "payroll:entry:write",
            "payroll:advance:write",
            "payroll:payment:create",
            "payroll:records:read_all",
            "attendance:record:edit",
            "attendance:presence:mark",
        )
    },
    "attendance:record:write": OM | {"sales"},
    "attendance:record:read_all": OM | {"sales"},
    "attendance:presence:mark_self": {"owner"},
    # sync: pulling your own attendance was open to every role except the accountant
    "attendance:record:read_own": {
        "owner",
        "manager",
        "sales",
        "achari",
        "worker",
        "artisan",
    },
}


# Old lists: sales desk = owner+manager+sales; bosses-only = owner+manager; owner-only = owner.
OMS = OM | {"sales"}
LEGACY_SALES = {
    **{
        a: OMS
        for a in (
            "sales:sale:create",
            "sales:sale:read",
            "sales:order:create",
            "sales:order:read",
            "sales:order:payment_add",
            "sales:order:invoice",
            "sales:invoice:read",
            "sales:credit_note:read",
            "sales:payment:add",
            "sales:customer:read",
            "sales:customer:write",
            "purchases:purchase:read",
        )
    },
    **{
        a: OM
        for a in (
            "sales:sale:edit",
            "sales:sale:return",
            "sales:payment:void",
            "sales:order:update",
            "sales:order:delete",
            "sales:customer:delete",
            "purchases:purchase:create",
            "purchases:purchase:confirm",
        )
    },
    "sales:sale:delete": {"owner"},
    "sales:order:payment_edit": {"owner"},
    "sales:order:payment_void": {"owner"},
}


@pytest.mark.parametrize("action", LEGACY_SALES)
@pytest.mark.parametrize("role", [r.value for r in UserRole])
def test_sales_and_purchases_parity_with_old_role_lists(role, action):
    assert evaluator.decide(stmts(role), action) == (role in LEGACY_SALES[action])


# Production and inventory. OMA = owner+manager+achari; items are readable by all but the accountant.
OMA = OM | {"achari"}
LEGACY_FLOOR = {
    **{
        a: OM
        for a in (
            "production:job:create",
            "production:job:assign",
            "production:job:run",
            "items:cost:read",
            "items:item:write",
            "items:stock:adjust",
            "inventory:raw_material:read",
            "inventory:raw_material:write",
            "inventory:raw_material:adjust",
            "inventory:stock_location:transfer",
            "inventory:master:read",
            "inventory:master:write",
            "machines:machine:read",
            "machines:machine:write",
            "machines:tool:manage",
        )
    },
    **{a: OMA for a in ("production:job:read", "bom:entry:read", "machines:tool:view")},
    "bom:entry:write": {"owner", "achari"},
    "items:item:read": {"owner", "manager", "sales", "achari", "worker", "artisan"},
    "inventory:stock_location:view": OMS,
    "inventory:raw_material:delete": {"owner"},
    "inventory:supplier:delete": {"owner"},
    "plants:plant_unit:write": {"owner"},
}


@pytest.mark.parametrize("action", LEGACY_FLOOR)
@pytest.mark.parametrize("role", [r.value for r in UserRole])
def test_production_and_inventory_parity_with_old_role_lists(role, action):
    assert evaluator.decide(stmts(role), action) == (role in LEGACY_FLOOR[action])


# 3D Lab, restoration, scans, expenses, assets. LAB_VIEW = every role but sales/accountant.
LEGACY_LAB = {
    "lab:part:read": {"owner", "manager", "achari", "worker", "artisan"},
    "lab:part:edit": OM,
    "restoration:project:access": {"owner", "artisan"},
    "restoration:cost:read": {"owner"},
    "scans:job:work": OM | {"artisan"},
    "scans:job:process": OM,
    "expenses:expense:write": OM,
    "expenses:expense:void": {"owner"},
    "assets:file:read": OM | {"artisan"},
    "assets:file:read_all": OM,
    "assets:file:write": OM,
}


@pytest.mark.parametrize("action", LEGACY_LAB)
@pytest.mark.parametrize("role", [r.value for r in UserRole])
def test_lab_restoration_scans_parity_with_old_role_lists(role, action):
    assert evaluator.decide(stmts(role), action) == (role in LEGACY_LAB[action])


# Letters, budgets, reports, dashboard.
LEGACY_DOCS = {
    "letters:quotation:issue": OMS,
    "letters:order_confirmation:issue": OMS,
    "letters:purchase_receipt:issue": OM,
    "letters:completion_certificate:issue": {"owner"},
    "letters:profile:write": {"owner"},
    "letters:letter:void": {"owner"},
    "letters:letter:delete": {"owner"},
    "budgets:budget:read": OM,
    "budgets:budget:write": {"owner"},
    "reports:report:read": OM,
    "reports:dashboard:read": OM,
}


@pytest.mark.parametrize("action", LEGACY_DOCS)
@pytest.mark.parametrize("role", [r.value for r in UserRole])
def test_letters_budgets_reports_parity_with_old_role_lists(role, action):
    assert evaluator.decide(stmts(role), action) == (role in LEGACY_DOCS[action])


def test_letter_kinds_match_the_registered_issue_actions():
    from app.models.letter import KINDS

    assert {f"letters:{k}:issue" for k in KINDS} <= set(ACTIONS)


# People, comments, site.
LEGACY_PEOPLE = {
    "people:user:read": OMS,
    "people:user:read_pay": OM,
    "people:user:create": {"owner"},
    "people:user:update": {"owner"},
    "people:user:reset_password": {"owner"},
    "people:user:invite": {"owner"},
    "comments:comment:review": OM,
    "site:content:manage": OM,
    "site:stats:read": OM,
}


@pytest.mark.parametrize("action", LEGACY_PEOPLE)
@pytest.mark.parametrize("role", [r.value for r in UserRole])
def test_people_comments_site_parity_with_old_role_lists(role, action):
    assert evaluator.decide(stmts(role), action) == (role in LEGACY_PEOPLE[action])


@pytest.mark.parametrize("action", LEGACY_WORK)
@pytest.mark.parametrize("role", [r.value for r in UserRole])
def test_payroll_and_attendance_parity_with_old_role_lists(role, action):
    assert evaluator.decide(stmts(role), action) == (role in LEGACY_WORK[action])


@pytest.mark.parametrize("action", LEGACY_FINANCE)
@pytest.mark.parametrize("role", [r.value for r in UserRole])
def test_finance_parity_with_old_role_lists(role, action):
    assert evaluator.decide(stmts(role), action) == (role in LEGACY_FINANCE[action])


def test_every_system_role_document_is_valid():
    for _, (_, s) in defaults.SYSTEM_ROLES.items():
        evaluator.validate_document(defaults.document(s))
    assert set(defaults.SYSTEM_ROLES) == {r.value for r in UserRole}


def test_deny_beats_allow_and_default_is_deny():
    s = [
        ({"effect": "Allow", "actions": ["finance:*"], "resources": ["*"]}, None),
        (
            {"effect": "Deny", "actions": ["finance:ledger:write"], "resources": ["*"]},
            None,
        ),
    ]
    assert evaluator.decide(s, "finance:ledger:read")
    assert not evaluator.decide(s, "finance:ledger:write")
    assert not evaluator.decide(s, "iam:role:read")


def test_plant_scoped_grant_only_applies_at_that_plant():
    s = [({"effect": "Allow", "actions": ["finance:ledger:read"]}, "p1")]
    assert evaluator.decide(s, "finance:ledger:read", ctx={"plant_unit_id": "p1"})
    assert not evaluator.decide(s, "finance:ledger:read", ctx={"plant_unit_id": "p2"})
    assert not evaluator.decide(s, "finance:ledger:read")


def test_self_condition():
    s = [
        (
            {
                "effect": "Allow",
                "actions": ["finance:ledger:read"],
                "condition": {"self": True},
            },
            None,
        )
    ]
    assert evaluator.decide(s, "finance:ledger:read", ctx={"user_id": 1, "owner_id": 1})
    assert not evaluator.decide(
        s, "finance:ledger:read", ctx={"user_id": 1, "owner_id": 2}
    )


def test_validation_rejects_junk():
    for doc in (
        {"version": 1, "statements": [{"effect": "Maybe", "actions": ["*"]}]},
        {"version": 1, "statements": [{"effect": "Allow", "actions": ["nope:x:y"]}]},
        {
            "version": 1,
            "statements": [
                {"effect": "Allow", "actions": ["*"], "condition": {"x": 1}}
            ],
        },
        {"version": 2, "statements": []},
    ):
        with pytest.raises(ValueError):
            evaluator.validate_document(doc)


def test_cannot_grant_what_you_do_not_hold():
    doc = defaults.document(
        [{"effect": "Allow", "actions": ["finance:*"], "resources": ["*"]}]
    )
    assert evaluator.can_grant(stmts("owner"), doc) == []
    assert "finance:ledger:write" in evaluator.can_grant(stmts("manager"), doc)


# ---- API (needs the test database, like the rest of the suite) ----


@pytest.fixture(autouse=True)
def seeded(db):
    from app.iam.service import ensure_system_roles

    ensure_system_roles(db)


def make_user(db, role):
    u = User(
        username=f"{role}_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        role=role,
        is_active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def act_as(user):
    app.dependency_overrides[get_current_user] = lambda: user


def test_finance_endpoint_follows_role(client, db):
    act_as(make_user(db, "accountant"))
    assert client.get("/finance/accounts").status_code == 200
    act_as(make_user(db, "worker"))
    assert client.get("/finance/accounts").status_code == 403


def test_manager_makes_a_role_within_own_rights_only(client, db):
    act_as(make_user(db, "manager"))
    ok = client.post("/iam/policies", json={
        "name": "read-books",
        "document": defaults.document(
            [{"effect": "Allow", "actions": ["finance:ledger:read"], "resources": ["*"]}]
        ),
    })  # fmt: skip
    assert ok.status_code == 201, ok.text
    too_much = client.post("/iam/policies", json={
        "name": "write-books",
        "document": defaults.document(
            [{"effect": "Allow", "actions": ["finance:ledger:write"], "resources": ["*"]}]
        ),
    })  # fmt: skip
    assert too_much.status_code == 403
    role = client.post(
        "/iam/roles", json={"label": "Book Reader", "policy_ids": [ok.json()["id"]]}
    )
    assert role.status_code == 201 and role.json()["key"] == "book_reader"


def test_extra_role_grants_and_session_ends(client, db):
    boss = make_user(db, "owner")
    person = make_user(db, "worker")
    act_as(boss)
    assert client.get("/iam/me").json()["allowed"] == list(ACTIONS)
    r = client.put(f"/iam/users/{person.id}/roles", json=[{"role_key": "accountant"}])
    assert r.status_code == 200, r.text
    db.refresh(person)
    assert person.token_version == 1
    act_as(person)
    assert client.get("/finance/accounts").status_code == 200
    assert "finance:ledger:write" not in client.get("/iam/me").json()["allowed"]


def test_owner_role_is_locked_and_simulator_works(client, db):
    boss = make_user(db, "owner")
    act_as(boss)
    roles = {r["key"]: r for r in client.get("/iam/roles").json()}
    assert client.delete(f"/iam/roles/{roles['owner']['id']}").status_code == 403
    assert (
        client.put(
            f"/iam/roles/{roles['owner']['id']}", json={"label": "x", "policy_ids": []}
        ).status_code
        == 403
    )
    acct = make_user(db, "accountant")
    sim = client.post(
        "/iam/simulate",
        json={"user_id": str(acct.id), "action": "finance:ledger:write"},
    )
    assert sim.json() == {"allowed": False}


def test_payroll_endpoints_follow_role_and_workers_see_only_their_own(client, db):
    act_as(make_user(db, "sales"))
    assert client.get("/payroll/summary").status_code == 403
    assert client.get("/attendance/all").status_code == 200
    act_as(make_user(db, "manager"))
    assert client.get("/payroll/summary").status_code == 200
    me = make_user(db, "worker")
    act_as(me)
    assert client.get("/payroll/summary").status_code == 403
    assert client.get("/attendance/all").status_code == 403
    assert client.get(f"/payroll/payments?user_id={uuid.uuid4()}").status_code == 200


def test_sales_and_purchases_endpoints_follow_role(client, db):
    act_as(make_user(db, "sales"))
    assert client.get("/sales/").status_code == 200
    assert client.get("/orders/").status_code == 200
    assert client.get("/purchases/").status_code == 200  # sales could always list
    assert client.post("/purchases/", json={}).status_code == 403  # but not create
    assert client.delete(f"/sales/{uuid.uuid4()}").status_code == 403
    act_as(make_user(db, "worker"))
    assert client.get("/sales/").status_code == 403
    assert client.get("/customers/").status_code == 403
    assert client.get("/invoices/").status_code == 403


def test_every_registered_action_is_covered_by_a_parity_table():
    tables = {
        **LEGACY_FINANCE,
        **LEGACY_WORK,
        **LEGACY_SALES,
        **LEGACY_FLOOR,
        **LEGACY_LAB,
        **LEGACY_DOCS,
        **LEGACY_PEOPLE,
    }
    skip = {a for a in ACTIONS if a.startswith("iam:")}
    assert set(ACTIONS) - skip == set(tables)


def test_items_hide_cost_without_the_cost_permission(client, db):
    from decimal import Decimal

    from app.models.item import Item

    item = Item(
        name="Chair",
        sku=f"CH-{uuid.uuid4().hex[:6]}",
        selling_price=Decimal("10"),
        purchase_price=Decimal("4"),
        production_cost=Decimal("3"),
        current_stock=1,
    )
    db.add(item)
    db.commit()
    act_as(make_user(db, "worker"))
    row = client.get(f"/items/{item.id}").json()
    assert Decimal(str(row["purchase_price"])) == 0
    act_as(make_user(db, "manager"))
    assert Decimal(str(client.get(f"/items/{item.id}").json()["purchase_price"])) == 4
    act_as(make_user(db, "accountant"))
    assert client.get(f"/items/{item.id}").status_code == 403
    db.delete(item)  # the shared test database keeps rows between tests
    db.commit()


def test_a_custom_role_is_honoured_by_offline_sync(client, db):
    from decimal import Decimal

    from app.models.item import Item

    item = Item(
        sku=f"SY-{uuid.uuid4().hex[:6]}",
        name="Sync chair",
        selling_price=Decimal("10"),
        purchase_price=Decimal("4"),
        production_cost=Decimal("3"),
        current_stock=1,
    )
    db.add(item)
    db.commit()
    since = "2000-01-01T00:00:00"
    clerk = make_user(db, "accountant")  # an accountant gets no items through sync
    act_as(clerk)
    assert client.get("/sync/pull", params={"last_sync": since}).json()["items"] == []
    act_as(make_user(db, "owner"))
    doc = defaults.document(
        [{"effect": "Allow", "actions": ["items:item:read"], "resources": ["*"]}]
    )
    pol = client.post("/iam/policies", json={"name": "see-items", "document": doc})
    role = client.post(
        "/iam/roles", json={"label": "Item Viewer", "policy_ids": [pol.json()["id"]]}
    )
    assert role.status_code == 201, role.text
    r = client.put(f"/iam/users/{clerk.id}/roles", json=[{"role_key": "item_viewer"}])
    assert r.status_code == 200, r.text
    act_as(clerk)
    rows = client.get("/sync/pull", params={"last_sync": since}).json()["items"]
    mine = [i for i in rows if i["sku"] == item.sku]
    assert mine and Decimal(str(mine[0]["purchase_price"])) == 0  # cost still hidden
    db.delete(item)  # the shared test database keeps rows between tests
    db.commit()


def test_a_custom_role_can_be_given_as_the_main_role_but_not_above_your_own(client, db):
    boss = make_user(db, "owner")
    act_as(boss)
    doc = defaults.document(
        [{"effect": "Allow", "actions": ["finance:ledger:read"], "resources": ["*"]}]
    )
    pol = client.post(
        "/iam/policies", json={"name": "books-only", "document": doc}
    ).json()
    r = client.post(
        "/iam/roles", json={"label": "Bookkeeper", "policy_ids": [pol["id"]]}
    )
    assert r.status_code == 201, r.text
    person = make_user(db, "worker")
    ok = client.patch(f"/auth/users/{person.id}", json={"role": "bookkeeper"})
    assert ok.status_code == 200, ok.text
    assert (
        client.patch(f"/auth/users/{person.id}", json={"role": "nonsense"}).status_code
        == 422
    )
    # someone who may update people but holds less than the owner cannot make an owner
    mgr_doc = defaults.document(
        [{"effect": "Allow", "actions": ["people:user:update"], "resources": ["*"]}]
    )
    p2 = client.post("/iam/policies", json={"name": "hr", "document": mgr_doc}).json()
    client.post("/iam/roles", json={"label": "HR Clerk", "policy_ids": [p2["id"]]})
    clerk = make_user(db, "hr_clerk")
    act_as(clerk)
    assert (
        client.patch(f"/auth/users/{person.id}", json={"role": "owner"}).status_code
        == 403
    )
    assert (
        client.patch(f"/auth/users/{boss.id}", json={"full_name": "x"}).status_code
        == 403
    )
