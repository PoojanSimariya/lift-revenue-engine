"""Integration tests for database configuration, environment guardrails, and session lifecycle."""

from __future__ import annotations

import uuid

import pytest
from lift.core.errors import DatabaseConfigurationError
from lift.storage.database import (
    get_database_url,
    get_session_factory,
    session_scope,
)
from lift.storage.orm_models import CustomerORM, MerchantORM
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def test_database_url_production_missing_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production requires explicit DATABASE_URL."""
    monkeypatch.setenv("LIFT_ENV", "production")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(
        DatabaseConfigurationError, match="DATABASE_URL environment variable is mandatory"
    ):
        get_database_url()


def test_database_url_production_sqlite_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production rejects SQLite databases."""
    monkeypatch.setenv("LIFT_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///prod.db")
    with pytest.raises(DatabaseConfigurationError, match="SQLite is not supported"):
        get_database_url()


def test_database_url_staging_missing_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Staging requires explicit DATABASE_URL."""
    monkeypatch.setenv("LIFT_ENV", "staging")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(
        DatabaseConfigurationError, match="DATABASE_URL environment variable is mandatory"
    ):
        get_database_url()


def test_database_url_staging_sqlite_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Staging rejects SQLite databases."""
    monkeypatch.setenv("LIFT_ENV", "staging")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    with pytest.raises(DatabaseConfigurationError, match="SQLite is not supported"):
        get_database_url()


def test_database_url_production_valid_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production accepts valid postgresql URL."""
    monkeypatch.setenv("LIFT_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/liftdb")
    assert get_database_url() == "postgresql://user:pass@localhost:5432/liftdb"


def test_database_url_development_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Development defaults to local sqlite file."""
    monkeypatch.setenv("LIFT_ENV", "development")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert get_database_url() == "sqlite:///lift_dev.db"


def test_database_url_test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test environment defaults to in-memory sqlite."""
    monkeypatch.setenv("LIFT_ENV", "test")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert get_database_url() == "sqlite:///:memory:"


def test_sqlite_foreign_keys_enforced(engine: Engine, session: Session) -> None:
    """PRAGMA foreign_keys = ON must reject orphan foreign key records."""
    non_existent_merchant_id = uuid.uuid4()
    orphan_customer = CustomerORM(
        merchant_id=non_existent_merchant_id,
        external_customer_id="orphan_001",
    )
    session.add(orphan_customer)
    with pytest.raises(IntegrityError):
        session.flush()


def test_session_scope_commits_on_success(engine: Engine) -> None:
    """session_scope commits transactional changes on normal completion."""
    factory = get_session_factory(engine)
    merchant_id = uuid.uuid4()

    with session_scope(factory) as s:
        merchant = MerchantORM(
            id=merchant_id,
            name="Session Scope Merchant",
            idempotency_salt="test_salt_123",
        )
        s.add(merchant)

    # Verify persisted in a new session
    with session_scope(factory) as s:
        fetched = s.scalar(select(MerchantORM).where(MerchantORM.id == merchant_id))
        assert fetched is not None
        assert fetched.name == "Session Scope Merchant"


def test_session_scope_rolls_back_on_exception(engine: Engine) -> None:
    """session_scope rolls back changes when an exception is raised."""
    factory = get_session_factory(engine)
    merchant_id = uuid.uuid4()

    with pytest.raises(RuntimeError, match="Simulated failure"):
        with session_scope(factory) as s:
            merchant = MerchantORM(
                id=merchant_id,
                name="Rollback Merchant",
                idempotency_salt="test_salt_456",
            )
            s.add(merchant)
            s.flush()
            raise RuntimeError("Simulated failure")

    # Verify not persisted
    with session_scope(factory) as s:
        fetched = s.scalar(select(MerchantORM).where(MerchantORM.id == merchant_id))
        assert fetched is None
