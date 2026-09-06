"""Unit tests for WebhookIngestionService: business attempts, monotonicity, and correlation."""

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone

import pytest
from lift.core.types import (
    AttemptStatus,
    DecisionType,
    FailureCategory,
    InterventionType,
    OpportunityState,
    PaymentMethod,
)
from lift.domain.models import (
    ExecutionRecord,
    RecoveryDecision,
    RecoveryOpportunity,
)
from lift.domain.models import (
    PaymentAttempt as DomainAttempt,
)
from lift.storage.base import Base
from lift.storage.database import create_db_engine, get_session_factory
from lift.storage.mappers import to_decision_orm, to_execution_record_orm
from lift.storage.orm_models import TaskQueueORM
from lift.storage.repositories import (
    OpportunityRepository,
    PaymentAttemptRepository,
    PaymentEvidenceRepository,
)
from lift.webhooks.reference import generate_reference_id
from lift.webhooks.service import WebhookIngestionService

SECRET = "test_webhook_secret_key_123"


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


def make_payload(event: str, entity_data: dict, entity_type: str = "payment") -> tuple[bytes, str]:
    """Helper to generate raw bytes and HMAC signature."""
    payload_dict = {"event": event, "payload": {entity_type: {"entity": entity_data}}}
    raw = json.dumps(payload_dict).encode("utf-8")
    sig = hmac.new(SECRET.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return raw, sig


def test_captured_attempt_does_not_regress_on_late_authorized(session):
    """Verify that a CAPTURED attempt does not regress to AUTHORIZED on delayed webhook."""
    service = WebhookIngestionService(session, SECRET)

    # 1. Capture event arrives first
    raw_cap, sig_cap = make_payload(
        "payment.captured",
        {"id": "pay_001", "order_id": "order_001", "amount": 50000, "status": "captured"},
    )
    res_cap = service.process_webhook("evt_cap_01", sig_cap, raw_cap)
    assert res_cap.status == "accepted"

    attempt_repo = PaymentAttemptRepository(session)
    att = attempt_repo.get_by_payment_id("pay_001")
    assert att is not None
    assert att.status == AttemptStatus.CAPTURED

    # 2. Delayed payment.authorized arrives for the same pay_001
    raw_auth, sig_auth = make_payload(
        "payment.authorized",
        {"id": "pay_001", "order_id": "order_001", "amount": 50000, "status": "authorized"},
    )
    res_auth = service.process_webhook("evt_auth_01", sig_auth, raw_auth)
    assert res_auth.status == "accepted"

    # Attempt MUST remain CAPTURED (monotonicity preserved)
    att_after = attempt_repo.get_by_payment_id("pay_001")
    assert att_after.status == AttemptStatus.CAPTURED

    # Opportunity remains RECOVERED
    opp_repo = OpportunityRepository(session)
    opp = opp_repo.find_by_order_id("order_001")
    assert opp.current_state == OpportunityState.RECOVERED


def test_captured_attempt_does_not_regress_on_late_failed(session):
    """Verify that a CAPTURED attempt does not regress to FAILED on delayed failure webhook."""
    service = WebhookIngestionService(session, SECRET)

    raw_cap, sig_cap = make_payload(
        "payment.captured",
        {"id": "pay_002", "order_id": "order_002", "amount": 50000, "status": "captured"},
    )
    service.process_webhook("evt_cap_02", sig_cap, raw_cap)

    # Delayed failure arrives for pay_002
    raw_fail, sig_fail = make_payload(
        "payment.failed",
        {"id": "pay_002", "order_id": "order_002", "amount": 50000, "status": "failed"},
    )
    service.process_webhook("evt_fail_02", sig_fail, raw_fail)

    attempt_repo = PaymentAttemptRepository(session)
    att = attempt_repo.get_by_payment_id("pay_002")
    assert att.status == AttemptStatus.CAPTURED

    opp_repo = OpportunityRepository(session)
    opp = opp_repo.find_by_order_id("order_002")
    assert opp.current_state == OpportunityState.RECOVERED


def test_authorized_then_captured_is_monotonic(session):
    """Verify failed -> authorized -> captured monotonic progression."""
    service = WebhookIngestionService(session, SECRET)
    attempt_repo = PaymentAttemptRepository(session)

    # 1. Failed
    raw1, sig1 = make_payload(
        "payment.failed",
        {"id": "pay_003", "order_id": "order_003", "amount": 10000},
    )
    service.process_webhook("evt_01", sig1, raw1)
    att1 = attempt_repo.get_by_payment_id("pay_003")
    assert att1.status == AttemptStatus.FAILED

    # 2. Authorized
    raw2, sig2 = make_payload(
        "payment.authorized",
        {"id": "pay_003", "order_id": "order_003", "amount": 10000},
    )
    service.process_webhook("evt_02", sig2, raw2)
    att2 = attempt_repo.get_by_payment_id("pay_003")
    assert att2.status == AttemptStatus.AUTHORIZED

    # 3. Captured
    raw3, sig3 = make_payload(
        "payment.captured",
        {"id": "pay_003", "order_id": "order_003", "amount": 10000},
    )
    service.process_webhook("evt_03", sig3, raw3)
    att3 = attempt_repo.get_by_payment_id("pay_003")
    assert att3.status == AttemptStatus.CAPTURED


def test_duplicate_failure_different_event_ids_does_not_increment_failure_count(session):
    """Verify that different event IDs for the SAME failed payment_id increment count once."""
    service = WebhookIngestionService(session, SECRET)
    opp_repo = OpportunityRepository(session)

    # First webhook for pay_100
    raw1, sig1 = make_payload(
        "payment.failed",
        {"id": "pay_100", "order_id": "order_100", "amount": 20000},
    )
    service.process_webhook("evt_first_delivery", sig1, raw1)

    opp = opp_repo.find_by_order_id("order_100")
    assert opp.failure_attempt_count == 1

    # Second webhook with a DIFFERENT event_id describing the SAME payment attempt pay_100
    raw2, sig2 = make_payload(
        "payment.failed",
        {"id": "pay_100", "order_id": "order_100", "amount": 20000},
    )
    service.process_webhook("evt_second_delivery_different_id", sig2, raw2)

    opp_after = opp_repo.find_by_order_id("order_100")
    # Must NOT increment failure_attempt_count again!
    assert opp_after.failure_attempt_count == 1


def test_new_payment_id_failure_increments_failure_count_once(session):
    """Verify that a second payment attempt with a different payment_id increments the counter."""
    service = WebhookIngestionService(session, SECRET)
    opp_repo = OpportunityRepository(session)

    raw1, sig1 = make_payload(
        "payment.failed",
        {"id": "pay_101", "order_id": "order_101", "amount": 20000},
    )
    service.process_webhook("evt_p1", sig1, raw1)
    opp = opp_repo.find_by_order_id("order_101")
    assert opp.failure_attempt_count == 1

    raw2, sig2 = make_payload(
        "payment.failed",
        {"id": "pay_102", "order_id": "order_101", "amount": 20000},
    )
    service.process_webhook("evt_p2", sig2, raw2)
    opp_after = opp_repo.find_by_order_id("order_101")
    assert opp_after.failure_attempt_count == 2


def test_failed_p1_then_failed_p2_counts_two_attempts(session):
    """Verify that P1 failed then P2 failed creates 2 attempts and failure_attempt_count == 2."""
    service = WebhookIngestionService(session, SECRET)
    attempt_repo = PaymentAttemptRepository(session)
    opp_repo = OpportunityRepository(session)

    raw1, sig1 = make_payload(
        "payment.failed",
        {"id": "pay_A", "order_id": "order_multi", "amount": 30000},
    )
    service.process_webhook("evt_A", sig1, raw1)

    raw2, sig2 = make_payload(
        "payment.failed",
        {"id": "pay_B", "order_id": "order_multi", "amount": 30000},
    )
    service.process_webhook("evt_B", sig2, raw2)

    opp = opp_repo.find_by_order_id("order_multi")
    attempts = attempt_repo.list_by_opportunity_id(opp.id)
    assert len(attempts) == 2
    assert opp.failure_attempt_count == 2


def test_duplicate_same_event_id(session):
    """Verify that exact duplicate delivery returns duplicate_acknowledged without reprocessing."""
    service = WebhookIngestionService(session, SECRET)

    raw, sig = make_payload(
        "payment.failed",
        {"id": "pay_dup", "order_id": "order_dup", "amount": 10000},
    )
    res1 = service.process_webhook("evt_dup_01", sig, raw)
    assert res1.status == "accepted"
    assert not res1.duplicate

    # Duplicate delivery
    res2 = service.process_webhook("evt_dup_01", sig, raw)
    assert res2.status == "duplicate_acknowledged"
    assert res2.duplicate is True


def test_payment_captured_enqueues_cancellation_task(session):
    """Verify that payment.captured enqueues CANCEL_PAYMENT_LINK task in the transaction."""
    service = WebhookIngestionService(session, SECRET)

    raw, sig = make_payload(
        "payment.captured",
        {"id": "pay_cap_cancel", "order_id": "order_cap_cancel", "amount": 50000},
    )
    service.process_webhook("evt_cap_cancel", sig, raw)

    queued = session.query(TaskQueueORM).all()
    cancel_tasks = [t for t in queued if t.task_type == "CANCEL_PAYMENT_LINK"]
    assert len(cancel_tasks) == 1
    assert cancel_tasks[0].payload["payment_id"] == "pay_cap_cancel"


def test_payment_authorized_does_not_recover(session):
    """Verify payment.authorized moves to AWAITING_SETTLEMENT, not RECOVERED."""
    service = WebhookIngestionService(session, SECRET)
    merchant, customer = service._get_or_create_default_context()
    opp_repo = OpportunityRepository(session)

    # Opportunity in expected valid state: ACTION_EXECUTING
    opp_repo.create_with_initial_attempt(
        RecoveryOpportunity(
            merchant_id=merchant.id,
            customer_id=customer.id,
            order_id="order_auth_only",
            initial_attempt_id=uuid.uuid4(),
            latest_attempt_id=uuid.uuid4(),
            amount_at_risk_subunits=50000,
            currency="INR",
            current_state=OpportunityState.ACTION_EXECUTING,
            failure_category=FailureCategory.TRANSIENT_NETWORK,
            organic_recovery_estimate=0.25,
            failure_attempt_count=1,
        ),
        DomainAttempt(
            customer_id=customer.id,
            recovery_opportunity_id=None,
            razorpay_payment_id="pay_auth_prev",
            razorpay_order_id="order_auth_only",
            amount_subunits=50000,
            payment_method=PaymentMethod.CARD,
            status=AttemptStatus.FAILED,
            gateway_created_at=datetime.now(timezone.utc),
        ),
    )[0]

    raw, sig = make_payload(
        "payment.authorized",
        {"id": "pay_auth_only", "order_id": "order_auth_only", "amount": 50000},
    )
    service.process_webhook("evt_auth_only", sig, raw)

    opp_after = opp_repo.find_by_order_id("order_auth_only")
    assert opp_after is not None
    assert opp_after.current_state == OpportunityState.AWAITING_SETTLEMENT
    assert opp_after.current_state != OpportunityState.RECOVERED


def test_payment_link_paid_correlates_from_local_mapping(session):
    """Verify payment_link.paid correlates using local execution record mapping."""
    service = WebhookIngestionService(session, SECRET)
    merchant, customer = service._get_or_create_default_context()

    opp_repo = OpportunityRepository(session)
    opp = opp_repo.create_with_initial_attempt(
        RecoveryOpportunity(
            merchant_id=merchant.id,
            customer_id=customer.id,
            order_id="order_plink_map",
            initial_attempt_id=uuid.uuid4(),
            latest_attempt_id=uuid.uuid4(),
            amount_at_risk_subunits=100000,
            failure_category=FailureCategory.TRANSIENT_NETWORK,
            organic_recovery_estimate=0.20,
        ),
        DomainAttempt(
            customer_id=customer.id,
            recovery_opportunity_id=None,
            razorpay_payment_id="pay_init_map",
            razorpay_order_id="order_plink_map",
            amount_subunits=100000,
            payment_method=PaymentMethod.CARD,
            status=AttemptStatus.FAILED,
            gateway_created_at=datetime.now(timezone.utc),
        ),
    )[0]

    # Create decision and execution voucher
    dec_id = uuid.uuid4()
    dec_orm = to_decision_orm(
        RecoveryDecision(
            id=dec_id,
            opportunity_id=opp.id,
            decision_type=DecisionType.AUTHORIZED,
        )
    )
    session.add(dec_orm)
    session.flush()

    ref_id = generate_reference_id(opp.id, 1)
    voucher = ExecutionRecord(
        decision_id=dec_id,
        attempt_index=1,
        idempotency_key="idem_001",
        reference_id=ref_id,
        intervention_type=InterventionType.DIRECT_PAYMENT_LINK_SMS,
        external_reference_id="plink_map_001",
    )
    session.add(to_execution_record_orm(voucher))
    session.flush()

    raw, sig = make_payload(
        "payment_link.paid",
        {"id": "plink_map_001", "reference_id": ref_id, "amount_paid": 100000},
        entity_type="payment_link",
    )
    res = service.process_webhook("evt_plink_paid_01", sig, raw)
    assert res.status == "accepted"

    opp_after = opp_repo.get_by_id(opp.id)
    assert opp_after.current_state == OpportunityState.RECOVERED


def test_payment_link_paid_correlates_from_notes_when_local_execution_record_missing(session):
    """Verify correlation from payload notes when no local execution record exists."""
    service = WebhookIngestionService(session, SECRET)
    merchant, customer = service._get_or_create_default_context()

    opp_repo = OpportunityRepository(session)
    opp = opp_repo.create_with_initial_attempt(
        RecoveryOpportunity(
            merchant_id=merchant.id,
            customer_id=customer.id,
            order_id="order_notes_test",
            initial_attempt_id=uuid.uuid4(),
            latest_attempt_id=uuid.uuid4(),
            amount_at_risk_subunits=75000,
            failure_category=FailureCategory.TRANSIENT_NETWORK,
            organic_recovery_estimate=0.20,
        ),
        DomainAttempt(
            customer_id=customer.id,
            recovery_opportunity_id=None,
            razorpay_payment_id="pay_notes_init",
            razorpay_order_id="order_notes_test",
            amount_subunits=75000,
            payment_method=PaymentMethod.CARD,
            status=AttemptStatus.FAILED,
            gateway_created_at=datetime.now(timezone.utc),
        ),
    )[0]

    # No execution record inserted locally!
    raw, sig = make_payload(
        "payment_link.paid",
        {
            "id": "plink_notes_999",
            "reference_id": "ref_unknown_hash_123",
            "amount_paid": 75000,
            "notes": {"opportunity_id": str(opp.id), "attempt_index": "1"},
        },
        entity_type="payment_link",
    )
    res = service.process_webhook("evt_notes_01", sig, raw)
    assert res.status == "accepted"

    opp_after = opp_repo.get_by_id(opp.id)
    assert opp_after.current_state == OpportunityState.RECOVERED


def test_payment_link_paid_without_any_correlation_does_not_guess_opportunity(session):
    """Verify that without mapping or notes, the engine does NOT guess; enqueues reconciliation."""
    service = WebhookIngestionService(session, SECRET)

    raw, sig = make_payload(
        "payment_link.paid",
        {
            "id": "plink_orphan_001",
            "reference_id": "ref_completely_unmapped",
            "amount_paid": 50000,
            "notes": {},
        },
        entity_type="payment_link",
    )
    res = service.process_webhook("evt_orphan_01", sig, raw)
    assert res.status == "accepted"
    assert res.task_enqueued == "RECONCILE_PAYMENT_LINK"

    tasks = session.query(TaskQueueORM).filter_by(task_type="RECONCILE_PAYMENT_LINK").all()
    assert len(tasks) == 1
    assert tasks[0].payload["payment_link_id"] == "plink_orphan_001"


def test_payment_link_partially_paid(session):
    """Verify payment_link.partially_paid records evidence but does NOT recover opportunity."""
    service = WebhookIngestionService(session, SECRET)
    merchant, customer = service._get_or_create_default_context()
    opp_repo = OpportunityRepository(session)

    opp = opp_repo.create_with_initial_attempt(
        RecoveryOpportunity(
            merchant_id=merchant.id,
            customer_id=customer.id,
            order_id="order_partial_test",
            initial_attempt_id=uuid.uuid4(),
            latest_attempt_id=uuid.uuid4(),
            amount_at_risk_subunits=100000,
            failure_category=FailureCategory.TRANSIENT_NETWORK,
            organic_recovery_estimate=0.20,
        ),
        DomainAttempt(
            customer_id=customer.id,
            recovery_opportunity_id=None,
            razorpay_payment_id="pay_partial_init",
            razorpay_order_id="order_partial_test",
            amount_subunits=100000,
            payment_method=PaymentMethod.CARD,
            status=AttemptStatus.FAILED,
            gateway_created_at=datetime.now(timezone.utc),
        ),
    )[0]

    raw, sig = make_payload(
        "payment_link.partially_paid",
        {
            "id": "plink_part_01",
            "reference_id": "ref_part_01",
            "amount_paid": 25000,
            "notes": {"opportunity_id": str(opp.id)},
        },
        entity_type="payment_link",
    )
    res = service.process_webhook("evt_part_01", sig, raw)
    assert res.status == "accepted"

    opp_after = opp_repo.get_by_id(opp.id)
    # MUST NOT be RECOVERED
    assert opp_after.current_state != OpportunityState.RECOVERED


def test_webhook_customer_context_resolution_and_isolation(session):
    """Verify customer context derivation, collision prevention, and unresolved isolation."""
    service = WebhookIngestionService(session, SECRET)
    opp_repo = OpportunityRepository(session)

    # 1. Resolvable customer A (by customer_id)
    raw1, sig1 = make_payload(
        "payment.failed",
        {
            "id": "pay_cust_a_1",
            "order_id": "order_cust_a_1",
            "amount": 20000,
            "customer_id": "cust_rzp_alpha",
        },
    )
    res1 = service.process_webhook("evt_cust_a_1", sig1, raw1)
    assert res1.status == "accepted"
    opp1 = opp_repo.find_by_order_id("order_cust_a_1")
    assert opp1 is not None

    # 2. Same resolvable customer A in another payment resolves to SAME customer
    raw2, sig2 = make_payload(
        "payment.failed",
        {
            "id": "pay_cust_a_2",
            "order_id": "order_cust_a_2",
            "amount": 30000,
            "customer_id": "cust_rzp_alpha",
        },
    )
    res2 = service.process_webhook("evt_cust_a_2", sig2, raw2)
    assert res2.status == "accepted"
    opp2 = opp_repo.find_by_order_id("order_cust_a_2")
    assert opp2 is not None
    assert opp1.customer_id == opp2.customer_id

    # 3. Different resolvable customer B does NOT collide with customer A
    raw3, sig3 = make_payload(
        "payment.failed",
        {
            "id": "pay_cust_b_1",
            "order_id": "order_cust_b_1",
            "amount": 40000,
            "customer_id": "cust_rzp_beta",
        },
    )
    res3 = service.process_webhook("evt_cust_b_1", sig3, raw3)
    assert res3.status == "accepted"
    opp3 = opp_repo.find_by_order_id("order_cust_b_1")
    assert opp3 is not None
    assert opp3.customer_id != opp1.customer_id

    # 4. Unresolved customer context does not collide or distort other unresolved customers
    raw_u1, sig_u1 = make_payload(
        "payment.failed",
        {
            "id": "pay_unres_01",
            "order_id": "order_unres_01",
            "amount": 15000,
        },
    )
    res_u1 = service.process_webhook("evt_unres_01", sig_u1, raw_u1)
    assert res_u1.status == "accepted"
    opp_u1 = opp_repo.find_by_order_id("order_unres_01")
    assert opp_u1 is not None

    raw_u2, sig_u2 = make_payload(
        "payment.failed",
        {
            "id": "pay_unres_02",
            "order_id": "order_unres_02",
            "amount": 15000,
        },
    )
    res_u2 = service.process_webhook("evt_unres_02", sig_u2, raw_u2)
    assert res_u2.status == "accepted"
    opp_u2 = opp_repo.find_by_order_id("order_unres_02")
    assert opp_u2 is not None

    # Crucial: Unresolved contexts MUST NOT share the same customer ID!
    assert opp_u1.customer_id != opp_u2.customer_id
    assert opp_u1.customer_id != opp1.customer_id


def test_webhook_captured_evidence_signature_integrity(session):
    """Verify that validated webhook retains the actual HMAC signature, not a fake fallback."""
    service = WebhookIngestionService(session, SECRET)
    evidence_repo = PaymentEvidenceRepository(session)

    raw, sig = make_payload(
        "payment.captured",
        {
            "id": "pay_sig_check_01",
            "order_id": "order_sig_check_01",
            "amount": 50000,
            "status": "captured",
        },
    )
    res = service.process_webhook("evt_sig_check_01", sig, raw)
    assert res.status == "accepted"

    ev = evidence_repo.get_by_payment_id("pay_sig_check_01")
    assert ev is not None
    assert ev.signature_hash == sig
    assert ev.signature_hash != "test_sig"
    assert not ev.signature_hash.startswith("reconciled_")
