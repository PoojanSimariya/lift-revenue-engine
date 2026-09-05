"""Integration test executing Alembic migration upgrade to head and downgrade to base."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from lift.storage.database import create_db_engine
from sqlalchemy import inspect


def test_alembic_upgrade_and_downgrade(tmp_path: Path) -> None:
    """Test full Alembic migration cycle on a dedicated test SQLite database."""
    db_file = tmp_path / "alembic_test.db"
    db_url = f"sqlite:///{db_file.as_posix()}"

    # Path to alembic.ini
    repo_root = Path(__file__).resolve().parent.parent.parent
    alembic_ini_path = repo_root / "packages" / "lift" / "alembic.ini"
    assert alembic_ini_path.exists(), f"alembic.ini not found at {alembic_ini_path}"

    config = Config(str(alembic_ini_path))
    config.set_main_option("script_location", str(repo_root / "packages" / "lift" / "alembic"))

    engine = create_db_engine(db_url)

    with engine.connect() as connection:
        config.attributes["connection"] = connection

        # 1. Upgrade to head
        command.upgrade(config, "head")

        # 2. Inspect tables in newly migrated database
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
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
            "alembic_version",
        }
        assert expected_tables.issubset(tables)

        # 3. Downgrade to base
        command.downgrade(config, "base")

        # 4. Verify all application tables were dropped
        inspector_after = inspect(connection)
        tables_after = set(inspector_after.get_table_names())
        assert not any(t in tables_after for t in expected_tables - {"alembic_version"})

        # 5. Re-upgrade to head to verify repeatable upgrade
        command.upgrade(config, "head")
        tables_reupgraded = set(inspect(connection).get_table_names())
        assert expected_tables.issubset(tables_reupgraded)

    engine.dispose()
