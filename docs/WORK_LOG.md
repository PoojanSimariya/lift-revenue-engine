# Engineering Work Log

This document records meaningful project milestones, decisions, discoveries, failures, and fixes.

The purpose is to preserve an understandable engineering history throughout the project.

---

## 2026-09-05 — Repository Foundation

### Completed

- Established the local project workspace.
- Initialized the Git repository.
- Established `main` as the primary branch.
- Created the initial project workspace structure.
- Added repository-level engineering guidelines.
- Added initial project context.
- Connected the local repository to GitHub.
- Published the initial `main` branch.

### Current Stage

Repository foundation completed.

### Next Stage

Product definition and architecture specification.

### Implementation State

No major application implementation has started.

---

## Engineering Principle

The repository should evolve through small, meaningful and reviewable increments rather than a single large generated implementation.

---

## 2026-09-05 — Architecture Discovery Started

### Completed

- Repository foundation verified.
- Development branch verified.
- Antigravity repository observation completed successfully.
- Antigravity confirmed project constraints and current status without modifying the repository.

### Decision

Architecture discovery will precede implementation.

### Review Strategy

The first architecture proposal will be independently challenged before implementation.

### Next Stage

Product definition and architecture specification.

---

## 2026-09-05 — Product Definition & Architecture Specification Completed

### Completed

- Authored complete, staff-grade product and architecture specification suite:
  - `docs/PRODUCT_SPECIFICATION.md`: Problem statement, personas, user journeys, scope boundaries, and competitive differentiation.
  - `docs/DOMAIN_MODEL.md`: Ubiquitous domain language, formal entity definitions, and lifecycle state machines.
  - `docs/ARCHITECTURE.md`: Topology comparison (Modular Monolith + Async Worker), component boundaries, 8-stage execution safety pipeline, and Razorpay integration.
  - `docs/DATA_MODEL.md`: Relational SQL schemas, constraints, integer subunit currency rules, and indexes.
  - `docs/AI_SYSTEM_DESIGN.md`: Non-authoritative AI boundary, predictive scoring vs. generative copy, prompt injection defense, and schema contracts.
  - `docs/EVALUATION_AND_SECURITY.md`: Mathematical formulation of Net Incremental Recovery Value (NIRV), 3 formal deterministic baselines, STRIDE threat model, and comprehensive test matrix.
  - `docs/UX_CONCEPT.md`: Fintech revenue workstation interface concepts (Decision Replay, Why-Not Inspector, Recovery Lab).
  - `docs/DEVELOPMENT_ROADMAP.md`: 7 verifiable milestones with concrete definitions of done.
  - `docs/adr/`: Formal ADRs covering architecture style (ADR-001), AI execution boundaries (ADR-002), intervention economics & baselines (ADR-003), dual-mode Razorpay adapter (ADR-004), and technology stack selection (ADR-005).

### Current Stage

Product and architecture specification completed. Submitted for independent review and challenge.

### Implementation State

No application code, package manifests, or database migrations created. Zero Git modifications. Awaiting review approval.

---

## 2026-09-05 — Architecture Correction Pass Completed (Review Verdict: Approved with Required Changes)

### Completed

