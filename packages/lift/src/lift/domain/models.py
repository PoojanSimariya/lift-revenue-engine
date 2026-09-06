"""Pure Pydantic v2 domain models for LIFT."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from lift.core.constants import INT64_MAX, INT64_MIN
from lift.core.errors import DataValidationError, TimeZoneError
from lift.core.types import (
    ActorType,
    AttemptStatus,
    DecisionType,
    ExecutionStatus,
    FailureCategory,
    InterventionType,
    OpportunityState,
    OrganicEstimationSource,
    PaymentMethod,
    RuleType,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DomainModel(BaseModel):
    """Base domain model configuration."""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        validate_assignment=True,
    )


class Merchant(DomainModel):
    """Merchant entity owning the recovery policies and idempotency salt."""

    id: UUID = Field(default_factory=uuid4)
    name: str
    default_currency: str = "INR"
    timezone: str = "Asia/Kolkata"
    idempotency_salt: str  # 32-byte hex secret
    settings: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise TimeZoneError(v, f"Invalid IANA timezone: '{v}'") from exc
        return v

    @field_validator("default_currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        clean = v.strip().upper()
        if len(clean) != 3:
            raise DataValidationError(
                "default_currency", v, "Currency must be a 3-letter ISO code."
            )
        return clean


class Customer(DomainModel):
    """Customer entity tracking contact fatigue and recovery metrics."""

    id: UUID = Field(default_factory=uuid4)
    merchant_id: UUID
    external_customer_id: str
    phone_hash: str | None = None
    email_hash: str | None = None
    risk_tier: int = 1  # 1=Standard, 2=Elevated, 3=VIP/Enterprise
    lifetime_recovery_count: int = 0
    lifetime_failure_count: int = 0
    rolling_contacts_7d: int = 0
    last_contacted_at: datetime | None = None
    created_at: datetime = Field(default_factory=_utc_now)

    @field_validator("rolling_contacts_7d", "lifetime_recovery_count", "lifetime_failure_count")
    @classmethod
    def validate_non_negative_counters(cls, v: int, info: Any) -> int:
        if v < 0:
            raise DataValidationError(info.field_name, v, "Count cannot be negative.")
        return v

    @field_validator("last_contacted_at")
    @classmethod
    def validate_last_contacted_at(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise DataValidationError(
                "last_contacted_at",
                v,
                "Datetime must be timezone-aware (naive datetime rejected).",
            )
        return v


class PaymentAttempt(DomainModel):
    """Immutable record of an individual transaction attempt."""

    id: UUID = Field(default_factory=uuid4)
    customer_id: UUID
    recovery_opportunity_id: UUID | None = None
    razorpay_payment_id: str
    razorpay_order_id: str
    attempt_sequence: int = 1
    amount_subunits: int
    currency: str = "INR"
    payment_method: PaymentMethod
    status: AttemptStatus = AttemptStatus.FAILED
    error_code: str | None = None
    error_description: str | None = None
    error_source: str | None = None
    error_step: str | None = None
    error_reason: str | None = None
    gateway_created_at: datetime
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    ingested_at: datetime = Field(default_factory=_utc_now)

    @field_validator("amount_subunits")
    @classmethod
    def validate_amount(cls, v: int) -> int:
        if isinstance(v, bool) or not isinstance(v, int):
            raise DataValidationError("amount_subunits", v, "Amount must be an integer.")
        if not (INT64_MIN <= v <= INT64_MAX):
            raise DataValidationError("amount_subunits", v, "Amount exceeds 64-bit bounds.")
        if v < 0:
            raise DataValidationError(
                "amount_subunits", v, "Payment attempt amount must be non-negative."
            )
        return v


class RecoveryOpportunity(DomainModel):
    """The central stateful aggregate managing the recovery lifecycle for a failed order."""

    id: UUID = Field(default_factory=uuid4)
    merchant_id: UUID
    customer_id: UUID
    order_id: str
    initial_attempt_id: UUID
    latest_attempt_id: UUID
    amount_at_risk_subunits: int
    currency: str = "INR"
    current_state: OpportunityState = OpportunityState.OPEN
    failure_category: FailureCategory
    organic_recovery_estimate: float
    organic_estimation_source: OrganicEstimationSource = OrganicEstimationSource.SEGMENT_PRIOR
    failure_attempt_count: int = 1
    total_interventions_count: int = 0
    total_contacts_count: int = 0
    version: int = 1
    opened_at: datetime = Field(default_factory=_utc_now)
    closed_at: datetime | None = None
    last_evaluated_at: datetime | None = None
    execution_claimed_at: datetime | None = None

    @field_validator("organic_recovery_estimate")
    @classmethod
    def validate_probability(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise DataValidationError(
                "organic_recovery_estimate", v, "Probability must be in [0.0, 1.0]."
            )
        return round(v, 4)

    @field_validator("amount_at_risk_subunits", mode="before")
    @classmethod
    def validate_amount(cls, v: Any) -> Any:
        if isinstance(v, bool) or not isinstance(v, int):
            raise DataValidationError("amount_at_risk_subunits", v, "Amount must be an integer.")
        if not (0 <= v <= INT64_MAX):
            raise DataValidationError(
                "amount_at_risk_subunits",
                v,
                f"Amount at risk must be in [0, INT64_MAX ({INT64_MAX})].",
            )
        return v


class InterventionCandidate(DomainModel):
    """A specific evaluated candidate intervention for an opportunity."""

    id: UUID = Field(default_factory=uuid4)
    opportunity_id: UUID
    intervention_type: InterventionType
    parameters: dict[str, Any] = Field(default_factory=dict)
    p_recovery: float
    p_organic: float
    direct_cost_subunits: int
    friction_cost_subunits: int
    risk_penalty_subunits: int
    expected_net_value_subunits: int
    confidence_score: float
    contact_fatigue: float = 0.0
    generated_at: datetime = Field(default_factory=_utc_now)

    @field_validator("p_recovery", "p_organic")
    @classmethod
    def validate_probabilities(cls, v: float, info: Any) -> float:
        if not (0.0 <= v <= 1.0):
            raise DataValidationError(info.field_name, v, "Probability must be in [0.0, 1.0].")
        return round(v, 4)

    @field_validator("confidence_score")
    @classmethod
    def validate_confidence(cls, v: float) -> float:
        if not (0.50 <= v <= 1.0):
            raise DataValidationError(
                "confidence_score",
                v,
                "Confidence score must be in [0.50, 1.0] (uncertainty in [0.0, 0.50]).",
            )
        return round(v, 4)

    @field_validator("contact_fatigue")
    @classmethod
    def validate_contact_fatigue(cls, v: float) -> float:
        if v < 0.0:
            raise DataValidationError("contact_fatigue", v, "Contact fatigue cannot be negative.")
        return round(v, 4)

    @field_validator("direct_cost_subunits", "friction_cost_subunits", "risk_penalty_subunits")
    @classmethod
    def validate_costs(cls, v: int, info: Any) -> int:
        if v < 0:
            raise DataValidationError(info.field_name, v, "Cost/penalty cannot be negative.")
        return v


class RecoveryDecision(DomainModel):
    """The authoritative policy resolution produced by the Deterministic Policy Engine."""

    id: UUID = Field(default_factory=uuid4)
    opportunity_id: UUID
    selected_candidate_id: UUID | None = None
    decision_type: DecisionType
    policy_evaluation_details: dict[str, Any] = Field(default_factory=dict)
    blocked_reason_code: str | None = None
    explanation: str = ""
    decided_at: datetime = Field(default_factory=_utc_now)


class ExecutionRecord(DomainModel):
    """Execution voucher guaranteeing idempotency and tracking dispatch."""

    id: UUID = Field(default_factory=uuid4)
    decision_id: UUID
    attempt_index: int
    idempotency_key: str
    reference_id: str
    intervention_type: InterventionType
    execution_status: ExecutionStatus = ExecutionStatus.CLAIMED
    external_reference_id: str | None = None
    failure_message: str | None = None
    claimed_at: datetime = Field(default_factory=_utc_now)
    executed_at: datetime | None = None
    task_id: UUID | None = None
    lease_version: int | None = None

    @field_validator("reference_id")
    @classmethod
    def validate_reference_id_length(cls, v: str) -> str:
        if len(v) > 40:
            raise DataValidationError(
                "reference_id", v, f"reference_id length ({len(v)}) exceeds 40 chars max."
            )
        return v

    @field_validator("attempt_index")
    @classmethod
    def validate_attempt_index(cls, v: int) -> int:
        if v < 1:
            raise DataValidationError("attempt_index", v, "attempt_index must be >= 1.")
        return v


class PolicyRule(DomainModel):
    """Merchant policy rule parameters."""

    id: UUID = Field(default_factory=uuid4)
    merchant_id: UUID
    rule_type: RuleType
    parameters: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    created_at: datetime = Field(default_factory=_utc_now)


class PaymentEvidence(DomainModel):
    """Cryptographic proof confirming payment settlement."""

    id: UUID = Field(default_factory=uuid4)
    opportunity_id: UUID
    razorpay_payment_id: str
    event_type: str
    signature_hash: str
    captured_amount_subunits: int
    verified_at: datetime = Field(default_factory=_utc_now)


class AuditEvent(DomainModel):
    """Append-only audit event for system transitions."""

    id: UUID = Field(default_factory=uuid4)
    merchant_id: UUID
    trace_id: str
    aggregate_type: str
    aggregate_id: UUID
    event_name: str
    state_before: dict[str, Any] | None = None
    state_after: dict[str, Any] | None = None
    actor_type: ActorType = ActorType.SYSTEM
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)
