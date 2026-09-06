"""Unit tests for customer contact reservation and serialization."""

from uuid import uuid4

import pytest
from lift.core.types import ExecutionStatus
from lift.storage.base import Base, utc_now
from lift.storage.database import create_db_engine, get_session_factory
from lift.storage.orm_models import (
    CustomerORM,
    ExecutionRecordORM,
    MerchantORM,
    PaymentAttemptORM,
    RecoveryDecisionORM,
    RecoveryOpportunityORM,
)
from lift.storage.repositories.customer import CustomerRepository


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


def test_contact_reservation_semantics(session_factory):
    """Test F: CLAIMED execution reserves contact slot; FAILED releases it;

    asserts no cross-worker decrement races.
    """
    merchant_id = uuid4()
    customer_id = uuid4()
    opp_1_id = uuid4()
    opp_2_id = uuid4()
    attempt_1_id = uuid4()
    attempt_2_id = uuid4()
    decision_1_id = uuid4()
    decision_2_id = uuid4()
    voucher_1_id = uuid4()
    voucher_2_id = uuid4()

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
            external_customer_id="cust_multi_123",
            phone_hash="hash_p",
            email_hash="hash_e",
            risk_tier=1,
            created_at=utc_now(),
        )
        attempt_1 = PaymentAttemptORM(
            id=attempt_1_id,
            customer_id=customer_id,
            razorpay_payment_id="pay_101",
            razorpay_order_id="order_101",
            attempt_sequence=1,
            amount_subunits=50000,
            currency="INR",
            payment_method="card",
            status="failed",
            gateway_created_at=utc_now(),
            raw_payload={},
        )
        attempt_2 = PaymentAttemptORM(
            id=attempt_2_id,
            customer_id=customer_id,
            razorpay_payment_id="pay_102",
            razorpay_order_id="order_102",
            attempt_sequence=1,
            amount_subunits=60000,
            currency="INR",
            payment_method="card",
            status="failed",
            gateway_created_at=utc_now(),
            raw_payload={},
        )
        opp_1 = RecoveryOpportunityORM(
            id=opp_1_id,
            merchant_id=merchant_id,
            customer_id=customer_id,
            order_id="order_101",
            initial_attempt_id=attempt_1_id,
            latest_attempt_id=attempt_1_id,
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
        opp_2 = RecoveryOpportunityORM(
            id=opp_2_id,
            merchant_id=merchant_id,
            customer_id=customer_id,
            order_id="order_102",
            initial_attempt_id=attempt_2_id,
            latest_attempt_id=attempt_2_id,
            amount_at_risk_subunits=60000,
            currency="INR",
            current_state="ACTION_EXECUTING",
            failure_category="AUTHENTICATION_TIMEOUT",
            organic_recovery_estimate=0.2,
            failure_attempt_count=1,
            total_interventions_count=1,
            total_contacts_count=1,
            execution_claimed_at=utc_now(),
        )
        decision_1 = RecoveryDecisionORM(
            id=decision_1_id,
            opportunity_id=opp_1_id,
            decision_type="AUTHORIZED",
            policy_evaluation_details={},
            explanation="Approved 1",
        )
        decision_2 = RecoveryDecisionORM(
            id=decision_2_id,
            opportunity_id=opp_2_id,
            decision_type="AUTHORIZED",
            policy_evaluation_details={},
            explanation="Approved 2",
        )
        session.add(merchant)
        session.flush()
        session.add(customer)
        session.flush()
        session.add_all([attempt_1, attempt_2])
        session.flush()
        session.add_all([opp_1, opp_2])
        session.flush()
        session.add_all([decision_1, decision_2])
        session.commit()

    # 1. Initially, 0 contacts
    with session_factory() as session:
        cust_repo = CustomerRepository(session)
        assert cust_repo.count_active_contacts_7d(customer_id) == 0

    # 2. Worker A reserves a contact slot (voucher 1 in CLAIMED)
    with session_factory() as session:
        v1 = ExecutionRecordORM(
            id=voucher_1_id,
            decision_id=decision_1_id,
            attempt_index=1,
            idempotency_key="idem_res_001",
            reference_id="ref_res_001",
            intervention_type="DIRECT_PAYMENT_LINK_SMS",
            execution_status=ExecutionStatus.CLAIMED.value,
            claimed_at=utc_now(),
        )
        session.add(v1)
        session.commit()

    with session_factory() as session:
        cust_repo = CustomerRepository(session)
        # CLAIMED reserves the contact slot!
        assert cust_repo.count_active_contacts_7d(customer_id) == 1

    # 3. Worker B allocates a concurrent contact slot (voucher 2 in CLAIMED)
    with session_factory() as session:
        v2 = ExecutionRecordORM(
            id=voucher_2_id,
            decision_id=decision_2_id,
            attempt_index=1,
            idempotency_key="idem_res_002",
            reference_id="ref_res_002",
            intervention_type="DIRECT_PAYMENT_LINK_WHATSAPP",
            execution_status=ExecutionStatus.CLAIMED.value,
            claimed_at=utc_now(),
        )
        session.add(v2)
        session.commit()

    with session_factory() as session:
        cust_repo = CustomerRepository(session)
        # 2 active contacts
        assert cust_repo.count_active_contacts_7d(customer_id) == 2

    # 4. Worker A's dispatch fails remotely or is reconciled as missing -> marked FAILED
    with session_factory() as session:
        v1_orm = session.get(ExecutionRecordORM, voucher_1_id)
        assert v1_orm is not None
        v1_orm.execution_status = ExecutionStatus.FAILED.value
        session.commit()

    # 5. Assert Worker B's contact reservation remains intact
    # (count drops from 2 to 1, exactly Worker B's)
    with session_factory() as session:
        cust_repo = CustomerRepository(session)
        active_count = cust_repo.count_active_contacts_7d(customer_id)
        assert active_count == 1

    # 6. Worker B successfully settles Phase 3 (EXECUTED)
    with session_factory() as session:
        v2_orm = session.get(ExecutionRecordORM, voucher_2_id)
        assert v2_orm is not None
        v2_orm.execution_status = ExecutionStatus.EXECUTED.value
        v2_orm.external_reference_id = "plink_202"
        v2_orm.executed_at = utc_now()
        session.commit()

    # 7. Still counts Worker B's contact (1)
    with session_factory() as session:
        cust_repo = CustomerRepository(session)
        assert cust_repo.count_active_contacts_7d(customer_id) == 1
