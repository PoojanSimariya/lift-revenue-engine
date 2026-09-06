# Data Model & Schema Specification

**Document Status:** Architecture Review Approved with Corrections
**Project:** Razorpay AI Buildathon 2026 — Track 03 (LIFT Engine)

---

## 1. Schema Design Principles

1. **Integer Subunits for Currency:** All monetary amounts are stored as 64-bit integers (`BIGINT`) representing currency subunits (e.g., paise for INR, cents for USD) to eliminate IEEE-754 floating-point inaccuracies.
2. **Immutable Audit Trails:** State changes, policy checks, and execution vouchers are written to append-only tables with microsecond timestamps and trace IDs.
3. **Strict Unique Constraints:** Idempotency keys, webhook event IDs (`x-razorpay-event-id`), and gateway payment IDs enforce database-level uniqueness to prevent duplicate execution under concurrent loads.
4. **PostgreSQL Pessimistic Row Locking (`SELECT ... FOR UPDATE`):** Concurrency control is strictly enforced using PostgreSQL row-level locks during state transitions and execution claims. SQLite is not supported for concurrent multi-process tests.
5. **Transactional Queue Enqueueing:** The PostgreSQL-backed task queue (`task_queue`) enables enqueuing tasks in the exact same transaction as webhook ingestion.

---

## 2. Table Definitions & Constraints

### 2.1 Table: `merchants`
Represents the merchant organization configuring the LIFT engine. Designed for clean multi-tenant hygiene even in the single-merchant buildathon deployment.
```sql
CREATE TABLE merchants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    default_currency VARCHAR(3) NOT NULL DEFAULT 'INR',
    timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Kolkata',
    idempotency_salt VARCHAR(64) NOT NULL, -- 32-byte hex generated via secrets.token_hex(32). Server-side secret, never exposed in API/UI.
    razorpay_key_id VARCHAR(128),
    razorpay_key_secret_encrypted TEXT,
    razorpay_webhook_secret_encrypted TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
```

### 2.2 Table: `customers`
Tracks customer contact history and contact fatigue metrics.
```sql
CREATE TABLE customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id UUID NOT NULL REFERENCES merchants(id) ON DELETE CASCADE,
    external_customer_id VARCHAR(128) NOT NULL,
    phone_hash VARCHAR(64), -- SHA-256 hash of normalized E.164 phone
    email_hash VARCHAR(64), -- SHA-256 hash of normalized lowercased email
    risk_tier INT NOT NULL DEFAULT 1, -- 1=Standard, 2=Elevated, 3=VIP/Enterprise
    lifetime_recovery_count INT NOT NULL DEFAULT 0,
    lifetime_failure_count INT NOT NULL DEFAULT 0,
    rolling_contacts_7d INT NOT NULL DEFAULT 0,
    last_contacted_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_merchant_external_customer UNIQUE (merchant_id, external_customer_id)
);
CREATE INDEX idx_customers_merchant_id ON customers(merchant_id);
```

### 2.3 Table: `policy_rules`
Deterministic merchant guardrails.
```sql
CREATE TABLE policy_rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id UUID NOT NULL REFERENCES merchants(id) ON DELETE CASCADE,
    rule_type VARCHAR(64) NOT NULL, -- 'QUIET_HOURS', 'MAX_CONTACTS_WINDOW', 'MIN_AMOUNT_SMS', 'MAX_RETRIES'
    parameters JSONB NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_policy_rules_merchant ON policy_rules(merchant_id, is_active);
```

### 2.4 Table: `webhook_events` (REQ-07)
Deduplicates incoming Razorpay webhooks using the authoritative `x-razorpay-event-id`.
```sql
CREATE TABLE webhook_events (
    event_id VARCHAR(128) PRIMARY KEY, -- x-razorpay-event-id (mandatory, HTTP 400 if absent)
    event_type VARCHAR(64) NOT NULL,
    payload JSONB NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING', -- 'PENDING', 'PROCESSED', 'FAILED'
    received_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP WITH TIME ZONE
);
CREATE INDEX idx_webhook_events_status ON webhook_events(status);
```