- Resolved all 11 blocking architecture review findings and all 5 additional review corrections:
  1. **REQ-01 (Define P(Organic)):** Formulated exact definitions of $P(\text{Organic} \mid \mathbf{x})$, distinguishing OBSERVED vs ESTIMATED vs CONFIGURED vs SIMULATED; defined data lineage with right-censoring at intervention timestamp to prevent intervention-contamination; defined uncertainty representation ($\sigma_{\text{org}}$) and hierarchical shrinkage; removed all undocumented hardcoded 0.05 defaults; established pessimistic test cohorts where LIFT correctly loses or abstains.
  2. **REQ-02 (Remove LLM Intervention Authority):** Completely removed `recommended_intervention_type` from the LLM output contract and schemas; LLM is strictly assistive (diagnosis reasoning, evidence clues, candidate observations, dunning drafts, explanations); deterministic policy engine selects winning intervention.
  3. **REQ-03 & REQ-10 (Atomic Execution Gate & PostgreSQL Concurrency):** Replaced conceptual gate with concrete 3-phase atomic transaction pipeline; selected PostgreSQL Pessimistic Row Locking (`SELECT ... FOR UPDATE`) as the sole concurrency mechanism; specified lock acquisition order (`customers` then `recovery_opportunities`), deadlock retry with jitter, transaction boundaries (no network I/O held under DB locks), and worker crash behavior; confirmed `attempt_index` sequence ownership by `RecoveryOpportunity`; excluded SQLite from concurrency correctness testing.
  4. **REQ-04 (Out-of-Order Webhooks):** Defined event ordering metadata, supported Razorpay fields, and monotonic terminal sink (`RECOVERED`) preventing older failure webhooks from overwriting verified payment captures.
  5. **REQ-05 (External API Success / Local DB Failure & Reconciliation):** Defined durable execution intent (`CLAIMED`), pre-retry gateway reconciliation queries against Razorpay (`fetch_order_payments` / payment link lookup), and background reconciliation reaper for stuck workers.
  6. **REQ-06 (Correct Smart Retry):** Removed fictitious generic `trigger_smart_retry` method from gateway adapter; restricted adapter to verified Razorpay REST APIs (Payment Links, Orders, Payments); established internal `RetryStrategyExecutor` for timed retry orchestration.
  7. **REQ-07 (Webhook Deduplication):** Defined `webhook_events` table keyed by authoritative `x-razorpay-event-id`; returns immediate HTTP 200 on duplicate without re-enqueuing.
  8. **REQ-08 (Payment Attempt / Opportunity Association):** Defined multi-attempt foreign-key architecture (`recovery_opportunity_id` in `payment_attempts` with 1..* relationship to `recovery_opportunities`).
  9. **REQ-09 (Define Contact Fatigue):** Established deterministic mathematical function for `ContactFatigue` with 48h exponential decay, rolling 7-day touch counters, and channel intrusion weights.
  10. **REQ-11 (Resolve Task Queue):** Evaluated alternatives and selected native PostgreSQL-backed task queue (`task_queue`) using `SELECT ... FOR UPDATE SKIP LOCKED`, achieving zero extra broker infrastructure and transactional enqueueing.
  11. **Additional Review Corrections:** Separated Measured Organic Baseline from Estimated Organic Recovery in UX; codified 5-second polling dashboard model (no WebSockets); established environment-variable credential management; clarified single-merchant demo scope; defined stuck-worker reaper sweepers.
- Updated all existing specification documents (`PRODUCT_SPECIFICATION.md`, `DOMAIN_MODEL.md`, `ARCHITECTURE.md`, `DATA_MODEL.md`, `AI_SYSTEM_DESIGN.md`, `EVALUATION_AND_SECURITY.md`, `UX_CONCEPT.md`, `DEVELOPMENT_ROADMAP.md`).
- Revised ADRs 001 through 005 and created ADR-006 (Concurrency & Task Queue) and ADR-007 (Webhook Ordering & Reconciliation).

### Implementation State

Zero application code, package manifests, or database migrations created. Zero Git modifications. Ready for Milestone 1 implementation upon review approval.

---

## 2026-09-05 — Independent Adversarial Audit Correction Pass (Claude Sonnet 4.6 Thinking MAX)

### Completed

