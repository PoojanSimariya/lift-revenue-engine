"""SQLAlchemy 2.0 ORM models corresponding strictly to docs/DATA_MODEL.md."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from lift.storage.base import Base, utc_now

# JSON variant: JSONB on PostgreSQL, standard JSON on SQLite
JSONType = JSON().with_variant(JSONB, "postgresql")


class MerchantORM(Base):
    """Represents the merchant organization configuring the LIFT engine."""

    __tablename__ = "merchants"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    default_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Kolkata")
    idempotency_salt: Mapped[str] = mapped_column(String(64), nullable=False)
    razorpay_key_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    razorpay_key_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    razorpay_webhook_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class CustomerORM(Base):
    """Tracks customer contact history and contact fatigue metrics."""

    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    external_customer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    phone_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    risk_tier: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    lifetime_recovery_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lifetime_failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rolling_contacts_7d: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_contacted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "merchant_id", "external_customer_id", name="uq_merchant_external_customer"
        ),
        Index("idx_customers_merchant_id", "merchant_id"),
    )


class PolicyRuleORM(Base):
    """Deterministic merchant guardrails."""

    __tablename__ = "policy_rules"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    rule_type: Mapped[str] = mapped_column(String(64), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (Index("idx_policy_rules_merchant", "merchant_id", "is_active"),)


class WebhookEventORM(Base):
    """Deduplicates incoming Razorpay webhooks using x-razorpay-event-id."""

    __tablename__ = "webhook_events"

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("idx_webhook_events_status", "status"),)


class TaskQueueORM(Base):
    """PostgreSQL-backed task queue processed via SELECT ... FOR UPDATE SKIP LOCKED."""

    __tablename__ = "task_queue"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="QUEUED")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    locked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (Index("idx_task_queue_poll", "status", "scheduled_at", "priority"),)


class PaymentAttemptORM(Base):
    """Immutable record of each payment transaction event ingested from Razorpay."""

    __tablename__ = "payment_attempts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    recovery_opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey(
            "recovery_opportunities.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_payment_attempts_opp",
        ),
        nullable=True,
    )
    razorpay_payment_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    razorpay_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    amount_subunits: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    payment_method: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_step: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    gateway_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        Index("idx_payment_attempts_order", "razorpay_order_id"),
        Index("idx_payment_attempts_opp", "recovery_opportunity_id"),
    )


class RecoveryOpportunityORM(Base):
    """The central stateful aggregate managing the recovery lifecycle for a failed order."""

    __tablename__ = "recovery_opportunities"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("customers.id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    initial_attempt_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("payment_attempts.id"), nullable=False
    )
    latest_attempt_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("payment_attempts.id"), nullable=False
    )
    amount_at_risk_subunits: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    current_state: Mapped[str] = mapped_column(String(32), nullable=False, default="OPEN")
    failure_category: Mapped[str] = mapped_column(String(64), nullable=False)
    organic_recovery_estimate: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    organic_estimation_source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="SEGMENT_PRIOR"
    )
    failure_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    total_interventions_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_contacts_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    execution_claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        UniqueConstraint("merchant_id", "order_id", name="uq_opportunity_order"),
        Index("idx_recovery_opps_state", "merchant_id", "current_state"),
        Index("idx_recovery_opps_opened", "opened_at"),
    )


class InterventionCandidateORM(Base):
    """Evaluated intervention slate for an opportunity."""

    __tablename__ = "intervention_candidates"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("recovery_opportunities.id", ondelete="CASCADE"), nullable=False
    )
    intervention_type: Mapped[str] = mapped_column(String(64), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    p_recovery: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    p_organic: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    direct_cost_subunits: Mapped[int] = mapped_column(BigInteger, nullable=False)
    friction_cost_subunits: Mapped[int] = mapped_column(BigInteger, nullable=False)
    risk_penalty_subunits: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expected_net_value_subunits: Mapped[int] = mapped_column(BigInteger, nullable=False)
    confidence_score: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    contact_fatigue: Mapped[float] = mapped_column(Numeric(7, 4), nullable=False, default=0.0)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (Index("idx_candidates_opp", "opportunity_id"),)


class RecoveryDecisionORM(Base):
    """The authoritative policy resolution produced by the Deterministic Policy Engine."""

    __tablename__ = "recovery_decisions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("recovery_opportunities.id", ondelete="CASCADE"), nullable=False
    )
    selected_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("intervention_candidates.id"), nullable=True
    )
    decision_type: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_evaluation_details: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    blocked_reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (Index("idx_decisions_opp", "opportunity_id"),)


class ExecutionRecordORM(Base):
    """Execution vouchers guaranteeing idempotency and tracking two-phase dispatch."""

    __tablename__ = "execution_records"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    decision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("recovery_decisions.id", ondelete="CASCADE"), nullable=False
    )
    attempt_index: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    reference_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    intervention_type: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_status: Mapped[str] = mapped_column(String(32), nullable=False, default="CLAIMED")
    external_reference_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_execution_idempotency", "idempotency_key"),
        Index("idx_execution_reference", "reference_id"),
    )


class PaymentEvidenceORM(Base):
    """Cryptographic proof confirming payment settlement."""

    __tablename__ = "payment_evidences"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("recovery_opportunities.id", ondelete="CASCADE"), nullable=False
    )
    razorpay_payment_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    signature_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    captured_amount_subunits: Mapped[int] = mapped_column(BigInteger, nullable=False)
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (Index("idx_payment_evidence_opp", "opportunity_id"),)


class AuditEventORM(Base):
    """Append-only log of all system transitions, ensuring full multi-tenant traceability."""

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("merchants.id", ondelete="CASCADE"), nullable=False
    )
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    event_name: Mapped[str] = mapped_column(String(64), nullable=False)
    state_before: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    state_after: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False, default="SYSTEM")
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSONType, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        Index("idx_audit_trace", "trace_id"),
        Index("idx_audit_merchant", "merchant_id"),
        Index("idx_audit_aggregate", "aggregate_type", "aggregate_id"),
    )
