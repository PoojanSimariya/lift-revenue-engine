"""Independent Causal Data-Generating Process (DGP) for counterfactual simulation.

This module acts as the objective, hidden reality (the 'world simulator').
Latent parameters and ground truth profiles defined here MUST NEVER be imported
or accessed by production scoring models, feature extractors, or policy engines.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Mapping

from lift.core.types import FailureCategory, InterventionType


@dataclass(frozen=True)
class CausalProfile:
    """Hidden ground truth causal parameters for a single simulated opportunity.

    Attributes:
        p_true_organic: Latent true probability of organic recovery.
        delta_p_map: Latent incremental recovery boosts attributable to each intervention.
        u_draw: The single uniform latent variable U_i ~ Uniform(0, 1) for this opportunity.
    """

    p_true_organic: float
    delta_p_map: Mapping[InterventionType, float]
    u_draw: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.p_true_organic <= 1.0:
            raise ValueError(f"p_true_organic must be in [0, 1], got {self.p_true_organic}")
        if not 0.0 <= self.u_draw <= 1.0:
            raise ValueError(f"u_draw must be in [0, 1], got {self.u_draw}")


@dataclass(frozen=True)
class RealizedOutcome:
    """The realized counterfactual outcome of applying an action to a causal profile."""

    recovered: bool
    organic_counterfactual: bool
    incremental_recovery: bool


class CausalDGP:
    """Causal Data-Generating Process modeling the true counterfactual world."""

    # Default latent organic recovery rates by failure category
    DEFAULT_ORGANIC_RATES: Mapping[FailureCategory, float] = {
        FailureCategory.TRANSIENT_NETWORK: 0.40,
        FailureCategory.AUTHENTICATION_TIMEOUT: 0.30,
        FailureCategory.INSUFFICIENT_FUNDS: 0.15,
        FailureCategory.INVALID_INSTRUMENT: 0.05,
        FailureCategory.HARD_ISSUER_DECLINE: 0.00,
    }

    # Default intervention causal recovery boosts by failure category
    DEFAULT_ACTION_BOOSTS: Mapping[FailureCategory, Mapping[InterventionType, float]] = {
        FailureCategory.TRANSIENT_NETWORK: {
            InterventionType.NO_ACTION: 0.00,
            InterventionType.INTERNAL_RETRY_SCHEDULE: 0.25,
            InterventionType.DIRECT_PAYMENT_LINK_SMS: 0.15,
            InterventionType.DIRECT_PAYMENT_LINK_WHATSAPP: 0.18,
            InterventionType.DIRECT_PAYMENT_LINK_EMAIL: 0.10,
            InterventionType.CUSTOM_WEBHOOK_OUTREACH: 0.12,
        },
        FailureCategory.AUTHENTICATION_TIMEOUT: {
            InterventionType.NO_ACTION: 0.00,
            InterventionType.INTERNAL_RETRY_SCHEDULE: 0.05,
            InterventionType.DIRECT_PAYMENT_LINK_SMS: 0.30,
            InterventionType.DIRECT_PAYMENT_LINK_WHATSAPP: 0.35,
            InterventionType.DIRECT_PAYMENT_LINK_EMAIL: 0.20,
            InterventionType.CUSTOM_WEBHOOK_OUTREACH: 0.25,
        },
        FailureCategory.INSUFFICIENT_FUNDS: {
            InterventionType.NO_ACTION: 0.00,
            InterventionType.INTERNAL_RETRY_SCHEDULE: 0.15,
            InterventionType.DIRECT_PAYMENT_LINK_SMS: 0.12,
            InterventionType.DIRECT_PAYMENT_LINK_WHATSAPP: 0.15,
            InterventionType.DIRECT_PAYMENT_LINK_EMAIL: 0.08,
            InterventionType.CUSTOM_WEBHOOK_OUTREACH: 0.10,
        },
        FailureCategory.INVALID_INSTRUMENT: {
            InterventionType.NO_ACTION: 0.00,
            InterventionType.INTERNAL_RETRY_SCHEDULE: 0.00,
            InterventionType.DIRECT_PAYMENT_LINK_SMS: 0.15,
            InterventionType.DIRECT_PAYMENT_LINK_WHATSAPP: 0.18,
            InterventionType.DIRECT_PAYMENT_LINK_EMAIL: 0.10,
            InterventionType.CUSTOM_WEBHOOK_OUTREACH: 0.12,
        },
        FailureCategory.HARD_ISSUER_DECLINE: {
            InterventionType.NO_ACTION: 0.00,
            InterventionType.INTERNAL_RETRY_SCHEDULE: 0.00,
            InterventionType.DIRECT_PAYMENT_LINK_SMS: 0.00,
            InterventionType.DIRECT_PAYMENT_LINK_WHATSAPP: 0.00,
            InterventionType.DIRECT_PAYMENT_LINK_EMAIL: 0.00,
            InterventionType.CUSTOM_WEBHOOK_OUTREACH: 0.00,
        },
    }

    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()

    def generate_profile(
        self,
        failure_category: FailureCategory,
        custom_p_organic: float | None = None,
        custom_boosts: Mapping[InterventionType, float] | None = None,
        u_draw: float | None = None,
    ) -> CausalProfile:
        """Generate a latent ground-truth profile for an opportunity.

        Args:
            failure_category: Category determining baseline latent parameters.
            custom_p_organic: Optional override for adversarial cohorts (e.g. 0.85).
            custom_boosts: Optional override for channel causal boosts.
            u_draw: Optional explicit latent draw U_i in [0, 1]. If None, sampled from RNG.
        """
        p_org = (
            custom_p_organic
            if custom_p_organic is not None
            else self.DEFAULT_ORGANIC_RATES[failure_category]
        )
        boosts = (
            custom_boosts
            if custom_boosts is not None
            else self.DEFAULT_ACTION_BOOSTS[failure_category]
        )
        u = u_draw if u_draw is not None else self.rng.random()

        return CausalProfile(
            p_true_organic=p_org,
            delta_p_map=boosts,
            u_draw=u,
        )

    @staticmethod
    def realize_outcome(
        profile: CausalProfile,
        action: InterventionType,
    ) -> RealizedOutcome:
        """Evaluate the authoritative causal outcome for an action on a profile.

        Causal Realization Rule:
        For opportunity i with latent U_i ~ Uniform(0, 1):
        1. Organic recovery: U_i < P_true_organic
        2. Incremental intervention recovery:
           P_true_organic <= U_i < min(1.0, P_true_organic + delta_P_true(action))
        3. Failed: U_i >= min(1.0, P_true_organic + delta_P_true(action))
        """
        u = profile.u_draw
        p_org = profile.p_true_organic
        delta_p = profile.delta_p_map.get(action, 0.0)
        total_p = min(1.0, p_org + delta_p)

        # 1. Organic recovery: happened independently of intervention
        if u < p_org:
            return RealizedOutcome(
                recovered=True,
                organic_counterfactual=True,
                incremental_recovery=False,
            )

        # 2. Incremental recovery: succeeded strictly because of intervention action
        if u < total_p:
            return RealizedOutcome(
                recovered=True,
                organic_counterfactual=False,
                incremental_recovery=True,
            )

        # 3. Failed: not recovered
        return RealizedOutcome(
            recovered=False,
            organic_counterfactual=False,
            incremental_recovery=False,
        )
