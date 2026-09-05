"""Automated architectural verification asserting strict DGP isolation.

Guarantees:
1. Zero production imports from lift.simulation.
2. Zero DGP latent parameters leaked into production domain models or scoring services.
"""

from __future__ import annotations

import ast
from pathlib import Path

from lift.domain.models import Customer, PaymentAttempt, RecoveryOpportunity
from lift.services.evaluation import InterventionEvaluationService
from lift.services.policy_gate import PolicyGateService


def test_zero_simulation_imports_in_production_code() -> None:
    """AST guard: Asserts that NO production module imports from lift.simulation."""
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    src_dir = repo_root / "packages" / "lift" / "src" / "lift"
    assert src_dir.exists(), f"Source directory not found: {src_dir}"

    production_files = [
        p for p in src_dir.rglob("*.py") if "simulation" not in p.parts and p.name != "__pycache__"
    ]

    assert len(production_files) > 10, f"Expected production files, found {len(production_files)}"

    violations: list[str] = []

    for file_path in production_files:
        content = file_path.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(file_path))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "simulation" in alias.name:
                        violations.append(f"{file_path.name}:{node.lineno} imports '{alias.name}'")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if "simulation" in module:
                    violations.append(f"{file_path.name}:{node.lineno} imports from '{module}'")

    assert not violations, (
        "Architectural violation: Production code must never import from lift.simulation.\n"
        + "\n".join(violations)
    )


def test_production_domain_models_do_not_contain_latent_dgp_fields() -> None:
    """Assert domain models do not expose latent DGP ground truth attributes."""
    opp_fields = set(RecoveryOpportunity.model_fields.keys())
    assert "p_true_organic" not in opp_fields
    assert "u_draw" not in opp_fields
    assert "delta_p_map" not in opp_fields
    assert "causal_profile" not in opp_fields

    attempt_fields = set(PaymentAttempt.model_fields.keys())
    assert "p_true_organic" not in attempt_fields
    assert "u_draw" not in attempt_fields

    customer_fields = set(Customer.model_fields.keys())
    assert "p_true_organic" not in customer_fields
    assert "u_draw" not in customer_fields


def test_production_service_signatures_reject_latent_parameters() -> None:
    """Assert production service evaluation methods do not accept latent DGP parameters."""
    import inspect

    eval_sig = inspect.signature(InterventionEvaluationService.evaluate_all_candidates)
    eval_params = set(eval_sig.parameters.keys())
    assert "p_true_organic" not in eval_params
    assert "causal_profile" not in eval_params
    assert "u_draw" not in eval_params

    gate_sig = inspect.signature(PolicyGateService.select_best_candidate)
    gate_params = set(gate_sig.parameters.keys())
    assert "p_true_organic" not in gate_params
    assert "causal_profile" not in gate_params
