"""Razorpay Test Mode gateway adapter implementation."""

import hashlib
import hmac
from typing import Any, cast

import httpx

from lift.core.errors import (
    GatewayAuthenticationError,
    GatewayError,
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


class RazorpayTestModeAdapter(PaymentGatewayAdapter):
    """Concrete adapter connecting to Razorpay REST APIs (Test Mode or Staging)."""

    DEFAULT_BASE_URL = "https://api.razorpay.com/v1"

    def __init__(
        self,
        key_id: str,
        key_secret: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.key_id = key_id
        self.key_secret = key_secret
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._client = client or httpx.Client(
            auth=(self.key_id, self.key_secret),
            timeout=self.timeout_seconds,
        )

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

    def _handle_response(self, response: httpx.Response) -> dict[str, Any]:
        """Validate response status and map errors into LIFT gateway exceptions."""
        if response.status_code == 401:
            raise GatewayAuthenticationError(
                "Authentication failed with Razorpay API (HTTP 401)",
                gateway_code="AUTHENTICATION_FAILED",
                details={"response": response.text},
            )
        if response.status_code == 404:
            raise GatewayResourceNotFoundError(
                "Requested resource was not found on Razorpay (HTTP 404)",
                gateway_code="RESOURCE_NOT_FOUND",
                details={"response": response.text},
            )
        if not response.is_success:
            err_code = None
            err_desc = response.text
            try:
                data = response.json()
                if isinstance(data, dict) and "error" in data:
                    err_code = data["error"].get("code")
                    err_desc = data["error"].get("description", response.text)
            except Exception:
                pass
            raise GatewayError(
                f"Razorpay API error ({response.status_code}): {err_desc}",
                gateway_code=err_code,
                details={"status_code": response.status_code, "response": response.text},
            )
        return cast(dict[str, Any], response.json())

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            resp = self._client.request(method, url, **kwargs)
            return self._handle_response(resp)
        except httpx.TimeoutException as exc:
            raise GatewayTimeoutError(
                f"Gateway request to {url} timed out after {self.timeout_seconds}s",
                gateway_code="GATEWAY_TIMEOUT",
                details={"url": url, "method": method},
            ) from exc
        except (
            GatewayError,
            GatewayAuthenticationError,
            GatewayResourceNotFoundError,
            GatewayTimeoutError,
        ):
            raise
        except httpx.RequestError as exc:
            raise GatewayError(
                f"Network request error communicating with Razorpay: {exc}",
                gateway_code="NETWORK_ERROR",
                details={"url": url, "method": method},
            ) from exc

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
        """Create a Razorpay Standard Payment Link with accept_partial=False."""
        cust_payload: dict[str, Any] = {}
        if customer.name:
            cust_payload["name"] = customer.name
        if customer.email:
            cust_payload["email"] = customer.email
        if customer.contact:
            cust_payload["contact"] = customer.contact

        payload: dict[str, Any] = {
            "amount": amount_subunits,
            "currency": currency,
            "accept_partial": False,
            "reference_id": reference_id,
            "description": description,
            "customer": cust_payload,
            "reminder_enable": False,
            "notes": notes or {},
        }
        if expire_by_epoch is not None:
            payload["expire_by"] = expire_by_epoch

        data = self._request("POST", "/payment_links", json=payload)
        return PaymentLinkResult(
            id=data["id"],
            short_url=data.get("short_url", ""),
            reference_id=data.get("reference_id", reference_id),
            status=data.get("status", "created"),
            amount=data.get("amount", amount_subunits),
            currency=data.get("currency", currency),
            expire_by=data.get("expire_by", expire_by_epoch),
            notes=data.get("notes", {}),
        )

    def fetch_payment_link(self, payment_link_id: str) -> PaymentLinkStatus:
        """Fetch current external status of a Payment Link by gateway ID."""
        data = self._request("GET", f"/payment_links/{payment_link_id}")
        return PaymentLinkStatus(
            id=data["id"],
            status=data.get("status", "created"),
            amount=data.get("amount", 0),
            currency=data.get("currency", "INR"),
            amount_paid=data.get("amount_paid", 0),
            reference_id=data.get("reference_id"),
            short_url=data.get("short_url"),
            expired_at=data.get("expired_at"),
            cancelled_at=data.get("cancelled_at"),
            notes=data.get("notes", {}),
        )

    def fetch_payment_link_by_reference_id(self, reference_id: str) -> PaymentLinkStatus | None:
        """Discover an existing Payment Link using the deterministic reference_id."""
        data = self._request("GET", "/payment_links", params={"reference_id": reference_id})
        if not isinstance(data, dict):
            raise GatewayError(
                f"Unexpected response format from Razorpay payment_links API: {type(data)}",
                gateway_code="MALFORMED_RESPONSE",
                details={"response": data},
            )
        # Razorpay Payment Link collection response:
        # {"entity": "collection", "count": 1, "items": [...]}
        items = data.get("items")
        if items is None and "payment_links" in data:
            items = data.get("payment_links")
        if items is None and isinstance(data, list):
            items = data

        if not isinstance(items, list):
            raise GatewayError(
                "Malformed response: 'items' collection is missing or invalid",
                gateway_code="MALFORMED_RESPONSE",
                details={"response": data},
            )
        if len(items) == 0:
            return None

        match = items[0]
        if not isinstance(match, dict) or "id" not in match:
            raise GatewayError(
                "Malformed item in payment link collection response",
                gateway_code="MALFORMED_RESPONSE",
                details={"item": match},
            )

        ref_id_val = str(match["reference_id"]) if match.get("reference_id") is not None else None
        canc_at_val = int(match["cancelled_at"]) if match.get("cancelled_at") is not None else None

        return PaymentLinkStatus(
            id=str(match["id"]),
            status=str(match.get("status", "created")),
            amount=int(match.get("amount", 0)),
            currency=str(match.get("currency", "INR")),
            amount_paid=int(match.get("amount_paid", 0)),
            reference_id=ref_id_val,
            short_url=str(match["short_url"]) if match.get("short_url") is not None else None,
            expired_at=int(match["expired_at"]) if match.get("expired_at") is not None else None,
            cancelled_at=canc_at_val,
            notes=match.get("notes", {}) if isinstance(match.get("notes"), dict) else {},
        )

    def cancel_payment_link(self, payment_link_id: str) -> bool:
        """Cancel an active Payment Link."""
        data = self._request("POST", f"/payment_links/{payment_link_id}/cancel")
        status: str = str(data.get("status", ""))
        return status == "cancelled"

    def fetch_payment(self, payment_id: str) -> GatewayPayment:
        """Fetch point-in-time payment attempt status and error details."""
        data = self._request("GET", f"/payments/{payment_id}")
        return GatewayPayment(
            id=data["id"],
            amount=data.get("amount", 0),
            currency=data.get("currency", "INR"),
            status=data.get("status", "failed"),
            order_id=data.get("order_id"),
            method=data.get("method"),
            error_code=data.get("error_code"),
            error_description=data.get("error_description"),
            error_source=data.get("error_source"),
            error_step=data.get("error_step"),
            error_reason=data.get("error_reason"),
            created_at=data.get("created_at"),
            captured_at=data.get("captured_at"),
        )

    def fetch_order(self, order_id: str) -> GatewayOrder:
        """Fetch order details from the gateway."""
        data = self._request("GET", f"/orders/{order_id}")
        return GatewayOrder(
            id=data["id"],
            amount=data.get("amount", 0),
            amount_paid=data.get("amount_paid", 0),
            amount_due=data.get("amount_due", 0),
            currency=data.get("currency", "INR"),
            status=data.get("status", "created"),
            attempts=data.get("attempts", 0),
            notes=data.get("notes", {}),
            created_at=data.get("created_at"),
        )

    def fetch_order_payments(self, order_id: str) -> list[GatewayPayment]:
        """Fetch all payment transactions associated with a specific order_id."""
        data = self._request("GET", f"/orders/{order_id}/payments")
        items = data.get("items", []) if isinstance(data, dict) else []
        return [
            GatewayPayment(
                id=item["id"],
                amount=item.get("amount", 0),
                currency=item.get("currency", "INR"),
                status=item.get("status", "failed"),
                order_id=item.get("order_id", order_id),
                method=item.get("method"),
                error_code=item.get("error_code"),
                error_description=item.get("error_description"),
                error_source=item.get("error_source"),
                error_step=item.get("error_step"),
                error_reason=item.get("error_reason"),
                created_at=item.get("created_at"),
                captured_at=item.get("captured_at"),
            )
            for item in items
        ]
