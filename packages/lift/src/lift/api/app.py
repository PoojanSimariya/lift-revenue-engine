"""FastAPI application factory for LIFT engine."""

from fastapi import FastAPI

from lift.webhooks.router import webhook_router


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    app = FastAPI(
        title="LIFT Revenue Recovery Engine",
        description="Autonomous revenue recovery decision engine integrated with Razorpay rails.",
        version="0.3.0",
    )

    @app.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:
        """Health check endpoint for container orchestrators."""
        return {"status": "ok"}

    app.include_router(webhook_router)
    return app
