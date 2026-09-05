# ADR-002: AI Execution Boundary & Non-Authoritative Architecture

## Status
Accepted (Revised after Principal Review)

## Context
Standard generative AI architectures frequently empower LLM agents to act autonomously: the model receives a prompt, determines the next tool call, and executes API mutations directly.

In fintech and revenue operations, this design pattern is unacceptably dangerous. LLMs are non-deterministic, susceptible to prompt injection, prone to hallucinations regarding financial amounts, and cannot reliably enforce hard merchant constraints (such as legal quiet hours or contact rate limits).

Furthermore, allowing the LLM to output a `recommended_intervention_type` creates a false illusion of decision authority that compromises deterministic safety guarantees.

## Decision
We establish a **Strict Non-Authoritative AI Boundary**:
1. **Zero Execution or Selection Authority (REQ-02):** The LLM is strictly prohibited from selecting, ranking, or authorizing interventions. The property `recommended_intervention_type` is **completely removed** from all LLM prompt templates and output schemas.
2. **AI Acts Strictly as Assistive Intelligence:** The LLM produces structured diagnostic reasoning, extracts factual clues from raw gateway strings, offers candidate observations, drafts personalized dunning copy, and generates plain-language decision explanations.
3. **Deterministic Policy Engine Decides:** A pure, deterministic rule engine evaluates the candidate slate, computes Net Incremental Recovery Value (NIRV), checks merchant policies (quiet hours, rolling contact limits), and selects the winning permitted intervention.
4. **Atomic Pessimistic Row-Locked Execution Gate (REQ-03, REQ-10):** Immediately before an approved action executes, the system claims the execution record under a PostgreSQL pessimistic row lock (`SELECT ... FOR UPDATE`), atomically allocating `attempt_index` and checking latest opportunity state.
5. **Verified Gateway Evidence Confirms:** Payment recovery is acknowledged solely through authenticated, cryptographically signed gateway events (`payment.captured`, `payment_link.paid`). AI models never declare a payment recovered.

## Consequences
### Positive:
- Total protection against prompt injection: malicious payloads in customer names or payment notes cannot induce unauthorized actions or bypass policy limits.
- 100% auditable and reproducible decisions: every action is justified by a deterministic policy evaluation and mathematical NIRV score.
- Elimination of double-charging or stale dunning: actions are blocked if payment settled organically.

### Negative:
- The LLM cannot adaptively choose brand new intervention types outside the predefined deterministic candidate taxonomy.
- Requires maintaining explicit Pydantic schemas and deterministic policy classes.
