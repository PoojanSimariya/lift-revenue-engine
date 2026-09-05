"""LIFT simulation, synthetic causal benchmark harness, and baseline strategies."""

from lift.simulation.baselines import (
    Baseline0HoldoutStrategy,
    Baseline1PeriodicRetryStrategy,
    Baseline2NaiveOutreachStrategy,
    LiftRecoveryStrategy,
    RecoveryStrategy,
    StrategyDecision,
)
from lift.simulation.benchmark import (
    BenchmarkRecord,
    BenchmarkReport,
    BenchmarkRunner,
    StrategyBenchmarkSummary,
)
from lift.simulation.cohorts import (
    create_hard_decline_cohort,
    create_high_fatigue_cohort,
    create_high_organic_cohort,
    create_micro_ticket_cohort,
)
from lift.simulation.dgp import CausalDGP, CausalProfile, RealizedOutcome
from lift.simulation.generator import (
    SyntheticBatchGenerator,
    SyntheticOpportunityBundle,
)

__all__ = [
    "Baseline0HoldoutStrategy",
    "Baseline1PeriodicRetryStrategy",
    "Baseline2NaiveOutreachStrategy",
    "BenchmarkRecord",
    "BenchmarkReport",
    "BenchmarkRunner",
    "CausalDGP",
    "CausalProfile",
    "LiftRecoveryStrategy",
    "RealizedOutcome",
    "RecoveryStrategy",
    "StrategyBenchmarkSummary",
    "StrategyDecision",
    "SyntheticBatchGenerator",
    "SyntheticOpportunityBundle",
    "create_hard_decline_cohort",
    "create_high_fatigue_cohort",
    "create_high_organic_cohort",
    "create_micro_ticket_cohort",
]
