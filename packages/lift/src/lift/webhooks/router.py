"""FastAPI router for incoming Razorpay webhooks."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from lift.config import get_settings
from lift.core.errors import DataValidationError, InvalidSignatureError
from lift.storage.database import create_db_engine, get_session_factory
from lift.webhooks.service import WebhookIngestionService

webhook_router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])

# Global session factory lazy cache
_session_factory: sessionmaker[Session] | None = None


def get_db_session_factory() -> sessionmaker[Session]:
    """Dependency returning configured sessionmaker."""
    global _session_factory
    if _session_factory is None:
        settings = get_settings()
        engine = create_db_engine(settings.DATABASE_URL)
        _session_factory = get_session_factory(engine)
    return _session_factory


@webhook_router.post("/razorpay", status_code=status.HTTP_200_OK)
async def ingest_razorpay_webhook(
    request: Request,
    x_razorpay_event_id: Annotated[str | None, Header(alias="x-razorpay-event-id")] = None,
    x_razorpay_signature: Annotated[str | None, Header(alias="x-razorpay-signature")] = None,
    session_factory: sessionmaker[Session] = Depends(get_db_session_factory),
) -> JSONResponse:
    """Ingest, verify, and persist Razorpay webhooks.

    Mandatory header checks:
    - x-razorpay-event-id: required (400 Bad Request if missing)
    - x-razorpay-signature: required (401 Unauthorized if missing)
    """
    if not x_razorpay_event_id:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "missing_x_razorpay_event_id"},
        )

    if not x_razorpay_signature:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"error": "missing_signature_header"},
        )

    raw_body = await request.body()
    settings = get_settings()

    session = session_factory()
    try:
        with session.begin():
            service = WebhookIngestionService(
                session=session,
                webhook_secret=settings.RAZORPAY_WEBHOOK_SECRET,
            )
            result = service.process_webhook(
                event_id=x_razorpay_event_id,
                signature=x_razorpay_signature,
                raw_body=raw_body,
            )

        # Committed successfully
        if result.duplicate:
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={"status": "duplicate_acknowledged"},
            )

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "accepted", "event_id": result.event_id},
        )
    except InvalidSignatureError:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"error": "invalid_webhook_signature"},
        )
    except DataValidationError:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "malformed_json"},
        )
    except Exception as exc:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "internal_error", "message": str(exc)},
        )
    finally:
        session.close()
