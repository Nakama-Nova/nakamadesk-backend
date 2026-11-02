"""
The ledger's rules, enforced by the database itself so no bug in the app (or a person
at a SQL prompt) can break them:

  * an entry has at least two lines and its debits equal its credits (checked when the
    transaction commits, so the lines can be inserted one by one)
  * entries and lines can never be updated, deleted or truncated: a mistake is fixed by
    a reversing entry
  * nothing can be posted on or before the date the books are closed through

One list of statements, used both when the tables are created from the models (tests)
and by the Alembic migration, so they cannot drift apart. No percent signs or colons
in here: SQLAlchemy would read them as placeholders.
"""

CHECK_ENTRY = """
CREATE OR REPLACE FUNCTION finance_check_entry() RETURNS trigger AS $$
DECLARE
    eid uuid;
    n integer;
    d numeric;
    c numeric;
BEGIN
    IF TG_TABLE_NAME = 'finance_entries' THEN
        eid := NEW.id;
    ELSE
        eid := NEW.entry_id;
    END IF;
    SELECT count(*), coalesce(sum(debit), 0), coalesce(sum(credit), 0)
      INTO n, d, c FROM finance_lines WHERE entry_id = eid;
    IF n < 2 THEN
        RAISE EXCEPTION USING MESSAGE = 'A journal entry needs at least two lines';
    END IF;
    IF d <> c THEN
        RAISE EXCEPTION USING MESSAGE =
            'Journal entry does not balance: debits ' || d || ' credits ' || c;
    END IF;
    RETURN NULL;
END
$$ LANGUAGE plpgsql
"""

NO_CHANGE = """
CREATE OR REPLACE FUNCTION finance_no_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION USING MESSAGE =
        'Ledger rows cannot be changed or deleted; post a reversing entry instead';
END
$$ LANGUAGE plpgsql
"""

OPEN_PERIOD = """
CREATE OR REPLACE FUNCTION finance_check_open_period() RETURNS trigger AS $$
DECLARE
    closed date;
BEGIN
    SELECT closed_through INTO closed FROM finance_settings WHERE id = 1;
    IF closed IS NOT NULL AND NEW.entry_date <= closed THEN
        RAISE EXCEPTION USING MESSAGE = 'The books are closed up to ' || closed;
    END IF;
    RETURN NEW;
END
$$ LANGUAGE plpgsql
"""

TRIGGERS = [
    """CREATE CONSTRAINT TRIGGER finance_entries_balanced
       AFTER INSERT ON finance_entries DEFERRABLE INITIALLY DEFERRED
       FOR EACH ROW EXECUTE FUNCTION finance_check_entry()""",
    """CREATE CONSTRAINT TRIGGER finance_lines_balanced
       AFTER INSERT ON finance_lines DEFERRABLE INITIALLY DEFERRED
       FOR EACH ROW EXECUTE FUNCTION finance_check_entry()""",
    """CREATE TRIGGER finance_entries_immutable
       BEFORE UPDATE OR DELETE ON finance_entries
       FOR EACH ROW EXECUTE FUNCTION finance_no_change()""",
    """CREATE TRIGGER finance_lines_immutable
       BEFORE UPDATE OR DELETE ON finance_lines
       FOR EACH ROW EXECUTE FUNCTION finance_no_change()""",
    """CREATE TRIGGER finance_entries_no_truncate
       BEFORE TRUNCATE ON finance_entries
       FOR EACH STATEMENT EXECUTE FUNCTION finance_no_change()""",
    """CREATE TRIGGER finance_lines_no_truncate
       BEFORE TRUNCATE ON finance_lines
       FOR EACH STATEMENT EXECUTE FUNCTION finance_no_change()""",
    """CREATE TRIGGER finance_entries_open_period
       BEFORE INSERT ON finance_entries
       FOR EACH ROW EXECUTE FUNCTION finance_check_open_period()""",
]

FUNCTIONS = [CHECK_ENTRY, NO_CHANGE, OPEN_PERIOD]

# Run once finance_lines exists (the last of the entry tables created).
INSTALL = FUNCTIONS + TRIGGERS

SETTINGS_ROW = (
    "INSERT INTO finance_settings (id, updated_at) VALUES (1, now()) "
    "ON CONFLICT DO NOTHING"
)

UNINSTALL = [
    "DROP TRIGGER IF EXISTS finance_lines_no_truncate ON finance_lines",
    "DROP TRIGGER IF EXISTS finance_entries_no_truncate ON finance_entries",
    "DROP TRIGGER IF EXISTS finance_lines_immutable ON finance_lines",
    "DROP TRIGGER IF EXISTS finance_entries_immutable ON finance_entries",
    "DROP TRIGGER IF EXISTS finance_lines_balanced ON finance_lines",
    "DROP TRIGGER IF EXISTS finance_entries_balanced ON finance_entries",
    "DROP TRIGGER IF EXISTS finance_entries_open_period ON finance_entries",
    "DROP FUNCTION IF EXISTS finance_check_entry()",
    "DROP FUNCTION IF EXISTS finance_no_change()",
    "DROP FUNCTION IF EXISTS finance_check_open_period()",
]
