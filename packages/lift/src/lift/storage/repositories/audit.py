"""Audit event repository for append-only traceability."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from lift.domain.models import AuditEvent
from lift.storage.mappers import to_audit_domain, to_audit_orm
from lift.storage.orm_models import AuditEventORM
from lift.storage.repositories.base import BaseRepository


class AuditEventRepository(BaseRepository):
    """Append-only repository for multi-tenant audit events."""

    def record_event(self, event: AuditEvent) -> AuditEvent:
        """Persist an audit event."""
        orm = to_audit_orm(event)
        self.session.add(orm)
        self.session.flush()
        return to_audit_domain(orm)

    def list_by_aggregate(self, aggregate_type: str, aggregate_id: UUID) -> list[AuditEvent]:
        """Query audit trail for a specific aggregate."""
        stmt = (
            select(AuditEventORM)
            .where(
                AuditEventORM.aggregate_type == aggregate_type,
                AuditEventORM.aggregate_id == aggregate_id,
            )
            .order_by(AuditEventORM.created_at.asc())
        )
        results = self.session.scalars(stmt).all()
        return [to_audit_domain(orm) for orm in results]

    def list_by_trace(self, trace_id: str) -> list[AuditEvent]:
        """Query audit trail by trace ID."""
        stmt = (
            select(AuditEventORM)
            .where(AuditEventORM.trace_id == trace_id)
            .order_by(AuditEventORM.created_at.asc())
        )
        results = self.session.scalars(stmt).all()
        return [to_audit_domain(orm) for orm in results]

    def list_by_merchant(self, merchant_id: UUID) -> list[AuditEvent]:
        """Query audit trail for an entire merchant tenant."""
        stmt = (
            select(AuditEventORM)
            .where(AuditEventORM.merchant_id == merchant_id)
            .order_by(AuditEventORM.created_at.asc())
        )
        results = self.session.scalars(stmt).all()
        return [to_audit_domain(orm) for orm in results]
