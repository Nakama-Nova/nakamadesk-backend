"""Every permission the system knows. A converted endpoint must use one of these.

Format  service:resource:verb   e.g. finance:ledger:write. Add a line when you convert
a module; policies may use * in any part (finance:*:read, *).
"""

ACTIONS = {
    "finance:ledger:read": "Read the chart of accounts, journals, ledgers and reports",
    "finance:balance_sheet:read": "Read the balance sheet",
    "finance:ledger:write": "Post, edit, reverse journals and close periods",
    "iam:role:read": "See roles, policies and who holds what",
    "iam:role:write": "Create and change roles and policies (only up to your own rights)",
    "iam:user_role:assign": "Give people roles (only up to your own rights)",
}
