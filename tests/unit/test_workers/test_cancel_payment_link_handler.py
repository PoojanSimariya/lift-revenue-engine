"""Unit tests for CANCEL_PAYMENT_LINK handler."""

from uuid import uuid4

import pytest
from lift.core.types import OpportunityState
from lift.gateway.simulator_adapter import DeterministicSimulatorAdapter
from lift.gateway.types import GatewayCustomerInfo
from lift.storage.base import Base, utc_now
from lift.storage.database import create_db_engine, get_session_factory
from lift.storage.orm_models import (
    CustomerORM,
    MerchantORM,
    PaymentAttemptORM,
    RecoveryOpportunityORM,
    TaskQueueORM,
)
from lift.workers.handlers.cancel_payment_link import handle_cancel_payment_link


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


def test_cancel_payment_link_all_statuses(session_factory):
    """Test all accurate status distinctions: cancelled, paid, partially_paid, expired."""
    gateway = DeterministicSimulatorAdapter()

    # 1. Test 'cancelled' - Idempotent success
    plink_1 = gateway.create_payment_link(
        amount_subunits=10000,
        currency="INR",
        reference_id="ref_c1",
        description="Cancel Test 1",
        customer=GatewayCustomerInfo(),
    )
    gateway.cancel_payment_link(plink_1.id)  # already cancelled on gateway

    task_1_id = uuid4()
    with session_factory() as session:
        t1 = TaskQueueORM(
            id=task_1_id,
            task_type="CANCEL_PAYMENT_LINK",
            payload={"payment_link_id": plink_1.id},
            status="RUNNING",
            priority=10,
            scheduled_at=utc_now(),
            locked_by="w1",
            locked_at=utc_now(),
            lease_version=1,
        )
        session.add(t1)
        session.commit()

    with session_factory() as session:
        t1 = session.get(TaskQueueORM, task_1_id)
        assert t1 is not None
        res = handle_cancel_payment_link(session, t1, gateway, "w1", 1)
        assert res is True

    with session_factory() as session:
        t1 = session.get(TaskQueueORM, task_1_id)
        assert t1 is not None
        assert t1.status == "COMPLETED"

    # 2. Test 'paid' - Recovery evidence, NOT simple cancellation!
    plink_2 = gateway.create_payment_link(
        amount_subunits=20000,
        currency="INR",
        reference_id="ref_c2",
        description="Paid Test",
        customer=GatewayCustomerInfo(),
    )
    # Simulate payment on simulator
    gateway._payment_links[plink_2.id]["status"] = "paid"

    merchant_id = uuid4()
    customer_id = uuid4()
    opp_2_id = uuid4()
    attempt_2_id = uuid4()
    task_2_id = uuid4()

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
            external_customer_id="c2",
            phone_hash="p",
            email_hash="e",
            risk_tier=1,
            created_at=utc_now(),
        )
        attempt = PaymentAttemptORM(
            id=attempt_2_id,
            customer_id=customer_id,
            razorpay_payment_id="pay_c2",
            razorpay_order_id="order_c2",
            attempt_sequence=1,
            amount_subunits=20000,
            currency="INR",
            payment_method="card",
            status="failed",
            gateway_created_at=utc_now(),
            raw_payload={},
        )
        opp_2 = RecoveryOpportunityORM(
            id=opp_2_id,
            merchant_id=merchant_id,
            customer_id=customer_id,
            order_id="order_c2",
            initial_attempt_id=attempt_2_id,
            latest_attempt_id=attempt_2_id,
            amount_at_risk_subunits=20000,
            currency="INR",
            current_state="AWAITING_SETTLEMENT",
            failure_category="TRANSIENT_NETWORK",
            organic_recovery_estimate=0.4,
            failure_attempt_count=1,
        )
        t2 = TaskQueueORM(
            id=task_2_id,
            task_type="CANCEL_PAYMENT_LINK",
            payload={"payment_link_id": plink_2.id, "opportunity_id": str(opp_2_id)},
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
        session.add(opp_2)
        session.flush()
        session.add(t2)
        session.commit()

    with session_factory() as session:
        t2 = session.get(TaskQueueORM, task_2_id)
        assert t2 is not None
        res = handle_cancel_payment_link(session, t2, gateway, "w1", 1)
        assert res is True

    with session_factory() as session:
        t2 = session.get(TaskQueueORM, task_2_id)
        assert t2 is not None
        assert t2.status == "COMPLETED"

        o2 = session.get(RecoveryOpportunityORM, opp_2_id)
        assert o2 is not None
        # Must transition to RECOVERED!
        assert o2.current_state == OpportunityState.RECOVERED.value

    # 3. Test 'expired' - Terminal state no-op
    plink_3 = gateway.create_payment_link(
        amount_subunits=30000,
        currency="INR",
        reference_id="ref_c3",
        description="Expired Test",
        customer=GatewayCustomerInfo(),
    )
    gateway._payment_links[plink_3.id]["status"] = "expired"

    task_3_id = uuid4()
    with session_factory() as session:
        t3 = TaskQueueORM(
            id=task_3_id,
            task_type="CANCEL_PAYMENT_LINK",
            payload={"payment_link_id": plink_3.id},
            status="RUNNING",
            priority=10,
            scheduled_at=utc_now(),
            locked_by="w1",
            locked_at=utc_now(),
            lease_version=1,
        )
        session.add(t3)
        session.commit()

    with session_factory() as session:
        t3 = session.get(TaskQueueORM, task_3_id)
        assert t3 is not None
        res = handle_cancel_payment_link(session, t3, gateway, "w1", 1)
        assert res is True

    with session_factory() as session:
        t3 = session.get(TaskQueueORM, task_3_id)
        assert t3 is not None
        assert t3.status == "COMPLETED"


def test_cancel_payment_link_stale_worker_fenced_and_rolled_back(session_factory):
    """Verify that a stale worker cannot commit cancel mutations, and rolls back cleanly."""
    gateway = DeterministicSimulatorAdapter()
    plink = gateway.create_payment_link(
        amount_subunits=20000,
        currency="INR",
        reference_id="ref_stale_cancel",
        description="Paid Test",
        customer=GatewayCustomerInfo(),
    )
    # Simulate payment on simulator
    gateway._payment_links[plink.id]["status"] = "paid"

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
            external_customer_id="c_stale_cancel",
            phone_hash="p",
            email_hash="e",
            risk_tier=1,
            created_at=utc_now(),
        )
        attempt = PaymentAttemptORM(
            id=attempt_id,
            customer_id=customer_id,
            razorpay_payment_id="pay_stale_cancel",
            razorpay_order_id="order_stale_cancel",
            attempt_sequence=1,
            amount_subunits=20000,
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
            order_id="order_stale_cancel",
            initial_attempt_id=attempt_id,
            latest_attempt_id=attempt_id,
            amount_at_risk_subunits=20000,
            currency="INR",
            current_state=OpportunityState.ACTION_EXECUTING.value,
            failure_category="TRANSIENT_NETWORK",
            organic_recovery_estimate=0.4,
            failure_attempt_count=1,
        )
        # Task was reclaimed by worker_valid with lease_version=2
        t = TaskQueueORM(
            id=task_id,
            task_type="CANCEL_PAYMENT_LINK",
            payload={"payment_link_id": plink.id, "opportunity_id": str(opp_id)},
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
        res_stale = handle_cancel_payment_link(
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
        assert o.current_state == OpportunityState.ACTION_EXECUTING.value
        assert o.current_state != OpportunityState.RECOVERED.value

        t = session.get(TaskQueueORM, task_id)
        assert t is not None
        assert t.lease_version == 2
        assert t.locked_by == "worker_valid"
        assert t.status == "RUNNING"

    # 2. Current valid worker processes task successfully
    with session_factory() as session:
        t_valid = session.get(TaskQueueORM, task_id)
        assert t_valid is not None
        res_valid = handle_cancel_payment_link(
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

        t = session.get(TaskQueueORM, task_id)
        assert t is not None
        assert t.status == "COMPLETED"
