# Project Context

## Project

Razorpay Buildathon Project

## Competition

Razorpay AI Buildathon 2026

## Selected Track

Track 03 — AI Revenue Recovery

## Current Product Direction

We are designing an intelligent revenue-recovery decisioning system.

The central problem is not simply predicting whether a failed payment will recover.

The system should determine which available intervention is expected to create the greatest incremental net recovery for a particular case while respecting:

- latest verified payment state
- merchant policy
- customer-contact limits
- intervention cost
- customer friction
- model confidence
- safety constraints
- idempotency
- execution constraints

## Working Product Name

LIFT

LIFT is an internal working name only.

The final public product identity has not yet been finalized.

## Core Product Principle

AI recommends.

Deterministic software decides.

Bounded executors act.

Verified payment evidence confirms the outcome.

Important decisions are auditable.

## Intended Product Differentiation

The project should not become a generic AI payment-recovery chatbot.

Its central differentiation should be decision quality and intervention economics.

The system should answer:

> Which intervention is most likely to create meaningful incremental recovery for this case, and is that intervention actually worth taking?

The product should be able to compare its strategy against clear baseline strategies and measure incremental economic value.

## Current Intended Capabilities

These are provisional and will be finalized during architecture review:

1. Revenue-at-risk detection
2. Payment/failure diagnosis
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

## Engineering Direction

The project should favor:

- explicit contracts
- simple architecture
- deterministic business logic where appropriate
- strong validation
- clear failure handling
- testability
- observability
- maintainability
- secure handling of external input
- minimal unnecessary infrastructure

Avoid:

- unnecessary microservices
- excessive agent frameworks
- giant modules
- hidden global state
- business logic buried in prompts
- generic AI-generated UI
- direct unconstrained LLM-to-money execution

## Originality Requirement

The project must have its own:

- product identity
- terminology
- visual language
- interaction patterns
- information architecture
- decision model
- documentation style
- engineering structure

We must not copy another Buildathon submission's:

- project name
- README wording
- UI
- architecture
- diagrams
- feature descriptions
- branding
- implementation structure

Similarity to the underlying business problem is expected because we are solving the official Buildathon problem.

The implementation and product experience must nevertheless be independently reasoned and visibly distinctive.

## AI-Assisted Development Roles

### ChatGPT

Project supervisor and senior engineering reviewer.

Responsibilities include:

- product direction
- architecture
- sequencing
- quality gates
- security review
- testing strategy
- tool coordination
- technical review
- final quality control

### Antigravity

Primary implementation environment.

Responsibilities include:

- repository implementation
- coding
- documentation
- tests
- local verification
- refactoring
- implementation artifacts

Antigravity must not independently change architectural direction.

### Claude

High-value specialist reviewer.

Use selectively for:

- adversarial architecture review
- security review
- concurrency review
- large-scale code review
- reliability review
- final production-readiness review

Claude usage should be reserved for work where its deeper review provides meaningful additional value.

## Git Discipline

Do not independently:

- change branches
- merge branches
- reset
- rebase
- cherry-pick
- create commits
- push changes

unless explicitly instructed.

Keep commits focused and meaningful.

Avoid giant one-shot commits.

## Current Phase

Phase 0 — Repository Foundation

## Current Objective

Complete product definition and architecture specification before major implementation begins.

## Current Implementation Status

No application implementation has started.

---

## Architecture Discovery Rules

The architecture must be derived from the product problem rather than from a preferred technology stack.

Technology selection should follow the simplest solution that can satisfy:

- correctness
- security
- reliability
- testability
- measurable evaluation
- maintainability
- genuine Razorpay integration
- clear AI boundaries

The project is intentionally being designed against the current competitive landscape of Razorpay Buildathon submissions.

Differentiation must therefore exist at the level of:

- product reasoning
- decision model
- economic objective
- user workflow
- terminology
- interaction model
- evaluation methodology
- engineering boundaries

Cosmetic differentiation is insufficient.

The architecture must not be copied or adapted from another Buildathon submission.

The final architecture must remain understandable to an engineer who has not participated in the project's development.

### Current working hypothesis

The product is exploring revenue recovery as an intervention-decisioning problem rather than only a payment-failure prediction or automated outreach problem.

The system should ultimately be capable of answering:

> What is the best permitted intervention for this recovery opportunity, why is it better than the alternatives, what incremental value is expected, and what evidence confirms the final outcome?

This is a hypothesis, not yet a frozen architecture decision.