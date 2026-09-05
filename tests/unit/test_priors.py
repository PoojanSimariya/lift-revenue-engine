"""Unit tests for global failure priors and Bayesian shrinkage."""

import pytest
from lift.core.constants import GLOBAL_FAILURE_PRIORS
from lift.core.errors import DataValidationError
from lift.core.types import FailureCategory
from lift.economics.priors import (
    calculate_shrunk_organic_probability,
    get_global_prior,
)


def test_global_priors_retrieval() -> None:
    for cat in FailureCategory:
        assert cat.value in GLOBAL_FAILURE_PRIORS
        prior = get_global_prior(cat)
        assert prior == GLOBAL_FAILURE_PRIORS[cat.value]

    # Unrecognized category returns conservative default
    assert get_global_prior("UNKNOWN_CAT") == 0.15


def test_bayesian_shrinkage_below_threshold() -> None:
    # N_obs = 10 (< 30), M = 20
    # p_segment = 0.50, p_prior for TRANSIENT_NETWORK = 0.40
    # weight_seg = 10 / 30 = 1/3, weight_prior = 20 / 30 = 2/3
    # p_shrunk = (1/3) * 0.50 + (2/3) * 0.40 = 0.1667 + 0.2667 = 0.4333
    shrunk = calculate_shrunk_organic_probability(
        p_segment=0.50,
        n_obs=10,
        failure_category=FailureCategory.TRANSIENT_NETWORK,
    )
    assert shrunk == 0.4333


def test_bayesian_shrinkage_zero_observations() -> None:
    # N_obs = 0 -> purely prior
    shrunk = calculate_shrunk_organic_probability(
        p_segment=0.80,
        n_obs=0,
        failure_category=FailureCategory.AUTHENTICATION_TIMEOUT,
    )
    assert shrunk == 0.30  # AUTHENTICATION_TIMEOUT prior is 0.30


def test_bayesian_shrinkage_above_threshold() -> None:
    # N_obs >= 30 -> returns p_segment un-shrunk
    res = calculate_shrunk_organic_probability(
        p_segment=0.55,
        n_obs=35,
        failure_category=FailureCategory.INSUFFICIENT_FUNDS,
    )
    assert res == 0.55


def test_bayesian_shrinkage_validation() -> None:
    with pytest.raises(DataValidationError):
        calculate_shrunk_organic_probability(
            p_segment=1.5,
            n_obs=10,
            failure_category=FailureCategory.TRANSIENT_NETWORK,
        )

    with pytest.raises(DataValidationError):
        calculate_shrunk_organic_probability(
            p_segment=0.5,
            n_obs=-5,
            failure_category=FailureCategory.TRANSIENT_NETWORK,
        )
