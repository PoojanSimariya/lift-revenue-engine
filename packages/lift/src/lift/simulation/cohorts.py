"""Pessimistic adversarial cohort generators for benchmark stress-testing."""

from __future__ import annotations

from lift.core.types import FailureCategory, InterventionType
from lift.domain.models import Merchant
from lift.simulation.generator import SyntheticBatchGenerator, SyntheticOpportunityBundle


def create_high_organic_cohort(
    generator: SyntheticBatchGenerator,
    count: int = 50,
    merchant: Merchant | None = None,
    p_organic: float = 0.85,
    amount_subunits: int = 350000,  # 3,500 INR
) -> list[SyntheticOpportunityBundle]:
    """Cohort 1: Very High Organic Recovery (3DS Drops on High-Intent Customers).

    Ground Truth: P_true_organic = 0.85. Customer retries organically within 5 min.
    Baseline 0 wins organically at zero cost.
    LIFT must choose NO_ACTION / PASSIVE_WAIT.
    If forced to intervene, LIFT loses heavily to Baseline 0 due to wasted fees and friction.
    """
    m = merchant or generator.generate_merchant(name="High Organic Merchant")
    bundles: list[SyntheticOpportunityBundle] = []

    for i in range(count):
        cust = generator.generate_customer(
            merchant_id=m.id,
            customer_index=1000 + i,
            rolling_contacts_7d=0,
            last_contacted_hours_ago=None,
        )
        bundle = generator.generate_bundle(
            merchant=m,
            customer=cust,
            attempt_index=1000 + i,
            fixed_failure_category=FailureCategory.AUTHENTICATION_TIMEOUT,
            fixed_amount_subunits=amount_subunits,
            custom_p_organic=p_organic,
            custom_boosts={itype: 0.0 for itype in InterventionType},
        )
        bundles.append(bundle)

    return bundles


def create_micro_ticket_cohort(
    generator: SyntheticBatchGenerator,
    count: int = 50,
    merchant: Merchant | None = None,
    amount_subunits: int = 4900,  # 49 INR (sub-50 INR ticket)
) -> list[SyntheticOpportunityBundle]:
    """Cohort 2: Micro-Ticket Orders (Sub-₹50 items).

    Gross value is tiny; direct communication costs (₹0.25 - ₹0.80) eat up potential margins.
    Baseline 2 incurs negative net value by blasting SMS/WhatsApp.
    LIFT must abstain from paid outreach, relying on passive wait or internal retries.
    """
    m = merchant or generator.generate_merchant(name="Micro Ticket Merchant")
    bundles: list[SyntheticOpportunityBundle] = []

    for i in range(count):
        cust = generator.generate_customer(
            merchant_id=m.id,
            customer_index=2000 + i,
            rolling_contacts_7d=0,
        )
        bundle = generator.generate_bundle(
            merchant=m,
            customer=cust,
            attempt_index=2000 + i,
            fixed_failure_category=FailureCategory.AUTHENTICATION_TIMEOUT,
            fixed_amount_subunits=amount_subunits,
            custom_p_organic=0.20,
            custom_boosts={itype: 0.0 for itype in InterventionType},
        )
        bundles.append(bundle)

    return bundles


def create_hard_decline_cohort(
    generator: SyntheticBatchGenerator,
    count: int = 50,
    merchant: Merchant | None = None,
    amount_subunits: int = 250000,  # 2,500 INR
) -> list[SyntheticOpportunityBundle]:
    """Cohort 3: Terminal Hard Declines (Stolen Cards, Closed Accounts).

    Ground Truth: P_true_organic = 0.0, Delta P = 0.0. Recovery is physically impossible.
    Baseline 1 wastes retry attempts and decline fees.
    LIFT classifies failure as HARD_ISSUER_DECLINE and halts immediately (NO_ACTION).
    """
    m = merchant or generator.generate_merchant(name="Hard Decline Merchant")
    bundles: list[SyntheticOpportunityBundle] = []

    for i in range(count):
        cust = generator.generate_customer(
            merchant_id=m.id,
            customer_index=3000 + i,
            rolling_contacts_7d=0,
        )
        bundle = generator.generate_bundle(
            merchant=m,
            customer=cust,
            attempt_index=3000 + i,
            fixed_failure_category=FailureCategory.HARD_ISSUER_DECLINE,
            fixed_amount_subunits=amount_subunits,
            custom_p_organic=0.00,
        )
        bundles.append(bundle)

    return bundles


def create_high_fatigue_cohort(
    generator: SyntheticBatchGenerator,
    count: int = 50,
    merchant: Merchant | None = None,
    rolling_contacts_7d: int = 3,
    amount_subunits: int = 200000,  # 2,000 INR
) -> list[SyntheticOpportunityBundle]:
    """Cohort 4: High Contact Fatigue History (Customer at Contact Limit).

    Customer has rolling_contacts_7d >= 3 or recent outreach.
    Baseline 2 sends another message, causing brand damage and severe friction penalties.
    LIFT deterministic policy gate blocks direct outreach (BLOCKED_CONTACT_LIMIT).
    """
    m = merchant or generator.generate_merchant(name="High Fatigue Merchant")
    bundles: list[SyntheticOpportunityBundle] = []

    for i in range(count):
        cust = generator.generate_customer(
            merchant_id=m.id,
            customer_index=4000 + i,
            rolling_contacts_7d=rolling_contacts_7d,
            last_contacted_hours_ago=2.0,  # recent contact
        )
        bundle = generator.generate_bundle(
            merchant=m,
            customer=cust,
            attempt_index=4000 + i,
            fixed_failure_category=FailureCategory.INVALID_INSTRUMENT,
            fixed_amount_subunits=amount_subunits,
            custom_p_organic=0.25,
        )
        bundles.append(bundle)

    return bundles
