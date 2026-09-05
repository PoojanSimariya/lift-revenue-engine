"""Deterministic policy rule definitions and boundary evaluators."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from lift.core.constants import (
    DEFAULT_MAX_CONTACTS_7D,
    DEFAULT_QUIET_HOURS_END_HOUR,
    DEFAULT_QUIET_HOURS_START_HOUR,
)
from lift.core.errors import TimeZoneError


@dataclass(frozen=True, slots=True)
class QuietHoursConfig:
    """Configuration for merchant quiet-hours window in merchant local timezone."""

    start_hour: int = DEFAULT_QUIET_HOURS_START_HOUR  # 21:00
    start_minute: int = 0
    end_hour: int = DEFAULT_QUIET_HOURS_END_HOUR  # 08:00
    end_minute: int = 0

    def is_quiet_hour(self, current_time: datetime, tz_name: str) -> bool:
        """Evaluate whether current_time falls within quiet hours in the merchant's timezone.

        Standard quiet window spans overnight: [21:00:00, 08:00:00).
        - 20:59:59 -> False (Allowed)
        - 21:00:00 -> True  (Quiet / Blocked)
        - 07:59:59 -> True  (Quiet / Blocked)
        - 08:00:00 -> False (Allowed)
        """
        try:
            tz = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise TimeZoneError(tz_name) from exc

        # Convert evaluation datetime to merchant local timezone
        if current_time.tzinfo is None:
            aware_time = current_time.replace(tzinfo=timezone.utc)
        else:
            aware_time = current_time

        local_time = aware_time.astimezone(tz)
        t = local_time.time()

        start_t = time(self.start_hour, self.start_minute)
        end_t = time(self.end_hour, self.end_minute)

        if self.start_hour > self.end_hour:
            # Overnight quiet window (e.g. 21:00 to 08:00)
            return t >= start_t or t < end_t
        else:
            # Daytime window
            return start_t <= t < end_t


@dataclass(frozen=True, slots=True)
class ContactCapConfig:
    """Configuration for customer rolling contact limits."""

    max_contacts_7d: int = DEFAULT_MAX_CONTACTS_7D

    def is_limit_exceeded(self, rolling_contacts_7d: int) -> bool:
        """True if customer has reached or exceeded max contact allowance (N >= 3)."""
        return rolling_contacts_7d >= self.max_contacts_7d
