"""End-to-end integration tests for Razorpay webhook ingestion engine."""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient
from lift.api.app import create_app
from lift.core.types import AttemptStatus, OpportunityState
from lift.gateway.simulator_adapter import DeterministicSimulatorAdapter
from lift.gateway.types import GatewayCustomerInfo
from lift.storage.base import Base
from lift.storage.database import create_db_engine, get_session_factory
from lift.storage.orm_models import (
    PaymentAttemptORM,
    PaymentEvidenceORM,
    RecoveryOpportunityORM,
    TaskQueueORM,
    WebhookEventORM,
)
from lift.webhooks.reference import generate_reference_id
from lift.webhooks.router import get_db_session_factory

WEBHOOK_SECRET = "e2e_webhook_secret_key_888"


@pytest.fixture
def e2e_context(monkeypatch):
    monkeypatch.setenv("LIFT_ENV", "test")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    engine = create_db_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = get_session_factory(engine)

    app = create_app()
    app.dependency_overrides[get_db_session_factory] = lambda: factory

    with TestClient(app) as client:
        yield client, factory

    Base.metadata.drop_all(engine)
    engine.dispose()


def make_signed_body(
    event_name: str, entity_data: dict, entity_type: str = "payment"
) -> tuple[bytes, str]:
    payload = {
        "event": event_name,
        "payload": {entity_type: {"entity": entity_data}},
    }
    raw = json.dumps(payload).encode("utf-8")
    sig = hmac.new(WEBHOOK_SECRET.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return raw, sig


def test_e2e_webhook_ingestion_lifecycle(e2e_context):
    """Full end-to-end test from inbound HTTP webhooks to database state and task queue."""
    client, session_factory = e2e_context

    # 1. Inbound payment.failed webhook
    raw_fail, sig_fail = make_signed_body(
        "payment.failed",
        {
            "id": "pay_e2e_001",
            "order_id": "order_e2e_100",
            "amount": 45000,
            "currency": "INR",
            "method": "card",
            "error_code": "BAD_REQUEST_ERROR",
            "error_description": "Payment authentication failed",
        },
    )

    resp_fail = client.post(
        "/api/v1/webhooks/razorpay",
        headers={
            "x-razorpay-event-id": "evt_e2e_fail_01",
            "x-razorpay-signature": sig_fail,
        },
        content=raw_fail,
    )
    assert resp_fail.status_code == 200
    assert resp_fail.json()["status"] == "accepted"

    # Verify DB state in clean session
    with session_factory() as session:
        # Webhook event recorded & processed
        evt = session.query(WebhookEventORM).filter_by(event_id="evt_e2e_fail_01").first()
        assert evt is not None
        assert evt.status == "PROCESSED"
        assert evt.event_type == "payment.failed"

        # Payment attempt recorded
        att = session.query(PaymentAttemptORM).filter_by(razorpay_payment_id="pay_e2e_001").first()
        assert att is not None
        assert att.status == "failed"
        assert att.amount_subunits == 45000

        # Opportunity initialized
        opp = session.query(RecoveryOpportunityORM).filter_by(order_id="order_e2e_100").first()
        assert opp is not None
        assert opp.current_state == "OPEN"
        assert opp.failure_attempt_count == 1
        opp_id = opp.id

        # Task enqueued in same transaction
        tasks = session.query(TaskQueueORM).filter_by(task_type="EVALUATE_OPPORTUNITY").all()
        assert len(tasks) == 1
        assert tasks[0].payload["opportunity_id"] == str(opp_id)
        assert tasks[0].status == "QUEUED"

    # 2. Inbound payment.captured webhook on the same payment attempt
    raw_cap, sig_cap = make_signed_body(
        "payment.captured",
        {
            "id": "pay_e2e_001",
            "order_id": "order_e2e_100",
            "amount": 45000,
            "currency": "INR",
            "method": "card",
        },
    )

    resp_cap = client.post(
        "/api/v1/webhooks/razorpay",
        headers={
            "x-razorpay-event-id": "evt_e2e_cap_01",
            "x-razorpay-signature": sig_cap,
        },
        content=raw_cap,
    )
    assert resp_cap.status_code == 200
    assert resp_cap.json()["status"] == "accepted"

    # Verify transition to RECOVERED and evidence creation
    with session_factory() as session:
        opp = session.query(RecoveryOpportunityORM).filter_by(id=opp_id).first()
        assert opp.current_state == OpportunityState.RECOVERED

        att = session.query(PaymentAttemptORM).filter_by(razorpay_payment_id="pay_e2e_001").first()
        assert att.status == AttemptStatus.CAPTURED

        evidence = (
            session.query(PaymentEvidenceORM).filter_by(razorpay_payment_id="pay_e2e_001").first()
        )
        assert evidence is not None
        assert evidence.captured_amount_subunits == 45000

        cancel_tasks = session.query(TaskQueueORM).filter_by(task_type="CANCEL_PAYMENT_LINK").all()
        assert len(cancel_tasks) == 1

    # 3. Duplicate delivery of payment.captured returns duplicate_acknowledged
    resp_dup = client.post(
        "/api/v1/webhooks/razorpay",
        headers={
            "x-razorpay-event-id": "evt_e2e_cap_01",
            "x-razorpay-signature": sig_cap,
        },
        content=raw_cap,
    )
    assert resp_dup.status_code == 200
    assert resp_dup.json()["status"] == "duplicate_acknowledged"


def test_payment_link_reconciliation_via_reference_id(e2e_context):
    """Verify end-to-end Payment Link creation in simulator and webhook payment correlation."""
    client, session_factory = e2e_context
    simulator = DeterministicSimulatorAdapter(seed_prefix="e2e")

    # Step A: Ingest initial payment failure to create opportunity
    raw_fail, sig_fail = make_signed_body(
        "payment.failed",
        {
            "id": "pay_link_test_01",
            "order_id": "order_link_test_01",
            "amount": 80000,
            "currency": "INR",
        },
    )
    client.post(
        "/api/v1/webhooks/razorpay",
        headers={"x-razorpay-event-id": "evt_link_init", "x-razorpay-signature": sig_fail},
        content=raw_fail,
    )

    with session_factory() as session:
        opp = session.query(RecoveryOpportunityORM).filter_by(order_id="order_link_test_01").first()
        assert opp is not None
        opp_id = opp.id

    # Step B: Create payment link using deterministic reference_id
    ref_id = generate_reference_id(opp_id, 1)
    link_res = simulator.create_payment_link(
        amount_subunits=80000,
        currency="INR",
        reference_id=ref_id,
        description="Recovery Link for Order",
        customer=GatewayCustomerInfo(name="Test E2E Customer"),
        notes={"opportunity_id": str(opp_id), "attempt_index": "1"},
    )
    assert link_res.reference_id == ref_id

    # Verify discovery via reference_id
    discovered = simulator.fetch_payment_link_by_reference_id(ref_id)
    assert discovered is not None
    assert discovered.id == link_res.id

    # Step C: Deliver payment_link.paid webhook with reference_id and notes
    raw_paid, sig_paid = make_signed_body(
        "payment_link.paid",
        {
            "id": link_res.id,
            "reference_id": ref_id,
            "amount_paid": 80000,
            "notes": {"opportunity_id": str(opp_id), "attempt_index": "1"},
        },
        entity_type="payment_link",
    )
    resp_paid = client.post(
        "/api/v1/webhooks/razorpay",
        headers={"x-razorpay-event-id": "evt_plink_paid_e2e", "x-razorpay-signature": sig_paid},
        content=raw_paid,
    )
    assert resp_paid.status_code == 200
    assert resp_paid.json()["status"] == "accepted"

    # Step D: Verify opportunity transitioned to RECOVERED and evidence persisted
    with session_factory() as session:
        opp_after = session.query(RecoveryOpportunityORM).filter_by(id=opp_id).first()
        assert opp_after.current_state == OpportunityState.RECOVERED

        evidence = (
            session.query(PaymentEvidenceORM).filter_by(razorpay_payment_id=link_res.id).first()
        )
        assert evidence is not None
        assert evidence.captured_amount_subunits == 80000
