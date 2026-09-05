"""Base repository interface."""

from __future__ import annotations

from sqlalchemy.orm import Session


class BaseRepository:
    """Base repository class binding an active SQLAlchemy session.

    The session is managed by the caller/application transaction boundary.
    Repositories execute within the caller's transaction and use flush()
    without directly committing or claiming transaction ownership.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
