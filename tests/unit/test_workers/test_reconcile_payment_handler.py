"""Unit tests for RECONCILE_PAYMENT task handler."""

from uuid import uuid4

import pytest
from lift.core.types import OpportunityState
from lift.gateway.simulator_adapter import DeterministicSimulatorAdapter
from lift.storage.base import Base, utc_now
from lift.storage.database import create_db_engine, get_session_factory
from lift.storage.orm_models import (
    CustomerORM,
    MerchantORM,
    PaymentAttemptORM,
    PaymentEvidenceORM,
    RecoveryOpportunityORM,
    TaskQueueORM,
)
from lift.workers.handlers.reconcile_payment import handle_reconcile_payment


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


def test_reconcile_payment_captured_transitions_to_recovered(session_factory):
    """Test payment.captured is authoritative recovery evidence

    transitioning opportunity to RECOVERED.
    """
    gateway = DeterministicSimulatorAdapter()
    payment_id = "pay_cap_999"
    # Seed payment in simulator
    gateway._payments[payment_id] = {
        "id": payment_id,
        "amount": 450000,
        "currency": "INR",
        "status": "captured",
        "order_id": "order_cap_999",
        "method": "upi",
    }

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
            external_customer_id="c_cap",
            phone_hash="p",
            email_hash="e",
            risk_tier=1,
            created_at=utc_now(),
        )
        attempt = PaymentAttemptORM(
            id=attempt_id,
            customer_id=customer_id,
            razorpay_payment_id=payment_id,
            razorpay_order_id="order_cap_999",
            attempt_sequence=1,
            amount_subunits=450000,
            currency="INR",
            payment_method="upi",
            status="failed",  # Initially failed
            gateway_created_at=utc_now(),
            raw_payload={},
        )
        opp = RecoveryOpportunityORM(
            id=opp_id,
            merchant_id=merchant_id,
            customer_id=customer_id,
            order_id="order_cap_999",
            initial_attempt_id=attempt_id,
            latest_attempt_id=attempt_id,
            amount_at_risk_subunits=450000,
            currency="INR",
            current_state=OpportunityState.AWAITING_SETTLEMENT.value,
            failure_category="TRANSIENT_NETWORK",
            organic_recovery_estimate=0.4,
            failure_attempt_count=1,
        )
        t = TaskQueueORM(
            id=task_id,
            task_type="RECONCILE_PAYMENT",
            payload={"payment_id": payment_id, "opportunity_id": str(opp_id)},
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
        session.add(t)
        session.commit()

    with session_factory() as session:
        t = session.get(TaskQueueORM, task_id)
        assert t is not None
        res = handle_reconcile_payment(session, t, gateway, "w1", 1)
        assert res is True

    with session_factory() as session:
        o = session.get(RecoveryOpportunityORM, opp_id)
        assert o is not None
        assert o.current_state == OpportunityState.RECOVERED.value

        att = session.get(PaymentAttemptORM, attempt_id)
        assert att is not None
        assert att.status == "captured"

        ev = (
            session.query(PaymentEvidenceORM)
            .filter(PaymentEvidenceORM.razorpay_payment_id == payment_id)
            .first()
        )
        assert ev is not None
        assert ev.captured_amount_subunits == 450000


def test_reconcile_payment_authorized_does_not_recover(session_factory):
    """Test STRICT AUTHORITY INVARIANT: payment.authorized != RECOVERED."""
    gateway = DeterministicSimulatorAdapter()
    payment_id = "pay_auth_999"
    gateway._payments[payment_id] = {
        "id": payment_id,
        "amount": 450000,
        "currency": "INR",
        "status": "authorized",
        "order_id": "order_auth_999",
        "method": "card",
    }

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
            external_customer_id="c_auth",
            phone_hash="p",
            email_hash="e",
            risk_tier=1,
            created_at=utc_now(),
        )
        attempt = PaymentAttemptORM(
            id=attempt_id,
            customer_id=customer_id,
            razorpay_payment_id=payment_id,
            razorpay_order_id="order_auth_999",
            attempt_sequence=1,
            amount_subunits=450000,
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
            order_id="order_auth_999",
            initial_attempt_id=attempt_id,
            latest_attempt_id=attempt_id,
            amount_at_risk_subunits=450000,
            currency="INR",
            current_state=OpportunityState.AWAITING_SETTLEMENT.value,
            failure_category="TRANSIENT_NETWORK",
            organic_recovery_estimate=0.4,
            failure_attempt_count=1,
        )
        t = TaskQueueORM(
            id=task_id,
            task_type="RECONCILE_PAYMENT",
            payload={"payment_id": payment_id, "opportunity_id": str(opp_id)},
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
        session.add(t)
        session.commit()

    with session_factory() as session:
        t = session.get(TaskQueueORM, task_id)
        assert t is not None
        res = handle_reconcile_payment(session, t, gateway, "w1", 1)
        assert res is True

    with session_factory() as session:
        o = session.get(RecoveryOpportunityORM, opp_id)
        assert o is not None
        # MUST NOT transition to RECOVERED! Remains in AWAITING_SETTLEMENT
        assert o.current_state == OpportunityState.AWAITING_SETTLEMENT.value
        assert o.current_state != OpportunityState.RECOVERED.value

        att = session.get(PaymentAttemptORM, attempt_id)
        assert att is not None
        assert att.status == "authorized"

        # Zero payment evidence recorded
        ev_count = (
            session.query(PaymentEvidenceORM)
            .filter(PaymentEvidenceORM.razorpay_payment_id == payment_id)
            .count()
        )
        assert ev_count == 0


def test_reconcile_payment_stale_worker_fenced_and_rolled_back(session_factory):
    """Verify that a stale worker cannot commit reconcile mutations, and rolls back cleanly."""
    gateway = DeterministicSimulatorAdapter()
    payment_id = "pay_stale_rec_001"
    gateway._payments[payment_id] = {
        "id": payment_id,
        "amount": 350000,
        "currency": "INR",
        "status": "captured",
        "order_id": "order_stale_rec_001",
        "method": "card",
    }

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
            external_customer_id="c_stale",
            phone_hash="p",
            email_hash="e",
            risk_tier=1,
            created_at=utc_now(),
        )
        attempt = PaymentAttemptORM(
            id=attempt_id,
            customer_id=customer_id,
            razorpay_payment_id=payment_id,
            razorpay_order_id="order_stale_rec_001",
            attempt_sequence=1,
            amount_subunits=350000,
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
            order_id="order_stale_rec_001",
            initial_attempt_id=attempt_id,
            latest_attempt_id=attempt_id,
            amount_at_risk_subunits=350000,
            currency="INR",
            current_state=OpportunityState.AWAITING_SETTLEMENT.value,
            failure_category="TRANSIENT_NETWORK",
            organic_recovery_estimate=0.4,
            failure_attempt_count=1,
        )
        # Task was reclaimed by worker_valid with lease_version=2
        t = TaskQueueORM(
            id=task_id,
            task_type="RECONCILE_PAYMENT",
            payload={"payment_id": payment_id, "opportunity_id": str(opp_id)},
            status="RUNNING",
            priority=10,
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
        session.add(t)
        session.commit()

    # 1. Stale worker runs with old lease_version=1
    with session_factory() as session:
        t_stale = session.get(TaskQueueORM, task_id)
        assert t_stale is not None
        res_stale = handle_reconcile_payment(
            session=session,
            task=t_stale,
            gateway=gateway,
            worker_id="worker_stale",
            claimed_lease_version=1,
        )
        assert res_stale is False

    # Verify zero business mutations committed to DB
    with session_factory() as session:
        o = session.get(RecoveryOpportunityORM, opp_id)
        assert o is not None
        assert o.current_state == OpportunityState.AWAITING_SETTLEMENT.value

        att = session.get(PaymentAttemptORM, attempt_id)
        assert att is not None
        assert att.status == "failed"

        ev_count = (
            session.query(PaymentEvidenceORM)
            .filter(PaymentEvidenceORM.razorpay_payment_id == payment_id)
            .count()
        )
        assert ev_count == 0

        t = session.get(TaskQueueORM, task_id)
        assert t is not None
        assert t.lease_version == 2
        assert t.locked_by == "worker_valid"
        assert t.status == "RUNNING"

    # 2. Current valid worker processes task successfully
    with session_factory() as session:
        t_valid = session.get(TaskQueueORM, task_id)
        assert t_valid is not None
        res_valid = handle_reconcile_payment(
            session=session,
            task=t_valid,
            gateway=gateway,
            worker_id="worker_valid",
            claimed_lease_version=2,
        )
        assert res_valid is True

    # Valid worker mutations are persisted
    with session_factory() as session:
        o = session.get(RecoveryOpportunityORM, opp_id)
        assert o is not None
        assert o.current_state == OpportunityState.RECOVERED.value

        att = session.get(PaymentAttemptORM, attempt_id)
        assert att is not None
        assert att.status == "captured"

        ev = (
            session.query(PaymentEvidenceORM)
            .filter(PaymentEvidenceORM.razorpay_payment_id == payment_id)
            .first()
        )
        assert ev is not None
        assert ev.signature_hash == f"reconciled_gateway_fetch:{payment_id}"
        assert not ev.signature_hash.startswith("sha256=")

        t = session.get(TaskQueueORM, task_id)
        assert t is not None
        assert t.status == "COMPLETED"
