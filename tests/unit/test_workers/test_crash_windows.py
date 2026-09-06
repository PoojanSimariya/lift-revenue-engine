"""Unit tests for Crash-Window Analysis (W1 through W9)."""

from datetime import timedelta
from uuid import uuid4

import pytest
from lift.core.types import ExecutionStatus, OpportunityState
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
from lift.workers.handlers.cancel_payment_link import handle_cancel_payment_link
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


def test_crash_window_w3_remote_success_local_crash_reconciled(session_factory):
    """W3: Crash during Phase 2 response transit.

    Razorpay created link, but worker crashed before Phase 3.
    Recovery: Reconciliation discovers live link via reference_id, updates voucher to EXECUTED,
    advances to AWAITING_SETTLEMENT, creating ZERO duplicate links.
    """
    gateway = DeterministicSimulatorAdapter()
    ref_id = "ref_w3_001"
    # Gateway link succeeded remotely
    plink = gateway.create_payment_link(
        amount_subunits=50000,
        currency="INR",
        reference_id=ref_id,
        description="W3 recovery",
        customer=GatewayCustomerInfo(),
    )

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
            name="M",
            default_currency="INR",
            timezone="Asia/Kolkata",
            idempotency_salt="s",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        customer = CustomerORM(
            id=customer_id,
            merchant_id=merchant_id,
            external_customer_id="c_w3",
            phone_hash="p",
            email_hash="e",
            risk_tier=1,
            created_at=utc_now(),
        )
        attempt = PaymentAttemptORM(
            id=attempt_id,
            customer_id=customer_id,
            razorpay_payment_id="pay_w3",
            razorpay_order_id="order_w3",
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
            order_id="order_w3",
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
            idempotency_key="idem_w3",
            reference_id=ref_id,
            intervention_type="DIRECT_PAYMENT_LINK_SMS",
            execution_status="CLAIMED",
            claimed_at=utc_now() - timedelta(seconds=120),
        )
        recon_task = TaskQueueORM(
            id=recon_task_id,
            task_type="RECONCILE_PAYMENT_LINK",
            payload={
                "opportunity_id": str(opp_id),
                "voucher_id": str(voucher_id),
                "reference_id": ref_id,
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

    # Reconciliation runs
    with session_factory() as session:
        t = session.get(TaskQueueORM, recon_task_id)
        assert t is not None
        res = handle_reconcile_payment_link(session, t, gateway, "worker_recon", 1)
        assert res is True

    # Assert: Exactly 1 link created on gateway (zero duplicates)
    assert len(gateway._payment_links) == 1

    with session_factory() as session:
        v = session.get(ExecutionRecordORM, voucher_id)
        assert v is not None
        assert v.execution_status == ExecutionStatus.EXECUTED.value
        assert v.external_reference_id == plink.id

        o = session.get(RecoveryOpportunityORM, opp_id)
        assert o is not None
        assert o.current_state == OpportunityState.AWAITING_SETTLEMENT.value


def test_crash_window_w7_cancel_payment_link_idempotent(session_factory):
    """W7: Crash during CANCEL_PAYMENT_LINK execution retried idempotently."""
    gateway = DeterministicSimulatorAdapter()
    plink = gateway.create_payment_link(
        amount_subunits=10000,
        currency="INR",
        reference_id="ref_w7",
        description="W7 cancel",
        customer=GatewayCustomerInfo(),
    )

    task_id = uuid4()
    with session_factory() as session:
        t = TaskQueueORM(
            id=task_id,
            task_type="CANCEL_PAYMENT_LINK",
            payload={"payment_link_id": plink.id},
            status="RUNNING",
            priority=10,
            scheduled_at=utc_now(),
            locked_by="w1",
            locked_at=utc_now(),
            lease_version=1,
        )
        session.add(t)
        session.commit()

    # Worker crashes; link was cancelled on gateway
    gateway.cancel_payment_link(plink.id)

    # Next attempt succeeds idempotently
    with session_factory() as session:
        t = session.get(TaskQueueORM, task_id)
        assert t is not None
        res = handle_cancel_payment_link(session, t, gateway, "w1", 1)
        assert res is True

    with session_factory() as session:
        t = session.get(TaskQueueORM, task_id)
        assert t is not None
        assert t.status == "COMPLETED"


def test_crash_window_w1_crash_before_phase_1_commit_reclaimed_by_reaper(session_factory):
    """W1: Worker crashes during Phase 1 before database commit.

    Durable state: task_queue is RUNNING with expired locked_at; zero vouchers or decisions
    created; opportunity remains OPEN.
    Recovery: Reaper reclaims expired task lease (increments lease_version to 2,
    resets status to QUEUED). A subsequent worker claims it and executes Phase 1 cleanly.
    """
    from lift.workers.reaper import ReaperDaemon

    gateway = DeterministicSimulatorAdapter()
    merchant_id = uuid4()
    customer_id = uuid4()
    opp_id = uuid4()
    attempt_id = uuid4()
    task_id = uuid4()

    with session_factory() as session:
        merchant = MerchantORM(
            id=merchant_id,
            name="M",
            default_currency="INR",
            timezone="Asia/Kolkata",
            idempotency_salt="s",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        customer = CustomerORM(
            id=customer_id,
            merchant_id=merchant_id,
            external_customer_id="c_w1",
            phone_hash="p",
            email_hash="e",
            risk_tier=1,
            created_at=utc_now(),
        )
        attempt = PaymentAttemptORM(
            id=attempt_id,
            customer_id=customer_id,
            razorpay_payment_id="pay_w1",
            razorpay_order_id="order_w1",
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
            order_id="order_w1",
            initial_attempt_id=attempt_id,
            latest_attempt_id=attempt_id,
            amount_at_risk_subunits=50000,
            currency="INR",
            current_state=OpportunityState.OPEN.value,
            failure_category="TRANSIENT_NETWORK",
            organic_recovery_estimate=0.4,
            failure_attempt_count=1,
        )
        # Worker crashed during Phase 1 -> task was left RUNNING with lease expired 120s ago
        task = TaskQueueORM(
            id=task_id,
            task_type="EVALUATE_OPPORTUNITY",
            payload={"opportunity_id": str(opp_id)},
            status="RUNNING",
            priority=10,
            scheduled_at=utc_now() - timedelta(seconds=150),
            locked_by="worker_dead_w1",
            locked_at=utc_now() - timedelta(seconds=120),
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
        session.add(task)
        session.commit()

    # Zero vouchers or decisions exist
    with session_factory() as session:
        assert session.query(ExecutionRecordORM).count() == 0
        assert session.query(RecoveryDecisionORM).count() == 0

    # Reaper recovers the expired lease
    reaper = ReaperDaemon(
        session_factory=session_factory,
        gateway=gateway,
        task_lease_timeout_seconds=60,
    )
    reclaimed = reaper.recover_expired_leases()
    assert reclaimed == 1

    # Task is reset to QUEUED with lease_version incremented to 2
    with session_factory() as session:
        t = session.get(TaskQueueORM, task_id)
        assert t is not None
        assert t.status == "QUEUED"
        assert t.locked_by is None
        assert t.lease_version == 2


def test_crash_window_w2_crash_before_external_http_reconciled(session_factory):
    """W2: Phase 1 committed, but worker crashes before external HTTP dispatch.

    Durable state: Voucher is CLAIMED, opportunity is in ACTION_EXECUTING. Gateway has NO link.
    Recovery: Reconciler verifies link absence on Razorpay via reference_id -> marks voucher FAILED,
    resets opportunity to OPEN, releasing contact slot without creating duplicate links.
    """
    gateway = DeterministicSimulatorAdapter()
    ref_id = "ref_w2_001"

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
            name="M",
            default_currency="INR",
            timezone="Asia/Kolkata",
            idempotency_salt="s",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        customer = CustomerORM(
            id=customer_id,
            merchant_id=merchant_id,
            external_customer_id="c_w2",
            phone_hash="p",
            email_hash="e",
            risk_tier=1,
            created_at=utc_now(),
        )
        attempt = PaymentAttemptORM(
            id=attempt_id,
            customer_id=customer_id,
            razorpay_payment_id="pay_w2",
            razorpay_order_id="order_w2",
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
            order_id="order_w2",
            initial_attempt_id=attempt_id,
            latest_attempt_id=attempt_id,
            amount_at_risk_subunits=50000,
            currency="INR",
            current_state=OpportunityState.ACTION_EXECUTING.value,
            failure_category="TRANSIENT_NETWORK",
            organic_recovery_estimate=0.4,
            failure_attempt_count=1,
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
            idempotency_key="idem_w2",
            reference_id=ref_id,
            intervention_type="DIRECT_PAYMENT_LINK_SMS",
            execution_status=ExecutionStatus.CLAIMED.value,
            claimed_at=utc_now() - timedelta(seconds=120),
        )
        recon_task = TaskQueueORM(
            id=recon_task_id,
            task_type="RECONCILE_PAYMENT_LINK",
            payload={
                "opportunity_id": str(opp_id),
                "voucher_id": str(voucher_id),
                "reference_id": ref_id,
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

    # Reconciler runs: gateway has NO link for ref_id
    with session_factory() as session:
        t = session.get(TaskQueueORM, recon_task_id)
        assert t is not None
        res = handle_reconcile_payment_link(session, t, gateway, "worker_recon", 1)
        assert res is True

    # Assert: zero links created on gateway, voucher marked FAILED (releasing contact slot),
    # and opportunity reset to OPEN
    assert len(gateway._payment_links) == 0
    with session_factory() as session:
        v = session.get(ExecutionRecordORM, voucher_id)
        assert v is not None
        assert v.execution_status == ExecutionStatus.FAILED.value

        o = session.get(RecoveryOpportunityORM, opp_id)
        assert o is not None
        assert o.current_state == OpportunityState.OPEN.value


def test_crash_window_w4_creation_timeout_unknown_outcome_reconciled(session_factory):
    """W4: Timeout or network error during create_payment_link.

    Durable state: Voucher CLAIMED; ambiguous whether link was created on Razorpay.
    Recovery: Reconciler checks reference_id on Razorpay:
    If link exists remotely -> backfills voucher to EXECUTED, moves opp to AWAITING_SETTLEMENT.
    Zero duplicate links created.
    """
    gateway = DeterministicSimulatorAdapter()
    ref_id = "ref_w4_ambiguous"
    # Gateway link was successfully processed remotely despite client timeout
    plink = gateway.create_payment_link(
        amount_subunits=50000,
        currency="INR",
        reference_id=ref_id,
        description="W4 ambiguous",
        customer=GatewayCustomerInfo(),
    )

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
            name="M",
            default_currency="INR",
            timezone="Asia/Kolkata",
            idempotency_salt="s",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        customer = CustomerORM(
            id=customer_id,
            merchant_id=merchant_id,
            external_customer_id="c_w4",
            phone_hash="p",
            email_hash="e",
            risk_tier=1,
            created_at=utc_now(),
        )
        attempt = PaymentAttemptORM(
            id=attempt_id,
            customer_id=customer_id,
            razorpay_payment_id="pay_w4",
            razorpay_order_id="order_w4",
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
            order_id="order_w4",
            initial_attempt_id=attempt_id,
            latest_attempt_id=attempt_id,
            amount_at_risk_subunits=50000,
            currency="INR",
            current_state=OpportunityState.ACTION_EXECUTING.value,
            failure_category="TRANSIENT_NETWORK",
            organic_recovery_estimate=0.4,
            failure_attempt_count=1,
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
            idempotency_key="idem_w4",
            reference_id=ref_id,
            intervention_type="DIRECT_PAYMENT_LINK_SMS",
            execution_status=ExecutionStatus.CLAIMED.value,
            claimed_at=utc_now() - timedelta(seconds=120),
        )
        recon_task = TaskQueueORM(
            id=recon_task_id,
            task_type="RECONCILE_PAYMENT_LINK",
            payload={
                "opportunity_id": str(opp_id),
                "voucher_id": str(voucher_id),
                "reference_id": ref_id,
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
        res = handle_reconcile_payment_link(session, t, gateway, "worker_recon", 1)
        assert res is True

    # Assert: Exactly 1 link on gateway, voucher EXECUTED, opp AWAITING_SETTLEMENT
    assert len(gateway._payment_links) == 1
    with session_factory() as session:
        v = session.get(ExecutionRecordORM, voucher_id)
        assert v is not None
        assert v.execution_status == ExecutionStatus.EXECUTED.value
        assert v.external_reference_id == plink.id

        o = session.get(RecoveryOpportunityORM, opp_id)
        assert o is not None
        assert o.current_state == OpportunityState.AWAITING_SETTLEMENT.value


def test_crash_window_w5_crash_before_phase_3_commit_reconciled(session_factory):
    """W5: Crash during or immediately prior to Phase 3 database commit.

    Durable state: Gateway has link, local session rolled back.
    Recovery: Reconciler resolves link, updates voucher to EXECUTED and opp to AWAITING_SETTLEMENT.
    """
    gateway = DeterministicSimulatorAdapter()
    ref_id = "ref_w5_commit_crash"
    plink = gateway.create_payment_link(
        amount_subunits=50000,
        currency="INR",
        reference_id=ref_id,
        description="W5 crash",
        customer=GatewayCustomerInfo(),
    )

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
            name="M",
            default_currency="INR",
            timezone="Asia/Kolkata",
            idempotency_salt="s",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        customer = CustomerORM(
            id=customer_id,
            merchant_id=merchant_id,
            external_customer_id="c_w5",
            phone_hash="p",
            email_hash="e",
            risk_tier=1,
            created_at=utc_now(),
        )
        attempt = PaymentAttemptORM(
            id=attempt_id,
            customer_id=customer_id,
            razorpay_payment_id="pay_w5",
            razorpay_order_id="order_w5",
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
            order_id="order_w5",
            initial_attempt_id=attempt_id,
            latest_attempt_id=attempt_id,
            amount_at_risk_subunits=50000,
            currency="INR",
            current_state=OpportunityState.ACTION_EXECUTING.value,
            failure_category="TRANSIENT_NETWORK",
            organic_recovery_estimate=0.4,
            failure_attempt_count=1,
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
            idempotency_key="idem_w5",
            reference_id=ref_id,
            intervention_type="DIRECT_PAYMENT_LINK_SMS",
            execution_status=ExecutionStatus.CLAIMED.value,
            claimed_at=utc_now() - timedelta(seconds=120),
        )
        recon_task = TaskQueueORM(
            id=recon_task_id,
            task_type="RECONCILE_PAYMENT_LINK",
            payload={
                "opportunity_id": str(opp_id),
                "voucher_id": str(voucher_id),
                "reference_id": ref_id,
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
        res = handle_reconcile_payment_link(session, t, gateway, "worker_recon", 1)
        assert res is True

    with session_factory() as session:
        v = session.get(ExecutionRecordORM, voucher_id)
        assert v is not None
        assert v.execution_status == ExecutionStatus.EXECUTED.value
        assert v.external_reference_id == plink.id

        o = session.get(RecoveryOpportunityORM, opp_id)
        assert o is not None
        assert o.current_state == OpportunityState.AWAITING_SETTLEMENT.value


def test_crash_window_w6_crash_around_task_finalization_idempotent(session_factory):
    """W6: Business state committed, but worker crashed before task completion.

    Durable state: Voucher EXECUTED, task expired RUNNING.
    Recovery: Subsequent execution or reconciler acknowledges already executed voucher and
    completes task idempotently.
    """
    gateway = DeterministicSimulatorAdapter()
    ref_id = "ref_w6_finalization"
    plink = gateway.create_payment_link(
        amount_subunits=50000,
        currency="INR",
        reference_id=ref_id,
        description="W6 finalization",
        customer=GatewayCustomerInfo(),
    )

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
            name="M",
            default_currency="INR",
            timezone="Asia/Kolkata",
            idempotency_salt="s",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        customer = CustomerORM(
            id=customer_id,
            merchant_id=merchant_id,
            external_customer_id="c_w6",
            phone_hash="p",
            email_hash="e",
            risk_tier=1,
            created_at=utc_now(),
        )
        attempt = PaymentAttemptORM(
            id=attempt_id,
            customer_id=customer_id,
            razorpay_payment_id="pay_w6",
            razorpay_order_id="order_w6",
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
            order_id="order_w6",
            initial_attempt_id=attempt_id,
            latest_attempt_id=attempt_id,
            amount_at_risk_subunits=50000,
            currency="INR",
            current_state=OpportunityState.AWAITING_SETTLEMENT.value,
            failure_category="TRANSIENT_NETWORK",
            organic_recovery_estimate=0.4,
            failure_attempt_count=1,
        )
        decision = RecoveryDecisionORM(
            id=decision_id,
            opportunity_id=opp_id,
            decision_type="AUTHORIZED",
            policy_evaluation_details={},
            explanation="Approved",
        )
        # Voucher was already marked EXECUTED before worker died!
        voucher = ExecutionRecordORM(
            id=voucher_id,
            decision_id=decision_id,
            attempt_index=1,
            idempotency_key="idem_w6",
            reference_id=ref_id,
            external_reference_id=plink.id,
            intervention_type="DIRECT_PAYMENT_LINK_SMS",
            execution_status=ExecutionStatus.EXECUTED.value,
            executed_at=utc_now() - timedelta(seconds=60),
        )
        recon_task = TaskQueueORM(
            id=recon_task_id,
            task_type="RECONCILE_PAYMENT_LINK",
            payload={
                "opportunity_id": str(opp_id),
                "voucher_id": str(voucher_id),
                "reference_id": ref_id,
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
        res = handle_reconcile_payment_link(session, t, gateway, "worker_recon", 1)
        assert res is True

    with session_factory() as session:
        t = session.get(TaskQueueORM, recon_task_id)
        assert t is not None
        assert t.status == "COMPLETED"
