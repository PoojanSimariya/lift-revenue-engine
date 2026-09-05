"""Direct unit tests for the Causal Data-Generating Process (DGP) outcome realization.

Verifies the mathematical boundary conditions and strict partition:
    u < p_org                 -> organic recovery
    p_org <= u < total_p      -> incremental recovery
    u >= total_p              -> failed
"""

from __future__ import annotations

from lift.core.types import InterventionType
from lift.simulation.dgp import CausalDGP, CausalProfile, RealizedOutcome


def test_dgp_outcome_when_u_is_zero() -> None:
    """When U_i = 0.0, any positive P_true_organic triggers organic recovery."""
    profile = CausalProfile(
        p_true_organic=0.30,
        delta_p_map={InterventionType.DIRECT_PAYMENT_LINK_SMS: 0.15},
        u_draw=0.0,
    )
    outcome = CausalDGP.realize_outcome(profile, InterventionType.DIRECT_PAYMENT_LINK_SMS)

    assert outcome == RealizedOutcome(
        recovered=True,
        organic_counterfactual=True,
        incremental_recovery=False,
    )


def test_dgp_outcome_when_u_is_one() -> None:
    """When U_i = 1.0, payment fails unless total probability reaches or exceeds 1.0.

    Because U_i is evaluated strictly as u < total_p, u = 1.0 fails even with total_p = 1.0.
    """
    profile = CausalProfile(
        p_true_organic=0.50,
        delta_p_map={InterventionType.DIRECT_PAYMENT_LINK_WHATSAPP: 0.30},
        u_draw=1.0,
    )
    outcome = CausalDGP.realize_outcome(profile, InterventionType.DIRECT_PAYMENT_LINK_WHATSAPP)

    assert outcome == RealizedOutcome(
        recovered=False,
        organic_counterfactual=False,
        incremental_recovery=False,
    )


def test_dgp_outcome_when_p_organic_is_zero() -> None:
    """When P_true_organic = 0.0, organic recovery is impossible.

    Outcome must be either incremental recovery or failed.
    """
    # 1. Under intervention window: u = 0.15 < total_p (0.25) -> incremental recovery
    profile_incremental = CausalProfile(
        p_true_organic=0.0,
        delta_p_map={InterventionType.INTERNAL_RETRY_SCHEDULE: 0.25},
        u_draw=0.15,
    )
    outcome_inc = CausalDGP.realize_outcome(
        profile_incremental, InterventionType.INTERNAL_RETRY_SCHEDULE
    )
    assert outcome_inc == RealizedOutcome(
        recovered=True,
        organic_counterfactual=False,
        incremental_recovery=True,
    )

    # 2. Outside intervention window: u = 0.35 >= total_p (0.25) -> failed
    profile_failed = CausalProfile(
        p_true_organic=0.0,
        delta_p_map={InterventionType.INTERNAL_RETRY_SCHEDULE: 0.25},
        u_draw=0.35,
    )
    outcome_failed = CausalDGP.realize_outcome(
        profile_failed, InterventionType.INTERNAL_RETRY_SCHEDULE
    )
    assert outcome_failed == RealizedOutcome(
        recovered=False,
        organic_counterfactual=False,
        incremental_recovery=False,
    )


def test_dgp_outcome_when_p_organic_is_one() -> None:
    """When P_true_organic = 1.0, all draws u < 1.0 recover organically."""
    profile = CausalProfile(
        p_true_organic=1.0,
        delta_p_map={InterventionType.DIRECT_PAYMENT_LINK_EMAIL: 0.10},
        u_draw=0.75,
    )
    outcome = CausalDGP.realize_outcome(profile, InterventionType.DIRECT_PAYMENT_LINK_EMAIL)

    assert outcome == RealizedOutcome(
        recovered=True,
        organic_counterfactual=True,
        incremental_recovery=False,
    )


