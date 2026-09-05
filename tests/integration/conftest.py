"""Integration test configuration and fixtures."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from lift.domain.models import Customer, Merchant
from lift.storage.base import Base
from lift.storage.database import create_db_engine, get_session_factory
from lift.storage.mappers import to_customer_orm, to_merchant_orm
from sqlalchemy import Engine
from sqlalchemy.orm import Session


@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure LIFT_ENV is set to test for integration tests."""
    monkeypatch.setenv("LIFT_ENV", "test")
    monkeypatch.delenv("DATABASE_URL", raising=False)


@pytest.fixture
def engine() -> Generator[Engine, None, None]:
    """Create a pristine in-memory SQLite engine with foreign keys enabled."""
    eng = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Generator[Session, None, None]:
    """Provide a transactional database session."""
    factory = get_session_factory(engine)
    sess = factory()
    try:
        yield sess
    finally:
        sess.rollback()
        sess.close()


@pytest.fixture
def persisted_merchant(session: Session, sample_merchant: Merchant) -> Merchant:
    """Persist and return sample merchant."""
    orm = to_merchant_orm(sample_merchant)
    session.add(orm)
    session.flush()
    return sample_merchant


@pytest.fixture
def persisted_customer(
    session: Session, persisted_merchant: Merchant, sample_customer: Customer
) -> Customer:
    """Persist and return sample customer."""
    orm = to_customer_orm(sample_customer)
    session.add(orm)
    session.flush()
    return sample_customer
