import os

class DbConfig:
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set. Example: postgresql+psycopg2://user:pass@host:5432/db")
    try:
        POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
        MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "10"))
        POOL_TIMEOUT = int(os.getenv("DB_POOL_TIMEOUT", "30"))
        POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "1800"))
    except ValueError as e:
        raise RuntimeError("Database pool configuration environment variables must be integers.") from e