### 2.5 Table: `task_queue` (REQ-11)
PostgreSQL-backed task queue processed via `SELECT ... FOR UPDATE SKIP LOCKED`.
```sql
CREATE TABLE task_queue (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_type VARCHAR(64) NOT NULL, -- 'PROCESS_WEBHOOK', 'EVALUATE_OPPORTUNITY', 'EXECUTE_INTERVENTION', 'RECONCILE_STUCK_WORKER'
    payload JSONB NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'QUEUED', -- 'QUEUED', 'RUNNING', 'COMPLETED', 'FAILED'
    priority INT NOT NULL DEFAULT 10,
    scheduled_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    locked_by VARCHAR(64),
    locked_at TIMESTAMP WITH TIME ZONE,
    attempts INT NOT NULL DEFAULT 0,
    max_attempts INT NOT NULL DEFAULT 3,
    last_error TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_task_queue_poll ON task_queue(status, scheduled_at, priority ASC);
```

### 2.6 Table: `payment_attempts` (REQ-08)
Immutable record of each payment transaction event ingested from Razorpay. Supports multiple attempts per recovery opportunity.
```sql
CREATE TABLE payment_attempts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    recovery_opportunity_id UUID, -- Nullable initially to allow circular insertion, set in step 3
    razorpay_payment_id VARCHAR(64) NOT NULL UNIQUE,
    razorpay_order_id VARCHAR(64) NOT NULL,
    attempt_sequence INT NOT NULL DEFAULT 1,
    amount_subunits BIGINT NOT NULL,
    currency VARCHAR(3) NOT NULL DEFAULT 'INR',
    payment_method VARCHAR(32) NOT NULL, -- 'card', 'upi', 'netbanking', 'wallet'
    status VARCHAR(32) NOT NULL, -- 'failed', 'authorized', 'captured'
    error_code VARCHAR(64),
    error_description TEXT,
    error_source VARCHAR(64),
    error_step VARCHAR(64),
    error_reason VARCHAR(64),
    gateway_created_at TIMESTAMP WITH TIME ZONE NOT NULL, -- Epoch created_at from Razorpay payload
    raw_payload JSONB NOT NULL, -- Preserves gateway truth. Secrets never stored; see PII policy below.
    ingested_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_payment_attempts_order ON payment_attempts(razorpay_order_id);
CREATE INDEX idx_payment_attempts_opp ON payment_attempts(recovery_opportunity_id);
```

### 2.7 Table: `recovery_opportunities` (REQ-01, REQ-03, REQ-08)
The central stateful aggregate managing the recovery lifecycle for a failed order.
```sql
CREATE TABLE recovery_opportunities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id UUID NOT NULL REFERENCES merchants(id) ON DELETE CASCADE,
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    order_id VARCHAR(64) NOT NULL,
    initial_attempt_id UUID NOT NULL REFERENCES payment_attempts(id),
    latest_attempt_id UUID NOT NULL REFERENCES payment_attempts(id), -- Initialized to initial_attempt_id, updated on subsequent attempts
    amount_at_risk_subunits BIGINT NOT NULL,
    currency VARCHAR(3) NOT NULL DEFAULT 'INR',
    current_state VARCHAR(32) NOT NULL DEFAULT 'OPEN',
    -- 'OPEN', 'IN_EVALUATION', 'ACTION_SCHEDULED', 'ACTION_EXECUTING', 'AWAITING_SETTLEMENT', 'RECONCILIATION_REQUIRED', 'ACTION_BLOCKED', 'ESCALATED_HUMAN', 'RECOVERED', 'EXPIRED', 'TERMINATED'
    failure_category VARCHAR(64) NOT NULL,
    organic_recovery_estimate DECIMAL(5, 4) NOT NULL,
    organic_estimation_source VARCHAR(32) NOT NULL, -- 'CALIBRATED_MODEL', 'SEGMENT_PRIOR', 'MERCHANT_CONFIG'
    failure_attempt_count INT NOT NULL DEFAULT 1,
    total_interventions_count INT NOT NULL DEFAULT 0, -- Owns the atomic attempt_index sequence
    total_contacts_count INT NOT NULL DEFAULT 0,
    opened_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP WITH TIME ZONE,
    last_evaluated_at TIMESTAMP WITH TIME ZONE,
    execution_claimed_at TIMESTAMP WITH TIME ZONE, -- Used by reaper to detect stuck workers (> 5m)
    version INT NOT NULL DEFAULT 1,
    CONSTRAINT uq_opportunity_order UNIQUE (merchant_id, order_id)
);
CREATE INDEX idx_recovery_opps_state ON recovery_opportunities(merchant_id, current_state);
CREATE INDEX idx_recovery_opps_opened ON recovery_opportunities(opened_at);

-- Foreign key linking payment_attempts back to recovery_opportunities:
ALTER TABLE payment_attempts
ADD CONSTRAINT fk_payment_attempts_opp
FOREIGN KEY (recovery_opportunity_id) REFERENCES recovery_opportunities(id) ON DELETE SET NULL;
```

