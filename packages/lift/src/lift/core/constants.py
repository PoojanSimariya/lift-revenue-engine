"""Immutable domain constants, economic parameters, and global prior values."""

from typing import Final

# 64-bit signed integer limits for monetary subunits
INT64_MIN: Final[int] = -9_223_372_036_854_775_808
INT64_MAX: Final[int] = 9_223_372_036_854_775_807

# Immutable Global Priors for organic recovery by failure category (ADR-003, REQ-01)
GLOBAL_FAILURE_PRIORS: Final[dict[str, float]] = {
    "TRANSIENT_NETWORK": 0.40,
    "AUTHENTICATION_TIMEOUT": 0.30,
    "INSUFFICIENT_FUNDS": 0.15,
    "INVALID_INSTRUMENT": 0.05,
    "HARD_ISSUER_DECLINE": 0.01,
}

# Channel intrusion weights w(a) for ContactFatigue calculation
# (DOMAIN_MODEL.md, EVALUATION_AND_SECURITY.md)
CHANNEL_FATIGUE_WEIGHTS: Final[dict[str, float]] = {
    "DIRECT_PAYMENT_LINK_WHATSAPP": 1.5,
    "DIRECT_PAYMENT_LINK_SMS": 1.0,
    "DIRECT_PAYMENT_LINK_EMAIL": 0.4,
    "CUSTOM_WEBHOOK_OUTREACH": 0.8,
    "WHATSAPP": 1.5,
    "SMS": 1.0,
    "EMAIL": 0.4,
    "CUSTOM_WEBHOOK": 0.8,
}

# Direct execution costs in integer currency subunits (paise for INR)
DIRECT_COSTS_SUBUNITS: Final[dict[str, int]] = {
    "NO_ACTION": 0,
    "INTERNAL_RETRY_SCHEDULE": 0,
    "DIRECT_PAYMENT_LINK_EMAIL": 10,  # 10 paise (0.10 INR)
    "DIRECT_PAYMENT_LINK_SMS": 25,  # 25 paise (0.25 INR)
    "DIRECT_PAYMENT_LINK_WHATSAPP": 80,  # 80 paise (0.80 INR)
    "CUSTOM_WEBHOOK_OUTREACH": 5,  # 5 paise (0.05 INR)
}

# Economic model hyper-parameters
DEFAULT_LAMBDA_FRICTION: Final[float] = 0.05
DEFAULT_BETA: Final[float] = 0.10
FATIGUE_HALF_LIFE_HOURS: Final[float] = 48.0
FATIGUE_SUPPRESSION_THRESHOLD: Final[float] = 4.0

# Bayesian shrinkage parameters for P(Organic)
SHRINKAGE_M: Final[int] = 20
SHRINKAGE_OBS_THRESHOLD: Final[int] = 30

# Deterministic policy defaults
DEFAULT_MAX_CONTACTS_7D: Final[int] = 3
DEFAULT_QUIET_HOURS_START_HOUR: Final[int] = 21  # 21:00:00 (9:00 PM)
DEFAULT_QUIET_HOURS_END_HOUR: Final[int] = 8  # 08:00:00 (8:00 AM)
