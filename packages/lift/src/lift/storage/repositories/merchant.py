"""Merchant repository for persistence operations on merchants."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from lift.domain.models import Merchant
from lift.storage.mappers import to_merchant_domain, to_merchant_orm
from lift.storage.orm_models import MerchantORM
from lift.storage.repositories.base import BaseRepository


class MerchantRepository(BaseRepository):
    """Repository handling merchant retrieval and creation."""

    def get_by_id(self, merchant_id: UUID) -> Merchant | None:
        stmt = select(MerchantORM).where(MerchantORM.id == merchant_id)
        orm = self.session.scalar(stmt)
        return to_merchant_domain(orm) if orm else None

    def get_first(self) -> Merchant | None:
        """Fetch the first/default merchant in the database."""
        stmt = select(MerchantORM).limit(1)
        orm = self.session.scalar(stmt)
        return to_merchant_domain(orm) if orm else None

    def create(self, merchant: Merchant) -> Merchant:
        orm = to_merchant_orm(merchant)
        self.session.add(orm)
        self.session.flush()
        return to_merchant_domain(orm)