#### 2.7.1 Circular FK Insertion Procedure
To cleanly insert the mutually-referencing `payment_attempts` and `recovery_opportunities` records without constraint violations, workers execute the following 3-step sequence within a single database transaction:
```sql
-- Step 1: Insert initial payment attempt with recovery_opportunity_id = NULL
INSERT INTO payment_attempts (id, customer_id, recovery_opportunity_id, razorpay_payment_id, razorpay_order_id, amount_subunits, currency, payment_method, status, error_code, raw_payload, gateway_created_at)
VALUES (:attempt_id, :customer_id, NULL, :payment_id, :order_id, :amount, 'INR', :method, 'failed', :error_code, :payload, :gateway_time);

-- Step 2: Insert recovery opportunity referencing the attempt for BOTH initial and latest attempt
INSERT INTO recovery_opportunities (id, merchant_id, customer_id, order_id, initial_attempt_id, latest_attempt_id, amount_at_risk_subunits, failure_category, organic_recovery_estimate, organic_estimation_source, current_state)
VALUES (:opp_id, :merchant_id, :customer_id, :order_id, :attempt_id, :attempt_id, :amount, :cat, :p_org, 'SEGMENT_PRIOR', 'OPEN');

-- Step 3: Backfill recovery_opportunity_id on the initial attempt
UPDATE payment_attempts
SET recovery_opportunity_id = :opp_id
WHERE id = :attempt_id;
```

### 2.8 Table: `intervention_candidates` (REQ-01)
Evaluated intervention slate for an opportunity.
```sql
CREATE TABLE intervention_candidates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    opportunity_id UUID NOT NULL REFERENCES recovery_opportunities(id) ON DELETE CASCADE,
    intervention_type VARCHAR(64) NOT NULL, -- 'NO_ACTION', 'INTERNAL_RETRY_SCHEDULE', 'DIRECT_PAYMENT_LINK_SMS', 'DIRECT_PAYMENT_LINK_WHATSAPP', 'DIRECT_PAYMENT_LINK_EMAIL', 'CUSTOM_WEBHOOK_OUTREACH'
    parameters JSONB NOT NULL,
    p_recovery DECIMAL(5, 4) NOT NULL,
    p_organic DECIMAL(5, 4) NOT NULL,
    direct_cost_subunits BIGINT NOT NULL,
    friction_cost_subunits BIGINT NOT NULL,
    risk_penalty_subunits BIGINT NOT NULL,
    expected_net_value_subunits BIGINT NOT NULL,
    confidence_score DECIMAL(5, 4) NOT NULL,
    generated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_candidates_opp ON intervention_candidates(opportunity_id);
```

### 2.9 Table: `recovery_decisions` (REQ-02)
The authoritative policy resolution produced by the **Deterministic Policy Engine**.
```sql
CREATE TABLE recovery_decisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    opportunity_id UUID NOT NULL REFERENCES recovery_opportunities(id) ON DELETE CASCADE,
    selected_candidate_id UUID REFERENCES intervention_candidates(id),
    decision_type VARCHAR(32) NOT NULL, -- 'AUTHORIZED', 'BLOCKED', 'NO_ACTION', 'ESCALATED'
    policy_evaluation_details JSONB NOT NULL,
    blocked_reason_code VARCHAR(64),
    explanation TEXT NOT NULL,
    decided_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_decisions_opp ON recovery_decisions(opportunity_id);
```

