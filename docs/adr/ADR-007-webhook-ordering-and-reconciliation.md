# ADR-007: Out-of-Order Webhooks, Event Deduplication & External Execution Reconciliation

## Status
Accepted

## Context
Payment gateways operate over distributed networks where webhook delivery is neither guaranteed to be in order nor delivered exactly once:
1. **Duplicate Deliveries:** Razorpay routinely retries webhooks until an HTTP 200 is returned, or issues duplicate deliveries during transient network partitions.
2. **Out-of-Order Webhooks:** A `payment.captured` or `order.paid` event may arrive before an older `payment.failed` event, or a delayed `payment.failed` may arrive after the customer already recovered organically.
3. **External Call Ambiguity:** An HTTP call to Razorpay (e.g. creating a Payment Link) may succeed remotely, but local persistence fails due to a network timeout or worker crash before the database can record the external link ID.

## Decision
1. **Webhook Validation & Deduplication via `x-razorpay-event-id` (REQ-07):**
   - The `x-razorpay-event-id` header is mandatory. Missing headers are rejected immediately with `HTTP 400 Bad Request`. LIFT never fabricates synthetic event IDs.
   - The ingestion endpoint records the unique `x-razorpay-event-id` header in the `webhook_events` table with a `PRIMARY KEY` constraint.
   - Duplicate arrivals are suppressed immediately: the endpoint returns `HTTP 200 OK` with `{"status": "duplicate_acknowledged"}` and appends an audit event `DUPLICATE_WEBHOOK_SUPPRESSED`. No background processing task is enqueued.
2. **Monotonic Terminal Settlement, Authorization & Expiration (REQ-04):**
   - Verified payment capture (`payment.captured`, `order.paid`, `payment_link.paid`) transitions the `RecoveryOpportunity` state monotonically to `RECOVERED`.
   - `payment.authorized` represents uncaptured/unsettled funds; it transitions the opportunity to (or holds it in) `AWAITING_SETTLEMENT`. It **must not** mark an opportunity `RECOVERED` on authorization alone.
   - `payment_link.expired` transitions the opportunity from `AWAITING_SETTLEMENT` to `OPEN` (if retry budget/window remains) or terminal `EXPIRED`.
   - `RECOVERED` is a monotonic terminal sink. Any delayed or out-of-order `payment.failed` event for that `order_id` is recorded immutably in `payment_attempts` for audit completeness, but the state transition is dropped (`STALE_FAILURE_SUPPRESSED`). An older failure can never overwrite a verified payment state.
3. **Deterministic `reference_id` & Durable Local Intent (REQ-05):**
   - Before executing an external mutation, the worker creates an `execution_records` row with status `CLAIMED`, deterministic idempotency key, and unique `reference_id` (`ref_<opp_id[:8]>_<attempt_index>`).
   - Razorpay does not document support for an `Idempotency-Key` header on Payment Link creation; correlation relies on passing `reference_id`.
   - If the external call succeeds, the record transitions to `EXECUTED` with `external_reference_id = payment_link.id`.
   - If the external call times out, the record transitions to `RECONCILIATION_REQUIRED`.
4. **Pre-Retry Gateway Query & Reconciliation Reaper (REQ-05):**
   - Before any execution is retried or re-dispatched, the system executes a **Reconciliation Query** against Razorpay: querying by `reference_id` or `fetch_order_payments(order_id)`.
   - If the remote resource exists, LIFT claims it and transitions to `AWAITING_SETTLEMENT` without creating a duplicate link.
   - A background **Reconciliation Reaper** sweeps opportunities stuck in `ACTION_EXECUTING` for $> 5$ minutes, running remote reconciliation queries.

## Consequences
### Positive:
- Total immunity to webhook retry storms and duplicate processing.
- Older delayed events cannot roll back settled payments.
- Guaranteed prevention of duplicate customer payment links on worker crash or network timeout.
- Clean recovery from distributed failures without requiring complex distributed consensus or saga frameworks.

### Negative:
- Reconciliation sweeps require read queries against the Razorpay REST API.
- Transient network failures require a brief reconciliation window before concluding execution status.
