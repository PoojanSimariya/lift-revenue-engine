"""Gateway DTOs and value types for payment provider interactions."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GatewayCustomerInfo:
    """Customer contact details supplied during Payment Link generation."""

    name: str | None = None
    email: str | None = None
    contact: str | None = None


@dataclass(frozen=True)
class PaymentLinkResult:
    """Result of creating an external Razorpay Payment Link."""

    id: str
    short_url: str
    reference_id: str
    status: str
    amount: int
    currency: str
    expire_by: int | None = None
    notes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PaymentLinkStatus:
    """External status and financial snapshot of a Payment Link."""

    id: str
    status: str  # "created", "partially_paid", "paid", "cancelled", "expired"
    amount: int
    currency: str
    amount_paid: int = 0
    reference_id: str | None = None
    short_url: str | None = None
    expired_at: int | None = None
    cancelled_at: int | None = None
    notes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class GatewayPayment:
    """Authoritative representation of a single payment transaction/attempt."""

    id: str
    amount: int
    currency: str
    status: str  # "created", "authorized", "captured", "failed", "refunded"
    order_id: str | None = None
    method: str | None = None
    error_code: str | None = None
    error_description: str | None = None
    error_source: str | None = None
    error_step: str | None = None
    error_reason: str | None = None
    created_at: int | None = None
    captured_at: int | None = None


@dataclass(frozen=True)
class GatewayOrder:
    """Gateway representation of an order grouping payment attempts."""

    id: str
    amount: int
    amount_paid: int
    amount_due: int
    currency: str
    status: str  # "created", "attempted", "paid"
    attempts: int = 0
    notes: dict[str, str] = field(default_factory=dict)
    created_at: int | None = None
