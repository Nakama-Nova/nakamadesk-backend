import sys
from sqlalchemy import create_engine, text
try:
    url = "postgresql://postgres:postgres123@localhost:5432/furnibiz_test"
    engine = create_engine(url)
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    print("Postgres connection successful!")
except Exception as e:
    print(f"Postgres connection failed: {e}")
    sys.exit(1)
