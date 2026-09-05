"""Domain enums and type definitions for LIFT."""

from enum import StrEnum


class OpportunityState(StrEnum):
    """The 11 formal lifecycle states of a RecoveryOpportunity."""

    OPEN = "OPEN"
    IN_EVALUATION = "IN_EVALUATION"
    ACTION_SCHEDULED = "ACTION_SCHEDULED"
    ACTION_EXECUTING = "ACTION_EXECUTING"
    AWAITING_SETTLEMENT = "AWAITING_SETTLEMENT"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    ACTION_BLOCKED = "ACTION_BLOCKED"
    ESCALATED_HUMAN = "ESCALATED_HUMAN"
    RECOVERED = "RECOVERED"
    EXPIRED = "EXPIRED"
    TERMINATED = "TERMINATED"


class FailureCategory(StrEnum):
    """Structured failure categories for payment attempts."""

    TRANSIENT_NETWORK = "TRANSIENT_NETWORK"
    AUTHENTICATION_TIMEOUT = "AUTHENTICATION_TIMEOUT"
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    INVALID_INSTRUMENT = "INVALID_INSTRUMENT"
    HARD_ISSUER_DECLINE = "HARD_ISSUER_DECLINE"


class InterventionType(StrEnum):
    """Permitted candidate intervention types evaluated by LIFT."""

    NO_ACTION = "NO_ACTION"
    INTERNAL_RETRY_SCHEDULE = "INTERNAL_RETRY_SCHEDULE"
    DIRECT_PAYMENT_LINK_SMS = "DIRECT_PAYMENT_LINK_SMS"
    DIRECT_PAYMENT_LINK_WHATSAPP = "DIRECT_PAYMENT_LINK_WHATSAPP"
    DIRECT_PAYMENT_LINK_EMAIL = "DIRECT_PAYMENT_LINK_EMAIL"
    CUSTOM_WEBHOOK_OUTREACH = "CUSTOM_WEBHOOK_OUTREACH"


class DecisionType(StrEnum):
    """Outcomes emitted by the Deterministic Policy Gate."""

    AUTHORIZED = "AUTHORIZED"
    BLOCKED = "BLOCKED"
    NO_ACTION = "NO_ACTION"
    ESCALATED = "ESCALATED"


class PaymentMethod(StrEnum):
    """Payment rails/instruments supported by Razorpay."""

    CARD = "card"
    UPI = "upi"
    NETBANKING = "netbanking"
    WALLET = "wallet"


class AttemptStatus(StrEnum):
    """Gateway statuses for PaymentAttempt records."""

    FAILED = "failed"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"


class ExecutionStatus(StrEnum):
    """Lifecycle statuses for ExecutionRecord vouchers."""

    CLAIMED = "CLAIMED"
    EXECUTED = "EXECUTED"
    CANCELLED_STALE_STATE = "CANCELLED_STALE_STATE"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    FAILED = "FAILED"


class RuleType(StrEnum):
    """Merchant policy rule types."""

    QUIET_HOURS = "QUIET_HOURS"
    MAX_CONTACTS_WINDOW = "MAX_CONTACTS_WINDOW"
    MIN_AMOUNT_SMS = "MIN_AMOUNT_SMS"
    MAX_RETRIES = "MAX_RETRIES"


class OrganicEstimationSource(StrEnum):
    """Lineage/provenance source of organic recovery estimates."""

    CALIBRATED_MODEL = "CALIBRATED_MODEL"
    SEGMENT_PRIOR = "SEGMENT_PRIOR"
    MERCHANT_CONFIG = "MERCHANT_CONFIG"


class ActorType(StrEnum):
    """Actor categories logged in append-only AuditEvent records."""

    SYSTEM = "SYSTEM"
    POLICY_GATE = "POLICY_GATE"
    OPERATOR = "OPERATOR"
    GATEWAY = "GATEWAY"
