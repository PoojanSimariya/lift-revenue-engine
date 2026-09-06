"""Integration tests for multi-worker concurrency and task queue polling."""

import concurrent.futures
import os

import pytest
from lift.storage.base import Base
from lift.storage.database import create_db_engine, get_session_factory
from lift.storage.repositories.task import TaskQueueRepository

# Detect whether PostgreSQL is configured in environment
POSTGRES_URL = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
HAS_POSTGRES = bool(
    POSTGRES_URL
    and (
        POSTGRES_URL.startswith("postgresql://")
        or POSTGRES_URL.startswith("postgresql+psycopg://")
        or POSTGRES_URL.startswith("postgres://")
    )
)
SKIP_REASON = "PostgreSQL required for multi-worker concurrency per M4/ADR-006"


@pytest.fixture
def pg_session_factory():
    """PostgreSQL session factory for multi-worker concurrency testing.

    Skips test if PostgreSQL is not configured or unavailable.
    """
    if not HAS_POSTGRES or not POSTGRES_URL:
        pytest.skip(SKIP_REASON)

    try:
        engine = create_db_engine(POSTGRES_URL)
        with engine.connect():
            pass
    except Exception as exc:
        pytest.skip(f"{SKIP_REASON} (Connection failed: {exc})")

    Base.metadata.create_all(engine)
    factory = get_session_factory(engine)
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.mark.skipif(not HAS_POSTGRES, reason=SKIP_REASON)
def test_concurrent_worker_claiming_no_duplicate_claims(pg_session_factory):
    """Test that multiple workers polling concurrently claim distinct tasks without duplicates."""
    num_tasks = 20
    task_ids = []

    # Enqueue 20 tasks
    with pg_session_factory() as session:
        repo = TaskQueueRepository(session)
        for i in range(num_tasks):
            t = repo.enqueue_task("DUMMY_TASK", {"index": i}, priority=10)
            task_ids.append(t.id)
        session.commit()

    claimed_by_worker: dict[str, list[str]] = {}

    def worker_poll(worker_name: str) -> list[str]:
        claimed = []
        with pg_session_factory() as session:
            repo = TaskQueueRepository(session)
            while True:
                claim = repo.claim_next_task(worker_name)
                if not claim:
                    break
                task, _ = claim
                claimed.append(str(task.id))
                session.commit()
        return claimed

    # Run 5 concurrent worker threads
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(worker_poll, f"worker_{w}"): f"worker_{w}" for w in range(5)}
        for future in concurrent.futures.as_completed(futures):
            worker_name = futures[future]
            claimed_by_worker[worker_name] = future.result()

    # Aggregate all claimed task IDs
    all_claimed = []
    for claims in claimed_by_worker.values():
        all_claimed.extend(claims)

    # Invariant: Every task was claimed at most once (zero duplicates)
    assert len(all_claimed) == num_tasks
    assert len(set(all_claimed)) == num_tasks
