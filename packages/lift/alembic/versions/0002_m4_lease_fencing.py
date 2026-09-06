"""M4 lease fencing and execution ownership schema updates.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-05 22:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. task_queue: add lease_version with check constraint
    with op.batch_alter_table("task_queue") as batch_op:
        batch_op.add_column(
            sa.Column("lease_version", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.create_check_constraint(
            "check_task_queue_lease_version_non_negative",
            "lease_version >= 0",
        )

    # 2. execution_records: add task_id and lease_version
    with op.batch_alter_table("execution_records") as batch_op:
        batch_op.add_column(sa.Column("task_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("lease_version", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_execution_records_task_id",
            "task_queue",
            ["task_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("idx_execution_task_id", ["task_id"])


def downgrade() -> None:
    # 1. execution_records: drop foreign key, index, and columns
    with op.batch_alter_table("execution_records") as batch_op:
        batch_op.drop_index("idx_execution_task_id")
        batch_op.drop_constraint("fk_execution_records_task_id", type_="foreignkey")
        batch_op.drop_column("lease_version")
        batch_op.drop_column("task_id")

    # 2. task_queue: drop check constraint and lease_version column
    with op.batch_alter_table("task_queue") as batch_op:
        batch_op.drop_constraint("check_task_queue_lease_version_non_negative", type_="check")
        batch_op.drop_column("lease_version")
