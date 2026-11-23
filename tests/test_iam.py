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