- Received independent adversarial architecture audit report from standalone Claude Sonnet 4.6 Thinking MAX (Verdict: **GO WITH REQUIRED CHANGES**).
- Completed systematic, documentation-only architecture correction pass across all specification files and ADRs to ensure every architectural concept, formula, state transition, and execution path is 100% implementable without an engineer inventing missing variables:
  1. **Finding 1 (Customer LTV / Friction Cost):** Replaced speculative `CustomerLTV(c)` with observable buildathon proxy `AmountAtRisk(i)`. Formula: $\text{FrictionCost}(a, c_i) = \lambda_{\text{friction}} \times \text{AmountAtRisk}(i) \times \text{ContactFatigue}(c_i, a, t)$, with $\lambda_{\text{friction}} = 0.05$. Fully computable from existing schema; undefined `CustomerLTV` variable eliminated.
  2. **Finding 2 (Computable ContactFatigue):** Replaced unimplementable history-scanning formula with closed-form buildathon formulation based strictly on `rolling_contacts_7d`, `last_contacted_at`, and candidate channel weight. Defined behavior for $N=0$ and NULL `last_contacted_at`.
  3. **Finding 3 (Atomic Contact-Count Update):** Phase 1 execution gate acquires customer row lock (`SELECT ... FOR UPDATE`), verifies policy limit, and if the action contacts the customer, atomically increments `rolling_contacts_7d` and sets `last_contacted_at = NOW()` before COMMIT. Eliminates TOCTOU counter bypass by parallel workers.
  4. **Finding 4 (Merchant Salt / Idempotency Key):** Added `idempotency_salt VARCHAR(64) NOT NULL` to `merchants` table (generated via `secrets.token_hex(32)`, never exposed in UI/API). Defined canonical hash input: `SHA256(UTF8(opportunity_id + ":" + intervention_type + ":" + attempt_index + ":" + merchant_salt))`.
  5. **Finding 5 (Payment Link Reconciliation):** Generated unique `reference_id` (`ref_<opp_id[:8]>_<attempt_index>`), stored locally in `execution_records` before external dispatch, passed to Razorpay Payment Link `reference_id` parameter. Reconciliation uses `reference_id` via webhooks or API lookup; does NOT assume unsupported idempotency headers.
  6. **Finding 6 (Economic Parameters Defined):** Full parameter table added defining meaning, unit, range, default, storage, and engine usage for $\lambda_{\text{friction}} = 0.05$, $\beta = 0.10$, $\text{Uncertainty}(i) = 1.0 - \text{confidence\_score}(i)$, and $P_{\text{global\_prior}}$ dictionary. Speculative $\sigma_{\text{rec}}$ eliminated.
  7. **Finding 7 (INTERNAL_RETRY_SCHEDULE Execution Path):** Defined strictly as scheduling a future Payment Link dispatch task in `task_queue` (e.g. morning quiet-hours exit). Direct card re-debiting is explicitly excluded as unsupported.
  8. **Finding 8 (`payment.authorized`):** Documented non-terminal status; transitions to / holds in `AWAITING_SETTLEMENT`. `RECOVERED` requires verified capture proof (`payment.captured`, `order.paid`, `payment_link.paid`).
  9. **Finding 9 (`payment_link.expired`):** Defined external webhook trigger transitioning opportunity from `AWAITING_SETTLEMENT` to `OPEN` (if retry budget/window remains) or terminal `EXPIRED`.
  10. **Finding 10 (Circular FK Insertion):** Documented 3-step transactional procedure inserting initial `payment_attempts` with `NULL` opportunity $\rightarrow$ inserting `recovery_opportunities` referencing the attempt $\rightarrow$ backfilling `recovery_opportunity_id`.
  11. **Finding 11 (`latest_attempt_id`):** Explicitly set to `initial_attempt_id` on creation; non-NULL in schema.
  12. **Finding 12 (Global Failure-Category Priors):** Defined immutable dictionary `GLOBAL_FAILURE_PRIORS` for Bayesian segment shrinkage.
  13. **Finding 13 (Quiet Hours Timezone):** Evaluated strictly in merchant timezone (`merchants.timezone`, default `'Asia/Kolkata'`). All claims of "local customer timezone" removed.
  14. **Finding 14 (PII Policy):** Documented SHA-256 hashing for `phone_hash` and `email_hash`; raw gateway payloads retained for audit/test debugging, secrets never stored.
  15. **Finding 15 (DGP Independence):** Causal DGP code isolated in `lift.simulation.dgp`; scoring models in `lift.recovery.*` import zero DGP parameters.
  16. **Finding 16 (Audit Event Tenancy):** Added `merchant_id UUID NOT NULL REFERENCES merchants(id)` to `audit_events`.
  17. **Finding 17 (M6 Scope):** Segmented into MVP/Demo-Critical (Overview, 5s polled grid, Decision Replay drawer, Why-Not list) vs Deferred Polish (sandbox sliders, automated browser suites).
  18. **Finding 18 (`IN_EVALUATION` Transition):** Worker atomically transitions opportunity from `OPEN` to `IN_EVALUATION` upon claiming task from `task_queue`.
  19. **Finding 19 (Missing Webhook Event ID):** Webhook endpoint rejects requests missing `x-razorpay-event-id` with `HTTP 400 Bad Request`.
- Documents updated: `docs/PRODUCT_SPECIFICATION.md`, `docs/DOMAIN_MODEL.md`, `docs/ARCHITECTURE.md`, `docs/DATA_MODEL.md`, `docs/AI_SYSTEM_DESIGN.md`, `docs/EVALUATION_AND_SECURITY.md`, `docs/UX_CONCEPT.md`, `docs/DEVELOPMENT_ROADMAP.md`, `docs/adr/ADR-003-intervention-economics-and-evaluation.md`, `docs/adr/ADR-004-razorpay-integration-and-simulation-boundary.md`, `docs/adr/ADR-006-concurrency-and-task-queue.md`, `docs/adr/ADR-007-webhook-ordering-and-reconciliation.md`.

### Implementation State

Zero application code, package manifests, or database migrations created. Zero Git operations (no commits, pushes, branch switches). All 19 findings addressed. Specifications are fully synchronized and ready for Milestone 1.
