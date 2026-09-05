"""Decision and candidate repositories for recovery policy outcomes."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from lift.domain.models import InterventionCandidate, RecoveryDecision
from lift.storage.mappers import (
    to_candidate_domain,
    to_candidate_orm,
    to_decision_domain,
    to_decision_orm,
)
from lift.storage.orm_models import InterventionCandidateORM, RecoveryDecisionORM
from lift.storage.repositories.base import BaseRepository


class CandidateRepository(BaseRepository):
    """Repository handling intervention candidate evaluations."""

    def create(self, candidate: InterventionCandidate) -> InterventionCandidate:
        orm = to_candidate_orm(candidate)
        self.session.add(orm)
        self.session.flush()
        return to_candidate_domain(orm)

    def create_many(self, candidates: list[InterventionCandidate]) -> list[InterventionCandidate]:
        orms = [to_candidate_orm(c) for c in candidates]
        self.session.add_all(orms)
        self.session.flush()
        return [to_candidate_domain(orm) for orm in orms]

    def list_by_opportunity_id(self, opportunity_id: UUID) -> list[InterventionCandidate]:
        stmt = (
            select(InterventionCandidateORM)
            .where(InterventionCandidateORM.opportunity_id == opportunity_id)
            .order_by(InterventionCandidateORM.generated_at.asc())
        )
        results = self.session.scalars(stmt).all()
        return [to_candidate_domain(orm) for orm in results]


class DecisionRepository(BaseRepository):
    """Repository handling authoritative policy resolutions."""

    def create(self, decision: RecoveryDecision) -> RecoveryDecision:
        orm = to_decision_orm(decision)
        self.session.add(orm)
        self.session.flush()
        return to_decision_domain(orm)

    def get_by_id(self, decision_id: UUID) -> RecoveryDecision | None:
        stmt = select(RecoveryDecisionORM).where(RecoveryDecisionORM.id == decision_id)
        orm = self.session.scalar(stmt)
        return to_decision_domain(orm) if orm else None

    def get_by_opportunity_id(self, opportunity_id: UUID) -> RecoveryDecision | None:
        stmt = (
            select(RecoveryDecisionORM)
            .where(RecoveryDecisionORM.opportunity_id == opportunity_id)
            .order_by(RecoveryDecisionORM.decided_at.desc())
        )
        orm = self.session.scalar(stmt)
        return to_decision_domain(orm) if orm else None
