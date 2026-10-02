"""Alembic migrations: apply cleanly, match the models, and render sane PostgreSQL DDL."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _alembic(db_url: str, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "APP_DB_URL": db_url}
    return subprocess.run(
        [sys.executable, "-W", "ignore", "-m", "alembic", *args],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120,
    )


def _ok(result: subprocess.CompletedProcess) -> str:
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    return result.stdout + result.stderr


@pytest.fixture
def sqlite_url(tmp_path) -> str:
    return f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"


def test_upgrade_check_downgrade_roundtrip(sqlite_url):
    _ok(_alembic(sqlite_url, "upgrade", "head"))
    assert "No new upgrade operations detected" in _ok(_alembic(sqlite_url, "check"))  # models == migrations
    _ok(_alembic(sqlite_url, "downgrade", "base"))
    _ok(_alembic(sqlite_url, "upgrade", "head"))  # and back up again


def test_single_linear_history(sqlite_url):
    heads = _ok(_alembic(sqlite_url, "heads"))
    assert heads.count("(head)") == 1


def test_postgres_ddl_creates_each_enum_type_once():
    """Regression: incidentstate is shared by three tables; creating it per table fails on PostgreSQL."""
    sql = _ok(_alembic("postgresql://u:p@localhost/x", "upgrade", "head", "--sql"))
    assert sql.count("CREATE TYPE incidentstate") == 1
    assert sql.count("CREATE TYPE role") == 1
    assert sql.count("CREATE TYPE datasourcehealthstatus") == 1
    assert sql.count("CREATE TYPE approvalstage") == 1
    assert sql.count("CREATE TYPE approvalstatus") == 1
    assert sql.count("CREATE TYPE risklevel") == 1  # shared by incidents and incident_approvals
    assert sql.count("risk_level risklevel") == 2
    assert sql.count("CREATE TYPE evidencetype") == 1
    assert "uq_incident_evidence_hash UNIQUE (incident_id, content_hash)" in sql  # idempotent re-runs
    assert "REFERENCES data_sources (id) ON DELETE RESTRICT" in sql  # a source with evidence cannot vanish
    assert "WHERE is_write_operation = true" in sql  # partial index for the write-operation audit check
    assert "DEFAULT now()" in sql
    assert "JSONB" in sql
    assert "WHERE status IN ('QUEUED', 'RUNNING')" in sql  # partial unique index: one active run per incident
    assert "WHERE status = 'PENDING'" in sql  # partial unique index: one open approval request per stage
    assert "ck_incident_approvals_rejected_requires_reason" in sql
    assert "ck_incident_approvals_decided_requires_actor" in sql


def test_postgres_downgrade_drops_enum_types():
    sql = _ok(_alembic("postgresql://u:p@localhost/x", "downgrade", "head:base", "--sql"))
    for enum in (
        "incidentstate", "role", "investigationrunstatus", "datasourcehealthstatus", "approvalstage", "approvalstatus", "risklevel",
        "evidencetype",
    ):
        assert f"DROP TYPE {enum}" in sql
