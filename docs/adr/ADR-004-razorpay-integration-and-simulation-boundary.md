# ADR-004: Dual-Mode Razorpay Adapter & Internal Retry Strategy Boundary

## Status
Accepted (Revised after Principal Review)

## Context
The project requires authentic integration with the official Razorpay ecosystem (Test Mode APIs, Orders, Payment Links, and Webhooks with cryptographic HMAC signatures) alongside high-speed deterministic batch simulations.

Review of official Razorpay documentation confirms that **Smart Payment Retries** are an automated background feature specifically tied to Razorpay Subscriptions (recurring card auto-debits), rather than a public, generic REST API endpoint for one-time payments.

Falsely assuming or exposing a generic `trigger_smart_retry` endpoint creates architectural fiction. One-time payment recovery requires dynamic Payment Links, alternative rails (UPI, Netbanking), or customer prompts, while timed re-attempts must be scheduled internally by our own engine.

## Decision
1. **Verified Public Razorpay Gateway Contract (REQ-06):**
   The `PaymentGatewayAdapter` exposes strictly verified, authentic Razorpay capabilities:
   - `verify_webhook_signature(raw_body, signature, secret) -> bool`
   - `create_payment_link(order_id, amount, customer_info, reference_id, options) -> PaymentLinkResult`
   - `fetch_payment(payment_id) -> GatewayPayment`
   - `fetch_order(order_id) -> GatewayOrder`
   - `fetch_order_payments(order_id) -> List[GatewayPayment]`
   - `cancel_payment_link(payment_link_id) -> bool`
   - The generic method `trigger_smart_retry` is **completely removed**. Direct card auto-debit without subscriptions is explicitly excluded as unsupported by Razorpay and RBI regulations.
2. **Internal `RetryStrategyExecutor` Component & Execution Path:**
   `INTERNAL_RETRY_SCHEDULE` represents scheduling a future Payment Link dispatch task in `task_queue` (e.g. after quiet hours or on salary day), NOT card re-debiting. Its execution path is: candidate $\rightarrow$ scheduled task $\rightarrow$ policy re-check $\rightarrow$ Payment Link creation/dispatch with unique `reference_id` $\rightarrow$ verification.
3. **Payment Link Reconciliation via Deterministic `reference_id` (REQ-05):**
   - Razorpay Payment Links API does not document support for an `Idempotency-Key` HTTP header.
   - Therefore, LIFT achieves distributed idempotency via durable local intent + deterministic `reference_id` + automated reconciliation:
     - Pre-Dispatch: Worker generates unique `reference_id` (`ref_<opp_id[:8]>_<attempt_index>`) and writes it to `execution_records` before making the external HTTP call.
     - Webhook Correlation: Incoming `payment_link.paid` / `payment_link.expired` webhooks match `payload.payment_link.entity.reference_id` directly to the execution voucher.
     - Recovery / Timeout Reconciliation: If the HTTP call times out or the worker crashes, the sweeper queries Razorpay or listens for webhooks echoing `reference_id`, linking the remote resource without duplicate link creation.
4. **Webhook Validation & Deduplication via `x-razorpay-event-id` (REQ-07):**
   The `x-razorpay-event-id` header is mandatory; missing event IDs are rejected with `HTTP 400 Bad Request`. Valid deliveries are deduplicated using `webhook_events` table before enqueuing.
5. **Monotonic Terminal Payment State (REQ-04):**
   Receipt of `payment.captured`, `order.paid`, or `payment_link.paid` transitions the opportunity monotonically to `RECOVERED`. `payment.authorized` is intermediate and does not mark an opportunity `RECOVERED`. Delayed or out-of-order failure events cannot roll back a verified capture.
6. **Dual-Mode Implementations:**
   - `RazorpayTestModeAdapter`: Direct integration with live Razorpay Test Mode REST APIs.
   - `DeterministicSimulatorAdapter`: Causal DGP simulator executing multi-thousand opportunity batch simulations across identical domain interfaces.

## Consequences
### Positive:
- Zero architectural fiction: all gateway methods reflect real, supported Razorpay APIs.
- Clear separation between gateway capabilities and internal LIFT scheduling strategies.
- Eliminates duplicate payment links through external status reconciliation.

### Negative:
- One-time card re-debits cannot be triggered programmatically without customer interaction (inherent to Indian 2FA regulations / RBI mandates); recovery relies on Payment Links and UPI intent.
