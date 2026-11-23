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

SALES_DESK = (
    "sales:sale:create",
    "sales:sale:read",
    "sales:order:create",
    "sales:order:read",
    "sales:order:payment_add",
    "sales:order:invoice",
    "sales:invoice:read",
    "sales:customer:read",
    "sales:customer:write",
    "purchases:purchase:read",
)
SALES_BOSS = SALES_DESK + (
    "sales:sale:edit",
    "sales:order:update",
    "sales:order:delete",
    "sales:customer:delete",
    "purchases:purchase:create",
    "purchases:purchase:confirm",
)

FLOOR_VIEW = ("items:item:read",)
PRODUCTION_BOSS = (
    "production:job:create",
    "production:job:read",
    "production:job:assign",
    "production:job:run",
    "bom:entry:read",
    "items:item:read",
    "items:cost:read",
    "items:item:write",
    "items:stock:adjust",
    "inventory:raw_material:read",
    "inventory:raw_material:write",
    "inventory:raw_material:adjust",
    "inventory:stock_location:view",
    "inventory:stock_location:transfer",
    "inventory:master:read",
    "inventory:master:write",
    "machines:machine:read",
    "machines:machine:write",
    "machines:tool:view",
    "machines:tool:manage",
)
ACHARI = (
    "production:job:read",
    "bom:entry:read",
    "bom:entry:write",
    "items:item:read",
    "machines:tool:view",
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
            *SALES_BOSS,
            *PRODUCTION_BOSS,
        ),
    ),
    "accountant": (
        "Accountant",
        _allow("finance:ledger:read", "finance:balance_sheet:read"),
    ),
    "sales": (
        "Sales",
        _allow(
            *ATTENDANCE_DESK, *SALES_DESK, *FLOOR_VIEW, "inventory:stock_location:view"
        ),
    ),
    "achari": ("Achari", _allow(*ACHARI)),
    "worker": ("Worker", _allow(*FLOOR_VIEW)),
    "artisan": ("Artisan", _allow(*FLOOR_VIEW)),
}


def document(statements):
    return {"version": 1, "statements": statements}
