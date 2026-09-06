"""Unit tests for lease fencing and stale worker mutation prevention."""

from uuid import uuid4

import pytest
from lift.core.errors import StaleWorkerFencedError
from lift.gateway.simulator_adapter import DeterministicSimulatorAdapter
from lift.services.execution import ExecutionSafetyService, Phase2OutcomeType, Phase2Result
from lift.storage.base import Base, utc_now
from lift.storage.database import create_db_engine, get_session_factory
from lift.storage.orm_models import (
    CustomerORM,
    ExecutionRecordORM,
    MerchantORM,
    PaymentAttemptORM,
    RecoveryDecisionORM,
    RecoveryOpportunityORM,
    TaskQueueORM,
)
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


def test_stale_task_operations_rejected(session_factory):
    """Test B: Stale worker cannot complete, retry, fail, or renew task."""
    with session_factory() as session:
        repo = TaskQueueRepository(session)
        task = repo.enqueue_task("EVALUATE_OPPORTUNITY", {"opp_id": "123"})
        session.commit()
        task_id = task.id

        # Worker A claims with lease_version=1
        claim_a = repo.claim_next_task("worker_a")
        assert claim_a is not None
        assert claim_a[1] == 1
        session.commit()

    # Simulate lease expiry & Worker B reclaim
    with session_factory() as session:
        repo = TaskQueueRepository(session)
        new_version = repo.reclaim_stuck_task(task_id)
        assert new_version == 2
        claim_b = repo.claim_next_task("worker_b")
        assert claim_b is not None
        assert claim_b[1] == 3
        session.commit()

    # Worker A resumes and tries mutations with stale lease_version=1
    with session_factory() as session:
        repo = TaskQueueRepository(session)

        # 1. Complete task fails
        assert not repo.complete_task(task_id, lease_version=1, worker_id="worker_a")

        # 2. Retry task fails
        assert not repo.retry_task(
            task_id,
            lease_version=1,
            worker_id="worker_a",
            error="stale error",
            next_attempt_at=utc_now(),
        )

        # 3. Permanent fail fails
        assert not repo.fail_task_permanently(
            task_id, lease_version=1, worker_id="worker_a", error="stale error"
        )

        # 4. Renew lease fails
        assert not repo.renew_lease(task_id, lease_version=1, worker_id="worker_a")

        # Task remains owned by worker_b with lease_version=3
        current = repo.get_by_id(task_id)
        assert current is not None
        assert current.status == "RUNNING"
        assert current.locked_by == "worker_b"
        assert current.lease_version == 3


