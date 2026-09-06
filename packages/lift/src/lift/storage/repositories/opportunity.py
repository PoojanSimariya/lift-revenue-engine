"""Opportunity repository implementing the 3-step circular FK insertion protocol."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from lift.core.errors import RecordNotFoundError
from lift.domain.models import PaymentAttempt, RecoveryOpportunity
from lift.storage.mappers import (
    to_attempt_domain,
    to_attempt_orm,
    to_opportunity_domain,
    to_opportunity_orm,
)
from lift.storage.orm_models import RecoveryOpportunityORM
from lift.storage.repositories.base import BaseRepository


class OpportunityRepository(BaseRepository):
    """Repository managing RecoveryOpportunity persistence and mutual attempt references.

    Transaction Ownership:
        The caller owns the atomic transaction boundary (e.g. `with session.begin():`).
        Methods execute within that transaction using `session.flush()`.
        If any step in the 3-step protocol fails, the exception propagates to the
        caller's transaction manager, triggering a full database ROLLBACK.
    """

    def create_with_initial_attempt(
        self, opportunity: RecoveryOpportunity, initial_attempt: PaymentAttempt
    ) -> tuple[RecoveryOpportunity, PaymentAttempt]:
        """Execute the atomic 3-step circular FK insertion protocol.

        Step 1: Insert payment attempt with recovery_opportunity_id = NULL.
        Step 2: Insert recovery opportunity referencing attempt for both initial & latest.
        Step 3: Backfill recovery_opportunity_id on initial attempt.

        Raises:
            Exception: Any database or constraint error propagates to trigger caller rollback.
        """
        # Step 1: Insert initial payment attempt with recovery_opportunity_id = NULL
        attempt_orm = to_attempt_orm(initial_attempt)
        attempt_orm.recovery_opportunity_id = None
        self.session.add(attempt_orm)
        self.session.flush()

        # Step 2: Insert recovery opportunity referencing attempt as initial & latest attempt
        opportunity_orm = to_opportunity_orm(opportunity)
        opportunity_orm.initial_attempt_id = attempt_orm.id
        opportunity_orm.latest_attempt_id = attempt_orm.id
        self.session.add(opportunity_orm)
        self.session.flush()

        # Step 3: Backfill recovery_opportunity_id on the initial payment attempt
        attempt_orm.recovery_opportunity_id = opportunity_orm.id
        self.session.flush()

        return to_opportunity_domain(opportunity_orm), to_attempt_domain(attempt_orm)

    def get_by_id(self, opportunity_id: UUID) -> RecoveryOpportunity | None:
        stmt = select(RecoveryOpportunityORM).where(RecoveryOpportunityORM.id == opportunity_id)
        orm = self.session.scalar(stmt)
        return to_opportunity_domain(orm) if orm else None

    def get_by_order_id(self, merchant_id: UUID, order_id: str) -> RecoveryOpportunity | None:
        stmt = select(RecoveryOpportunityORM).where(
            RecoveryOpportunityORM.merchant_id == merchant_id,
            RecoveryOpportunityORM.order_id == order_id,
        )
        orm = self.session.scalar(stmt)
        return to_opportunity_domain(orm) if orm else None

    def find_by_order_id(self, order_id: str) -> RecoveryOpportunity | None:
        """Find opportunity by order_id across any merchant."""
        stmt = select(RecoveryOpportunityORM).where(RecoveryOpportunityORM.order_id == order_id)
        orm = self.session.scalar(stmt)
        return to_opportunity_domain(orm) if orm else None

    def update(self, opportunity: RecoveryOpportunity) -> RecoveryOpportunity:
        stmt = select(RecoveryOpportunityORM).where(RecoveryOpportunityORM.id == opportunity.id)
        orm = self.session.scalar(stmt)
        if not orm:
            raise RecordNotFoundError("RecoveryOpportunity", opportunity.id)

        orm.current_state = opportunity.current_state.value
        orm.version = opportunity.version
        orm.closed_at = opportunity.closed_at
        orm.last_evaluated_at = opportunity.last_evaluated_at
        orm.execution_claimed_at = opportunity.execution_claimed_at
        orm.failure_attempt_count = opportunity.failure_attempt_count
        orm.total_interventions_count = opportunity.total_interventions_count
        orm.total_contacts_count = opportunity.total_contacts_count
        self.session.flush()
        return to_opportunity_domain(orm)

    def associate_additional_attempt(
        self, opportunity_id: UUID, attempt: PaymentAttempt
    ) -> tuple[RecoveryOpportunity, PaymentAttempt]:
        """Associate a subsequent payment attempt with an existing opportunity.

        Inserts the new attempt with recovery_opportunity_id set and updates
        opportunity.latest_attempt_id to point to the new attempt.
        """
        stmt = select(RecoveryOpportunityORM).where(RecoveryOpportunityORM.id == opportunity_id)
        opp_orm = self.session.scalar(stmt)
        if not opp_orm:
            raise RecordNotFoundError("RecoveryOpportunity", opportunity_id)

        attempt_orm = to_attempt_orm(attempt)
        attempt_orm.recovery_opportunity_id = opportunity_id
        self.session.add(attempt_orm)
        self.session.flush()

        opp_orm.latest_attempt_id = attempt_orm.id
        self.session.flush()

        return to_opportunity_domain(opp_orm), to_attempt_domain(attempt_orm)

    def lock_for_update(self, opportunity_id: UUID) -> RecoveryOpportunityORM | None:
        """Pessimistically lock and fetch a recovery opportunity by ID."""
        bind = self.session.get_bind()
        stmt = select(RecoveryOpportunityORM).where(RecoveryOpportunityORM.id == opportunity_id)
        if not (bind and bind.dialect.name == "sqlite"):
            stmt = stmt.with_for_update()
        return self.session.scalar(stmt)

    def find_stuck_executing(self, timeout_seconds: int = 90) -> list[RecoveryOpportunityORM]:
        """Find opportunities stuck in ACTION_EXECUTING where execution_claimed_at

        exceeds timeout.
        """
        from datetime import timedelta

        from lift.storage.base import utc_now

        cutoff = utc_now() - timedelta(seconds=timeout_seconds)
        stmt = (
            select(RecoveryOpportunityORM)
            .where(
                RecoveryOpportunityORM.current_state == "ACTION_EXECUTING",
                RecoveryOpportunityORM.execution_claimed_at <= cutoff,
            )
            .order_by(RecoveryOpportunityORM.execution_claimed_at.asc())
        )
        return list(self.session.scalars(stmt).all())
