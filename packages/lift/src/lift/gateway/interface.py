"""Abstract payment gateway adapter interface (Protocol)."""

from typing import Protocol, runtime_checkable

from lift.gateway.types import (
    GatewayCustomerInfo,
    GatewayOrder,
    GatewayPayment,
    PaymentLinkResult,
    PaymentLinkStatus,
)


@runtime_checkable
class PaymentGatewayAdapter(Protocol):
    """Abstract interface defining verified payment gateway interactions.

    Decouples LIFT domain and execution engines from vendor-specific REST/SDK
    details and enables deterministic in-memory simulation.
    """

    def verify_webhook_signature(
        self,
        raw_body: bytes,
        signature: str,
        secret: str,
    ) -> bool:
        """Verify the cryptographic HMAC-SHA256 signature of a raw webhook body."""
        ...

    def create_payment_link(
        self,
        amount_subunits: int,
        currency: str,
        reference_id: str,
        description: str,
        customer: GatewayCustomerInfo,
        expire_by_epoch: int | None = None,
        notes: dict[str, str] | None = None,
    ) -> PaymentLinkResult:
        """Create a Razorpay Standard Payment Link with a deterministic reference_id.

        The caller must provide safe correlation metadata in `notes`:
        notes = {
            "opportunity_id": str(opportunity_id),
            "attempt_index": str(attempt_index),
            "order_id": str(order_id),
        }
        No secrets, credentials, or sensitive customer PII are placed in notes.
        """
        ...

    def fetch_payment_link(self, payment_link_id: str) -> PaymentLinkStatus:
        """Fetch current external status of a Payment Link by gateway ID (e.g. plink_123)."""
        ...

    def fetch_payment_link_by_reference_id(self, reference_id: str) -> PaymentLinkStatus | None:
        """Discover an existing Payment Link using the deterministic reference_id.

        Used strictly for Payment Link discovery when local DB writes fail after external creation.
        Returns None if no payment link with this reference_id exists.
        """
        ...

    def cancel_payment_link(self, payment_link_id: str) -> bool:
        """Cancel an active Payment Link (e.g. when order recovers organically)."""
        ...

    def fetch_payment(self, payment_id: str) -> GatewayPayment:
        """Fetch details and authoritative status of a specific payment attempt."""
        ...

    def fetch_order(self, order_id: str) -> GatewayOrder:
        """Fetch order details and current payment status from the gateway."""
        ...

    def fetch_order_payments(self, order_id: str) -> list[GatewayPayment]:
        """Fetch all payment transactions associated with a specific order_id.

        Used strictly for payment-state reconciliation, NOT for Payment Link discovery.
        """
        ...
