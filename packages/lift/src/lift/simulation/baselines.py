"""Formal baseline recovery strategies and LIFT strategy adapter.

Every strategy produces an explicit StrategyDecision specifying the selected action
before the benchmark realizes causal outcomes in the DGP world.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

from lift.core.types import DecisionType, FailureCategory, InterventionType
from lift.domain.models import Customer, Merchant, PaymentAttempt, RecoveryOpportunity
from lift.economics.priors import get_global_prior
from lift.services.evaluation import InterventionEvaluationService
from lift.services.policy_gate import PolicyGateService


@dataclass(frozen=True)
class StrategyDecision:
    """The explicit decision emitted by a strategy for an opportunity.

    Attributes:
        strategy_name: Name of the strategy emitting this decision.
        selected_action: The chosen InterventionType action.
        decision_type: Policy decision classification (AUTHORIZED, BLOCKED, NO_ACTION).
        rationale: Explanatory rationale for the decision.
        confidence_score: Model confidence score for the recommendation.
        p_estimated_organic: Statistical estimate of P(Organic) produced by the strategy.
            Must strictly reflect production estimates, NEVER latent DGP ground truth.
    """

    strategy_name: str
    selected_action: InterventionType
    decision_type: DecisionType
    rationale: str
    confidence_score: float = 1.0
    p_estimated_organic: float | None = None


class RecoveryStrategy(ABC):
    """Abstract base class for all benchmark recovery strategies."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the strategy."""

    @abstractmethod
    def evaluate(
        self,
        opportunity: RecoveryOpportunity,
        attempt: PaymentAttempt,
        customer: Customer,
        merchant: Merchant,
        eval_time: datetime | None = None,
    ) -> StrategyDecision:
        """Evaluate an opportunity and emit an explicit StrategyDecision."""


class Baseline0HoldoutStrategy(RecoveryStrategy):
    """Baseline 0: Pure Organic Holdout (Do Nothing / Passive Wait).

    Strictly withholds merchant intervention to measure true counterfactual organic recovery.
    Incurs 0 direct cost and 0 customer friction.
    """

    @property
    def name(self) -> str:
        return "Baseline 0 (Pure Organic Holdout)"

    def evaluate(
        self,
        opportunity: RecoveryOpportunity,
        attempt: PaymentAttempt,
        customer: Customer,
        merchant: Merchant,
        eval_time: datetime | None = None,
    ) -> StrategyDecision:
        return StrategyDecision(
            strategy_name=self.name,
            selected_action=InterventionType.NO_ACTION,
            decision_type=DecisionType.NO_ACTION,
            rationale=(
                "Pure organic holdout control: deliberately withholding merchant intervention."
            ),
            confidence_score=1.0,
            p_estimated_organic=None,
        )


class Baseline1PeriodicRetryStrategy(RecoveryStrategy):
    """Baseline 1: Static Periodic Retry (Fixed Rule Dunning).

    Blindly schedules a future internal retry regardless of failure cause or fatigue.
    Wastes time and retry budget on hard issuer declines.
    """

    @property
    def name(self) -> str:
        return "Baseline 1 (Static Periodic Retry)"

    def evaluate(
        self,
        opportunity: RecoveryOpportunity,
        attempt: PaymentAttempt,
        customer: Customer,
        merchant: Merchant,
        eval_time: datetime | None = None,
    ) -> StrategyDecision:
        return StrategyDecision(
            strategy_name=self.name,
            selected_action=InterventionType.INTERNAL_RETRY_SCHEDULE,
            decision_type=DecisionType.AUTHORIZED,
            rationale="Static periodic retry policy: blindly scheduling future retry attempt.",
            confidence_score=1.0,
            p_estimated_organic=None,
        )


class Baseline2NaiveOutreachStrategy(RecoveryStrategy):
    """Baseline 2: Naive Immediate Outreach (Dunning Blast).

    Blindly dispatches immediate customer outreach (SMS/WhatsApp) for every failure.
    Ignores order size (fires on sub-₹50 tickets), high organic recovery, and contact fatigue.
    """

    def __init__(
        self,
        channel: InterventionType = InterventionType.DIRECT_PAYMENT_LINK_SMS,
    ) -> None:
        self.channel = channel

    @property
    def name(self) -> str:
        return "Baseline 2 (Naive Generic Outreach)"

    def evaluate(
        self,
        opportunity: RecoveryOpportunity,
        attempt: PaymentAttempt,
        customer: Customer,
        merchant: Merchant,
        eval_time: datetime | None = None,
    ) -> StrategyDecision:
        return StrategyDecision(
            strategy_name=self.name,
            selected_action=self.channel,
            decision_type=DecisionType.AUTHORIZED,
            rationale=(
                f"Naive generic outreach policy: immediately dispatching {self.channel.value}."
            ),
            confidence_score=1.0,
            p_estimated_organic=None,
        )


