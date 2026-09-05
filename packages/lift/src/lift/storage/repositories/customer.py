"""Customer repository for persistence operations on customer records."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from lift.core.errors import RecordNotFoundError
from lift.domain.models import Customer
from lift.storage.mappers import to_customer_domain, to_customer_orm
from lift.storage.orm_models import CustomerORM
from lift.storage.repositories.base import BaseRepository


class CustomerRepository(BaseRepository):
    """Repository handling customer records and rolling contact histories."""

    def get_by_id(self, customer_id: UUID) -> Customer | None:
        stmt = select(CustomerORM).where(CustomerORM.id == customer_id)
        orm = self.session.scalar(stmt)
        return to_customer_domain(orm) if orm else None

    def get_by_external_id(self, merchant_id: UUID, external_customer_id: str) -> Customer | None:
        stmt = select(CustomerORM).where(
            CustomerORM.merchant_id == merchant_id,
            CustomerORM.external_customer_id == external_customer_id,
        )
        orm = self.session.scalar(stmt)
        return to_customer_domain(orm) if orm else None

    def create(self, customer: Customer) -> Customer:
        orm = to_customer_orm(customer)
        self.session.add(orm)
        self.session.flush()
        return to_customer_domain(orm)

    def update(self, customer: Customer) -> Customer:
        stmt = select(CustomerORM).where(CustomerORM.id == customer.id)
        orm = self.session.scalar(stmt)
        if not orm:
            raise RecordNotFoundError("Customer", customer.id)

        orm.phone_hash = customer.phone_hash
        orm.email_hash = customer.email_hash
        orm.risk_tier = customer.risk_tier
        orm.lifetime_recovery_count = customer.lifetime_recovery_count
        orm.lifetime_failure_count = customer.lifetime_failure_count
        orm.rolling_contacts_7d = customer.rolling_contacts_7d
        orm.last_contacted_at = customer.last_contacted_at
        self.session.flush()
        return to_customer_domain(orm)
