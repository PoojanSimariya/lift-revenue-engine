"""Baseline schema with 12 core tables for LIFT engine.

Revision ID: 0001
Revises:
Create Date: 2026-09-05 12:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

json_type = sa.JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    # 1. merchants
    op.create_table(
        "merchants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("default_currency", sa.String(length=3), nullable=False, server_default="INR"),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Asia/Kolkata"),
        sa.Column("idempotency_salt", sa.String(length=64), nullable=False),
        sa.Column("razorpay_key_id", sa.String(length=128), nullable=True),
        sa.Column("razorpay_key_secret_encrypted", sa.Text(), nullable=True),
        sa.Column("razorpay_webhook_secret_encrypted", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # 2. customers
    op.create_table(
        "customers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), nullable=False),
        sa.Column("external_customer_id", sa.String(length=128), nullable=False),
        sa.Column("phone_hash", sa.String(length=64), nullable=True),
        sa.Column("email_hash", sa.String(length=64), nullable=True),
        sa.Column("risk_tier", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("lifetime_recovery_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lifetime_failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rolling_contacts_7d", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_contacted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "merchant_id", "external_customer_id", name="uq_merchant_external_customer"
        ),
    )
    op.create_index("idx_customers_merchant_id", "customers", ["merchant_id"])

    # 3. policy_rules
    op.create_table(
        "policy_rules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), nullable=False),
        sa.Column("rule_type", sa.String(length=64), nullable=False),
        sa.Column("parameters", json_type, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_policy_rules_merchant", "policy_rules", ["merchant_id", "is_active"])

    # 4. webhook_events
    op.create_table(
        "webhook_events",
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index("idx_webhook_events_status", "webhook_events", ["status"])

    # 5. task_queue
    op.create_table(
        "task_queue",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="QUEUED"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_by", sa.String(length=64), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_task_queue_poll", "task_queue", ["status", "scheduled_at", "priority"])

    # 6. payment_attempts
    op.create_table(
        "payment_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("recovery_opportunity_id", sa.Uuid(), nullable=True),
        sa.Column("razorpay_payment_id", sa.String(length=64), nullable=False),
        sa.Column("razorpay_order_id", sa.String(length=64), nullable=False),
        sa.Column("attempt_sequence", sa.Integer(), nullable=False),
        sa.Column("amount_subunits", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="INR"),
        sa.Column("payment_method", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_description", sa.Text(), nullable=True),
        sa.Column("error_source", sa.String(length=32), nullable=True),
        sa.Column("error_step", sa.String(length=32), nullable=True),
        sa.Column("error_reason", sa.String(length=64), nullable=True),
        sa.Column("gateway_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_payload", json_type, nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("razorpay_payment_id"),
    )
    op.create_index("idx_attempts_order", "payment_attempts", ["razorpay_order_id"])
    op.create_index("idx_attempts_opp", "payment_attempts", ["recovery_opportunity_id"])

    # 7. recovery_opportunities
    op.create_table(
        "recovery_opportunities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.String(length=64), nullable=False),
        sa.Column("initial_attempt_id", sa.Uuid(), nullable=False),
        sa.Column("latest_attempt_id", sa.Uuid(), nullable=False),
        sa.Column("amount_at_risk_subunits", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="INR"),
        sa.Column("current_state", sa.String(length=32), nullable=False, server_default="OPEN"),
        sa.Column("failure_category", sa.String(length=32), nullable=False),
        sa.Column("organic_recovery_estimate", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column(
            "organic_estimation_source",
            sa.String(length=32),
            nullable=False,
            server_default="FALLBACK",
        ),
        sa.Column("failure_attempt_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("total_interventions_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_contacts_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("execution_claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["initial_attempt_id"], ["payment_attempts.id"]),
        sa.ForeignKeyConstraint(["latest_attempt_id"], ["payment_attempts.id"]),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("merchant_id", "order_id", name="uq_merchant_order"),
    )
    op.create_index(
        "idx_opportunities_order", "recovery_opportunities", ["merchant_id", "order_id"]
    )
    op.create_index("idx_opportunities_state", "recovery_opportunities", ["current_state"])

    # Now add circular FK from payment_attempts.recovery_opportunity_id -> recovery_opportunities.id
    # Note: On SQLite, create_foreign_key is batch-mode or metadata-defined.
    # In alembic, we add foreign key constraint:
    with op.batch_alter_table("payment_attempts") as batch_op:
        batch_op.create_foreign_key(
            "fk_payment_attempts_opportunity",
            "recovery_opportunities",
            ["recovery_opportunity_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # 8. intervention_candidates
    op.create_table(
        "intervention_candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("opportunity_id", sa.Uuid(), nullable=False),
        sa.Column("intervention_type", sa.String(length=64), nullable=False),
        sa.Column("parameters", json_type, nullable=False),
        sa.Column("p_recovery", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("p_organic", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("direct_cost_subunits", sa.BigInteger(), nullable=False),
        sa.Column("friction_cost_subunits", sa.BigInteger(), nullable=False),
        sa.Column("risk_penalty_subunits", sa.BigInteger(), nullable=False),
        sa.Column("expected_net_value_subunits", sa.BigInteger(), nullable=False),
        sa.Column("confidence_score", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column(
            "contact_fatigue",
            sa.Numeric(precision=7, scale=4),
            nullable=False,
            server_default="0.0",
        ),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["opportunity_id"], ["recovery_opportunities.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_candidates_opp", "intervention_candidates", ["opportunity_id"])

    # 9. recovery_decisions
    op.create_table(
        "recovery_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("opportunity_id", sa.Uuid(), nullable=False),
        sa.Column("selected_candidate_id", sa.Uuid(), nullable=True),
        sa.Column("decision_type", sa.String(length=32), nullable=False),
        sa.Column("policy_evaluation_details", json_type, nullable=False),
        sa.Column("blocked_reason_code", sa.String(length=64), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["opportunity_id"], ["recovery_opportunities.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["selected_candidate_id"], ["intervention_candidates.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_decisions_opp", "recovery_decisions", ["opportunity_id"])

    # 10. execution_records
    op.create_table(
        "execution_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("decision_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_index", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("reference_id", sa.String(length=64), nullable=False),
        sa.Column("intervention_type", sa.String(length=64), nullable=False),
        sa.Column(
            "execution_status", sa.String(length=32), nullable=False, server_default="CLAIMED"
        ),
        sa.Column("external_reference_id", sa.String(length=128), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["decision_id"], ["recovery_decisions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint("reference_id"),
    )
    op.create_index("idx_execution_idempotency", "execution_records", ["idempotency_key"])
    op.create_index("idx_execution_reference", "execution_records", ["reference_id"])

    # 11. payment_evidences
    op.create_table(
        "payment_evidences",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("opportunity_id", sa.Uuid(), nullable=False),
        sa.Column("razorpay_payment_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("signature_hash", sa.String(length=128), nullable=False),
        sa.Column("captured_amount_subunits", sa.BigInteger(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["opportunity_id"], ["recovery_opportunities.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("razorpay_payment_id"),
    )
    op.create_index("idx_payment_evidence_opp", "payment_evidences", ["opportunity_id"])

    # 12. audit_events
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("event_name", sa.String(length=64), nullable=False),
        sa.Column("state_before", json_type, nullable=True),
        sa.Column("state_after", json_type, nullable=True),
        sa.Column("actor_type", sa.String(length=32), nullable=False, server_default="SYSTEM"),
        sa.Column("metadata", json_type, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_audit_trace", "audit_events", ["trace_id"])
    op.create_index("idx_audit_merchant", "audit_events", ["merchant_id"])
    op.create_index("idx_audit_aggregate", "audit_events", ["aggregate_type", "aggregate_id"])


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("payment_evidences")
    op.drop_table("execution_records")
    op.drop_table("recovery_decisions")
    op.drop_table("intervention_candidates")
    with op.batch_alter_table("payment_attempts") as batch_op:
        batch_op.drop_constraint("fk_payment_attempts_opportunity", type_="foreignkey")
    op.drop_table("recovery_opportunities")
    op.drop_table("payment_attempts")
    op.drop_table("task_queue")
    op.drop_table("webhook_events")
    op.drop_table("policy_rules")
    op.drop_table("customers")
    op.drop_table("merchants")
