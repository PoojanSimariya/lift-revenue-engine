"""Integration tests for ReaperDaemon lease recovery and reconciliation sweep."""

from datetime import timedelta
from uuid import uuid4

import pytest
from lift.gateway.simulator_adapter import DeterministicSimulatorAdapter
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
from lift.workers.reaper import ReaperDaemon


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


def test_reaper_lease_recovery_and_stuck_opportunity_sweep(session_factory):
    """Test ReaperDaemon recovering expired leases and enqueuing reconciliation for stuck opps."""
    gateway = DeterministicSimulatorAdapter()
    reaper = ReaperDaemon(
        session_factory=session_factory,
        gateway=gateway,
        task_lease_timeout_seconds=60,
        opportunity_stuck_timeout_seconds=90,
    )

    now = utc_now()
    task_expired_id = uuid4()
    task_active_id = uuid4()

    merchant_id = uuid4()
    customer_id = uuid4()
    opp_stuck_id = uuid4()
    attempt_id = uuid4()
    decision_id = uuid4()
    voucher_id = uuid4()

    with session_factory() as session:
        # Task 1: Expired lease (locked 80s ago)
        t_expired = TaskQueueORM(
            id=task_expired_id,
            task_type="EVALUATE_OPPORTUNITY",
            payload={},
            status="RUNNING",
            priority=10,
            scheduled_at=now,
            locked_by="dead_worker",
            locked_at=now - timedelta(seconds=80),
            lease_version=3,
        )
        # Task 2: Active lease (locked 10s ago)
        t_active = TaskQueueORM(
            id=task_active_id,
            task_type="EVALUATE_OPPORTUNITY",
            payload={},
            status="RUNNING",
            priority=10,
            scheduled_at=now,
            locked_by="live_worker",
            locked_at=now - timedelta(seconds=10),
            lease_version=1,
        )

        merchant = MerchantORM(
            id=merchant_id,
            name="M",
            default_currency="INR",
            timezone="Asia/Kolkata",
            idempotency_salt="s",
            created_at=now,
            updated_at=now,
        )
        customer = CustomerORM(
            id=customer_id,
            merchant_id=merchant_id,
            external_customer_id="cust_reap",
            phone_hash="p",
            email_hash="e",
            risk_tier=1,
            created_at=now,
        )
        attempt = PaymentAttemptORM(
            id=attempt_id,
            customer_id=customer_id,
            razorpay_payment_id="pay_reap",
            razorpay_order_id="order_reap",
            attempt_sequence=1,
            amount_subunits=50000,
            currency="INR",
            payment_method="card",
            status="failed",
            gateway_created_at=now,
            raw_payload={},
        )
        opp = RecoveryOpportunityORM(
            id=opp_stuck_id,
            merchant_id=merchant_id,
            customer_id=customer_id,
            order_id="order_reap",
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
            execution_claimed_at=now - timedelta(seconds=120),  # Stuck > 90s
        )
        decision = RecoveryDecisionORM(
            id=decision_id,
            opportunity_id=opp_stuck_id,
            decision_type="AUTHORIZED",
            policy_evaluation_details={},
            explanation="Approved",
        )
        voucher = ExecutionRecordORM(
            id=voucher_id,
            decision_id=decision_id,
            attempt_index=1,
            idempotency_key="idem_reap",
            reference_id="ref_reap_001",
            intervention_type="DIRECT_PAYMENT_LINK_SMS",
            execution_status="CLAIMED",
            task_id=task_expired_id,
            lease_version=3,
            claimed_at=now - timedelta(seconds=120),
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
        session.add_all([t_expired, t_active])
        session.flush()
        session.add(voucher)
        session.commit()

    # Run Reaper cycle
    reclaimed, swept = reaper.run_cycle()

    # Assertions
    assert reclaimed == 1  # Only t_expired was reclaimed
    assert swept == 1  # opp_stuck was swept for reconciliation

    with session_factory() as session:
        # t_expired is now QUEUED with lease_version=4 and unowned
        t1 = session.get(TaskQueueORM, task_expired_id)
        assert t1 is not None
        assert t1.status == "QUEUED"
        assert t1.lease_version == 4
        assert t1.locked_by is None

        # t_active is untouched
        t2 = session.get(TaskQueueORM, task_active_id)
        assert t2 is not None
        assert t2.status == "RUNNING"
        assert t2.lease_version == 1
        assert t2.locked_by == "live_worker"

        # Reconciliation task was enqueued
        recon_tasks = (
            session.query(TaskQueueORM)
            .filter(TaskQueueORM.task_type == "RECONCILE_PAYMENT_LINK")
            .all()
        )
        assert len(recon_tasks) == 1
        assert recon_tasks[0].payload["opportunity_id"] == str(opp_stuck_id)
        assert recon_tasks[0].payload["reference_id"] == "ref_reap_001"
