# UI / UX Product Experience & Architecture

**Document Status:** Architecture Review Approved with Corrections
**Project:** Razorpay AI Buildathon 2026 — Track 03 (LIFT Engine)

---

## 1. UX Philosophy & Operational Rigor

LIFT is an industrial-grade **fintech revenue operations workstation**, not a generic conversational AI chatbot or decorative dashboard.

### Core Design Rules
1. **High Information Density & Clarity:** Built for billing engineers and revenue operators who prioritize actionable tabular data, timestamps, failure codes, and audit trails over fluffy marketing widgets.
2. **Deterministic Transparency:** Every action, recommendation, and policy block displays the concrete rule, economic formula, or timestamp that triggered it.
3. **Scientific Integrity (Distinguish Measured vs. Estimated):** The UX **never** displays an estimate as an observed fact. The UI explicitly separates **"Measured Organic Baseline"** (from un-intervened holdouts) from **"Estimated Organic Recovery"** (model forecast).
4. **Honest Refresh Architecture (Polling Model):** The workstation relies on **predictable 5-second periodic polling and manual refresh buttons**. It does not invent or imply complex WebSocket streaming.
5. **State-Driven Visual Language:** Clear semantic color coding anchored in fintech conventions:
   - `RECOVERED`: Crisp Emerald Green (accompanied by external payment proof badge).
   - `OPEN` / `IN_EVALUATION`: Calm Slate Blue.
   - `ACTION_BLOCKED`: Muted Amber / Ochre with explicit policy pill (e.g., `BLOCKED: QUIET_HOURS`).
   - `ESCALATED`: Distinct Warning Orange.
   - `EXPIRED` / `ABANDONED`: Neutral Graphite.

---

