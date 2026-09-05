"""Payment evidence repository for storing cryptographic proof of payment settlement."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from lift.domain.models import PaymentEvidence
from lift.storage.mappers import to_evidence_domain, to_evidence_orm
from lift.storage.orm_models import PaymentEvidenceORM
from lift.storage.repositories.base import BaseRepository


class PaymentEvidenceRepository(BaseRepository):
    """Repository handling cryptographic payment settlement evidence."""

    def create(self, evidence: PaymentEvidence) -> PaymentEvidence:
        orm = to_evidence_orm(evidence)
        self.session.add(orm)
        self.session.flush()
        return to_evidence_domain(orm)

    def get_by_payment_id(self, razorpay_payment_id: str) -> PaymentEvidence | None:
        stmt = select(PaymentEvidenceORM).where(
            PaymentEvidenceORM.razorpay_payment_id == razorpay_payment_id
        )
        orm = self.session.scalar(stmt)
        return to_evidence_domain(orm) if orm else None

    def list_by_opportunity_id(self, opportunity_id: UUID) -> list[PaymentEvidence]:
        stmt = (
            select(PaymentEvidenceORM)
            .where(PaymentEvidenceORM.opportunity_id == opportunity_id)
            .order_by(PaymentEvidenceORM.verified_at.asc())
        )
        results = self.session.scalars(stmt).all()
        return [to_evidence_domain(orm) for orm in results]
