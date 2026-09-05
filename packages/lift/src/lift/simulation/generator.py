"""Deterministic, seedable synthetic transaction and customer generator."""

from __future__ import annotations

import hashlib
import math
import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from lift.core.types import (
    AttemptStatus,
    FailureCategory,
    InterventionType,
    OpportunityState,
    OrganicEstimationSource,
    PaymentMethod,
)
from lift.domain.models import Customer, Merchant, PaymentAttempt, RecoveryOpportunity
from lift.simulation.dgp import CausalDGP, CausalProfile


@dataclass(frozen=True)
class SyntheticOpportunityBundle:
    """An opportunity bundled with its initial attempt, customer, and hidden causal profile."""

    merchant: Merchant
    customer: Customer
    attempt: PaymentAttempt
    opportunity: RecoveryOpportunity
    causal_profile: CausalProfile


class SyntheticBatchGenerator:
    """Deterministic, seedable synthetic generator for benchmark evaluation.

    Guarantees:
        For the supported Python/runtime/dependency versions, identical seeds
        produce identical serialized synthetic batches.
    """

    # Base reference timestamp (fixed epoch for deterministic simulation)
    BASE_TIMESTAMP = datetime(2026, 9, 6, 9, 0, 0, tzinfo=timezone.utc)

    def __init__(self, seed: int = 42) -> None:
        self.seed = seed
        self.rng = random.Random(seed)
        self.dgp = CausalDGP(self.rng)

    def generate_merchant(
        self,
        merchant_id: uuid.UUID | None = None,
        name: str = "Simulated Merchant",
        timezone_str: str = "Asia/Kolkata",
    ) -> Merchant:
        """Generate a deterministic test merchant."""
        m_id = merchant_id or uuid.UUID(int=self.rng.getrandbits(128))
        salt = hashlib.sha256(f"merchant_salt_{self.seed}_{m_id}".encode()).hexdigest()[:32]
        return Merchant(
            id=m_id,
            name=name,
            default_currency="INR",
            timezone=timezone_str,
            idempotency_salt=salt,
            created_at=self.BASE_TIMESTAMP,
            updated_at=self.BASE_TIMESTAMP,
        )

    def generate_customer(
        self,
        merchant_id: uuid.UUID,
        customer_index: int,
        rolling_contacts_7d: int = 0,
        last_contacted_hours_ago: float | None = None,
        risk_tier: int = 1,
    ) -> Customer:
        """Generate a customer record with contact history."""
        cust_id = uuid.UUID(int=self.rng.getrandbits(128))
        ext_id = f"cust_sim_{self.seed}_{customer_index:05d}"
        phone_hash = hashlib.sha256(f"phone_{ext_id}".encode()).hexdigest()[:16]
        email_hash = hashlib.sha256(f"email_{ext_id}".encode()).hexdigest()[:16]

        last_contacted_at = (
            self.BASE_TIMESTAMP - timedelta(hours=last_contacted_hours_ago)
            if last_contacted_hours_ago is not None
            else None
        )

        return Customer(
            id=cust_id,
            merchant_id=merchant_id,
            external_customer_id=ext_id,
            phone_hash=phone_hash,
            email_hash=email_hash,
            risk_tier=risk_tier,
            rolling_contacts_7d=rolling_contacts_7d,
            last_contacted_at=last_contacted_at,
            created_at=self.BASE_TIMESTAMP - timedelta(days=30),
        )

    def generate_amount_subunits(self) -> int:
        """Sample transaction amount in integer paise using lognormal distribution.

        Produces realistic basket sizes (median ~1,800 INR, min ~20 INR, max ~50,000 INR).
        """
        # mu = 7.5 (~1800 INR in rupees), sigma = 0.85
        rupees = math.exp(self.rng.gauss(7.5, 0.85))
        rupees = max(20.0, min(50000.0, rupees))
        # Convert to integer paise (subunits)
        return int(round(rupees * 100))

    def sample_payment_method(self) -> PaymentMethod:
        """Sample payment method reflecting Indian e-commerce distribution."""
        r = self.rng.random()
        if r < 0.55:
            return PaymentMethod.UPI
        elif r < 0.90:
            return PaymentMethod.CARD
        else:
            return PaymentMethod.NETBANKING

    def sample_failure_category(self) -> FailureCategory:
        """Sample failure category reflecting realistic gateway decline rates."""
        r = self.rng.random()
        if r < 0.35:
            return FailureCategory.AUTHENTICATION_TIMEOUT
        elif r < 0.60:
            return FailureCategory.TRANSIENT_NETWORK
        elif r < 0.85:
            return FailureCategory.INSUFFICIENT_FUNDS
        elif r < 0.95:
            return FailureCategory.INVALID_INSTRUMENT
        else:
            return FailureCategory.HARD_ISSUER_DECLINE

    def generate_bundle(
        self,
        merchant: Merchant,
        customer: Customer,
        attempt_index: int,
        fixed_failure_category: FailureCategory | None = None,
        fixed_amount_subunits: int | None = None,
        custom_p_organic: float | None = None,
        custom_boosts: Mapping[InterventionType, float] | None = None,
        timestamp_offset_hours: float = 0.0,
    ) -> SyntheticOpportunityBundle:
        """Generate a complete bundle: Attempt + Opportunity + CausalProfile."""
        opp_id = uuid.UUID(int=self.rng.getrandbits(128))
        att_id = uuid.UUID(int=self.rng.getrandbits(128))
        order_id = f"order_{self.seed}_{attempt_index:06d}"
        payment_id = f"pay_{self.seed}_{attempt_index:06d}"

        amount = fixed_amount_subunits or self.generate_amount_subunits()
        method = self.sample_payment_method()
        category = fixed_failure_category or self.sample_failure_category()

        created_at = self.BASE_TIMESTAMP + timedelta(hours=timestamp_offset_hours)

        # Mapping error details
        error_details = {
            FailureCategory.AUTHENTICATION_TIMEOUT: (
                "BAD_REQUEST_ERROR",
                "Payment authentication timed out",
                "bank",
                "payment_authentication",
                "3ds_timeout",
            ),
            FailureCategory.TRANSIENT_NETWORK: (
                "GATEWAY_ERROR",
                "Gateway connection timed out",
                "gateway",
                "payment_authorization",
                "network_error",
            ),
            FailureCategory.INSUFFICIENT_FUNDS: (
                "BAD_REQUEST_ERROR",
                "Insufficient account balance",
                "bank",
                "payment_authorization",
                "insufficient_funds",
            ),
            FailureCategory.INVALID_INSTRUMENT: (
                "BAD_REQUEST_ERROR",
                "Card expired or invalid",
                "bank",
                "payment_authentication",
                "invalid_card",
            ),
            FailureCategory.HARD_ISSUER_DECLINE: (
                "BAD_REQUEST_ERROR",
                "Card declined by issuing bank",
                "bank",
                "payment_authorization",
                "card_declined",
            ),
        }[category]

        attempt = PaymentAttempt(
            id=att_id,
            customer_id=customer.id,
            recovery_opportunity_id=opp_id,
            razorpay_payment_id=payment_id,
            razorpay_order_id=order_id,
            attempt_sequence=1,
            amount_subunits=amount,
            currency="INR",
            payment_method=method,
            status=AttemptStatus.FAILED,
            error_code=error_details[0],
            error_description=error_details[1],
            error_source=error_details[2],
            error_step=error_details[3],
            error_reason=error_details[4],
            gateway_created_at=created_at,
            raw_payload={"mock": True, "seed": self.seed},
            ingested_at=created_at + timedelta(seconds=1),
        )

        opportunity = RecoveryOpportunity(
            id=opp_id,
            merchant_id=merchant.id,
            customer_id=customer.id,
            order_id=order_id,
            initial_attempt_id=att_id,
            latest_attempt_id=att_id,
            amount_at_risk_subunits=amount,
            currency="INR",
            current_state=OpportunityState.OPEN,
            failure_category=category,
            organic_recovery_estimate=0.25,  # Baseline default placeholder
            organic_estimation_source=OrganicEstimationSource.SEGMENT_PRIOR,
            failure_attempt_count=1,
            total_interventions_count=0,
            total_contacts_count=0,
            version=1,
            opened_at=created_at,
        )

        # Generate latent ground-truth profile from DGP
        causal_profile = self.dgp.generate_profile(
            failure_category=category,
            custom_p_organic=custom_p_organic,
            custom_boosts=custom_boosts,
        )

        return SyntheticOpportunityBundle(
            merchant=merchant,
            customer=customer,
            attempt=attempt,
            opportunity=opportunity,
            causal_profile=causal_profile,
        )

    def generate_batch(
        self,
        count: int = 100,
        merchant_name: str = "Simulated Merchant",
    ) -> list[SyntheticOpportunityBundle]:
        """Generate a deterministic batch of opportunities with distinct customers."""
        merchant = self.generate_merchant(name=merchant_name)
        bundles: list[SyntheticOpportunityBundle] = []

        for i in range(count):
            # Stagger timestamps across hours of the day
            hour_offset = (i % 24) + (i / count) * 48.0
            customer = self.generate_customer(
                merchant_id=merchant.id,
                customer_index=i,
                rolling_contacts_7d=self.rng.choice([0, 0, 0, 1, 1, 2]),
                last_contacted_hours_ago=(
                    self.rng.uniform(6.0, 72.0) if self.rng.random() < 0.5 else None
                ),
            )
            bundle = self.generate_bundle(
                merchant=merchant,
                customer=customer,
                attempt_index=i,
                timestamp_offset_hours=hour_offset,
            )
            bundles.append(bundle)

        return bundles

    @staticmethod
    def serialize_bundle(bundle: SyntheticOpportunityBundle) -> dict[str, Any]:
        """Serialize a synthetic bundle to a deterministic dictionary."""
        return {
            "order_id": bundle.opportunity.order_id,
            "payment_id": bundle.attempt.razorpay_payment_id,
            "amount_subunits": bundle.attempt.amount_subunits,
            "payment_method": bundle.attempt.payment_method.value,
            "failure_category": bundle.opportunity.failure_category.value,
            "error_reason": bundle.attempt.error_reason,
            "customer_external_id": bundle.customer.external_customer_id,
            "customer_rolling_contacts_7d": bundle.customer.rolling_contacts_7d,
            "p_true_organic": bundle.causal_profile.p_true_organic,
            "u_draw": bundle.causal_profile.u_draw,
        }

    @classmethod
    def serialize_batch(cls, batch: list[SyntheticOpportunityBundle]) -> list[dict[str, Any]]:
        """Serialize an entire batch to deterministic list of dictionaries."""
        return [cls.serialize_bundle(b) for b in batch]
