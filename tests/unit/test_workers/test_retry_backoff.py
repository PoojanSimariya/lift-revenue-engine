"""Unit tests for task retry and backoff semantics."""

from datetime import timedelta
from uuid import uuid4

import pytest
from lift.storage.base import Base, utc_now
from lift.storage.database import create_db_engine, get_session_factory
from lift.storage.repositories.task import TaskQueueRepository


@pytest.fixture
def session_factory(monkeypatch):
    monkeypatch.setenv("LIFT_ENV", "test")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = get_session_factory(engine)
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_retry_increments_attempts_and_sets_schedule(session_factory):
    """Test that retry_task increments attempts and schedules for future execution."""
    task_id = uuid4()
    with session_factory() as session:
        repo = TaskQueueRepository(session)
        task = repo.enqueue_task("EVALUATE_OPPORTUNITY", {"opp_id": "123"})
        session.commit()
        task_id = task.id

        claim = repo.claim_next_task("worker_1")
        assert claim is not None
        session.commit()

    # Retry 1
    next_time = utc_now() + timedelta(seconds=15)
    with session_factory() as session:
        repo = TaskQueueRepository(session)
        res = repo.retry_task(
            task_id,
            lease_version=1,
            worker_id="worker_1",
            error="Temporary 500 error",
            next_attempt_at=next_time,
        )
        assert res is True
        session.commit()

    with session_factory() as session:
        repo = TaskQueueRepository(session)
        t = repo.get_by_id(task_id)
        assert t is not None
        assert t.status == "QUEUED"
        assert t.attempts == 1
        assert t.last_error == "Temporary 500 error"
        assert t.locked_by is None
        assert t.locked_at is None


def test_retry_exceeding_max_attempts_fails(session_factory):
    """Test that retrying beyond max_attempts marks task FAILED."""
    task_id = uuid4()
    with session_factory() as session:
        repo = TaskQueueRepository(session)
        task = repo.enqueue_task("EVALUATE_OPPORTUNITY", {"opp_id": "123"})
        session.commit()
        task_id = task.id

    # Simulate attempt 1, 2, 3
    for attempt_num in range(1, 4):
        with session_factory() as session:
            repo = TaskQueueRepository(session)
            claim = repo.claim_next_task(f"worker_{attempt_num}")
            assert claim is not None
            task_obj, version = claim
            session.commit()

        with session_factory() as session:
            repo = TaskQueueRepository(session)
            repo.retry_task(
                task_id,
                lease_version=version,
                worker_id=f"worker_{attempt_num}",
                error=f"Error {attempt_num}",
                next_attempt_at=utc_now(),
            )
            session.commit()

    with session_factory() as session:
        repo = TaskQueueRepository(session)
        t = repo.get_by_id(task_id)
        assert t is not None
        # Max attempts reached (3 >= 3)
        assert t.attempts == 3
        assert t.status == "FAILED"
        assert t.last_error == "Error 3"
