# Engineering Development Roadmap & Milestones

**Document Status:** Pending Architecture Review
**Project:** Razorpay AI Buildathon 2026 — Track 03 (LIFT Engine)

---

## 1. Roadmap Principles

Each milestone is designed around a **verifiable, testable functional increment** rather than arbitrary code generation. No milestone is considered complete without accompanying unit/integration tests and updated documentation.

```mermaid
gantt
    title LIFT Engineering Milestones
    dateFormat  YYYY-MM-DD
    section Phase 1: Core Foundation
    M1: Domain & Economics Engine       :m1, 2026-09-06, 3d
    M2: Synthetic Data & Baselines       :m2, after m1, 3d
    section Phase 2: Gateway & Safety
    M3: Razorpay Webhook & Ingestion     :m3, after m2, 3d
    M4: 8-Stage Execution Safety Gate    :m4, after m3, 3d
    section Phase 3: AI & Intelligence
    M5: Constrained AI & Diagnosis       :m5, after m4, 3d
    section Phase 4: Product Experience
    M6: Console, Replay & Recovery Lab   :m6, after m5, 4d
    section Phase 5: Verification & Polish
    M7: E2E Verification & Hardening     :m7, after m6, 3d
```

---

## 2. Milestone Breakdown

### Milestone 1: Core Domain Model, State Machine & Deterministic Economic Engine
- **Objective:** Establish foundational business entities, circular FK insertion procedure, multi-attempt state transition machines, computable contact fatigue function, and mathematical implementation of Net Incremental Recovery Value (NIRV) with `AmountAtRisk` friction proxy.
- **Core Deliverables:**
  - Pydantic domain models for `PaymentAttempt`, `RecoveryOpportunity`, `InterventionCandidate`, `MerchantPolicy`.
  - Multi-attempt order association (`uq_opportunity_order` with 1..* `PaymentAttempt` foreign key) and 3-step circular FK insertion sequence with non-null `latest_attempt_id`.
  - Strict Opportunity State Machine with transition validation, `attempt_index` sequence ownership, and monotonic terminal sink (`RECOVERED`).
  - Deterministic implementation of NIRV formula, including explicit $P(\text{Organic})$ parameterization (OBSERVED vs ESTIMATED vs CONFIGURED vs SIMULATED), `AmountAtRisk` friction proxy with $\lambda_{\text{friction}} = 0.05$, and integer subunit currency calculations.
  - Closed-form computable `ContactFatigue` function based on `rolling_contacts_7d` and `last_contacted_at`.
- **Definition of Done:**
  - 100% unit test coverage on currency math, NIRV calculation, fatigue decay, and valid/invalid state transitions.
  - Invariant verified: No transition out of `RECOVERED` state permitted, even on delayed failure events.
- **Documentation:** Inline docstrings and architecture alignment notes.

### Milestone 2: Synthetic Causal Data Generator & Baseline Strategy Harness
- **Objective:** Create a reproducible, seeded synthetic benchmark generator with an independent causal DGP (`lift.simulation.dgp`), implementing the three formal baseline strategies and pessimistic cohorts.
- **Core Deliverables:**
  - Seedable generator (`seed=42`, `seed=2026`) producing realistic distributions of customers, cards, failure codes, and diurnal timestamps.
  - Implementation of Baseline 0 (Pure Organic Holdout), Baseline 1 (Static Periodic Retry), and Baseline 2 (Naive Generic Outreach).
  - Four pessimistic test cohorts where LIFT correctly loses to simpler baselines or abstains (High Organic Recovery $P_{\text{org}} = 0.85$, Micro-Ticket sub-₹50, Terminal Hard Declines, High Friction).
  - Comparative benchmark runner outputting metrics table (Gross GMV, Measured Organic Baseline, Estimated Organic Recovery, Net Incremental Value).
  - AST verification asserting zero DGP parameter imports inside scoring models (`lift.recovery.*`).
- **Definition of Done:**
  - Test verifying exact mathematical reproducibility across two runs with identical seeds.
  - Proof that LIFT chooses `NO_ACTION` / `PASSIVE_WAIT` on high organic recovery cohorts, and loses heavily to Baseline 0 if forced to intervene.
- **Documentation:** Benchmark methodology guide in `docs/`.

### Milestone 3: Razorpay Integration & Webhook Ingestion Engine
- **Objective:** Implement authentic integration with Razorpay Test Mode, `x-razorpay-event-id` validation, deterministic `reference_id` generation, and PostgreSQL-backed task queue.
- **Core Deliverables:**
  - `PaymentGatewayAdapter` interface with `RazorpayTestModeAdapter` and `DeterministicSimulatorAdapter` (generic `trigger_smart_retry` removed; authentic Payment Links with `reference_id` and status APIs only).
  - Webhook endpoint ingesting `payment.failed`, `payment.authorized`, `payment.captured`, `payment_link.paid`, and `payment_link.expired`.
  - Constant-time HMAC-SHA256 signature verification.
  - Mandatory `x-razorpay-event-id` validation (HTTP 400 if missing) and deduplication via `webhook_events` table returning immediate HTTP 200 without duplicate processing.
  - PostgreSQL-backed task queue enqueueing (`task_queue`) in the same database transaction as webhook insert (`< 50ms` response).
