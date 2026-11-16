"""
Shared test set-up. Run from the app folder:   python -m pytest -q

The rule and scoring tests need no database. The database tests use the
SalesMesh database from app/.env and are skipped if it cannot be reached.
They only read data or check that forbidden actions are refused, so running
them does not change any lead or account.
"""
import sys
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))


@pytest.fixture(scope="session")
def database():
    from salesmesh import db
    try:
        db.query("SELECT 1 FROM invitations LIMIT 1")
    except Exception as e:  # MySQL not running, wrong password, or setup_database.py not run yet
        pytest.skip(f"Database not available: {e}")
    return db


@pytest.fixture(scope="session")
def people(database):
    """Real accounts from the database, found by role (no emails or passwords in the code)."""
    rows = database.query("SELECT u.user_id, u.email, u.role, tm.full_name FROM users u "
                          "JOIN team_members tm ON tm.user_id = u.user_id "
                          "WHERE u.is_active = 1 ORDER BY u.user_id")
    as_user = lambda r: {k: r[k] for k in ("user_id", "email", "role", "full_name")}
    marketers = [as_user(r) for r in rows if r["role"] == "marketer"]
    managers = [as_user(r) for r in rows if r["role"] == "manager"]
    if len(marketers) < 2 or not managers:
        pytest.skip("Needs at least two active marketers and one manager")
    return {"manager": managers[0], "m1": marketers[0], "m2": marketers[1]}


@pytest.fixture(scope="session")
def open_lead_of(database):
    def find(user_id):
        row = database.query_one("SELECT lead_id FROM leads WHERE record_type = 'pipeline' AND assigned_to = %s "
                                 "AND stage IN ('New', 'Contacted', 'Qualified') LIMIT 1", (user_id,))
        if row is None:
            pytest.skip("No open lead for this marketer")
        return row["lead_id"]
    return find
