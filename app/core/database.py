"""Database session management and engine configuration (AppLocker-safe, no greenlet dependency)."""

from typing import Generator
from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.core.config import settings

# Normalize DB URL for the synchronous psycopg2 driver (bypasses greenlet C-extension blocked by Windows AppLocker)
db_url = settings.APP_DB_URL
if db_url.startswith(("postgresql+asyncpg://", "postgresql://", "postgres://")):
    db_url = "postgresql+psycopg2://" + db_url.split("://", 1)[1]

connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}

engine = create_engine(
    db_url,
    echo=(settings.LOG_LEVEL == "DEBUG"),
    pool_pre_ping=True,
    connect_args=connect_args,
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
)

# Deterministic constraint names so Alembic migrations are stable and reversible
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

Base = declarative_base(metadata=MetaData(naming_convention=NAMING_CONVENTION))


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session per request."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
