"""Every permission the system knows. A converted endpoint must use one of these.

Format  service:resource:verb   e.g. finance:ledger:write. Add a line when you convert
a module; policies may use * in any part (finance:*:read, *).
"""

ACTIONS = {
    "finance:ledger:read": "Read the chart of accounts, journals, ledgers and reports",
    "finance:balance_sheet:read": "Read the balance sheet",
    "finance:ledger:write": "Post, edit, reverse journals and close periods",
    "payroll:summary:read": "See what every person is owed and owes back",
    "payroll:entry:write": "Add or remove piece, overtime and bonus entries",
    "payroll:advance:write": "Give advances and record repayments",
    "payroll:payment:create": "Pay wages and pay-outs",
    "payroll:records:read_all": "See anyone's payroll, advances and payments (otherwise only your own)",
    "attendance:record:write": "Check people in and out and record attendance for them",
    "attendance:record:read_all": "See everyone's attendance",
    "attendance:record:edit": "Correct an attendance record",
    "attendance:presence:mark": "Mark someone present from the phone with GPS",
    "attendance:presence:mark_self": "Mark your own presence (the owner only, by default)",
    "iam:role:read": "See roles, policies and who holds what",
    "iam:role:write": "Create and change roles and policies (only up to your own rights)",
    "iam:user_role:assign": "Give people roles (only up to your own rights)",
}
