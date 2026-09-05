"""Integer subunit monetary representation and currency arithmetic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lift.core.constants import INT64_MAX, INT64_MIN
from lift.core.errors import CurrencyMismatchError, DataValidationError


@dataclass(frozen=True, slots=True)
class Money:
    """Immutable monetary value represented strictly as integer currency subunits (e.g. paise).

    Prevents IEEE-754 floating-point inaccuracies and enforces strict currency isolation.
    """

    amount_subunits: int
    currency: str = "INR"

    def __post_init__(self) -> None:
        # Prevent boolean (which is a subclass of int in Python) or floats
        if isinstance(self.amount_subunits, bool) or not isinstance(self.amount_subunits, int):
            raise DataValidationError(
                "amount_subunits",
                self.amount_subunits,
                f"Subunit amount must be integer, got {type(self.amount_subunits).__name__}.",
            )

        if not (INT64_MIN <= self.amount_subunits <= INT64_MAX):
            raise DataValidationError(
                "amount_subunits",
                self.amount_subunits,
                f"Amount {self.amount_subunits} exceeds signed 64-bit integer range.",
            )

        if not isinstance(self.currency, str) or len(self.currency.strip()) != 3:
            raise DataValidationError(
                "currency",
                self.currency,
                "Currency must be a 3-letter ISO code string.",
            )

        # Standardize uppercase currency
        object.__setattr__(self, "currency", self.currency.strip().upper())

    @classmethod
    def from_subunits(cls, subunits: int, currency: str = "INR") -> Money:
        """Construct Money directly from integer subunits."""
        return cls(amount_subunits=subunits, currency=currency)

    @classmethod
    def from_paise(cls, paise: int, currency: str = "INR") -> Money:
        """Construct Money from INR paise."""
        return cls(amount_subunits=paise, currency=currency)

    @classmethod
    def from_rupees_integer(cls, rupees: int, currency: str = "INR") -> Money:
        """Construct Money from an exact integer rupee amount (multiplied by 100)."""
        if isinstance(rupees, bool) or not isinstance(rupees, int):
            raise DataValidationError(
                "rupees",
                rupees,
                f"Rupee amount must be an integer, got {type(rupees).__name__}.",
            )
        return cls(amount_subunits=rupees * 100, currency=currency)

    @classmethod
    def zero(cls, currency: str = "INR") -> Money:
        """Return a zero-value Money instance for the specified currency."""
        return cls(amount_subunits=0, currency=currency)

    def _assert_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchError(self.currency, other.currency)

    def __add__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._assert_same_currency(other)
        result = self.amount_subunits + other.amount_subunits
        return Money(amount_subunits=result, currency=self.currency)

    def __sub__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._assert_same_currency(other)
        result = self.amount_subunits - other.amount_subunits
        return Money(amount_subunits=result, currency=self.currency)

    def __mul__(self, scalar: int) -> Money:
        if isinstance(scalar, bool) or not isinstance(scalar, int):
            raise DataValidationError(
                "scalar",
                scalar,
                f"Multiplication scalar must be an integer, got {type(scalar).__name__}.",
            )
        result = self.amount_subunits * scalar
        return Money(amount_subunits=result, currency=self.currency)

    def __rmul__(self, scalar: int) -> Money:
        return self.__mul__(scalar)

    def __floordiv__(self, divisor: int) -> Money:
        if isinstance(divisor, bool) or not isinstance(divisor, int):
            raise DataValidationError(
                "divisor",
                divisor,
                f"Division divisor must be an integer, got {type(divisor).__name__}.",
            )
        if divisor == 0:
            raise ZeroDivisionError("Cannot divide Money by integer zero.")
        result = self.amount_subunits // divisor
        return Money(amount_subunits=result, currency=self.currency)

    def __neg__(self) -> Money:
        return Money(amount_subunits=-self.amount_subunits, currency=self.currency)

    def __pos__(self) -> Money:
        return self

    def __abs__(self) -> Money:
        return Money(amount_subunits=abs(self.amount_subunits), currency=self.currency)

    def __lt__(self, other: Money) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._assert_same_currency(other)
        return self.amount_subunits < other.amount_subunits

    def __le__(self, other: Money) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._assert_same_currency(other)
        return self.amount_subunits <= other.amount_subunits

    def __gt__(self, other: Money) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._assert_same_currency(other)
        return self.amount_subunits > other.amount_subunits

    def __ge__(self, other: Money) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._assert_same_currency(other)
        return self.amount_subunits >= other.amount_subunits

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Money):
            return False
        return self.currency == other.currency and self.amount_subunits == other.amount_subunits

    @property
    def is_zero(self) -> bool:
        """True if the amount is exactly zero."""
        return self.amount_subunits == 0

    @property
    def is_positive(self) -> bool:
        """True if the amount is strictly greater than zero."""
        return self.amount_subunits > 0

    @property
    def is_negative(self) -> bool:
        """True if the amount is strictly less than zero."""
        return self.amount_subunits < 0

    def as_subunits(self) -> int:
        """Return the raw integer subunit count."""
        return self.amount_subunits

    def __str__(self) -> str:
        return f"{self.amount_subunits} {self.currency} subunits"

    def __repr__(self) -> str:
        return f"Money(amount_subunits={self.amount_subunits}, currency='{self.currency}')"
