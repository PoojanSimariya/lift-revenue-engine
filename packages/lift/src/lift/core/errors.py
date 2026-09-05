"""Domain exception hierarchy for LIFT."""

from typing import Any


class LiftError(Exception):
    """Base class for all domain exceptions in LIFT."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class InvalidStateTransitionError(LiftError):
    """Raised when an illegal lifecycle state transition is attempted."""

    def __init__(self, from_state: str, to_state: str, reason: str = "") -> None:
        msg = f"Invalid state transition from '{from_state}' to '{to_state}'."
        if reason:
            msg += f" Reason: {reason}"
        super().__init__(msg, {"from_state": from_state, "to_state": to_state, "reason": reason})


class TerminalStateMutationError(LiftError):
    """Raised when an attempt is made to mutate or transition out of an immutable terminal state."""

    def __init__(self, current_state: str, action: str) -> None:
        msg = (
            f"Cannot execute action '{action}' on terminal state '{current_state}'. "
            "State is immutable."
        )
        super().__init__(msg, {"current_state": current_state, "action": action})


class CurrencyMismatchError(LiftError):
    """Raised when arithmetic operations are attempted between differing currency codes."""

    def __init__(self, currency_a: str, currency_b: str) -> None:
        msg = f"Currency mismatch: cannot operate between '{currency_a}' and '{currency_b}'."
        super().__init__(msg, {"currency_a": currency_a, "currency_b": currency_b})


class DataValidationError(LiftError):
    """Raised when numeric, probability, or identifier bounds are violated."""

    def __init__(self, field: str, value: Any, message: str) -> None:
        msg = f"Validation failed for '{field}' with value '{value}': {message}"
        super().__init__(msg, {"field": field, "value": value, "message": message})


class PolicyViolationError(LiftError):
    """Raised when an intervention violates merchant guardrails."""

    def __init__(self, rule_type: str, reason: str) -> None:
        msg = f"Policy violation on '{rule_type}': {reason}"
        super().__init__(msg, {"rule_type": rule_type, "reason": reason})


class IdempotencyConflictError(LiftError):
    """Raised when an idempotency key conflict is detected."""

    def __init__(
        self, idempotency_key: str, message: str = "Duplicate idempotency key conflict"
    ) -> None:
        msg = f"{message}: {idempotency_key}"
        super().__init__(msg, {"idempotency_key": idempotency_key})


class CircularReferenceError(LiftError):
    """Raised when payment attempt and opportunity relationship ordering is violated."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class TimeZoneError(LiftError):
    """Raised when an invalid timezone identifier is supplied."""

    def __init__(self, timezone_str: str, message: str = "Invalid or unsupported timezone") -> None:
        msg = f"{message}: '{timezone_str}'"
        super().__init__(msg, {"timezone": timezone_str})


class DatabaseConfigurationError(LiftError):
    """Raised when database connection settings or environment guardrails are violated."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class RecordNotFoundError(LiftError):
    """Raised when a requested database record cannot be found."""

    def __init__(self, entity_name: str, identifier: Any) -> None:
        msg = f"{entity_name} not found with identifier: '{identifier}'"
        super().__init__(msg, {"entity_name": entity_name, "identifier": identifier})
