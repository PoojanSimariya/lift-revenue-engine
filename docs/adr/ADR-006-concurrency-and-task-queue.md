# ADR-006: Concurrency Control via Pessimistic Row Locking and PostgreSQL Task Queue

## Status
Accepted

## Context
Payment recovery operations involve money-affecting mutations: creating payment links, scheduling retries, and recording settlement evidence. Concurrent webhook deliveries for the same order (e.g. repeated failure events or a quick customer retry) create severe race condition hazards:
1. **Double-Charging / Duplicate Execution:** Two workers concurrently attempting to dispatch a payment link for the same failed attempt.
2. **Stale State Execution (TOCTOU):** A payment is captured organically, but a parallel worker dispatches a scheduled payment link or SMS because its in-memory view was stale.
3. **Queue Infrastructure Complexity:** Traditional async architectures rely on Celery or Arq with Redis/RabbitMQ, requiring dual-state synchronization between the database and the queue broker.

## Decision
1. **PostgreSQL Pessimistic Row Locking (`SELECT ... FOR UPDATE`):**
   - For all execution gating, opportunity state transitions, and contact counter updates, workers acquire exclusive row-level locks.
   - Lock acquisition order is strictly enforced to guarantee deadlock freedom:
     1. Lock `customers` row (`SELECT ... FOR UPDATE WHERE id = :customer_id`).
     2. Lock `recovery_opportunities` row (`SELECT ... FOR UPDATE WHERE id = :opportunity_id`).
   - Sessions configure `SET lock_timeout = '2s'`. On contention or deadlock (`40P01`), workers rollback and retry with exponential backoff and jitter (max 3 retries).
2. **Atomic Contact-Counter Mutation Under Lock:**
   - If the selected intervention contacts the customer (SMS, WhatsApp, Email, Custom Webhook), the worker executes:
     `UPDATE customers SET rolling_contacts_7d = rolling_contacts_7d + 1, last_contacted_at = NOW() WHERE id = :customer_id`
     **inside the Phase 1 transaction before COMMIT**.
   - Non-contact actions (`NO_ACTION`, `INTERNAL_RETRY_SCHEDULE`) do NOT increment rolling contacts or update `last_contacted_at`.
   - **Why This Prevents TOCTOU Limit Bypasses:** Any concurrent worker attempting outreach for the same customer must wait on the customer row lock. When acquired, it sees the updated counter and recent timestamp, preventing concurrent workers from exceeding merchant contact caps.
3. **Atomic 3-Phase Execution Pipeline:**
   - **Phase 1 (Atomic Claim & Contact State Update):** Under the locks, verify latest state (`OPEN` or `ACTION_SCHEDULED`), evaluate merchant policy rules, increment `rolling_contacts_7d` if outreach, increment `total_interventions_count` to atomically allocate `attempt_index`, generate deterministic `reference_id = 'ref_' + opp_id[:8] + '_' + attempt_index`, compute deterministic idempotency key `sha256(opp_id + ":" + type + ":" + attempt_index + ":" + merchant.idempotency_salt)`, insert `execution_records` row with status `CLAIMED`, update opportunity to `ACTION_EXECUTING`, and COMMIT transaction (releasing all locks).
   - **Phase 2 (Out-of-Transaction Dispatch):** External HTTP API call to Razorpay (passing `reference_id`) or customer messaging channel is executed **outside** of any database transaction. For `INTERNAL_RETRY_SCHEDULE`, enqueues a future dispatch task into `task_queue`. Zero database locks are held during network transit.
   - **Phase 3 (Settlement Update):** Fast transaction updating execution status, recording external reference ID, and advancing opportunity to `AWAITING_SETTLEMENT`.
4. **PostgreSQL Task Queue using `SELECT ... FOR UPDATE SKIP LOCKED`:**
   - Background tasks reside in the `task_queue` relational table.
   - Webhook ingestion and task enqueueing occur in the **exact same database transaction**.
   - Workers poll tasks using `FOR UPDATE SKIP LOCKED`, preventing lock contention between parallel worker processes.
   - Claiming a task atomically transitions opportunity from `OPEN` to `IN_EVALUATION`.
4. **Testing Concurrency Exclusively on PostgreSQL:**
   - SQLite lacks multi-process row-level `SELECT ... FOR UPDATE` semantics. Concurrency correctness tests must execute against real PostgreSQL (via Docker / Testcontainers). SQLite is restricted to non-concurrent unit tests of mathematical formulas.

## Consequences
### Positive:
- Zero race conditions: latest-state check and execution voucher claim happen atomically under row locks.
- Zero extra infrastructure: eliminates Redis, RabbitMQ, and Celery operational overhead.
- Transactional enqueueing: impossible for a webhook to be acknowledged without its processing task being persisted.
- Deadlock-free operation guaranteed by strict hierarchical lock ordering.

### Negative:
- In-flight database connections are held during the atomic claim transaction (mitigated by sub-5ms local transaction boundaries).
- Requires Docker or local PostgreSQL instance for running concurrency test suites.
