"""Database engine, session management, and environment configuration guardrails."""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from lift.core.errors import DatabaseConfigurationError


def get_database_url(env_name: str | None = None) -> str:
    """Resolve database URL with strict environment-aware validation.

    Production and staging environments strictly require a PostgreSQL DATABASE_URL.
    SQLite is only permitted in explicit test and development environments.

    Raises:
        DatabaseConfigurationError: If production lacks DATABASE_URL or uses SQLite.
    """
    env_val = env_name if env_name is not None else (os.getenv("LIFT_ENV") or "development")
    env = env_val.strip().lower()
    raw_url = os.getenv("DATABASE_URL", "").strip()

    if env in ("production", "staging"):
        if not raw_url:
            raise DatabaseConfigurationError(
                f"DATABASE_URL environment variable is mandatory in {env} environment."
            )
        if raw_url.startswith("sqlite"):
            raise DatabaseConfigurationError(
                f"SQLite is not supported in {env} environment. PostgreSQL is required."
            )
        return raw_url

    if raw_url:
        return raw_url

    if env == "test":
        return "sqlite:///:memory:"

    return "sqlite:///lift_dev.db"


def _set_sqlite_foreign_keys_pragma(dbapi_conn: Any, connection_record: Any) -> None:
    """Enforce relational foreign key constraints in SQLite."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;")
    cursor.close()


def create_db_engine(
    url: str | None = None,
    echo: bool = False,
    **kwargs: Any,
) -> Engine:
    """Create and configure a SQLAlchemy 2.0 Engine with SQLite PRAGMA enforcement.

    Args:
        url: Optional database connection URL (defaults to get_database_url()).
        echo: Whether to log generated SQL statements.
        kwargs: Additional engine kwargs.

    Returns:
        Configured SQLAlchemy Engine.
    """
    db_url = url or get_database_url()

    if db_url.startswith("sqlite"):
        engine_kwargs: dict[str, Any] = {"echo": echo}
        if ":memory:" in db_url:
            engine_kwargs["poolclass"] = StaticPool
            engine_kwargs["connect_args"] = {"check_same_thread": False}
        engine_kwargs.update(kwargs)
        engine = create_engine(db_url, **engine_kwargs)
        event.listen(engine, "connect", _set_sqlite_foreign_keys_pragma)
        return engine

    # PostgreSQL / other RDBMS
    engine_kwargs = {"echo": echo}
    engine_kwargs.update(kwargs)
    return create_engine(db_url, **engine_kwargs)


def get_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a configured sessionmaker bound to the given engine."""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    """Provide a transactional session context.

    Owns the atomic transaction boundary: commits on successful exit,
    rolls back on unhandled exception, and always closes the session.
    """
    session = session_factory()
    try:
        with session.begin():
            yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
