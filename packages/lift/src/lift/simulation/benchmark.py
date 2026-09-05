"""Comparative benchmark runner and counterfactual metrics engine."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Sequence

from lift.core.constants import DEFAULT_LAMBDA_FRICTION
from lift.core.types import DecisionType, InterventionType
from lift.economics.fatigue import (
    NON_CONTACT_INTERVENTIONS,
    calculate_contact_fatigue,
    resolve_intervention_type,
)
from lift.economics.nirv import get_direct_cost_subunits
from lift.simulation.baselines import (
    Baseline0HoldoutStrategy,
    Baseline1PeriodicRetryStrategy,
    Baseline2NaiveOutreachStrategy,
    LiftRecoveryStrategy,
    RecoveryStrategy,
    StrategyDecision,
)
from lift.simulation.dgp import CausalDGP, RealizedOutcome
from lift.simulation.generator import SyntheticOpportunityBundle


@dataclass(frozen=True)
class BenchmarkRecord:
    """Individual outcome record for an opportunity evaluated under a specific strategy.

    Mandatory fields:
        opportunity_id: Unique opportunity identifier.
        strategy: Name of the strategy evaluated.
        selected_action: InterventionType chosen by the strategy.
        recovered: True if the payment recovered (organically or via intervention).
        recovered_amount: Recovered GMV in integer subunits (paise).
        organic_counterfactual: True if payment would recover organically under Baseline 0.
        direct_cost: Provider/gateway cost in integer subunits.
        friction_cost: Brand annoyance cost in integer subunits.
        nirv: Net Incremental Recovery Value in integer subunits.
    """

    opportunity_id: uuid.UUID
    strategy: str
    selected_action: InterventionType
    recovered: bool
    recovered_amount: int
    organic_counterfactual: bool
    direct_cost: int
    friction_cost: int
    nirv: int

    # Metadata & separation of ground truth vs estimate
    decision_type: DecisionType
    p_true_organic: float
    p_estimated_organic: float | None
    incremental_recovery: bool


@dataclass(frozen=True)
class StrategyBenchmarkSummary:
    """Aggregated metrics for a single strategy across an evaluated benchmark batch."""

    strategy_name: str
    total_opportunities: int
    gross_gmv_at_risk_subunits: int
    recovered_count: int
    recovered_gmv_subunits: int
    organic_recovery_count: int
    organic_recovery_gmv_subunits: int
    incremental_recovery_count: int
    incremental_recovery_gmv_subunits: int
    total_direct_costs_subunits: int
    total_friction_costs_subunits: int
    total_nirv_subunits: int
    abstention_count: int

    @property
    def recovery_rate(self) -> float:
        if self.total_opportunities == 0:
            return 0.0
        return self.recovered_count / self.total_opportunities

    @property
    def incremental_recovery_rate(self) -> float:
        if self.total_opportunities == 0:
            return 0.0
        return self.incremental_recovery_count / self.total_opportunities


@dataclass(frozen=True)
class BenchmarkReport:
    """Full benchmark report containing all opportunity records and strategy summaries."""

    records: list[BenchmarkRecord]
    summaries: Mapping[str, StrategyBenchmarkSummary]


class BenchmarkRunner:
    """Fair, comparative benchmark runner evaluating strategies across identical DGP realities."""

    def __init__(
        self,
        strategies: Sequence[RecoveryStrategy] | None = None,
        lambda_friction: float = DEFAULT_LAMBDA_FRICTION,
    ) -> None:
        self.strategies: list[RecoveryStrategy] = list(
            strategies
            if strategies is not None
            else [
                Baseline0HoldoutStrategy(),
                Baseline1PeriodicRetryStrategy(),
                Baseline2NaiveOutreachStrategy(),
                LiftRecoveryStrategy(),
            ]
        )
        self.lambda_friction = lambda_friction

    def evaluate_opportunity(
        self,
        bundle: SyntheticOpportunityBundle,
        eval_time: datetime | None = None,
    ) -> list[BenchmarkRecord]:
        """Evaluate a single opportunity bundle across all configured strategies.

        Fair Causal Sequence:
            1. Strategy evaluates opportunity -> StrategyDecision
            2. Extract chosen action
            3. Apply action to DGP world using the SAME latent U_i draw
            4. Calculate direct cost and customer friction
            5. Compute NIRV strictly above organic counterfactual
        """
        now = eval_time or bundle.opportunity.opened_at
        records: list[BenchmarkRecord] = []
        opp = bundle.opportunity
        amount = opp.amount_at_risk_subunits

        for strategy in self.strategies:
            # 1. Strategy produces explicit decision
            decision: StrategyDecision = strategy.evaluate(
                opportunity=opp,
                attempt=bundle.attempt,
                customer=bundle.customer,
                merchant=bundle.merchant,
                eval_time=now,
            )

            # 2. Selected action
            action = decision.selected_action

            # 3. Apply action to DGP world with the SAME U_i latent variable
            realized: RealizedOutcome = CausalDGP.realize_outcome(
                profile=bundle.causal_profile,
                action=action,
            )

            # 4. Compute costs
            direct_cost = get_direct_cost_subunits(action)
            fatigue = calculate_contact_fatigue(
                intervention_type=action,
                rolling_contacts_7d=bundle.customer.rolling_contacts_7d,
                last_contacted_at=bundle.customer.last_contacted_at,
                current_time=now,
            )
            resolved_action = resolve_intervention_type(action)
            if resolved_action in NON_CONTACT_INTERVENTIONS:
                friction_cost = 0
            else:
                raw_friction = self.lambda_friction * amount * max(0.0, fatigue)
                friction_cost = round(raw_friction)

            # 5. NIRV Accounting
            recovered_amount = amount if realized.recovered else 0
            organic_amount = amount if realized.organic_counterfactual else 0

            # NIRV = Incremental Gross Recovered - Direct Cost - Friction Cost
            # Incremental Gross Recovered = Recovered Amount - Organic Counterfactual Amount
            incremental_amount = recovered_amount - organic_amount
            nirv = incremental_amount - direct_cost - friction_cost

            records.append(
                BenchmarkRecord(
                    opportunity_id=opp.id,
                    strategy=strategy.name,
                    selected_action=action,
                    recovered=realized.recovered,
                    recovered_amount=recovered_amount,
                    organic_counterfactual=realized.organic_counterfactual,
                    direct_cost=direct_cost,
                    friction_cost=friction_cost,
                    nirv=nirv,
                    decision_type=decision.decision_type,
                    p_true_organic=bundle.causal_profile.p_true_organic,
                    p_estimated_organic=decision.p_estimated_organic,
                    incremental_recovery=realized.incremental_recovery,
                )
            )

        return records

    def run_batch(
        self,
        bundles: Sequence[SyntheticOpportunityBundle],
        eval_time: datetime | None = None,
    ) -> BenchmarkReport:
        """Run entire benchmark batch across all strategies and aggregate metrics."""
        all_records: list[BenchmarkRecord] = []

        for bundle in bundles:
            recs = self.evaluate_opportunity(bundle, eval_time=eval_time)
            all_records.extend(recs)

        # Aggregate summaries per strategy
        summaries: dict[str, StrategyBenchmarkSummary] = {}
        for strategy in self.strategies:
            strat_recs = [r for r in all_records if r.strategy == strategy.name]
            total_opps = len(strat_recs)
            gross_gmv = sum(b.opportunity.amount_at_risk_subunits for b in bundles)
            rec_count = sum(1 for r in strat_recs if r.recovered)
            rec_gmv = sum(r.recovered_amount for r in strat_recs)
            org_count = sum(1 for r in strat_recs if r.organic_counterfactual)
            org_gmv = sum(r.recovered_amount for r in strat_recs if r.organic_counterfactual)
            inc_count = sum(1 for r in strat_recs if r.incremental_recovery)
            inc_gmv = sum(r.recovered_amount for r in strat_recs if r.incremental_recovery)
            total_direct = sum(r.direct_cost for r in strat_recs)
            total_friction = sum(r.friction_cost for r in strat_recs)
            total_nirv = sum(r.nirv for r in strat_recs)
            abstentions = sum(
                1 for r in strat_recs if r.selected_action == InterventionType.NO_ACTION
            )

            summaries[strategy.name] = StrategyBenchmarkSummary(
                strategy_name=strategy.name,
                total_opportunities=total_opps,
                gross_gmv_at_risk_subunits=gross_gmv,
                recovered_count=rec_count,
                recovered_gmv_subunits=rec_gmv,
                organic_recovery_count=org_count,
                organic_recovery_gmv_subunits=org_gmv,
                incremental_recovery_count=inc_count,
                incremental_recovery_gmv_subunits=inc_gmv,
                total_direct_costs_subunits=total_direct,
                total_friction_costs_subunits=total_friction,
                total_nirv_subunits=total_nirv,
                abstention_count=abstentions,
            )

        return BenchmarkReport(records=all_records, summaries=summaries)

    @staticmethod
    def format_summary_table(report: BenchmarkReport) -> str:
        """Format strategy summaries into a markdown comparison table."""
        header = (
            "| Strategy | Opportunities | Recov. Rate | Incr. Rate | "
            "Recovered GMV (₹) | Organic Baseline (₹) | Direct Cost (₹) | "
            "Friction Cost (₹) | Net Value (NIRV ₹) | Abstentions |\n"
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
        )
        rows: list[str] = []
        for summary in report.summaries.values():
            rec_rupees = summary.recovered_gmv_subunits / 100.0
            org_rupees = summary.organic_recovery_gmv_subunits / 100.0
            direct_rupees = summary.total_direct_costs_subunits / 100.0
            friction_rupees = summary.total_friction_costs_subunits / 100.0
            nirv_rupees = summary.total_nirv_subunits / 100.0
            row = (
                f"| {summary.strategy_name} | {summary.total_opportunities} | "
                f"{summary.recovery_rate:.1%} | {summary.incremental_recovery_rate:.1%} | "
                f"₹{rec_rupees:,.2f} | ₹{org_rupees:,.2f} | ₹{direct_rupees:,.2f} | "
                f"₹{friction_rupees:,.2f} | **₹{nirv_rupees:,.2f}** | "
                f"{summary.abstention_count} |"
            )
            rows.append(row)

        return header + "\n".join(rows)
