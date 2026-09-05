"""Unit tests for Baseline strategies, fair execution sequence, and record schemas."""

from __future__ import annotations

from lift.core.types import DecisionType, FailureCategory, InterventionType
from lift.simulation.baselines import (
    Baseline0HoldoutStrategy,
    Baseline1PeriodicRetryStrategy,
    Baseline2NaiveOutreachStrategy,
    LiftRecoveryStrategy,
)
from lift.simulation.benchmark import BenchmarkRunner
from lift.simulation.generator import SyntheticBatchGenerator


def test_baseline_0_pure_organic_holdout() -> None:
    """Baseline 0 must always emit NO_ACTION with zero costs."""
    gen = SyntheticBatchGenerator(seed=1)
    bundle = gen.generate_batch(count=1)[0]

    b0 = Baseline0HoldoutStrategy()
    decision = b0.evaluate(
        opportunity=bundle.opportunity,
        attempt=bundle.attempt,
        customer=bundle.customer,
        merchant=bundle.merchant,
    )

    assert decision.strategy_name == "Baseline 0 (Pure Organic Holdout)"
    assert decision.selected_action == InterventionType.NO_ACTION
    assert decision.decision_type == DecisionType.NO_ACTION


def test_baseline_1_static_periodic_retry() -> None:
    """Baseline 1 must always emit INTERNAL_RETRY_SCHEDULE even on hard declines."""
    gen = SyntheticBatchGenerator(seed=2)
    bundle = gen.generate_bundle(
        merchant=gen.generate_merchant(),
        customer=gen.generate_customer(merchant_id=gen.generate_merchant().id, customer_index=1),
        attempt_index=1,
        fixed_failure_category=FailureCategory.HARD_ISSUER_DECLINE,
    )

    b1 = Baseline1PeriodicRetryStrategy()
    decision = b1.evaluate(
        opportunity=bundle.opportunity,
        attempt=bundle.attempt,
        customer=bundle.customer,
        merchant=bundle.merchant,
    )

    assert decision.strategy_name == "Baseline 1 (Static Periodic Retry)"
    assert decision.selected_action == InterventionType.INTERNAL_RETRY_SCHEDULE
    assert decision.decision_type == DecisionType.AUTHORIZED


def test_baseline_2_naive_generic_outreach() -> None:
    """Baseline 2 must blindly emit immediate outreach on any failure."""
    gen = SyntheticBatchGenerator(seed=3)
    bundle = gen.generate_batch(count=1)[0]

    b2 = Baseline2NaiveOutreachStrategy(channel=InterventionType.DIRECT_PAYMENT_LINK_SMS)
    decision = b2.evaluate(
        opportunity=bundle.opportunity,
        attempt=bundle.attempt,
        customer=bundle.customer,
        merchant=bundle.merchant,
    )

    assert decision.strategy_name == "Baseline 2 (Naive Generic Outreach)"
    assert decision.selected_action == InterventionType.DIRECT_PAYMENT_LINK_SMS
    assert decision.decision_type == DecisionType.AUTHORIZED


def test_lift_strategy_uses_production_estimate_not_dgp() -> None:
    """LIFT strategy must populate p_estimated_organic without accessing DGP latent truth."""
    gen = SyntheticBatchGenerator(seed=4)
    bundle = gen.generate_bundle(
        merchant=gen.generate_merchant(),
        customer=gen.generate_customer(merchant_id=gen.generate_merchant().id, customer_index=1),
        attempt_index=1,
        fixed_failure_category=FailureCategory.AUTHENTICATION_TIMEOUT,
        custom_p_organic=0.88,  # DGP ground truth is 0.88
    )

    lift_strat = LiftRecoveryStrategy()
    decision = lift_strat.evaluate(
        opportunity=bundle.opportunity,
        attempt=bundle.attempt,
        customer=bundle.customer,
        merchant=bundle.merchant,
    )

    # Production estimate is derived from GLOBAL_FAILURE_PRIORS (0.30 for AUTHENTICATION_TIMEOUT)
    # It must NOT match the latent DGP ground truth of 0.88!
    assert decision.p_estimated_organic == 0.30
    assert bundle.causal_profile.p_true_organic == 0.88
    assert decision.p_estimated_organic != bundle.causal_profile.p_true_organic


def test_benchmark_record_schema_and_nirv_conservation() -> None:
    """Benchmark evaluation must produce all 9 mandatory fields and satisfy NIRV conservation."""
    gen = SyntheticBatchGenerator(seed=5)
    bundles = gen.generate_batch(count=10)

    runner = BenchmarkRunner()
    report = runner.run_batch(bundles)

    assert len(report.records) == 10 * 4  # 10 bundles x 4 strategies

    for record in report.records:
        # 1. Mandatory schema fields
        assert record.opportunity_id is not None
        assert record.strategy in [s.name for s in runner.strategies]
        assert isinstance(record.selected_action, InterventionType)
        assert isinstance(record.recovered, bool)
        assert isinstance(record.recovered_amount, int)
        assert isinstance(record.organic_counterfactual, bool)
        assert isinstance(record.direct_cost, int)
        assert isinstance(record.friction_cost, int)
        assert isinstance(record.nirv, int)

        # 2. Strict conservation of value check:
        # NIRV = recovered_amount - (organic_counterfactual * amount) - direct_cost - friction_cost
        bundle = next(b for b in bundles if b.opportunity.id == record.opportunity_id)
        amount = bundle.opportunity.amount_at_risk_subunits
        organic_amount = amount if record.organic_counterfactual else 0

        expected_nirv = (
            record.recovered_amount - organic_amount - record.direct_cost - record.friction_cost
        )
        assert record.nirv == expected_nirv, (
            f"NIRV violation for strategy {record.strategy}: "
            f"actual {record.nirv} != expected {expected_nirv}"
        )
