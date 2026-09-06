"""Configuration and environment settings for LIFT engine."""

from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment or .env file."""

    ENVIRONMENT: str = "development"
    DATABASE_URL: str = Field(
        default="postgresql://lift:lift_pass@localhost:5432/lift_dev",
        description="Database connection URL. Production/staging MUST supply via env.",
    )
    RAZORPAY_KEY_ID: str = Field(default="", description="Razorpay API Key ID.")
    RAZORPAY_KEY_SECRET: str = Field(default="", description="Razorpay API Key Secret.")
    RAZORPAY_WEBHOOK_SECRET: str = Field(default="", description="Razorpay Webhook Secret.")
    ASPIRATIONAL_WEBHOOK_TIMEOUT_MS: int = 50

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_database_url(cls, v: str, info: Any) -> str:
        """Enforce PostgreSQL connection strings in production and staging environments."""
        env = info.data.get("ENVIRONMENT", "development") if info.data else "development"
        if env in ("production", "staging"):
            if not v or "sqlite" in v.lower():
                msg = f"Production and staging require PostgreSQL DATABASE_URL. Got: '{v}'"
                raise ValueError(msg)
        return v


def get_settings() -> Settings:
    """Return application settings loaded from current environment."""
    return Settings()