def test_stale_worker_phase_3_settlement_rejected(session_factory):
    """Test A (CRITICAL): Stale worker in Phase 3 cannot settle execution or transition opportunity.

    Worker A claims lease=7
    Lease expires
    Worker B reclaims task with lease=8
    Worker A wakes up and attempts Phase 3
    Assert Worker A cannot update execution_records
    Assert Worker A cannot transition opportunity
    Assert Worker A cannot complete task
    Assert transaction rolls back and emits StaleWorkerFencedError
    """
    gateway = DeterministicSimulatorAdapter()

    # Seed initial merchant, customer, opportunity, voucher, and task
    merchant_id = uuid4()
    customer_id = uuid4()
    opp_id = uuid4()
    attempt_id = uuid4()
    decision_id = uuid4()
    voucher_id = uuid4()
    task_id = uuid4()

    with session_factory() as session:
        merchant = MerchantORM(
            id=merchant_id,
            name="Test Merchant",
            default_currency="INR",
            timezone="Asia/Kolkata",
            idempotency_salt="salt_123",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        customer = CustomerORM(
            id=customer_id,
            merchant_id=merchant_id,
            external_customer_id="cust_123",
            phone_hash="hash_p",
            email_hash="hash_e",
            risk_tier=1,
            created_at=utc_now(),
        )
        attempt = PaymentAttemptORM(
            id=attempt_id,
            customer_id=customer_id,
            razorpay_payment_id="pay_001",
            razorpay_order_id="order_001",
            attempt_sequence=1,
            amount_subunits=50000,
            currency="INR",
            payment_method="card",
            status="failed",
            gateway_created_at=utc_now(),
            raw_payload={},
        )
        opp = RecoveryOpportunityORM(
            id=opp_id,
            merchant_id=merchant_id,
            customer_id=customer_id,
            order_id="order_001",
            initial_attempt_id=attempt_id,
            latest_attempt_id=attempt_id,
            amount_at_risk_subunits=50000,
            currency="INR",
            current_state="ACTION_EXECUTING",
            failure_category="AUTHENTICATION_TIMEOUT",
            organic_recovery_estimate=0.2,
            failure_attempt_count=1,
            total_interventions_count=1,
            total_contacts_count=1,
            execution_claimed_at=utc_now(),
        )
        task = TaskQueueORM(
            id=task_id,
            task_type="EVALUATE_OPPORTUNITY",
            payload={"opportunity_id": str(opp_id)},
            status="RUNNING",
            priority=10,
            scheduled_at=utc_now(),
            locked_by="worker_a",
            locked_at=utc_now(),
            lease_version=7,
        )
        decision = RecoveryDecisionORM(
            id=decision_id,
            opportunity_id=opp_id,
            decision_type="AUTHORIZED",
            policy_evaluation_details={},
            explanation="Approved",
        )
        voucher = ExecutionRecordORM(
            id=voucher_id,
            decision_id=decision_id,
            attempt_index=1,
            idempotency_key="idem_001",
            reference_id="ref_test_001",
            intervention_type="DIRECT_PAYMENT_LINK_SMS",
            execution_status="CLAIMED",
            task_id=task_id,
            lease_version=7,
            claimed_at=utc_now(),
        )
        session.add(merchant)
        session.flush()

        session.add(customer)
        session.flush()

        session.add(attempt)
        session.flush()

        session.add(opp)
        session.flush()

        session.add(decision)
        session.flush()

        session.add(task)
        session.flush()

        session.add(voucher)
        session.commit()

    # Simulate: Worker A freezes during Phase 2.
    # Worker B or Lease Reaper detects lease expired and reclaims task (lease_version=8)
    with session_factory() as session:
        t = session.get(TaskQueueORM, task_id)
        assert t is not None
        t.lease_version = 8
        t.locked_by = "worker_b"
        session.commit()

    # Worker A wakes up and attempts Phase 3 settlement with stale lease_version=7
    with session_factory() as session:
        safety_service = ExecutionSafetyService(session=session, gateway=gateway)
        fake_phase_2 = Phase2Result(
            outcome_type=Phase2OutcomeType.SUCCESS,
            external_reference_id="plink_stale_123",
        )

        with pytest.raises(StaleWorkerFencedError) as exc_info:
            safety_service.execute_phase_3(
                task_id=task_id,
                claimed_lease_version=7,
                worker_id="worker_a",
                voucher_id=voucher_id,
                opportunity_id=opp_id,
                phase_2_result=fake_phase_2,
            )

        assert exc_info.value.details["claimed_lease"] == 7
        assert exc_info.value.details["current_lease"] == 8

    # Verify: NO mutations occurred!
    with session_factory() as session:
        v = session.get(ExecutionRecordORM, voucher_id)
        assert v is not None
        assert v.execution_status == "CLAIMED"
        assert v.external_reference_id is None
        assert v.executed_at is None

        o = session.get(RecoveryOpportunityORM, opp_id)
        assert o is not None
        assert o.current_state == "ACTION_EXECUTING"

        t = session.get(TaskQueueORM, task_id)
        assert t is not None
        assert t.status == "RUNNING"
        assert t.locked_by == "worker_b"
        assert t.lease_version == 8
