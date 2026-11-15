"""Fresh real retained-review databases preserve defaults and atomic preparation."""

import sqlite3

import pytest
from tests.unit.native_host import test_human_review as fixture


def test_review_fixture_defaults_committed_seeds_and_database_isolation(tmp_path):
    first = tmp_path / "first.sqlite"
    second = tmp_path / "second.sqlite"
    fixture._initialize_human_database(first)
    fixture._initialize_human_database(second)
    with sqlite3.connect(first) as db:
        db.execute("UPDATE users SET status='disabled'")
    with sqlite3.connect(second) as db:
        with sqlite3.connect(tmp_path / "platform-defaults.sqlite") as defaults:
            for pragma in ("journal_mode", "synchronous", "foreign_keys"):
                assert (
                    db.execute("PRAGMA " + pragma).fetchone()
                    == defaults.execute("PRAGMA " + pragma).fetchone()
                )
        assert db.execute("SELECT status,phone FROM users").fetchone() == (
            "active",
            "13900000001",
        )
        for table in (
            "organizations",
            "users",
            "human_accounts",
            "human_account_memberships",
            "ontology_projects",
            "mcp_servers",
            "mcp_policies",
        ):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 1
        assert not db.in_transaction


@pytest.mark.parametrize("failure", ["schema", "seed"])
def test_review_fixture_refuses_invalid_input_without_partial_database(
    tmp_path, monkeypatch, failure
):
    path = tmp_path / "invalid.sqlite"
    if failure == "schema":
        monkeypatch.setattr(fixture, "_FROZEN_SCHEMA", fixture._FROZEN_SCHEMA + "\nINVALID_SQL;\n")
        error = sqlite3.OperationalError
    else:
        monkeypatch.setattr(fixture, "_IDS", {**fixture._IDS, "account": None})
        error = sqlite3.IntegrityError
    with pytest.raises(error):
        fixture._initialize_human_database(path)
    with sqlite3.connect(path) as db:
        assert (
            db.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0] == 0
        )
