# Engineering Guidelines

This repository is being developed as an industry-grade software project for the Razorpay AI Buildathon 2026.

## Core principles

- Prefer simple, explicit architecture over unnecessary complexity.
- AI must never directly control money-affecting execution.
- Deterministic business rules must remain deterministic.
- External events must be treated as untrusted input.
- Money-affecting operations must be idempotent.
- Important decisions must be auditable.
- Never fabricate test results, metrics, integrations, or capabilities.
- Never commit secrets or credentials.
- Do not add dependencies without a clear reason.
- Do not modify unrelated functionality.
- Do not remove tests merely to make the suite pass.
- Do not hide failures.
- Every meaningful feature must have appropriate tests.
- Keep business logic out of the UI.
- Keep provider-specific logic behind clear interfaces.
- Prefer maintainable code that another engineer can understand quickly.

## AI-assisted development

AI coding tools may assist with implementation, but all architecture, security, business logic, and execution boundaries must remain explicit and reviewable.

LLM output must never be treated as an authorization to perform a money-affecting action.

## Development discipline

- Work in small, reviewable milestones.
- Do not implement the entire product in one pass.
- Do not introduce infrastructure before it is justified.
- Keep project documentation synchronized with meaningful architectural changes.
- Test behavior rather than merely testing implementation details.
- Treat failures and edge cases as first-class engineering requirements.

## Git discipline

- Do not create, delete, merge, reset, rebase, cherry-pick, commit, or push branches unless explicitly instructed.
- Do not alter unrelated branches.
- Keep commits focused and meaningful.
- Avoid giant one-shot commits containing unrelated work.

## Current project status

Architecture and detailed implementation decisions have not yet been finalized.

Major implementation must not begin until the architecture specification has been reviewed and approved.