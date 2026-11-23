"""The built-in roles. They reproduce what the old UserRole lists allowed, module by module.

Seeded into the database by the migration; also the fallback if a row is missing.
Only converted modules appear here - add a role's new actions when a module moves over.
"""

ALLOW_ALL = [{"effect": "Allow", "actions": ["*"], "resources": ["*"]}]


def _allow(*actions):
    return [{"effect": "Allow", "actions": list(actions), "resources": ["*"]}]


SYSTEM_ROLES = {
    "owner": ("Owner", ALLOW_ALL),
    "manager": (
        "Manager",
        _allow(
            "finance:ledger:read",
            "iam:role:read",
            "iam:role:write",
            "iam:user_role:assign",
        ),
    ),
    "accountant": (
        "Accountant",
        _allow("finance:ledger:read", "finance:balance_sheet:read"),
    ),
    "sales": ("Sales", []),
    "achari": ("Achari", []),
    "worker": ("Worker", []),
    "artisan": ("Artisan", []),
}


def document(statements):
    return {"version": 1, "statements": statements}
