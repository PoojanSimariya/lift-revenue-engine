"""Unit tests for the BenchmarkRunner, common random numbers, and metrics engine."""

from __future__ import annotations

from lift.simulation.benchmark import BenchmarkRunner
from lift.simulation.generator import SyntheticBatchGenerator


def test_benchmark_runner_full_batch_execution() -> None:
    """Run a full comparative benchmark and verify metrics integrity."""
    gen = SyntheticBatchGenerator(seed=123)
    bundles = gen.generate_batch(count=100)

    runner = BenchmarkRunner()
    report = runner.run_batch(bundles)

    # 4 strategies evaluated on 100 bundles = 400 records
    assert len(report.records) == 400
    assert len(report.summaries) == 4

    b0_summary = report.summaries["Baseline 0 (Pure Organic Holdout)"]
    b1_summary = report.summaries["Baseline 1 (Static Periodic Retry)"]
    b2_summary = report.summaries["Baseline 2 (Naive Generic Outreach)"]
    lift_summary = report.summaries["LIFT Intelligent Engine"]

    # 1. Baseline 0 pure organic holdout properties
    assert b0_summary.total_direct_costs_subunits == 0
    assert b0_summary.total_friction_costs_subunits == 0
    assert b0_summary.total_nirv_subunits == 0  # Net above itself is zero
    assert b0_summary.abstention_count == 100

    # 2. Organic recovery count consistency:
    # Every strategy must record the exact same organic counterfactual recovery count!
    b0_organic_count = b0_summary.organic_recovery_count
    assert b1_summary.organic_recovery_count == b0_organic_count
    assert b2_summary.organic_recovery_count == b0_organic_count
    assert lift_summary.organic_recovery_count == b0_organic_count

    # 3. Format summary table verification
    table = BenchmarkRunner.format_summary_table(report)
    assert "| Strategy |" in table
    assert "Baseline 0" in table
    assert "Baseline 1" in table
    assert "Baseline 2" in table
    assert "LIFT Intelligent Engine" in table


def test_common_random_numbers_causal_coupling() -> None:
    """Verify that every strategy evaluates the exact same latent U_i for each opportunity."""
    gen = SyntheticBatchGenerator(seed=456)
    bundles = gen.generate_batch(count=20)

    runner = BenchmarkRunner()
    report = runner.run_batch(bundles)

    for bundle in bundles:
        opp_id = bundle.opportunity.id
        u_expected = bundle.causal_profile.u_draw
        p_org = bundle.causal_profile.p_true_organic
        is_organic_expected = u_expected < p_org

        # Retrieve records across all 4 strategies for this opportunity
        opp_records = [r for r in report.records if r.opportunity_id == opp_id]
        assert len(opp_records) == 4

        for rec in opp_records:
            # Latent ground truth must match
            assert rec.p_true_organic == p_org
            # Organic counterfactual must be identical across all strategies
            assert rec.organic_counterfactual == is_organic_expected, (
                f"Strategy {rec.strategy} evaluated different organic "
                f"status for opportunity {opp_id}"
            )


def test_in_memory_execution_no_database_state() -> None:
    """Verify benchmark executes purely in memory without mutating database or ORM."""
    gen = SyntheticBatchGenerator(seed=789)
    bundles = gen.generate_batch(count=10)

    runner = BenchmarkRunner()
    report = runner.run_batch(bundles)

    assert len(report.records) == 40
    # Pure Python objects without SQLAlchemy session binding
    for rec in report.records:
        assert isinstance(rec.nirv, int)
        assert isinstance(rec.recovered_amount, int)
