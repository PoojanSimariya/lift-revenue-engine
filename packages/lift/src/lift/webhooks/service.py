"""Webhook application service executing ingestion, correlation, and monotonic transitions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from lift.core.errors import (
    DataValidationError,
    InvalidSignatureError,
)
from lift.core.types import (
    AttemptStatus,
    ExecutionStatus,
    FailureCategory,
    OpportunityState,
    PaymentMethod,
)
from lift.domain.models import (
    Customer,
    Merchant,
    PaymentAttempt,
    PaymentEvidence,
    RecoveryOpportunity,
)
from lift.domain.state_machine import OpportunityStateMachine
from lift.storage.repositories import (
    CustomerRepository,
    DecisionRepository,
    ExecutionRecordRepository,
    MerchantRepository,
    OpportunityRepository,
    PaymentAttemptRepository,
    PaymentEvidenceRepository,
    TaskQueueRepository,
    WebhookEventRepository,
)
from lift.webhooks.verifier import verify_webhook_signature


@dataclass(frozen=True)
class WebhookProcessingResult:
    """Outcome of processing an inbound webhook."""

    status: str  # "accepted", "duplicate_acknowledged"
    event_id: str
    event_type: str | None = None
    duplicate: bool = False
    opportunity_id: str | None = None
    attempt_id: str | None = None
    task_enqueued: str | None = None


class WebhookIngestionService:
    """Transactional webhook application service.

    Adheres strictly to the side-effect boundary:
    Executes ZERO external network requests during webhook handling.
    Downstream actions are committed as durable tasks in task_queue.
    """

    def __init__(
        self,
        session: Session,
        webhook_secret: str,
    ) -> None:
        self.session = session
        self.webhook_secret = webhook_secret

        # Repositories bound to active session
        self.webhook_repo = WebhookEventRepository(session)
        self.opportunity_repo = OpportunityRepository(session)
        self.attempt_repo = PaymentAttemptRepository(session)
        self.task_repo = TaskQueueRepository(session)
        self.evidence_repo = PaymentEvidenceRepository(session)
        self.voucher_repo = ExecutionRecordRepository(session)
        self.decision_repo = DecisionRepository(session)
        self.merchant_repo = MerchantRepository(session)
        self.customer_repo = CustomerRepository(session)

    def _resolve_or_create_customer_context(
        self,
        merchant_id: UUID | None = None,
        payment_entity: dict[str, Any] | None = None,
        payment_id: str | None = None,
    ) -> tuple[Merchant, Customer]:
        """Resolve or provision merchant and customer for initial payment events.

        Derives stable customer identity from Razorpay customer_id, contact, or email if present.
        If customer identity cannot be resolved from the gateway payload, isolates the unresolved
        context using the payment identifier to avoid distorting contact fatigue across
        unrelated customers.
        """
        merchant = None
        if merchant_id:
            merchant = self.merchant_repo.get_by_id(merchant_id)
        if not merchant:
            merchant = self.merchant_repo.get_first()
        if not merchant:
            merchant = self.merchant_repo.create(
                Merchant(
                    name="Default Merchant",
                    default_currency="INR",
                    timezone="Asia/Kolkata",
                    idempotency_salt="default_salt_00000000000000000000",
                )
            )

        external_cust_id: str
        phone_hash: str | None = None
        email_hash: str | None = None

        if payment_entity:
            rzp_cust_id = payment_entity.get("customer_id")
            contact = payment_entity.get("contact")
            email = payment_entity.get("email")

            if rzp_cust_id and str(rzp_cust_id).strip():
                external_cust_id = str(rzp_cust_id).strip()
            elif contact and str(contact).strip():
                phone_raw = str(contact).strip()
                phone_hash = hashlib.sha256(phone_raw.encode("utf-8")).hexdigest()
                external_cust_id = f"cust_contact_{phone_hash[:16]}"
            elif email and str(email).strip():
                email_raw = str(email).strip().lower()
                email_hash = hashlib.sha256(email_raw.encode("utf-8")).hexdigest()
                external_cust_id = f"cust_email_{email_hash[:16]}"
            elif payment_id:
                external_cust_id = f"cust_unresolved_{payment_id}"
            else:
                external_cust_id = f"cust_unresolved_{uuid4().hex[:12]}"
        elif payment_id:
            external_cust_id = f"cust_unresolved_{payment_id}"
        else:
            external_cust_id = "cust_default_webhook"

        customer = self.customer_repo.get_by_external_id(merchant.id, external_cust_id)
        if not customer:
            customer = self.customer_repo.create(
                Customer(
                    merchant_id=merchant.id,
                    external_customer_id=external_cust_id,
                    phone_hash=phone_hash,
                    email_hash=email_hash,
                    risk_tier=1,
                )
            )
        return merchant, customer

    def _get_or_create_default_context(
        self,
        merchant_id: UUID | None = None,
    ) -> tuple[Merchant, Customer]:
        """Backward-compatible context resolution."""
        return self._resolve_or_create_customer_context(merchant_id=merchant_id)

    def _parse_method(self, method_str: str | None) -> PaymentMethod:
        valid_methods = {m.value for m in PaymentMethod}
        if method_str and method_str in valid_methods:
            return PaymentMethod(method_str)
        return PaymentMethod.CARD

    def process_webhook(
        self,
        event_id: str,
        signature: str | None,
        raw_body: bytes,
        merchant_id: UUID | None = None,
    ) -> WebhookProcessingResult:
        """Process inbound Razorpay webhook inside the caller's transaction."""
        # 1. Cryptographic Signature Verification
        if not verify_webhook_signature(raw_body, signature, self.webhook_secret):
            raise InvalidSignatureError("Webhook HMAC-SHA256 signature verification failed")

        # 2. Parse payload JSON
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except Exception as exc:
            msg = "Malformed JSON body"
            raise DataValidationError("raw_body", raw_body.decode("utf-8", "ignore"), msg) from exc

        event_type = payload.get("event", "unknown")

        # 3. Mandatory Deduplication
        is_new, _ = self.webhook_repo.record_event_if_absent(event_id, event_type, payload)
        if not is_new:
            return WebhookProcessingResult(
                status="duplicate_acknowledged",
                event_id=event_id,
                event_type=event_type,
                duplicate=True,
            )

        task_enqueued: str | None = None
        target_opp_id: str | None = None
        target_attempt_id: str | None = None

        # 4. Handle Payment Entity Events
        p_entity = payload.get("payload", {}).get("payment", {}).get("entity")
        if p_entity and isinstance(p_entity, dict):
            payment_id = p_entity["id"]
            order_id = p_entity.get("order_id")
            amount_subunits = p_entity.get("amount", 0)
            currency = p_entity.get("currency", "INR")
            method_str = p_entity.get("method")

            target_attempt_status = "failed"
            if event_type == "payment.captured":
                target_attempt_status = "captured"
            elif event_type == "payment.authorized":
                target_attempt_status = "authorized"

            existing_attempt = self.attempt_repo.get_by_payment_id(payment_id)
            opp: RecoveryOpportunity | None = None

            if existing_attempt is not None:
                updated_attempt = self.attempt_repo.update_status_monotonic(
                    attempt_id=existing_attempt.id,
                    new_status=target_attempt_status,
                    error_code=p_entity.get("error_code"),
                    error_description=p_entity.get("error_description"),
                    raw_payload=payload,
                )
                target_attempt_id = str(updated_attempt.id)

                if existing_attempt.recovery_opportunity_id:
                    opp = self.opportunity_repo.get_by_id(existing_attempt.recovery_opportunity_id)
                if not opp and order_id:
                    opp = self.opportunity_repo.find_by_order_id(order_id)
            else:
                opp = self.opportunity_repo.find_by_order_id(order_id) if order_id else None

                if opp is None:
                    merchant, customer = self._resolve_or_create_customer_context(
                        merchant_id=merchant_id,
                        payment_entity=p_entity,
                        payment_id=payment_id,
                    )
                    initial_opp = RecoveryOpportunity(
                        merchant_id=merchant.id,
                        customer_id=customer.id,
                        order_id=order_id or f"order_auto_{payment_id}",
                        initial_attempt_id=uuid4(),
                        latest_attempt_id=uuid4(),
                        amount_at_risk_subunits=amount_subunits or 10000,
                        currency=currency,
                        current_state=OpportunityState.OPEN,
                        failure_category=FailureCategory.TRANSIENT_NETWORK,
                        organic_recovery_estimate=0.25,
                        failure_attempt_count=1 if event_type == "payment.failed" else 0,
                    )
                    method = self._parse_method(method_str)
                    initial_att = PaymentAttempt(
                        customer_id=customer.id,
                        recovery_opportunity_id=None,
                        razorpay_payment_id=payment_id,
                        razorpay_order_id=order_id or initial_opp.order_id,
                        attempt_sequence=1,
                        amount_subunits=amount_subunits or 10000,
                        currency=currency,
                        payment_method=method,
                        status=AttemptStatus(target_attempt_status),
                        error_code=p_entity.get("error_code"),
                        error_description=p_entity.get("error_description"),
                        error_source=p_entity.get("error_source"),
                        error_step=p_entity.get("error_step"),
                        error_reason=p_entity.get("error_reason"),
                        gateway_created_at=datetime.now(timezone.utc),
                        raw_payload=payload,
                    )
                    opp, created_att = self.opportunity_repo.create_with_initial_attempt(
                        initial_opp, initial_att
                    )
                    target_attempt_id = str(created_att.id)
                else:
                    if event_type == "payment.failed":
                        opp.failure_attempt_count += 1
                        opp = self.opportunity_repo.update(opp)

                    method = self._parse_method(method_str)
                    new_att = PaymentAttempt(
                        customer_id=opp.customer_id,
                        recovery_opportunity_id=opp.id,
                        razorpay_payment_id=payment_id,
                        razorpay_order_id=order_id or opp.order_id,
                        attempt_sequence=opp.failure_attempt_count + 1,
                        amount_subunits=amount_subunits or opp.amount_at_risk_subunits,
                        currency=currency or opp.currency,
                        payment_method=method,
                        status=AttemptStatus(target_attempt_status),
                        error_code=p_entity.get("error_code"),
                        error_description=p_entity.get("error_description"),
                        error_source=p_entity.get("error_source"),
                        error_step=p_entity.get("error_step"),
                        error_reason=p_entity.get("error_reason"),
                        gateway_created_at=datetime.now(timezone.utc),
                        raw_payload=payload,
                    )
                    opp, created_att = self.opportunity_repo.associate_additional_attempt(
                        opp.id, new_att
                    )
                    target_attempt_id = str(created_att.id)

            if opp:
                target_opp_id = str(opp.id)
                if event_type == "payment.failed":
                    OpportunityStateMachine.handle_payment_failed(
                        opp, event_name="payment.failed", increment_attempt_count=False
                    )
                    self.opportunity_repo.update(opp)
                    task = self.task_repo.enqueue_task(
                        task_type="EVALUATE_OPPORTUNITY",
                        payload={"opportunity_id": str(opp.id), "payment_id": payment_id},
                    )
                    task_enqueued = task.task_type
                elif event_type == "payment.authorized":
                    OpportunityStateMachine.handle_payment_authorized(
                        opp, event_name="payment.authorized"
                    )
                    self.opportunity_repo.update(opp)
                elif event_type == "payment.captured":
                    OpportunityStateMachine.handle_payment_captured(
                        opp, event_name="payment.captured"
                    )
                    self.opportunity_repo.update(opp)

                    if not self.evidence_repo.get_by_payment_id(payment_id):
                        captured_amount = amount_subunits or opp.amount_at_risk_subunits
                        self.evidence_repo.create(
                            PaymentEvidence(
                                opportunity_id=opp.id,
                                razorpay_payment_id=payment_id,
                                event_type="payment.captured",
                                signature_hash=signature or "unsigned_webhook",
                                captured_amount_subunits=captured_amount,
                            )
                        )
                    task = self.task_repo.enqueue_task(
                        task_type="CANCEL_PAYMENT_LINK",
                        payload={"opportunity_id": str(opp.id), "payment_id": payment_id},
                    )
                    task_enqueued = task.task_type

        # 5. Handle Order Paid Events
        elif event_type == "order.paid":
            order_entity = payload.get("payload", {}).get("order", {}).get("entity", {})
            order_id = order_entity.get("id")
            if order_id:
                opp = self.opportunity_repo.find_by_order_id(order_id)
                if opp:
                    target_opp_id = str(opp.id)
                    OpportunityStateMachine.handle_payment_captured(opp, event_name="order.paid")
                    self.opportunity_repo.update(opp)
                    task = self.task_repo.enqueue_task(
                        task_type="CANCEL_PAYMENT_LINK",
                        payload={"opportunity_id": str(opp.id), "order_id": order_id},
                    )
                    task_enqueued = task.task_type

        # 6. Handle Payment Link Events (Algorithm 2)
        pl_entity = payload.get("payload", {}).get("payment_link", {}).get("entity")
        if pl_entity and isinstance(pl_entity, dict):
            reference_id = pl_entity.get("reference_id")
            link_id = pl_entity.get("id")
            notes = pl_entity.get("notes") or {}

            opp = None
            voucher = None

            # Step 1: Local persisted mapping
            if reference_id:
                voucher = self.voucher_repo.get_by_reference_id(reference_id)
            if not voucher and link_id:
                voucher = self.voucher_repo.get_by_external_reference_id(link_id)

            if voucher:
                decision = self.decision_repo.get_by_id(voucher.decision_id)
                if decision:
                    opp = self.opportunity_repo.get_by_id(decision.opportunity_id)

            # Step 2: Trusted payload metadata notes
            if not opp and isinstance(notes, dict) and "opportunity_id" in notes:
                try:
                    opp_uuid = UUID(notes["opportunity_id"])
                    opp = self.opportunity_repo.get_by_id(opp_uuid)
                except (ValueError, TypeError):
                    opp = None

            # Step 3: Fallback (Neither Local Mapping Nor Notes Present) - DO NOT GUESS
            if not opp:
                task = self.task_repo.enqueue_task(
                    task_type="RECONCILE_PAYMENT_LINK",
                    payload={
                        "payment_link_id": link_id,
                        "reference_id": reference_id,
                        "event": event_type,
                    },
                )
                self.webhook_repo.mark_processed(event_id)
                return WebhookProcessingResult(
                    status="accepted",
                    event_id=event_id,
                    event_type=event_type,
                    task_enqueued=task.task_type,
                )

            target_opp_id = str(opp.id)
            if event_type == "payment_link.paid":
                OpportunityStateMachine.handle_payment_captured(opp, event_name="payment_link.paid")
                self.opportunity_repo.update(opp)

                if voucher:
                    voucher.execution_status = ExecutionStatus.EXECUTED
                    voucher.executed_at = datetime.now(timezone.utc)
                    self.voucher_repo.update(voucher)

                evidence_id = link_id or f"plink_{event_id}"
                if not self.evidence_repo.get_by_payment_id(evidence_id):
                    amt = pl_entity.get("amount_paid", opp.amount_at_risk_subunits)
                    self.evidence_repo.create(
                        PaymentEvidence(
                            opportunity_id=opp.id,
                            razorpay_payment_id=evidence_id,
                            event_type="payment_link.paid",
                            signature_hash=signature or "unsigned_webhook",
                            captured_amount_subunits=amt,
                        )
                    )
                task = self.task_repo.enqueue_task(
                    task_type="CANCEL_PAYMENT_LINK",
                    payload={"opportunity_id": str(opp.id), "payment_link_id": link_id},
                )
                task_enqueued = task.task_type
            elif event_type == "payment_link.partially_paid":
                OpportunityStateMachine.handle_payment_link_partially_paid(
                    opp, event_name="payment_link.partially_paid"
                )
                self.opportunity_repo.update(opp)
            elif event_type == "payment_link.expired":
                OpportunityStateMachine.handle_payment_link_expired(
                    opp, retry_budget_remaining=True
                )
                self.opportunity_repo.update(opp)

        self.webhook_repo.mark_processed(event_id)

        return WebhookProcessingResult(
            status="accepted",
            event_id=event_id,
            event_type=event_type,
            opportunity_id=target_opp_id,
            attempt_id=target_attempt_id,
            task_enqueued=task_enqueued,
        )