def test_dgp_boundary_u_exactly_equals_p_organic() -> None:
    """Boundary test: u == P_true_organic must NOT be classified as organic.

    Rule: u < p_org is organic. u == p_org enters the incremental window [p_org, total_p).
    Uses exact dyadic fraction 0.25 (1/4) to prevent IEEE 754 float rounding inaccuracies.
    """
    profile = CausalProfile(
        p_true_organic=0.25,
        delta_p_map={InterventionType.DIRECT_PAYMENT_LINK_SMS: 0.25},
        u_draw=0.25,
    )
    outcome = CausalDGP.realize_outcome(profile, InterventionType.DIRECT_PAYMENT_LINK_SMS)

    # Exactly at 0.25: not organic (< 0.25 is False), but incremental (< 0.50 is True)
    assert outcome == RealizedOutcome(
        recovered=True,
        organic_counterfactual=False,
        incremental_recovery=True,
    )


def test_dgp_boundary_u_exactly_equals_total_p() -> None:
    """Boundary test: u == min(1.0, p_org + delta) must be classified as FAILED.

    Rule: u < total_p recovers incrementally. At u == total_p, the intervention has failed.
    Uses exact dyadic values 0.25 + 0.25 = 0.50 to guarantee exact boundary representation.
    """
    profile = CausalProfile(
        p_true_organic=0.25,
        delta_p_map={InterventionType.DIRECT_PAYMENT_LINK_SMS: 0.25},
        u_draw=0.50,
    )
    outcome = CausalDGP.realize_outcome(profile, InterventionType.DIRECT_PAYMENT_LINK_SMS)

    assert outcome == RealizedOutcome(
        recovered=False,
        organic_counterfactual=False,
        incremental_recovery=False,
    )


def test_dgp_outcome_when_delta_is_zero() -> None:
    """When delta_P_true = 0.0, incremental recovery is strictly impossible.

    total_p = p_org. The interval [p_org, total_p) is empty.
    """
    profile_below = CausalProfile(
        p_true_organic=0.25,
        delta_p_map={InterventionType.NO_ACTION: 0.0},
        u_draw=0.125,
    )
    outcome_below = CausalDGP.realize_outcome(profile_below, InterventionType.NO_ACTION)
    assert outcome_below == RealizedOutcome(
        recovered=True,
        organic_counterfactual=True,
        incremental_recovery=False,
    )

    profile_at_boundary = CausalProfile(
        p_true_organic=0.25,
        delta_p_map={InterventionType.NO_ACTION: 0.0},
        u_draw=0.25,
    )
    outcome_boundary = CausalDGP.realize_outcome(profile_at_boundary, InterventionType.NO_ACTION)
    assert outcome_boundary == RealizedOutcome(
        recovered=False,
        organic_counterfactual=False,
        incremental_recovery=False,
    )

    profile_above = CausalProfile(
        p_true_organic=0.25,
        delta_p_map={InterventionType.NO_ACTION: 0.0},
        u_draw=0.50,
    )
    outcome_above = CausalDGP.realize_outcome(profile_above, InterventionType.NO_ACTION)
    assert outcome_above == RealizedOutcome(
        recovered=False,
        organic_counterfactual=False,
        incremental_recovery=False,
    )


def test_dgp_clamping_when_p_organic_plus_delta_exceeds_one() -> None:
    """When P_true_organic + delta > 1.0, total_p must clamp to exactly 1.0.

    Any draw in [p_org, 1.0) recovers incrementally.
    """
    # p_org = 0.75, delta = 0.50 -> sum = 1.25, clamped to 1.0 (exact in IEEE 754)
    profile_high = CausalProfile(
        p_true_organic=0.75,
        delta_p_map={InterventionType.DIRECT_PAYMENT_LINK_WHATSAPP: 0.50},
        u_draw=0.875,
    )
    outcome_high = CausalDGP.realize_outcome(
        profile_high, InterventionType.DIRECT_PAYMENT_LINK_WHATSAPP
    )
    assert outcome_high == RealizedOutcome(
        recovered=True,
        organic_counterfactual=False,
        incremental_recovery=True,
    )

    # Edge of clamped boundary: u = 1.0 is not strictly less than clamped total_p (1.0) -> failed
    profile_boundary = CausalProfile(
        p_true_organic=0.75,
        delta_p_map={InterventionType.DIRECT_PAYMENT_LINK_WHATSAPP: 0.50},
        u_draw=1.0,
    )
    outcome_boundary = CausalDGP.realize_outcome(
        profile_boundary, InterventionType.DIRECT_PAYMENT_LINK_WHATSAPP
    )
    assert outcome_boundary == RealizedOutcome(
        recovered=False,
        organic_counterfactual=False,
        incremental_recovery=False,
    )
