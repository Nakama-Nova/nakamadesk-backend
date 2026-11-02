"""
The starting chart of accounts (docs/FINANCE_LEDGER_DESIGN.md, section 3), as proposed
and still waiting for the CA's approval. The migration seeds a frozen copy of this list;
ensure_chart() adds anything missing (used by tests and fresh development databases).
"""

# (code, name, type, normal side)
CHART = [
    ("1000", "Cash", "asset", "debit"),
    ("1010", "Bank and UPI", "asset", "debit"),
    ("1090", "Unmatched payments (clearing)", "asset", "debit"),
    ("1100", "Customers owe us", "asset", "debit"),
    ("1300", "Staff advances", "asset", "debit"),
    ("1400", "Machines (at cost)", "asset", "debit"),
    ("1410", "Machines: depreciation so far", "asset", "credit"),
    ("2000", "We owe suppliers", "liability", "credit"),
    ("2100", "Customer advances", "liability", "credit"),
    ("2200", "Wages owed", "liability", "credit"),
    ("2300", "GST payable", "liability", "credit"),
    ("3000", "Owner's capital and drawings", "equity", "credit"),
    ("4000", "Sales", "income", "credit"),
    ("5000", "Raw material bought", "expense", "debit"),
    ("5100", "Wages", "expense", "debit"),
    ("5200", "Repairs and maintenance", "expense", "debit"),
    ("5300", "Depreciation", "expense", "debit"),
    ("5400", "Rent, power, transport and other costs", "expense", "debit"),
    ("5900", "Other expenses", "expense", "debit"),
]

NORMAL_SIDE = {
    "asset": "debit",
    "expense": "debit",
    "liability": "credit",
    "equity": "credit",
    "income": "credit",
}
