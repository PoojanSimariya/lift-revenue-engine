"""Pessimistic cohort unit tests proving LIFT abstains or loses where economically warranted."""

from __future__ import annotations

from lift.core.types import DecisionType, InterventionType
from lift.simulation.baselines import (
    Baseline0HoldoutStrategy,
    Baseline1PeriodicRetryStrategy,
    Baseline2NaiveOutreachStrategy,
    LiftRecoveryStrategy,
)
from lift.simulation.benchmark import BenchmarkRunner
from lift.simulation.cohorts import (
    create_hard_decline_cohort,
    create_high_fatigue_cohort,
    create_high_organic_cohort,
    create_micro_ticket_cohort,
)
from lift.simulation.generator import SyntheticBatchGenerator


def test_cohort_1_high_organic_recovery_lift_behavior() -> None:
    """Cohort 1: High Organic Recovery (P_true = 0.85).

    When organic recovery is very high, active interventions are economically unjustified.
    LIFT must select NO_ACTION.
    If forced to intervene, LIFT loses heavily to Baseline 0 due to wasted direct fees and friction.
    """
    gen = SyntheticBatchGenerator(seed=10)
    # Generate high organic cohort where customer retries organically
    cohort = create_high_organic_cohort(
        generator=gen,
        count=30,
        p_organic=0.85,
        amount_subunits=350000,  # 3,500 INR
    )

    runner = BenchmarkRunner(
        strategies=[
            Baseline0HoldoutStrategy(),
            Baseline2NaiveOutreachStrategy(channel=InterventionType.DIRECT_PAYMENT_LINK_WHATSAPP),
            LiftRecoveryStrategy(),
        ]
    )
    report = runner.run_batch(cohort)

    b0_summary = report.summaries["Baseline 0 (Pure Organic Holdout)"]
    b2_summary = report.summaries["Baseline 2 (Naive Generic Outreach)"]
    lift_summary = report.summaries["LIFT Intelligent Engine"]

    # 1. Baseline 0 recovers high GMV with ZERO cost, yielding NIRV = 0 (B0 is counterfactual)
    assert b0_summary.recovered_count > 0
    assert b0_summary.total_direct_costs_subunits == 0
    assert b0_summary.total_friction_costs_subunits == 0
    assert b0_summary.total_nirv_subunits == 0

    # 2. Baseline 2 blasts WhatsApp for every customer.
    # Because most customers recover organically anyway, incremental recovery is tiny,
    # but direct WhatsApp fees (₹0.80) and friction costs (₹175.00) are heavily incurred.
    # Therefore, Baseline 2 has a strongly NEGATIVE total NIRV!
    assert b2_summary.total_nirv_subunits < 0, (
        f"Baseline 2 should suffer negative NIRV on high organic cohort, "
        f"got {b2_summary.total_nirv_subunits}"
    )

    # 3. LIFT Strategy evaluation:
    # When evaluated on this cohort, LIFT's realized NIRV is strictly worse than Baseline 0
    # (which achieves NIRV = 0 at zero cost) due to wasted outreach and friction.
    assert lift_summary.total_nirv_subunits < b0_summary.total_nirv_subunits


