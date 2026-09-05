"""Deterministic in-memory gateway simulator for hermetic offline testing."""

import hashlib
import hmac
import time
from typing import Any

from lift.core.errors import (
    GatewayResourceNotFoundError,
    GatewayTimeoutError,
)
from lift.gateway.interface import PaymentGatewayAdapter
from lift.gateway.types import (
    GatewayCustomerInfo,
    GatewayOrder,
    GatewayPayment,
    PaymentLinkResult,
    PaymentLinkStatus,
)


class DeterministicSimulatorAdapter(PaymentGatewayAdapter):
    """In-memory deterministic simulator implementing the PaymentGatewayAdapter protocol.

    Provides hermetic, offline simulation of Razorpay payment links, orders,
    and payment transactions without network dependencies.
    """

    def __init__(self, seed_prefix: str = "sim") -> None:
        self.seed_prefix = seed_prefix
        self._payment_links: dict[str, dict[str, Any]] = {}
        self._links_by_reference: dict[str, str] = {}
        self._payments: dict[str, dict[str, Any]] = {}
        self._orders: dict[str, dict[str, Any]] = {}
        self._order_payments: dict[str, list[str]] = {}
        self._counter: int = 0
        self._timeout_next: bool = False

    def reset(self) -> None:
        """Clear all in-memory state."""
        self._payment_links.clear()
        self._links_by_reference.clear()
        self._payments.clear()
        self._orders.clear()
        self._order_payments.clear()
        self._counter = 0
        self._timeout_next = False

    def simulate_network_timeout_on_next_call(self) -> None:
        """Arm the adapter to raise a GatewayTimeoutError on the very next operation."""
        self._timeout_next = True

    def _check_timeout(self) -> None:
        if self._timeout_next:
            self._timeout_next = False
            raise GatewayTimeoutError("Simulated network timeout communicating with gateway.")

    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}_{self.seed_prefix}_{self._counter:06d}"

    def verify_webhook_signature(
        self,
        raw_body: bytes,
        signature: str,
        secret: str,
    ) -> bool:
        """Verify constant-time HMAC-SHA256 signature against raw webhook bytes."""
        if not signature or not secret:
            return False
        expected = hmac.new(
            secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

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
        """Simulate creating a Razorpay Payment Link."""
        self._check_timeout()
        link_id = self._next_id("plink")
        short_url = f"https://rzp.io/i/{link_id}"
        notes_dict = dict(notes or {})

        record = {
            "id": link_id,
            "status": "created",
            "amount": amount_subunits,
            "amount_paid": 0,
            "currency": currency,
            "reference_id": reference_id,
            "short_url": short_url,
            "description": description,
            "customer": {
                "name": customer.name,
                "email": customer.email,
                "contact": customer.contact,
            },
            "expire_by": expire_by_epoch,
            "notes": notes_dict,
            "created_at": int(time.time()),
            "cancelled_at": None,
            "expired_at": None,
        }
        self._payment_links[link_id] = record
        self._links_by_reference[reference_id] = link_id

        return PaymentLinkResult(
            id=link_id,
            short_url=short_url,
            reference_id=reference_id,
            status="created",
            amount=amount_subunits,
            currency=currency,
            expire_by=expire_by_epoch,
            notes=notes_dict,
        )

    def fetch_payment_link(self, payment_link_id: str) -> PaymentLinkStatus:
        """Fetch simulated Payment Link status."""
        self._check_timeout()
        record = self._payment_links.get(payment_link_id)
        if not record:
            raise GatewayResourceNotFoundError(
                f"Simulated payment link {payment_link_id} not found",
                gateway_code="RESOURCE_NOT_FOUND",
            )
        return PaymentLinkStatus(
            id=record["id"],
            status=record["status"],
            amount=record["amount"],
            currency=record["currency"],
            amount_paid=record["amount_paid"],
            reference_id=record.get("reference_id"),
            short_url=record.get("short_url"),
            expired_at=record.get("expired_at"),
            cancelled_at=record.get("cancelled_at"),
            notes=record.get("notes", {}),
        )

    def fetch_payment_link_by_reference_id(self, reference_id: str) -> PaymentLinkStatus | None:
        """Discover an existing Payment Link using deterministic reference_id."""
        self._check_timeout()
        link_id = self._links_by_reference.get(reference_id)
        if not link_id:
            return None
        return self.fetch_payment_link(link_id)

    def cancel_payment_link(self, payment_link_id: str) -> bool:
        """Cancel a simulated Payment Link."""
        self._check_timeout()
        record = self._payment_links.get(payment_link_id)
        if not record:
            raise GatewayResourceNotFoundError(
                f"Simulated payment link {payment_link_id} not found",
                gateway_code="RESOURCE_NOT_FOUND",
            )
        record["status"] = "cancelled"
        record["cancelled_at"] = int(time.time())
        return True

    def fetch_payment(self, payment_id: str) -> GatewayPayment:
        """Fetch simulated payment transaction details."""
        self._check_timeout()
        record = self._payments.get(payment_id)
        if not record:
            raise GatewayResourceNotFoundError(
                f"Simulated payment {payment_id} not found",
                gateway_code="RESOURCE_NOT_FOUND",
            )
        return GatewayPayment(
            id=record["id"],
            amount=record["amount"],
            currency=record["currency"],
            status=record["status"],
            order_id=record.get("order_id"),
            method=record.get("method"),
            error_code=record.get("error_code"),
            error_description=record.get("error_description"),
            error_source=record.get("error_source"),
            error_step=record.get("error_step"),
            error_reason=record.get("error_reason"),
            created_at=record.get("created_at"),
            captured_at=record.get("captured_at"),
        )

    def fetch_order(self, order_id: str) -> GatewayOrder:
        """Fetch simulated order status."""
        self._check_timeout()
        record = self._orders.get(order_id)
        if not record:
            raise GatewayResourceNotFoundError(
                f"Simulated order {order_id} not found",
                gateway_code="RESOURCE_NOT_FOUND",
            )
        return GatewayOrder(
            id=record["id"],
            amount=record["amount"],
            amount_paid=record["amount_paid"],
            amount_due=record["amount_due"],
            currency=record["currency"],
            status=record["status"],
            attempts=record.get("attempts", 0),
            notes=record.get("notes", {}),
            created_at=record.get("created_at"),
        )

    def fetch_order_payments(self, order_id: str) -> list[GatewayPayment]:
        """Fetch all simulated payment transactions for an order."""
        self._check_timeout()
        payment_ids = self._order_payments.get(order_id, [])
        return [self.fetch_payment(pid) for pid in payment_ids if pid in self._payments]

    # --- Test Helper Methods ---

    def simulate_order(
        self,
        order_id: str,
        amount: int,
        currency: str = "INR",
        status: str = "created",
        notes: dict[str, str] | None = None,
    ) -> GatewayOrder:
        """Seed a simulated order in memory."""
        record = {
            "id": order_id,
            "amount": amount,
            "amount_paid": amount if status == "paid" else 0,
            "amount_due": 0 if status == "paid" else amount,
            "currency": currency,
            "status": status,
            "attempts": 0,
            "notes": notes or {},
            "created_at": int(time.time()),
        }
        self._orders[order_id] = record
        return self.fetch_order(order_id)

    def simulate_payment(
        self,
        payment_id: str,
        amount: int,
        order_id: str | None = None,
        status: str = "captured",
        currency: str = "INR",
        error_code: str | None = None,
        error_description: str | None = None,
    ) -> GatewayPayment:
        """Seed a simulated payment transaction in memory."""
        record = {
            "id": payment_id,
            "amount": amount,
            "currency": currency,
            "status": status,
            "order_id": order_id,
            "method": "card",
            "error_code": error_code,
            "error_description": error_description,
            "created_at": int(time.time()),
            "captured_at": int(time.time()) if status == "captured" else None,
        }
        self._payments[payment_id] = record
        if order_id:
            if order_id not in self._order_payments:
                self._order_payments[order_id] = []
            if payment_id not in self._order_payments[order_id]:
                self._order_payments[order_id].append(payment_id)
        return self.fetch_payment(payment_id)

    def simulate_payment_link_paid(self, reference_id: str) -> PaymentLinkStatus:
        """Simulate a customer completing payment on a link."""
        link_id = self._links_by_reference.get(reference_id)
        if not link_id or link_id not in self._payment_links:
            raise GatewayResourceNotFoundError(
                f"Payment link with reference_id {reference_id} not found"
            )
        record = self._payment_links[link_id]
        record["status"] = "paid"
        record["amount_paid"] = record["amount"]
        return self.fetch_payment_link(link_id)
