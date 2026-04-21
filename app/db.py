import os
from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.constants import DbConfig

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set. Example: postgresql+psycopg2://user:pass@host:5432/db")

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=DbConfig.POOL_SIZE,
    max_overflow=DbConfig.MAX_OVERFLOW,
    pool_timeout=DbConfig.POOL_TIMEOUT,
    pool_recycle=DbConfig.POOL_RECYCLE,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)

def get_db():
    """FastAPI dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@contextmanager
def session_scope():
    """For scripts/Celery tasks."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
