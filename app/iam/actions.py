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
    "sales:sale:create": "Make a sale",
    "sales:sale:read": "See sales and their items",
    "sales:sale:edit": "Edit a sale",
    "sales:sale:delete": "Delete a sale",
    "sales:order:create": "Take an order",
    "sales:order:read": "See orders",
    "sales:order:update": "Change an order, its items and its status",
    "sales:order:payment_add": "Record a payment on an order",
    "sales:order:payment_edit": "Correct an order payment",
    "sales:order:payment_void": "Void an order payment",
    "sales:order:invoice": "Turn an order into an invoice",
    "sales:order:delete": "Delete an order",
    "sales:invoice:read": "See invoices and their PDFs",
    "sales:customer:read": "Search and view customers",
    "sales:customer:write": "Add and edit customers",
    "sales:customer:delete": "Delete a customer",
    "purchases:purchase:create": "Record a purchase",
    "purchases:purchase:read": "See purchases",
    "purchases:purchase:confirm": "Confirm a purchase (stock comes in)",
    "iam:role:read": "See roles, policies and who holds what",
    "iam:role:write": "Create and change roles and policies (only up to your own rights)",
    "iam:user_role:assign": "Give people roles (only up to your own rights)",
}