- **Definition of Done:**
  - Integration tests verifying rejection of invalid/tampered signatures (`401 Unauthorized`) and missing event ID (`400 Bad Request`).
  - Deduplication test proving repeated webhook delivery with identical event ID creates exactly one task.
  - Successful creation and status polling of actual Razorpay Test Mode Payment Links using unique `reference_id`.
- **Documentation:** Gateway setup instructions and required environment variables in `.env.example`.

### Milestone 4: Atomic Execution Safety Gate & Concurrency Pipeline
- **Objective:** Build the 3-phase execution safety pipeline with PostgreSQL pessimistic row locking (`SELECT ... FOR UPDATE`), atomic `attempt_index` allocation, atomic contact counter increment, and external reconciliation.
- **Core Deliverables:**
  - PostgreSQL pessimistic row locking acquiring locks in strict global order (`customers` then `recovery_opportunities`).
  - Atomic verification of latest state, checking policy rules (quiet hours in merchant timezone), incrementing `rolling_contacts_7d` and setting `last_contacted_at = NOW()` if outreach, and incrementing `total_interventions_count` to allocate `attempt_index`.
  - Deterministic idempotency key computation: `sha256(opp_id + ":" + type + ":" + attempt_index + ":" + merchant_salt)` using `merchants.idempotency_salt`.
  - Two-phase execution dispatch (DB lock released before external HTTP call).
  - Stuck-worker and reconciliation reaper sweeping `IN_EVALUATION` $> 2$ min and `ACTION_EXECUTING` $> 5$ min, querying Razorpay API via `reference_id` before retrying.
  - Worker lifecycle management: worker claims task and transitions opportunity from `OPEN` to `IN_EVALUATION`.
- **Definition of Done:**
  - Multi-process concurrency tests against PostgreSQL asserting zero duplicate executions and zero contact-limit bypasses under concurrent webhook storms.
  - Test verifying an action is cleanly aborted if a `payment.captured` event arrives right before execution claim.
  - Reconciliation test proving remote payment links are claimed without duplicate creation on worker crash.
- **Documentation:** Execution safety invariants and concurrency guide.

### Milestone 5: Constrained AI Layer & Explainable Diagnosis
- **Objective:** Integrate the failure diagnosis classifier and constrained generative model for copy generation and natural language explanations with zero intervention authority.
- **Core Deliverables:**
  - Tabular failure taxonomy classifier mapping raw codes into 5 core failure classes.
  - Assistive LLM client with Pydantic JSON Schema enforcement (`recommended_intervention_type` strictly removed; outputs diagnostic evidence, candidate observations, and message drafts).
  - Prompt sanitization preventing prompt injection from customer metadata.
  - Decision explanation generator for the audit trail.
  - Graceful fallback to deterministic defaults on model timeout or error.
- **Definition of Done:**
  - Adversarial tests asserting that prompt injection payloads in customer names fail to alter JSON schemas or bypass policies.
  - Unit test verifying LLM output contract does NOT contain intervention selection authority.
- **Documentation:** Schema specifications and prompt design guide.

### Milestone 6: Operator Workstation, Decision Replay & Recovery Lab UI
- **Objective:** Deliver the fintech revenue operations interface with Decision Replay, Why-Not Inspector, Recovery Lab, 5-second polling, and honest organic metric labels.
- **Scope Segmentation (Audit finding resolution):**
  - **MVP / Demo-Critical Features:**
    - High-density Revenue-at-Risk Overview with NIRV waterfall chart explicitly separating Measured Organic Baseline from Estimated Organic Recovery.
    - Recovery Opportunities Data Grid with 5-second periodic polling and manual sync.
    - Step-by-step **Decision Replay** drawer displaying exact diagnosis, economic slate, policy results, and payment evidence.
    - **Why-Not? Inspector** detailing blocked decisions (quiet hours, contact limits, negative NIRV).
    - **Recovery Lab Benchmark View** displaying side-by-side strategy metrics (Baselines 0, 1, 2 vs LIFT) and pessimistic cohort results.
  - **Deferred Polish Features (Post-Demo):**
    - Interactive parameter adjustment sliders in Recovery Lab for ad-hoc parameter sensitivity exploration.
    - End-to-end browser automation suites (Playwright/Cypress); verification relies on React component testing and comprehensive API integration tests.
- **Definition of Done:**
  - All MVP/Demo-critical UI flows fully operational on mock and live test-mode data.
  - Zero placeholder UI or mock chat widgets.
- **Documentation:** UI user guide and workflow screenshots.

### Milestone 7: End-to-End System Hardening, Evaluation Benchmarking & Polish
- **Objective:** Final end-to-end validation across both Razorpay Test Mode and large-scale simulation batches.
- **Core Deliverables:**
  - Full automated test suite execution (unit, integration, PostgreSQL concurrency, security, E2E).
  - Formal evaluation report benchmarking LIFT against Baselines 0, 1, and 2 across 10,000 synthetic opportunities.
  - Verification of pessimistic test cohorts where LIFT correctly loses or abstains.
  - Video demonstration script and architecture review walkthrough.
- **Definition of Done:**
  - All test suites green; zero lint errors; zero security vulnerabilities.
  - Single-tenant configuration verified via `.env.example`.