### 2.10 Table: `execution_records` (REQ-03, REQ-05)
Execution vouchers guaranteeing idempotency and tracking two-phase dispatch.
```sql
CREATE TABLE execution_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    decision_id UUID NOT NULL REFERENCES recovery_decisions(id) ON DELETE CASCADE,
    attempt_index INT NOT NULL, -- Allocated atomically from recovery_opportunities.total_interventions_count
    idempotency_key VARCHAR(128) NOT NULL UNIQUE, -- SHA256(opp_id + ":" + type + ":" + attempt_index + ":" + merchant_salt)
    reference_id VARCHAR(64) NOT NULL UNIQUE, -- Deterministic reference passed to gateway: ref_<opp_id[:8]>_<attempt_index>
    intervention_type VARCHAR(64) NOT NULL,
    execution_status VARCHAR(32) NOT NULL DEFAULT 'CLAIMED', -- 'CLAIMED', 'EXECUTED', 'CANCELLED_STALE_STATE', 'RECONCILIATION_REQUIRED', 'FAILED'
    external_reference_id VARCHAR(128), -- e.g., Razorpay Payment Link ID (plink_12345)
    failure_message TEXT,
    claimed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    executed_at TIMESTAMP WITH TIME ZONE
);
CREATE INDEX idx_execution_idempotency ON execution_records(idempotency_key);
CREATE INDEX idx_execution_reference ON execution_records(reference_id);
```

### 2.11 Table: `payment_evidences` (REQ-04)
Cryptographic proof confirming payment settlement.
```sql
CREATE TABLE payment_evidences (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    opportunity_id UUID NOT NULL REFERENCES recovery_opportunities(id) ON DELETE CASCADE,
    razorpay_payment_id VARCHAR(64) NOT NULL UNIQUE,
    event_type VARCHAR(64) NOT NULL,
    signature_hash VARCHAR(128) NOT NULL,
    captured_amount_subunits BIGINT NOT NULL,
    verified_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_payment_evidence_opp ON payment_evidences(opportunity_id);
```

### 2.12 Table: `audit_events`
Append-only log of all system transitions, ensuring full multi-tenant traceability and replayability.
```sql
CREATE TABLE audit_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id UUID NOT NULL REFERENCES merchants(id) ON DELETE CASCADE,
    trace_id VARCHAR(64) NOT NULL,
    aggregate_type VARCHAR(64) NOT NULL,
    aggregate_id UUID NOT NULL,
    event_name VARCHAR(64) NOT NULL,
    state_before JSONB,
    state_after JSONB,
    actor_type VARCHAR(32) NOT NULL, -- 'SYSTEM', 'POLICY_GATE', 'OPERATOR', 'GATEWAY'
    metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_audit_trace ON audit_events(trace_id);
CREATE INDEX idx_audit_merchant ON audit_events(merchant_id);
CREATE INDEX idx_audit_aggregate ON audit_events(aggregate_type, aggregate_id);
```

---

## 3. Privacy, PII & Raw Payload Storage Policy

1. **Hashing of Sensitive Identifiers:**
   - Customer phone numbers and email addresses are hashed via SHA-256 (`phone_hash`, `email_hash`) upon ingestion to support returning-customer recognition without storing plaintext contact directories.
2. **Gateway Raw Payloads:**
   - Razorpay webhook payloads stored in `payment_attempts.raw_payload` contain gateway event metadata necessary for cryptographic signature verification, dispute audit trails, and reconciliation.
   - Razorpay webhooks never contain raw card PANs or CVVs.
   - Merchant credentials (API keys, webhook secrets) are never logged or stored in `raw_payload`.
3. **Retention Policy:**
   - In the buildathon evaluation and demo environment, raw payloads are retained for the lifetime of the demo dataset to enable deterministic replay and verification. In a production deployment, raw payloads are subject to a 90-day retention and archiving lifecycle.
