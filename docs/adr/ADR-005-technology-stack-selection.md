# ADR-005: Technology Stack Selection

## Status
Accepted (Revised after Principal Review)

## Context
The project requires:
1. Strict schema validation and JSON serialization for untrusted inputs and LLM structured outputs.
2. Mathematical and statistical modeling for Intervention Economics, counterfactuals, and seeded simulation generation.
3. High-performance, low-latency webhook ingestion ($< 50\text{ms}$ response target) with atomic task enqueueing.
4. Official Razorpay API integration with HMAC-SHA256 signature verification.
5. An auditable relational data store with pessimistic row-locking semantics and transactional task queuing.
6. A high-density, reliable fintech operations UI with Decision Replay, Recovery Lab, and Opportunity explorer.

## Options Considered for Background Execution & Task Queue (REQ-11)
- **Option 1: Redis + Celery:** Heavy operational footprint, complex broker configuration, separate failure domains, dual-state management.
- **Option 2: Redis + Arq:** Lightweight, but introduces a second infrastructure daemon (Redis) that must be kept synchronized with database transactions.
- **Option 3: PostgreSQL Task Queue using `FOR UPDATE SKIP LOCKED` (Selected):** Zero extra infrastructure. Tasks are enqueued within the exact same database transaction as webhook events (atomic delivery; zero dropped tasks). Multi-worker contention is completely avoided via native row-level skip locking. Perfect for our operational volume ($< 500\text{ tasks/sec}$).

## Options Considered for Concurrency Control (REQ-10)
- **Option 1: Optimistic Versioning (`version` column):** Suffers from high transaction abort and rollback rates during bursty webhook storms for the same order.
- **Option 2: PostgreSQL Pessimistic Row Locking (`SELECT ... FOR UPDATE`) (Selected):** Strict row-level serializability on `recovery_opportunities` and `customers`. Clean deadlock prevention via hierarchical lock acquisition.

## Final Stack Decision
- **Backend Core:** Python 3.11+ with FastAPI, Pydantic v2, and SQLAlchemy 2.0.
- **Background Worker & Queue (REQ-11):** Native **PostgreSQL task queue using `SELECT ... FOR UPDATE SKIP LOCKED`**. Zero Redis or Celery dependencies.
- **Concurrency & Locking (REQ-10):** **PostgreSQL Pessimistic Row Locking (`SELECT ... FOR UPDATE`)**. SQLite is strictly restricted to fast unit tests of mathematical formulas and domain rules; all concurrency and execution-gate tests require PostgreSQL (via Docker / Testcontainers).
- **Official Gateway Client (REQ-06):** Official `razorpay` Python SDK with custom `PaymentGatewayAdapter` interfaces.
- **Database & Queue Engine:** PostgreSQL 16+.
- **Frontend Console:** Modern TypeScript SPA (Vite + React) with **predictable 5-second periodic polling and manual sync** (no WebSockets).
- **Configuration & Secrets:** Strict environment variable injection (`.env`) with Pydantic `BaseSettings`. Single-merchant demo deployment scope.

## Consequences
### Positive:
- Drastically reduced infrastructure complexity: single PostgreSQL database handles relational data, immutable audit logs, deduplication, and task queues.
- Webhook ingestion and task enqueueing are 100% transactional: impossible to acknowledge a webhook without persisting the processing task.
- Clean concurrency guarantees verified against real PostgreSQL locking behavior.
- Python ecosystem provides native support for NumPy/SciPy statistical modeling and Pydantic schema validation.

### Negative:
- Frontend polling generates predictable lightweight HTTP traffic every 5 seconds rather than event-driven push (well within local server capacity).
