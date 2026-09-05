# ADR-001: Architecture Style — Modular Monolith with PostgreSQL Task Worker

## Status
Accepted (Revised after Principal Review)

## Context
The system must process inbound payment failure events from payment gateways (Razorpay webhooks), calculate recovery economics, evaluate merchant policies, draft outreach, and execute bounded actions.

Razorpay webhooks require immediate acknowledgment ($< 2000\text{ms}$, ideally $< 50\text{ms}$). Complex AI calls, model inference, and multi-step execution workflows cannot be run synchronously within the HTTP webhook handler without severe timeout risk.

Conversely, a fully distributed microservices architecture (e.g., separate services for Ingestion, Decisioning, LLM, Execution, Audit) introduces excessive operational friction: distributed transaction management, network serialization, deployment overhead, and complex local testing.

Furthermore, introducing a separate external queue broker (Redis, RabbitMQ, or Celery) adds operational overhead, dual-system failure modes, and potential disconnect between database transactions and message dispatch.

## Decision
We adopt a **Modular Monolith with a PostgreSQL-Backed Task Worker (`FOR UPDATE SKIP LOCKED`)** sharing a unified relational database:
1. **API & Ingestion Service:** Fast, lightweight HTTP service that verifies webhook signatures, records raw events, deduplicates on `x-razorpay-event-id`, and enqueues tasks within the **same local database transaction**.
2. **Background Task Worker:** Consumes tasks from `task_queue` using PostgreSQL `SELECT ... FOR UPDATE SKIP LOCKED`, manages opportunity state transitions, runs ML/AI inference, evaluates deterministic policies, and dispatches bounded actions.
3. **Modular Internal Boundaries:** Strict separation of internal domain packages with zero circular dependencies.

## Consequences
### Positive:
- Single infrastructure dependency: PostgreSQL handles core transactional entities, immutable audit logs, deduplication, and task queues.
- Webhook response latency remains $< 50\text{ms}$, eliminating webhook timeout retries from Razorpay.
- Transactional enqueueing: Enqueueing happens inside the same DB transaction as the webhook/event write (guaranteed atomicity; zero orphan tasks or lost events).
- `FOR UPDATE SKIP LOCKED` eliminates task lock contention across multiple concurrent worker processes.
- Identical code paths can be executed inline during batch simulations and asynchronously during live webhook ingestion.

### Negative:
- Web and worker processes must share deployment cycles and schema migrations.
- High-volume queuing load shares database connection pools with operational queries (mitigated by bounded task batch sizes and indexing).
