"""Database session management with SQLAlchemy 2.0 async engine and SQLite WAL pragmas."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from backend.app.core.config import settings


class Base(DeclarativeBase):
    """Base declarative class for all SQLAlchemy ORM models."""
    pass


# Create async database engine
engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    future=True,
)


@event.listens_for(engine.sync_engine, "connect")
def configure_sqlite_pragmas(dbapi_connection, connection_record):
    """Configure SQLite pragmas for Write-Ahead Logging (WAL) and foreign keys.
    
    Notes:
    - journal_mode=WAL: Allows concurrent readers and one writer without blocking.
    - busy_timeout=5000: Mitigates write contention by waiting up to 5s before locking errors.
    - synchronous=NORMAL: Guarantees crash consistency against app crashes with high throughput.
    - foreign_keys=ON: Enforces relational integrity across parent/child records.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA busy_timeout=5000;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA foreign_keys=ON;")
    finally:
        cursor.close()


# Async session factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for providing an async database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def transaction_scope() -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional async session scope for atomic operations."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Initialize database tables for testing and development."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
