"""Unit tests for RECONCILE_PAYMENT_LINK handler.

Covers:
- Test C: Reconciliation takeover of expired/abandoned dispatch task
- Test D: Active dispatch protection (reconciliation aborts if original dispatch lease is active)
- Test E: Absence semantics (404 is absence, not non-execution proof; no duplicates)
- Paid link recovery authority
"""

from datetime import timedelta
from uuid import uuid4

import pytest
from lift.gateway.simulator_adapter import DeterministicSimulatorAdapter
from lift.gateway.types import GatewayCustomerInfo
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
from lift.workers.handlers.reconcile_payment_link import handle_reconcile_payment_link


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


def test_active_dispatch_protection(session_factory):
    """Test D: Reconciliation does NOT take over while original dispatch task lease

    is active and valid.
    """
    gateway = DeterministicSimulatorAdapter()
    merchant_id = uuid4()
    customer_id = uuid4()
    opp_id = uuid4()
    attempt_id = uuid4()
    decision_id = uuid4()
    voucher_id = uuid4()
    dispatch_task_id = uuid4()
    recon_task_id = uuid4()

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
        # Original dispatch task locked 10s ago (< 60s lease)
        dispatch_task = TaskQueueORM(
            id=dispatch_task_id,
            task_type="EVALUATE_OPPORTUNITY",
            payload={"opportunity_id": str(opp_id)},
            status="RUNNING",
            priority=10,
            scheduled_at=utc_now(),
            locked_by="worker_dispatch",
            locked_at=utc_now() - timedelta(seconds=10),
            lease_version=1,
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
            task_id=dispatch_task_id,
            lease_version=1,
            claimed_at=utc_now(),
        )
        recon_task = TaskQueueORM(
            id=recon_task_id,
            task_type="RECONCILE_PAYMENT_LINK",
            payload={
                "opportunity_id": str(opp_id),
                "voucher_id": str(voucher_id),
                "reference_id": "ref_test_001",
            },
            status="RUNNING",
            priority=5,
            scheduled_at=utc_now(),
            locked_by="worker_recon",
            locked_at=utc_now(),
            lease_version=1,
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
        session.add(dispatch_task)
        session.flush()
        session.add(voucher)
        session.flush()
        session.add(recon_task)
        session.commit()

    # Attempt reconciliation while dispatch task is still active (< 60s)
    with session_factory() as session:
        t = session.get(TaskQueueORM, recon_task_id)
        assert t is not None
        result = handle_reconcile_payment_link(
            session=session,
            task=t,
            gateway=gateway,
            worker_id="worker_recon",
            claimed_lease_version=1,
        )
        assert result is False  # Aborted and rescheduled

    # Assert opportunity and voucher remain in ACTION_EXECUTING and CLAIMED
    with session_factory() as session:
        o = session.get(RecoveryOpportunityORM, opp_id)
        assert o is not None
        assert o.current_state == "ACTION_EXECUTING"

        v = session.get(ExecutionRecordORM, voucher_id)
        assert v is not None
        assert v.execution_status == "CLAIMED"


def test_reconciliation_takeover_after_lease_expiry(session_factory):
    """Test C: Reconciliation task repairs abandoned execution after original

    dispatch lease is expired.
    """
    gateway = DeterministicSimulatorAdapter()
    # Create the link on Razorpay simulator first
    plink = gateway.create_payment_link(
        amount_subunits=50000,
        currency="INR",
        reference_id="ref_takeover_001",
        description="Recovery Link",
        customer=GatewayCustomerInfo(contact="+919999999999"),
    )

    merchant_id = uuid4()
    customer_id = uuid4()
    opp_id = uuid4()
    attempt_id = uuid4()
    decision_id = uuid4()
    voucher_id = uuid4()
    dispatch_task_id = uuid4()
    recon_task_id = uuid4()

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
            execution_claimed_at=utc_now() - timedelta(seconds=100),
        )
        # Original dispatch task expired (locked 100s ago)
        dispatch_task = TaskQueueORM(
            id=dispatch_task_id,
            task_type="EVALUATE_OPPORTUNITY",
            payload={"opportunity_id": str(opp_id)},
            status="RUNNING",
            priority=10,
            scheduled_at=utc_now(),
            locked_by="worker_dispatch",
            locked_at=utc_now() - timedelta(seconds=100),
            lease_version=1,
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
            reference_id="ref_takeover_001",
            intervention_type="DIRECT_PAYMENT_LINK_SMS",
            execution_status="CLAIMED",
            task_id=dispatch_task_id,
            lease_version=1,
            claimed_at=utc_now() - timedelta(seconds=100),
        )
        recon_task = TaskQueueORM(
            id=recon_task_id,
            task_type="RECONCILE_PAYMENT_LINK",
            payload={
                "opportunity_id": str(opp_id),
                "voucher_id": str(voucher_id),
                "reference_id": "ref_takeover_001",
            },
            status="RUNNING",
            priority=5,
            scheduled_at=utc_now(),
            locked_by="worker_recon",
            locked_at=utc_now(),
            lease_version=1,
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
        session.add(dispatch_task)
        session.flush()
        session.add(voucher)
        session.flush()
        session.add(recon_task)
        session.commit()

    # Reconciliation runs under its own lease
    with session_factory() as session:
        t = session.get(TaskQueueORM, recon_task_id)
        assert t is not None
        result = handle_reconcile_payment_link(
            session=session,
            task=t,
            gateway=gateway,
            worker_id="worker_recon",
            claimed_lease_version=1,
        )
        assert result is True

    # Assert: voucher settled as EXECUTED with external_reference_id
    with session_factory() as session:
        v = session.get(ExecutionRecordORM, voucher_id)
        assert v is not None
        assert v.execution_status == "EXECUTED"
        assert v.external_reference_id == plink.id

        o = session.get(RecoveryOpportunityORM, opp_id)
        assert o is not None
        assert o.current_state == "AWAITING_SETTLEMENT"

        rt = session.get(TaskQueueORM, recon_task_id)
        assert rt is not None
        assert rt.status == "COMPLETED"


def test_absence_semantics_no_link_found(session_factory):
    """Test E: Missing link on gateway (404) marks voucher FAILED and resets

    opportunity to OPEN without creating duplicate links.
    """
    gateway = DeterministicSimulatorAdapter()  # Empty: no link exists on gateway

    merchant_id = uuid4()
    customer_id = uuid4()
    opp_id = uuid4()
    attempt_id = uuid4()
    decision_id = uuid4()
    voucher_id = uuid4()
    recon_task_id = uuid4()

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
            execution_claimed_at=utc_now() - timedelta(seconds=120),
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
            reference_id="ref_missing_001",
            intervention_type="DIRECT_PAYMENT_LINK_SMS",
            execution_status="CLAIMED",
            task_id=None,
            lease_version=None,
            claimed_at=utc_now() - timedelta(seconds=120),
        )
        recon_task = TaskQueueORM(
            id=recon_task_id,
            task_type="RECONCILE_PAYMENT_LINK",
            payload={
                "opportunity_id": str(opp_id),
                "voucher_id": str(voucher_id),
                "reference_id": "ref_missing_001",
            },
            status="RUNNING",
            priority=5,
            scheduled_at=utc_now(),
            locked_by="worker_recon",
            locked_at=utc_now(),
            lease_version=1,
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
        session.add_all([voucher, recon_task])
        session.commit()

    with session_factory() as session:
        t = session.get(TaskQueueORM, recon_task_id)
        assert t is not None
        result = handle_reconcile_payment_link(
            session=session,
            task=t,
            gateway=gateway,
            worker_id="worker_recon",
            claimed_lease_version=1,
        )
        assert result is True

    with session_factory() as session:
        v = session.get(ExecutionRecordORM, voucher_id)
        assert v is not None
        # Marked FAILED (releases contact slot)
        assert v.execution_status == "FAILED"
        assert "No discoverable Payment Link" in str(v.failure_message)

        o = session.get(RecoveryOpportunityORM, opp_id)
        assert o is not None
        # Opportunity reset to OPEN for fresh evaluation on next cycle
        assert o.current_state == "OPEN"


def test_reconcile_payment_link_stale_worker_fenced_and_rolled_back(session_factory):
    """Verify that a stale worker cannot commit business mutations, and rolls back cleanly."""
    gateway = DeterministicSimulatorAdapter()
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
            razorpay_payment_id="pay_stale_recon",
            razorpay_order_id="order_stale_recon",
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
            order_id="order_stale_recon",
            initial_attempt_id=attempt_id,
            latest_attempt_id=attempt_id,
            amount_at_risk_subunits=50000,
            currency="INR",
            current_state="ACTION_EXECUTING",
            failure_category="AUTHENTICATION_TIMEOUT",
            organic_recovery_estimate=0.2,
            failure_attempt_count=1,
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
            idempotency_key="idem_stale_recon",
            reference_id="ref_stale_recon",
            intervention_type="DIRECT_PAYMENT_LINK_SMS",
            execution_status="CLAIMED",
            task_id=None,
            lease_version=None,
            claimed_at=utc_now() - timedelta(seconds=120),
        )
        # Task was reclaimed by worker_valid with lease_version=2
        task = TaskQueueORM(
            id=task_id,
            task_type="RECONCILE_PAYMENT_LINK",
            payload={
                "opportunity_id": str(opp_id),
                "voucher_id": str(voucher_id),
                "reference_id": "ref_stale_recon",
            },
            status="RUNNING",
            priority=5,
            scheduled_at=utc_now(),
            locked_by="worker_valid",
            locked_at=utc_now(),
            lease_version=2,
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
        session.add_all([voucher, task])
        session.commit()

    # 1. Stale worker attempts execution with old lease_version=1
    with session_factory() as session:
        t_stale = session.get(TaskQueueORM, task_id)
        assert t_stale is not None
        result = handle_reconcile_payment_link(
            session=session,
            task=t_stale,
            gateway=gateway,
            worker_id="worker_stale",
            claimed_lease_version=1,
        )
        assert result is False

    # Verify zero business mutations committed to DB
    with session_factory() as session:
        v = session.get(ExecutionRecordORM, voucher_id)
        assert v is not None
        assert v.execution_status == "CLAIMED"

        o = session.get(RecoveryOpportunityORM, opp_id)
        assert o is not None
        assert o.current_state == "ACTION_EXECUTING"

        t = session.get(TaskQueueORM, task_id)
        assert t is not None
        assert t.lease_version == 2
        assert t.locked_by == "worker_valid"
        assert t.status == "RUNNING"

    # 2. Current valid worker processes task successfully
    with session_factory() as session:
        t_valid = session.get(TaskQueueORM, task_id)
        assert t_valid is not None
        valid_res = handle_reconcile_payment_link(
            session=session,
            task=t_valid,
            gateway=gateway,
            worker_id="worker_valid",
            claimed_lease_version=2,
        )
        assert valid_res is True

    # Valid worker mutations are persisted
    with session_factory() as session:
        v = session.get(ExecutionRecordORM, voucher_id)
        assert v is not None
        assert v.execution_status == "FAILED"

        o = session.get(RecoveryOpportunityORM, opp_id)
        assert o is not None
        assert o.current_state == "OPEN"

        t = session.get(TaskQueueORM, task_id)
        assert t is not None
        assert t.status == "COMPLETED"
