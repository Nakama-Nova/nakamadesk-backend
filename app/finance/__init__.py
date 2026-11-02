"""
The finance ledger: a double-entry book of every rupee, kept apart from the rest of the
app. Nothing outside this package imports it except main.py (the routes) and the models
list (so migrations see the tables). See docs/FINANCE_LEDGER_DESIGN.md.
"""