class LiftRecoveryStrategy(RecoveryStrategy):
    """LIFT Intelligent Recovery Strategy.

    Optimizes Net Incremental Recovery Value (NIRV) above estimated organic baseline.
    Enforces deterministic merchant guardrails and non-linear contact fatigue.
    Abstains (NO_ACTION) when NIRV is negative or policies block outreach.
    """

    # Estimated recovery lift per channel relative to organic prior
    DEFAULT_ESTIMATED_LIFTS: Mapping[FailureCategory, Mapping[InterventionType, float]] = {
        FailureCategory.TRANSIENT_NETWORK: {
            InterventionType.NO_ACTION: 0.00,
            InterventionType.INTERNAL_RETRY_SCHEDULE: 0.20,
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
            InterventionType.DIRECT_PAYMENT_LINK_EMAIL: 0.18,
            InterventionType.CUSTOM_WEBHOOK_OUTREACH: 0.20,
        },
        FailureCategory.INSUFFICIENT_FUNDS: {
            InterventionType.NO_ACTION: 0.00,
            InterventionType.INTERNAL_RETRY_SCHEDULE: 0.12,
            InterventionType.DIRECT_PAYMENT_LINK_SMS: 0.10,
            InterventionType.DIRECT_PAYMENT_LINK_WHATSAPP: 0.12,
            InterventionType.DIRECT_PAYMENT_LINK_EMAIL: 0.05,
            InterventionType.CUSTOM_WEBHOOK_OUTREACH: 0.08,
        },
        FailureCategory.INVALID_INSTRUMENT: {
            InterventionType.NO_ACTION: 0.00,
            InterventionType.INTERNAL_RETRY_SCHEDULE: 0.00,
            InterventionType.DIRECT_PAYMENT_LINK_SMS: 0.15,
            InterventionType.DIRECT_PAYMENT_LINK_WHATSAPP: 0.18,
            InterventionType.DIRECT_PAYMENT_LINK_EMAIL: 0.08,
            InterventionType.CUSTOM_WEBHOOK_OUTREACH: 0.10,
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

    def __init__(
        self,
        evaluation_service: InterventionEvaluationService | None = None,
        policy_gate: PolicyGateService | None = None,
    ) -> None:
        self.eval_service = evaluation_service or InterventionEvaluationService()
        self.policy_gate = policy_gate or PolicyGateService()

    @property
    def name(self) -> str:
        return "LIFT Intelligent Engine"

    def evaluate(
        self,
        opportunity: RecoveryOpportunity,
        attempt: PaymentAttempt,
        customer: Customer,
        merchant: Merchant,
        eval_time: datetime | None = None,
    ) -> StrategyDecision:
        now = eval_time or datetime.now(timezone.utc)
        category = opportunity.failure_category

        # 1. Compute production estimate of P(Organic) from priors (NEVER uses DGP!)
        p_est_org = get_global_prior(category)

        # 2. Build candidate probability estimates
        channel_lifts = self.DEFAULT_ESTIMATED_LIFTS.get(category, {})
        # On micro-ticket transactions (< ₹50 / 5000 paise), suppress paid outreach (ADR-003)
        is_micro_ticket = opportunity.amount_at_risk_subunits < 5000

        p_rec_map: dict[InterventionType, float] = {}
        for itype in InterventionType:
            lift = channel_lifts.get(itype, 0.0)
            if is_micro_ticket and itype not in (
                InterventionType.NO_ACTION,
                InterventionType.INTERNAL_RETRY_SCHEDULE,
            ):
                # Suppress paid outreach on micro-tickets to prevent burning margin
                lift = 0.0
            p_rec_map[itype] = min(1.0, p_est_org + lift)

        # 3. Evaluate candidate slate via InterventionEvaluationService
        candidates = self.eval_service.evaluate_all_candidates(
            opportunity=opportunity,
            customer=customer,
            p_recovery_by_type=p_rec_map,
            p_organic=p_est_org,
            confidence_score=0.90,
            eval_time=now,
        )

        # 4. Filter and select best permitted candidate via PolicyGateService
        decision = self.policy_gate.select_best_candidate(
            candidates=candidates,
            opportunity=opportunity,
            merchant=merchant,
            customer=customer,
            eval_time=now,
        )

        # 5. Resolve winning action
        selected_action = InterventionType.NO_ACTION
        if decision.decision_type == DecisionType.AUTHORIZED and decision.selected_candidate_id:
            winning_cand = next(
                (c for c in candidates if c.id == decision.selected_candidate_id),
                None,
            )
            if winning_cand:
                selected_action = winning_cand.intervention_type

        return StrategyDecision(
            strategy_name=self.name,
            selected_action=selected_action,
            decision_type=decision.decision_type,
            rationale=decision.explanation,
            confidence_score=0.90,
            p_estimated_organic=p_est_org,
        )
