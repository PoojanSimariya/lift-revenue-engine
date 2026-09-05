"""Unit tests for TaskQueueRepository enqueue operations."""

import pytest
from lift.storage.base import Base
from lift.storage.database import create_db_engine, get_session_factory
from lift.storage.repositories.task import TaskQueueRepository


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setenv("LIFT_ENV", "test")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = get_session_factory(engine)
    sess = factory()
    yield sess
    sess.rollback()
    sess.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_enqueue_task_in_transaction(session):
    """Verify that enqueue_task properly records task into task_queue within transaction."""
    repo = TaskQueueRepository(session)
    task = repo.enqueue_task(
        task_type="EVALUATE_OPPORTUNITY",
        payload={"opportunity_id": "opp_123"},
        priority=5,
    )

    assert task.id is not None
    assert task.task_type == "EVALUATE_OPPORTUNITY"
    assert task.payload == {"opportunity_id": "opp_123"}
    assert task.status == "QUEUED"
    assert task.priority == 5
    assert task.attempts == 0
    assert task.max_attempts == 3
    assert task.scheduled_at is not None

    # Verify query
    queried = repo.get_by_id(task.id)
    assert queried is not None
    assert queried.task_type == "EVALUATE_OPPORTUNITY"


def test_enqueue_cancel_payment_link_task(session):
    """Verify enqueuing CANCEL_PAYMENT_LINK task."""
    repo = TaskQueueRepository(session)
    task = repo.enqueue_task(
        task_type="CANCEL_PAYMENT_LINK",
        payload={"payment_link_id": "plink_123", "reason": "recovered"},
    )
    assert task.task_type == "CANCEL_PAYMENT_LINK"
    assert task.priority == 10  # default
    assert task.payload["payment_link_id"] == "plink_123"
