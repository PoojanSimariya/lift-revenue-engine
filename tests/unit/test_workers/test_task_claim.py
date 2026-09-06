"""Unit tests for atomic task queue claiming and lease generation."""

from datetime import timedelta

import pytest
from lift.storage.base import Base, utc_now
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


def test_claim_next_task_increments_lease_version(session):
    """Test that claiming a task sets status to RUNNING and increments lease_version."""
    repo = TaskQueueRepository(session)
    task = repo.enqueue_task("EVALUATE_OPPORTUNITY", {"opp_id": "123"}, priority=5)
    session.commit()

    assert task.lease_version == 0
    assert task.status == "QUEUED"

    claim = repo.claim_next_task("worker_1")
    assert claim is not None
    claimed_task, lease_version = claim
    assert lease_version == 1
    assert claimed_task.lease_version == 1
    assert claimed_task.status == "RUNNING"
    assert claimed_task.locked_by == "worker_1"
    assert claimed_task.locked_at is not None


def test_claim_empty_queue_returns_none(session):
    """Test that claiming from an empty queue returns None."""
    repo = TaskQueueRepository(session)
    claim = repo.claim_next_task("worker_1")
    assert claim is None


def test_reclaim_stuck_task_increments_lease(session):
    """Test that reclaiming a stuck running task increments lease_version and requeues."""
    repo = TaskQueueRepository(session)
    task = repo.enqueue_task("EVALUATE_OPPORTUNITY", {"opp_id": "123"})
    session.commit()

    claim = repo.claim_next_task("worker_1")
    assert claim is not None
    _, version_1 = claim
    assert version_1 == 1

    # Reclaim stuck task
    new_version = repo.reclaim_stuck_task(task.id)
    assert new_version == 2
    session.commit()

    # Worker 2 can now claim it
    claim_2 = repo.claim_next_task("worker_2")
    assert claim_2 is not None
    claimed_2, version_3 = claim_2
    assert version_3 == 3
    assert claimed_2.locked_by == "worker_2"


def test_claim_priority_and_schedule_ordering(session):
    """Test that highest priority (lower number) and earliest scheduled tasks are claimed first."""
    repo = TaskQueueRepository(session)
    now = utc_now()
    t_low = repo.enqueue_task("TASK_LOW", {}, priority=20, scheduled_at=now)
    t_high = repo.enqueue_task("TASK_HIGH", {}, priority=5, scheduled_at=now)
    repo.enqueue_task("TASK_FUTURE", {}, priority=1, scheduled_at=now + timedelta(hours=1))
    session.commit()

    # First claim must be t_high
    claim_1 = repo.claim_next_task("worker_1")
    assert claim_1 is not None
    assert claim_1[0].id == t_high.id

    # Second claim must be t_low (future task is not eligible yet)
    claim_2 = repo.claim_next_task("worker_1")
    assert claim_2 is not None
    assert claim_2[0].id == t_low.id

    # Third claim is None because t_future is in the future
    claim_3 = repo.claim_next_task("worker_1")
    assert claim_3 is None
