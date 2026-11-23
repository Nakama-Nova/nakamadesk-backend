"""The built-in roles. They reproduce what the old UserRole lists allowed, module by module.

Seeded into the database by the migration; also the fallback if a row is missing.
Only converted modules appear here - add a role's new actions when a module moves over.
"""

ALLOW_ALL = [{"effect": "Allow", "actions": ["*"], "resources": ["*"]}]


def _allow(*actions):
    return [{"effect": "Allow", "actions": list(actions), "resources": ["*"]}]


PAYMASTER = (
    "payroll:summary:read",
    "payroll:entry:write",
    "payroll:advance:write",
    "payroll:payment:create",
    "payroll:records:read_all",
)
ATTENDANCE_DESK = ("attendance:record:write", "attendance:record:read_all")
ATTENDANCE_BOSS = ATTENDANCE_DESK + (
    "attendance:record:edit",
    "attendance:presence:mark",
)

SYSTEM_ROLES = {
    "owner": ("Owner", ALLOW_ALL),
    "manager": (
        "Manager",
        _allow(
            "finance:ledger:read",
            "iam:role:read",
            "iam:role:write",
            "iam:user_role:assign",
            *PAYMASTER,
            *ATTENDANCE_BOSS,
        ),
    ),
    "accountant": (
        "Accountant",
        _allow("finance:ledger:read", "finance:balance_sheet:read"),
    ),
    "sales": ("Sales", _allow(*ATTENDANCE_DESK)),
    "achari": ("Achari", []),
    "worker": ("Worker", []),
    "artisan": ("Artisan", []),
}


def document(statements):
    return {"version": 1, "statements": statements}
