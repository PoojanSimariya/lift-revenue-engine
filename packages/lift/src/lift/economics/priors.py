"""Deterministic failure category priors and Bayesian shrinkage for organic recovery estimation."""

from __future__ import annotations

from lift.core.constants import (
    GLOBAL_FAILURE_PRIORS,
    SHRINKAGE_M,
    SHRINKAGE_OBS_THRESHOLD,
)
from lift.core.errors import DataValidationError
from lift.core.types import FailureCategory


def get_global_prior(failure_category: FailureCategory | str) -> float:
    """Retrieve the immutable global prior organic recovery rate for a failure category."""
    cat_str = (
        failure_category.value
        if isinstance(failure_category, FailureCategory)
        else str(failure_category)
    )
    norm = cat_str.strip().upper()
    if norm in GLOBAL_FAILURE_PRIORS:
        return GLOBAL_FAILURE_PRIORS[norm]
    return 0.15  # Default conservative prior if category is unrecognized


def calculate_shrunk_organic_probability(
    p_segment: float,
    n_obs: int,
    failure_category: FailureCategory | str,
    m: int = SHRINKAGE_M,
    obs_threshold: int = SHRINKAGE_OBS_THRESHOLD,
) -> float:
    """Compute the Bayesian shrunk organic recovery probability.

    Formula (EVALUATION_AND_SECURITY.md, Section 2.3):
        When n_obs < 30:
            p_shrunk = (n_obs / (n_obs + M)) * p_segment + (M / (n_obs + M)) * p_prior[cat]
            where M = 20
        When n_obs >= 30:
            p_shrunk = p_segment

    Args:
        p_segment: Empirical or modeled segment probability in [0.0, 1.0].
        n_obs: Number of historical observations in this segment.
        failure_category: Failure category determining the prior anchor.
        m: Shrinkage strength weight (default 20).
        obs_threshold: Observation count threshold below which shrinkage applies (default 30).

    Returns:
        float: Shrunk probability rounded to 4 decimal places.
    """
    if not (0.0 <= p_segment <= 1.0):
        raise DataValidationError("p_segment", p_segment, "Must be in [0.0, 1.0].")
    if n_obs < 0:
        raise DataValidationError("n_obs", n_obs, "Observation count cannot be negative.")

    if n_obs >= obs_threshold:
        return round(p_segment, 4)

    p_prior = get_global_prior(failure_category)
    weight_segment = n_obs / (n_obs + m)
    weight_prior = m / (n_obs + m)

    shrunk = (weight_segment * p_segment) + (weight_prior * p_prior)
    return round(shrunk, 4)
