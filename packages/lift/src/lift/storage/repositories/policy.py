"""Policy rule repository for storing and querying deterministic merchant guardrails."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from lift.core.errors import RecordNotFoundError
from lift.domain.models import PolicyRule
from lift.storage.mappers import to_policy_rule_domain, to_policy_rule_orm
from lift.storage.orm_models import PolicyRuleORM
from lift.storage.repositories.base import BaseRepository


class PolicyRuleRepository(BaseRepository):
    """Repository handling merchant policy rules."""

    def create(self, rule: PolicyRule) -> PolicyRule:
        orm = to_policy_rule_orm(rule)
        self.session.add(orm)
        self.session.flush()
        return to_policy_rule_domain(orm)

    def list_active_by_merchant(self, merchant_id: UUID) -> list[PolicyRule]:
        stmt = (
            select(PolicyRuleORM)
            .where(
                PolicyRuleORM.merchant_id == merchant_id,
                PolicyRuleORM.is_active.is_(True),
            )
            .order_by(PolicyRuleORM.created_at.asc())
        )
        results = self.session.scalars(stmt).all()
        return [to_policy_rule_domain(orm) for orm in results]

    def deactivate(self, rule_id: UUID) -> PolicyRule:
        stmt = select(PolicyRuleORM).where(PolicyRuleORM.id == rule_id)
        orm = self.session.scalar(stmt)
        if not orm:
            raise RecordNotFoundError("PolicyRule", rule_id)

        orm.is_active = False
        self.session.flush()
        return to_policy_rule_domain(orm)