## 2. Primary Navigation & Workspaces

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ LIFT | Revenue Recovery Engine      [Live Test Mode ●] [Org: Acme SaaS (Single)]│
│ Refresh: [Auto 5s ⟳] [Sync Now]                                                 │
├──────────────┬──────────────────────────────────────────────────────────────────┤
│ ❖ Overview   │                                                                  │
│ ▤ Opps (142) │  Active Revenue at Risk       Net Incremental Recovered (30d)    │
│ ⤿ Replay     │  ₹ 14,82,500                  ₹ 6,18,450 (Uplift: +28.4%)        │
│ ⊘ Why Not?   │                                                                  │
│ 🧪 Rec Lab   ├──────────────────────────────────────────────────────────────────┤
│ ⚠ Escalation │  Recovery Opportunities (Polled - 5s Cadence)                    │
│ ⚙ Policies   │  ID      Customer      Amount    Failure Diagnosis  Decision     │
│              │  opp_01  kartik@...    ₹ 4,500   3DS_TIMEOUT        LINK_SENT    │
│              │  opp_02  anita@...     ₹ 12,000  INSUFFICIENT_FUNDS TIMED_RETRY  │
│              │  opp_03  rohit@...     ₹ 2,100   AUTHENTICATION     BLOCKED      │
└──────────────┴──────────────────────────────────────────────────────────────────┘
```

The application is structured into six dedicated operational workspaces:

### 2.1 Workspace 1: Revenue-at-Risk Overview
- **Header Metric Strip:**
  - *Active Revenue at Risk:* Total value of currently open failed payment opportunities.
  - *Gross Recovered GMV vs. Organic Recovery:* Clear visual split showing **"Measured Organic Baseline"** (from holdout control cohorts) vs. **"Estimated Organic Recovery"** (for in-flight cohorts).
  - *Net Incremental Recovery (NIR):* Realized uplift minus direct communication costs, customer friction penalties, and organic baseline.
  - *Current Recovery Rate & Customer Contact Rate:* Operational metrics refreshed on every 5s poll.
- **Intervention Economics Waterfall Chart:**
  Visual breakdown with scientifically honest labels:
  $\text{Gross Recovered GMV} \rightarrow \mathbf{Less\ Estimated/Measured\ Organic\ Recovery} \rightarrow \text{Less Direct SMS/API Costs} \rightarrow \text{Less Contact Fatigue Friction} \rightarrow \mathbf{Net\ Incremental\ Recovery\ Value\ (NIRV)}$.

### 2.2 Workspace 2: Recovery Opportunities Data Grid
- **Interactive Data Grid:** Filterable, paginated table of recovery opportunities refreshed via 5s polling or on-demand sync.
- **Filters:** By Opportunity State (`OPEN`, `ACTION_SCHEDULED`, `ACTION_BLOCKED`, `RECOVERED`, `EXPIRED`), Payment Method (`UPI`, `Cards`, `Netbanking`), Failure Category, Amount Range, and Customer Risk Tier.
- **Row Quick-View:** Clicking any row slides out the **Opportunity Inspector Drawer** showing current status, failure timeline, and customer rolling contact tally.

### 2.3 Workspace 3: Decision Replay & Opportunity Deep Dive
Provides an immutable, chronologically reconstructable view of any recovery case:
- **Phase 1: Ingestion & Diagnosis:** Gateway error code, raw failure message, and structured classification category.
- **Phase 2: Economic Candidate Slate:** A comparative table of all generated intervention candidates showing:
  - Candidate Type (`NO_ACTION`, `INTERNAL_RETRY_SCHEDULE`, `DIRECT_PAYMENT_LINK_SMS`, `DIRECT_PAYMENT_LINK_WHATSAPP`, `DIRECT_PAYMENT_LINK_EMAIL`, `CUSTOM_WEBHOOK_OUTREACH`).
  - $P(\text{Recovery})$ vs. $P(\text{Organic})$ (labeled as **"Estimated Organic Recovery"**).
  - Direct Execution Cost (₹).
  - Calculated Contact Fatigue Cost (₹) based on $\text{AmountAtRisk} \times \lambda_{\text{friction}} \times \text{ContactFatigue}$.
  - Calculated Net Incremental Value (NIRV).
  - Calibrated Model Confidence Score.
- **Phase 3: Policy & Execution Gate Audit:**
  - Check of every policy rule evaluated (Rule name, Threshold, Evaluated Value, Result: Pass/Fail).
  - Authorizing execution voucher with unique Idempotency Key, allocated `attempt_index`, and deterministic `reference_id`.
  - Atomic pre-flight latest state verification record.
- **Phase 4: Verified Payment Evidence:**
  - Gateway event type (`payment.captured` or `payment_link.paid`).
  - Cryptographic HMAC signature verification proof with microsecond timestamp.
  - Final reconciliation confirmation.

### 2.4 Workspace 4: Blocked Decisions ("Why Not?") Inspector
Surfaces all actions suppressed by deterministic policy or economic guardrails:
- Searchable log of every candidate that was rejected.
- Grouped by suppression reason code:
  - `BLOCKED_QUIET_HOURS` (Outreach suppressed between 21:00 and 08:00 merchant timezone, e.g. `Asia/Kolkata`).
  - `BLOCKED_CONTACT_LIMIT` (Customer reached maximum touches in rolling 7-day window).
  - `BLOCKED_NEGATIVE_NET_VALUE` (Intervention cost + friction exceeded expected recovery gain).
  - `BLOCKED_STALE_STATE` (Payment settled organically before scheduled action executed).
- For each blocked action, a 1-click **"Inspect Math & Policy"** modal displays the exact threshold and values.

### 2.5 Workspace 5: Recovery Lab (Strategy Comparison Bench)
A dedicated simulation and backtesting bench for evaluating recovery strategies against held-out batches:
- **Strategy Selector:** Compare up to 4 strategies side-by-side:
  1. *Baseline 0: Do Nothing (Pure Organic Holdout)*
  2. *Baseline 1: Static Periodic Retry (+24h, +72h)*
  3. *Baseline 2: Naive Immediate Outreach*
  4. *LIFT Intelligent Economic Engine*
- **Pessimistic Scenario Selector (REQ-01):** Allows operators to test adversarial splits (High Organic Recovery cohort, Micro-Ticket cohort, Terminal Hard Declines) where LIFT correctly loses or abstains.
- **Parameter Adjusters:** Interactive sliders to adjust operational assumptions (SMS cost, WhatsApp cost, friction sensitivity $\lambda_{\text{friction}}$).
- **Comparative Output:**
  - Net Incremental Recovery bar charts with honest organic counterfactual subtraction.
  - Customer Contact Frequency histograms.
  - Return on Dunning Spend (RODS) ratio.

### 2.6 Workspace 6: Human Escalation Queue
- Dedicated queue for opportunities flagged with `ESCALATED_HUMAN` (e.g., invoices $> ₹50,000$ with low AI confidence or high-value enterprise accounts).
- Operator can:
  - One-click approve recommended intervention.
  - Override with customized payment link.
  - Terminate recovery opportunity.
  - Add internal operator audit notes.
