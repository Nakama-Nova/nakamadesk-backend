import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings

if os.getenv("VERCEL"):
    # Serverless: every invocation may be a fresh process, so keep no pool of our
    # own. Use the database's pooler address (Supabase: port 6543) in DATABASE_URL.
    engine = create_engine(
        settings.DATABASE_URL, poolclass=NullPool, pool_pre_ping=True
    )
else:
    engine = create_engine(
        settings.DATABASE_URL,
        pool_size=20,
        max_overflow=10,
        pool_timeout=30,
        pool_recycle=1800,
        pool_pre_ping=True,
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
