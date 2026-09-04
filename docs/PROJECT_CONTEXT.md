# Project Context

## Competition

Razorpay AI Buildathon 2026

## Selected Track

Track 03 — AI Revenue Recovery

## Working Product Name

LIFT

> Note: LIFT is an internal working name and is not yet the final public product identity.

## Product Direction

The project is being designed as an intelligent revenue-recovery decisioning system.

The core problem is not simply predicting whether a failed payment will recover.

The system should determine which available intervention is likely to produce the greatest incremental net recovery for a case while respecting:

- latest payment state
- merchant policy
- customer-contact limits
- intervention cost
- customer friction
- model confidence
- safety constraints
- idempotency
- execution constraints

## Core Product Principle

AI recommends.

Deterministic software decides.

Bounded executors act.

Verified payment evidence confirms.

Important decisions are auditable.

## Intended Differentiator

The product should focus on intervention economics and incremental recovery rather than being a generic payment-recovery chatbot.

The system should compare its strategy against meaningful baselines and measure incremental economic value.

## Intended Capabilities

The product direction currently includes:

1. Revenue-at-risk detection
2. Failure diagnosis
3. Recovery prediction
4. Candidate intervention generation
5. Intervention economics
6. Deterministic policy evaluation
7. Execution-time stopping rules
8. Idempotent execution
9. Audit trail
10. Batch simulation
11. Strategy comparison
12. Decision Replay
13. Blocked-action explanations
14. Razorpay Test Mode integration
15. Razorpay webhook processing
16. Human-review fallback
17. AI uncertainty handling

These capabilities are provisional until the architecture review is complete.

## AI Boundary

AI may assist with:

- failure diagnosis
- structured recommendations
- communication generation
- decision explanations

AI must not independently authorize:

- money-affecting execution
- payment-state truth
- idempotency decisions
- policy authorization
- monetary calculations
- audit authority

## Originality Requirement

The final product must have its own:

- product identity
- terminology
- information architecture
- visual language
- interaction patterns
- decision model
- documentation style
- engineering structure

The implementation must not copy another Buildathon submission's:

- name
- README wording
- UI
- architecture
- diagrams
- feature descriptions
- branding

The project should be independently reasoned and visibly distinct.

## Development Tools

### ChatGPT

Project supervisor, product direction, architecture review, engineering review, QA strategy, tool coordination, prioritization, and final decision-making.

### Antigravity

Primary implementation environment for coding, documentation, testing, and development execution.

### Claude

Reserved for high-value architectural, security, adversarial, large-scale code-review, and final quality-review tasks.

### Other tools

Other development or AI tools may be introduced when they provide a clear advantage for a specific task.

## Current Stage

Repository foundation.

No major application implementation has started.

## Immediate Objective

Produce and review:

- product requirements
- system architecture
- component boundaries
- data model
- AI boundaries
- execution model
- evaluation methodology
- security model
- testing strategy
- deployment approach
- implementation roadmap

Major implementation begins only after this architecture gate is approved.