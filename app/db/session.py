"""Async SQLAlchemy 2.x session & engine management for SQLite (aiosqlite) and MySQL (aiomysql)."""

from pathlib import Path
from typing import AsyncGenerator, Optional
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from app.config import settings
from app.db.models import Base

_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def init_db_engine(database_url: Optional[str] = None) -> AsyncEngine:
    global _engine, _session_factory
    db_url = database_url or settings.database_url

    if db_url.startswith("sqlite"):
        raw_path = db_url.split("///", 1)[-1]
        if raw_path and raw_path != ":memory:":
            parent = Path(raw_path).resolve().parent
            parent.mkdir(parents=True, exist_ok=True)

        _engine = create_async_engine(
            db_url,
            echo=False,
            connect_args={"check_same_thread": False, "timeout": 30},
        )

        @event.listens_for(_engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    else:
        _engine = create_async_engine(
            db_url,
            echo=False,
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_size=10,
            max_overflow=20,
        )

    _session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    return _engine


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        return init_db_engine()
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        init_db_engine()
    assert _session_factory is not None
    return _session_factory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def create_all_tables() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
