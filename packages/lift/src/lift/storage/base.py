"""SQLAlchemy 2.0 DeclarativeBase foundation."""

from datetime import datetime, timezone

from sqlalchemy.orm import DeclarativeBase


def utc_now() -> datetime:
    """Deterministic timezone-aware UTC timestamp generator."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Base declarative class for all LIFT persistence entities."""

    pass
