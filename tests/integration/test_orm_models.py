"""Integration tests verifying SQLAlchemy ORM models, tables, columns, and constraints."""

from __future__ import annotations

from lift.storage.base import Base
from sqlalchemy import Engine, inspect


def test_all_twelve_tables_exist_in_metadata() -> None:
    """Verify exactly 12 core tables exist in Base metadata."""
    expected_tables = {
        "merchants",
        "customers",
        "policy_rules",
        "webhook_events",
        "task_queue",
        "payment_attempts",
        "recovery_opportunities",
        "intervention_candidates",
        "recovery_decisions",
        "execution_records",
        "payment_evidences",
        "audit_events",
    }
    actual_tables = set(Base.metadata.tables.keys())
    assert expected_tables.issubset(actual_tables)
    assert len(expected_tables) == 12


def test_tables_created_in_database(engine: Engine) -> None:
    """Verify all 12 tables are created in the SQLite database."""
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    expected_tables = {
        "merchants",
        "customers",
        "policy_rules",
        "webhook_events",
        "task_queue",
        "payment_attempts",
        "recovery_opportunities",
        "intervention_candidates",
        "recovery_decisions",
        "execution_records",
        "payment_evidences",
        "audit_events",
    }
    for table in expected_tables:
        assert table in table_names, f"Table {table} missing from database"


def test_column_nullability_and_types(engine: Engine) -> None:
    """Inspect columns to ensure required fields are not nullable."""
    inspector = inspect(engine)

    # merchants
    merchant_cols = {c["name"]: c for c in inspector.get_columns("merchants")}
    assert not merchant_cols["id"]["nullable"]
    assert not merchant_cols["name"]["nullable"]
    assert not merchant_cols["default_currency"]["nullable"]
    assert not merchant_cols["timezone"]["nullable"]
    assert not merchant_cols["idempotency_salt"]["nullable"]
    assert merchant_cols["razorpay_key_id"]["nullable"]

    # customers
    customer_cols = {c["name"]: c for c in inspector.get_columns("customers")}
    assert not customer_cols["id"]["nullable"]
    assert not customer_cols["merchant_id"]["nullable"]
    assert not customer_cols["external_customer_id"]["nullable"]
    assert customer_cols["phone_hash"]["nullable"]
    assert customer_cols["email_hash"]["nullable"]

    # payment_attempts
    attempt_cols = {c["name"]: c for c in inspector.get_columns("payment_attempts")}
    assert not attempt_cols["id"]["nullable"]
    assert not attempt_cols["customer_id"]["nullable"]
    assert attempt_cols["recovery_opportunity_id"]["nullable"]  # Can be NULL at Step 1
    assert not attempt_cols["razorpay_payment_id"]["nullable"]
    assert not attempt_cols["amount_subunits"]["nullable"]

    # recovery_opportunities
    opp_cols = {c["name"]: c for c in inspector.get_columns("recovery_opportunities")}
    assert not opp_cols["id"]["nullable"]
    assert not opp_cols["initial_attempt_id"]["nullable"]
    assert not opp_cols["latest_attempt_id"]["nullable"]
    assert not opp_cols["current_state"]["nullable"]
    assert not opp_cols["amount_at_risk_subunits"]["nullable"]

    # execution_records
    exec_cols = {c["name"]: c for c in inspector.get_columns("execution_records")}
    assert not exec_cols["id"]["nullable"]
    assert not exec_cols["decision_id"]["nullable"]
    assert not exec_cols["idempotency_key"]["nullable"]
    assert not exec_cols["reference_id"]["nullable"]
    assert not exec_cols["execution_status"]["nullable"]


def test_unique_constraints_and_indices(engine: Engine) -> None:
    """Verify unique constraints and index coverage."""
    inspector = inspect(engine)

    # Check merchant_order unique constraint on recovery_opportunities
    opp_uqs = inspector.get_unique_constraints("recovery_opportunities")
    opp_uq_cols = [set(u["column_names"]) for u in opp_uqs]
    assert {"merchant_id", "order_id"} in opp_uq_cols

    # Check customer merchant_id + external_customer_id unique constraint
    cust_uqs = inspector.get_unique_constraints("customers")
    cust_uq_cols = [set(u["column_names"]) for u in cust_uqs]
    assert {"merchant_id", "external_customer_id"} in cust_uq_cols

    # Check indexes exist
    attempt_indexes = {idx["name"] for idx in inspector.get_indexes("payment_attempts")}
    assert "idx_payment_attempts_order" in attempt_indexes
    assert "idx_payment_attempts_opp" in attempt_indexes

    audit_indexes = {idx["name"] for idx in inspector.get_indexes("audit_events")}
    assert "idx_audit_trace" in audit_indexes
    assert "idx_audit_merchant" in audit_indexes
    assert "idx_audit_aggregate" in audit_indexes