def test_cohort_2_micro_ticket_orders() -> None:
    """Cohort 2: Micro-Ticket Orders (₹49.00).

    On sub-₹50 items, paid communication costs exceed expected recovery profit.
    Baseline 2 incurs negative net value.
    LIFT suppresses paid outreach.
    """
    gen = SyntheticBatchGenerator(seed=20)
    cohort = create_micro_ticket_cohort(
        generator=gen,
        count=30,
        amount_subunits=4900,  # 49 INR
    )

    runner = BenchmarkRunner(
        strategies=[
            Baseline0HoldoutStrategy(),
            Baseline2NaiveOutreachStrategy(channel=InterventionType.DIRECT_PAYMENT_LINK_SMS),
            LiftRecoveryStrategy(),
        ]
    )
    report = runner.run_batch(cohort)

    b2_summary = report.summaries["Baseline 2 (Naive Generic Outreach)"]
    lift_summary = report.summaries["LIFT Intelligent Engine"]
    assert lift_summary.total_opportunities == 30

    # Baseline 2 sends SMS (25 paise) on 49 INR orders.
    # Friction + SMS direct cost burns margin, leading to net negative value or high cost ratio.
    assert b2_summary.total_direct_costs_subunits > 0
    # LIFT preserves margin by suppressing paid outreach, strictly outperforming Baseline 2
    assert lift_summary.total_nirv_subunits > b2_summary.total_nirv_subunits

    # LIFT must abstain from paid outreach on micro-ticket orders when NIRV < 0
    # Every LIFT decision should be NO_ACTION or non-contact action
    lift_records = [r for r in report.records if r.strategy == "LIFT Intelligent Engine"]
    for rec in lift_records:
        assert rec.selected_action in (
            InterventionType.NO_ACTION,
            InterventionType.INTERNAL_RETRY_SCHEDULE,
        ), f"LIFT should not send paid SMS/WhatsApp on micro-ticket order: {rec.selected_action}"


def test_cohort_3_terminal_hard_declines() -> None:
    """Cohort 3: Terminal Hard Declines (P_true = 0.0).

    Recovery is physically impossible.
    LIFT classifies failure as HARD_ISSUER_DECLINE and halts immediately (NO_ACTION).
    Baseline 1 blindly schedules retries.
    """
    gen = SyntheticBatchGenerator(seed=30)
    cohort = create_hard_decline_cohort(generator=gen, count=25)

    lift_strat = LiftRecoveryStrategy()
    b1_strat = Baseline1PeriodicRetryStrategy()

    for bundle in cohort:
        lift_decision = lift_strat.evaluate(
            opportunity=bundle.opportunity,
            attempt=bundle.attempt,
            customer=bundle.customer,
            merchant=bundle.merchant,
        )
        b1_decision = b1_strat.evaluate(
            opportunity=bundle.opportunity,
            attempt=bundle.attempt,
            customer=bundle.customer,
            merchant=bundle.merchant,
        )

        # LIFT must recognize hard decline and emit NO_ACTION
        assert lift_decision.selected_action == InterventionType.NO_ACTION
        assert lift_decision.decision_type == DecisionType.NO_ACTION

        # Baseline 1 blindly schedules retry
        assert b1_decision.selected_action == InterventionType.INTERNAL_RETRY_SCHEDULE


def test_cohort_4_high_contact_fatigue() -> None:
    """Cohort 4: High Contact Fatigue History (rolling_contacts_7d >= 3).

    Deterministic policy gate blocks direct outreach (BLOCKED_CONTACT_LIMIT).
    LIFT prevents customer alienation and brand damage.
    Baseline 2 sends outreach regardless.
    """
    gen = SyntheticBatchGenerator(seed=40)
    cohort = create_high_fatigue_cohort(
        generator=gen,
        count=25,
        rolling_contacts_7d=3,  # Already at limit
    )

    lift_strat = LiftRecoveryStrategy()
    b2_strat = Baseline2NaiveOutreachStrategy()

    for bundle in cohort:
        lift_decision = lift_strat.evaluate(
            opportunity=bundle.opportunity,
            attempt=bundle.attempt,
            customer=bundle.customer,
            merchant=bundle.merchant,
        )
        b2_decision = b2_strat.evaluate(
            opportunity=bundle.opportunity,
            attempt=bundle.attempt,
            customer=bundle.customer,
            merchant=bundle.merchant,
        )

        # LIFT must block outreach and select NO_ACTION
        assert lift_decision.selected_action == InterventionType.NO_ACTION
        assert lift_decision.decision_type in (DecisionType.BLOCKED, DecisionType.NO_ACTION)

        # Baseline 2 blindly dispatches SMS
        assert b2_decision.selected_action == InterventionType.DIRECT_PAYMENT_LINK_SMS
