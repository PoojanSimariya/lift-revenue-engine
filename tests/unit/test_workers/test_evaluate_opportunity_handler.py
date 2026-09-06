"""Unit tests for EVALUATE_OPPORTUNITY task handler."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from lift.core.types import ExecutionStatus, OpportunityState
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
from lift.workers.handlers.evaluate_opportunity import handle_evaluate_opportunity


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


def test_evaluate_opportunity_already_recovered_drops_task(session_factory):
    """Test that an already RECOVERED opportunity cleanly completes task without side effects."""
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
            external_customer_id="c1",
            phone_hash="p",
            email_hash="e",
            risk_tier=1,
            created_at=utc_now(),
        )
        attempt = PaymentAttemptORM(
            id=attempt_id,
            customer_id=customer_id,
            razorpay_payment_id="pay_rec_01",
            razorpay_order_id="order_rec_01",
            attempt_sequence=1,
            amount_subunits=50000,
            currency="INR",
            payment_method="card",
            status="captured",
            gateway_created_at=utc_now(),
            raw_payload={},
        )
        opp = RecoveryOpportunityORM(
            id=opp_id,
            merchant_id=merchant_id,
            customer_id=customer_id,
            order_id="order_rec_01",
            initial_attempt_id=attempt_id,
            latest_attempt_id=attempt_id,
            amount_at_risk_subunits=50000,
            currency="INR",
            current_state=OpportunityState.RECOVERED.value,  # Already recovered!
            failure_category="TRANSIENT_NETWORK",
            organic_recovery_estimate=0.4,
            failure_attempt_count=1,
        )
        task = TaskQueueORM(
            id=task_id,
            task_type="EVALUATE_OPPORTUNITY",
            payload={"opportunity_id": str(opp_id)},
            status="RUNNING",
            priority=10,
            scheduled_at=utc_now(),
            locked_by="w1",
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
        session.add(task)
        session.commit()

    with session_factory() as session:
        t = session.get(TaskQueueORM, task_id)
        assert t is not None
        res = handle_evaluate_opportunity(session, t, gateway, "w1", 1)
        assert res is True

    # Task is COMPLETED, no external link created
    with session_factory() as session:
        t = session.get(TaskQueueORM, task_id)
        assert t is not None
        assert t.status == "COMPLETED"
        assert len(gateway._payment_links) == 0


def test_evaluate_opportunity_dispatches_outreach(session_factory, monkeypatch):
    """Test standard evaluation: creates voucher, dispatches link,

    and advances opportunity to AWAITING_SETTLEMENT.
    """
    # Explicit daytime timestamp (14:00 Asia/Kolkata / 08:30 UTC) outside quiet hours
    daytime_utc = datetime(2026, 9, 5, 8, 30, tzinfo=timezone.utc)
    monkeypatch.setattr("lift.services.execution.utc_now", lambda: daytime_utc)

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
            external_customer_id="9999999999",
            phone_hash="p",
            email_hash="e",
            risk_tier=1,
            created_at=utc_now(),
        )
        attempt = PaymentAttemptORM(
            id=attempt_id,
            customer_id=customer_id,
            razorpay_payment_id="pay_disp_01",
            razorpay_order_id="order_disp_01",
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
            order_id="order_disp_01",
            initial_attempt_id=attempt_id,
            latest_attempt_id=attempt_id,
            amount_at_risk_subunits=50000,
            currency="INR",
            current_state=OpportunityState.OPEN.value,
            failure_category="AUTHENTICATION_TIMEOUT",
            organic_recovery_estimate=0.2,
            failure_attempt_count=1,
        )
        task = TaskQueueORM(
            id=task_id,
            task_type="EVALUATE_OPPORTUNITY",
            payload={"opportunity_id": str(opp_id)},
            status="RUNNING",
            priority=10,
            scheduled_at=utc_now(),
            locked_by="w1",
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
        session.add(task)
        session.commit()

    with session_factory() as session:
        t = session.get(TaskQueueORM, task_id)
        assert t is not None
        res = handle_evaluate_opportunity(session, t, gateway, "w1", 1)
        assert res is True

    # Verify: External link created
    assert len(gateway._payment_links) == 1
    plink_id = list(gateway._payment_links.keys())[0]

    with session_factory() as session:
        # Task completed
        t = session.get(TaskQueueORM, task_id)
        assert t is not None
        assert t.status == "COMPLETED"

        # Opportunity advanced to AWAITING_SETTLEMENT
        o = session.get(RecoveryOpportunityORM, opp_id)
        assert o is not None
        assert o.current_state == OpportunityState.AWAITING_SETTLEMENT.value

        # Execution record voucher updated to EXECUTED with external reference ID
        vouchers = (
            session.query(ExecutionRecordORM).filter(ExecutionRecordORM.task_id == task_id).all()
        )
        assert len(vouchers) == 1
        assert vouchers[0].execution_status == ExecutionStatus.EXECUTED.value
        assert vouchers[0].external_reference_id == plink_id
        assert vouchers[0].lease_version == 1


def test_internal_retry_schedule_has_no_outreach_uplift_and_phase1_fk_flush(session_factory):
    """Bug 1 & 2: Prove INTERNAL_RETRY_SCHEDULE does not receive outreach uplift

    and Phase 1 flushes RecoveryDecisionORM before ExecutionRecordORM.
    """
    gateway = DeterministicSimulatorAdapter()
    merchant_id = uuid4()
    customer_id = uuid4()
    opp_id = uuid4()
    attempt_id = uuid4()
    task_id = uuid4()
    daytime_utc = datetime(2026, 9, 5, 8, 30, tzinfo=timezone.utc)

    with session_factory() as session:
        merchant = MerchantORM(
            id=merchant_id,
            name="M",
            default_currency="INR",
            timezone="Asia/Kolkata",
            idempotency_salt="s",
            created_at=daytime_utc,
            updated_at=daytime_utc,
        )
        customer = CustomerORM(
            id=customer_id,
            merchant_id=merchant_id,
            external_customer_id="cust_no_uplift",
            phone_hash="p",
            email_hash="e",
            risk_tier=1,
            created_at=daytime_utc,
        )
        attempt = PaymentAttemptORM(
            id=attempt_id,
            customer_id=customer_id,
            razorpay_payment_id="pay_no_up",
            razorpay_order_id="order_no_up",
            attempt_sequence=1,
            amount_subunits=50000,
            currency="INR",
            payment_method="card",
            status="failed",
            gateway_created_at=daytime_utc,
            raw_payload={},
        )
        opp = RecoveryOpportunityORM(
            id=opp_id,
            merchant_id=merchant_id,
            customer_id=customer_id,
            order_id="order_no_up",
            initial_attempt_id=attempt_id,
            latest_attempt_id=attempt_id,
            amount_at_risk_subunits=50000,
            currency="INR",
            current_state=OpportunityState.OPEN.value,
            failure_category="AUTHENTICATION_TIMEOUT",
            organic_recovery_estimate=0.2,
            failure_attempt_count=1,
        )
        task = TaskQueueORM(
            id=task_id,
            task_type="EVALUATE_OPPORTUNITY",
            payload={"opportunity_id": str(opp_id)},
            status="RUNNING",
            priority=10,
            scheduled_at=daytime_utc,
            locked_by="w1",
            locked_at=daytime_utc,
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

    with session_factory() as session:
        from lift.core.types import DecisionType
        from lift.services.execution import ExecutionSafetyService

        safety = ExecutionSafetyService(session=session, gateway=gateway)
        res = safety.execute_phase_1(
            opportunity_id=opp_id,
            task_id=task_id,
            claimed_lease_version=1,
            worker_id="w1",
            eval_time=daytime_utc,
        )
        session.commit()

        assert res.should_dispatch is True
        assert res.voucher_id is not None

        # Verify: Decision was persisted and voucher references it without FK violation
        voucher = session.get(ExecutionRecordORM, res.voucher_id)
        assert voucher is not None
        assert voucher.execution_status == ExecutionStatus.CLAIMED.value
        decision = session.get(RecoveryDecisionORM, voucher.decision_id)
        assert decision is not None
        assert decision.decision_type == DecisionType.AUTHORIZED.value
        assert "DIRECT_PAYMENT_LINK" in decision.explanation


def test_phase_2_pre_call_lookup_ambiguous_error_prevents_creation(session_factory, mocker):
    """Test P0-3: Ambiguous pre-call lookup errors MUST NOT fall through to create."""
    from lift.core.errors import GatewayError, GatewayTimeoutError
    from lift.gateway.types import PaymentLinkStatus
    from lift.services.execution import ExecutionSafetyService, Phase1Result, Phase2OutcomeType

    gateway = DeterministicSimulatorAdapter()
    create_spy = mocker.spy(gateway, "create_payment_link")

    phase_1_result = Phase1Result(
        action_required=True,
        should_dispatch=True,
        opportunity_id=uuid4(),
        voucher_id=uuid4(),
        amount_subunits=50000,
        currency="INR",
        reference_id="ref_ambiguous_test_01",
    )

    with session_factory() as session:
        safety = ExecutionSafetyService(session=session, gateway=gateway)

        # 1. Case C1: GatewayTimeoutError on lookup -> UNKNOWN_OR_RETRYABLE, zero create calls!
        mocker.patch.object(
            gateway,
            "fetch_payment_link_by_reference_id",
            side_effect=GatewayTimeoutError("Connection to Razorpay timed out"),
        )
        res_timeout = safety.execute_phase_2(phase_1_result)
        assert res_timeout.outcome_type == Phase2OutcomeType.UNKNOWN_OR_RETRYABLE
        assert create_spy.call_count == 0

        # 2. Case C2: Network error on lookup -> UNKNOWN_OR_RETRYABLE, zero create calls!
        mocker.patch.object(
            gateway,
            "fetch_payment_link_by_reference_id",
            side_effect=GatewayError("Failed to connect", gateway_code="NETWORK_ERROR"),
        )
        res_network = safety.execute_phase_2(phase_1_result)
        assert res_network.outcome_type == Phase2OutcomeType.UNKNOWN_OR_RETRYABLE
        assert create_spy.call_count == 0

        # 3. Case C3: 5xx / 429 server error on lookup -> UNKNOWN_OR_RETRYABLE, zero create calls!
        mocker.patch.object(
            gateway,
            "fetch_payment_link_by_reference_id",
            side_effect=GatewayError("Internal Server Error", details={"status_code": 502}),
        )
        res_502 = safety.execute_phase_2(phase_1_result)
        assert res_502.outcome_type == Phase2OutcomeType.UNKNOWN_OR_RETRYABLE
        assert create_spy.call_count == 0

        # 4. Case A: Lookup finds existing link -> SUCCESS, zero create calls!
        existing_link = PaymentLinkStatus(
            id="plink_found_existing",
            amount=50000,
            currency="INR",
            status="created",
            short_url="https://rzp.io/i/found",
            reference_id="ref_ambiguous_test_01",
        )
        mocker.patch.object(
            gateway,
            "fetch_payment_link_by_reference_id",
            return_value=existing_link,
        )
        res_found = safety.execute_phase_2(phase_1_result)
        assert res_found.outcome_type == Phase2OutcomeType.SUCCESS
        assert res_found.external_reference_id == "plink_found_existing"
        assert create_spy.call_count == 0

        # 5. Case B: Lookup confirms absence (None) -> create succeeds!
        mocker.patch.object(
            gateway,
            "fetch_payment_link_by_reference_id",
            return_value=None,
        )
        res_create = safety.execute_phase_2(phase_1_result)
        assert res_create.outcome_type == Phase2OutcomeType.SUCCESS
        assert res_create.external_reference_id is not None
        assert create_spy.call_count == 1
