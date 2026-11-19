"""Business rules that need no database: stages, follow-up checks, due dates, tiers, fair assignment, sign-in rules."""
from datetime import date, datetime, timedelta

import pytest

from salesmesh import auth, pipeline, scoring

TODAY = date(2026, 11, 2)


# ---- stages (FR-04)
@pytest.mark.parametrize("current, outcome, expected", [
    ("New", "No answer", "Contacted"),
    ("New", "Interested", "Qualified"),
    ("Contacted", "Needs follow-up", "Contacted"),
    ("Qualified", "No answer", "Qualified"),      # open leads never move backwards
    ("Qualified", "Converted", "Converted"),
    ("Contacted", "Lost", "Lost"),
])
def test_next_stage(current, outcome, expected):
    assert pipeline.next_stage(current, outcome) == expected


def test_closed_lead_cannot_take_a_follow_up():
    with pytest.raises(ValueError):
        pipeline.next_stage("Converted", "Interested")


def test_unknown_outcome_is_refused():
    with pytest.raises(ValueError):
        pipeline.next_stage("New", "Maybe")


# ---- follow-up form checks (FR-04, defect D03)
def test_valid_follow_up_has_no_problems():
    assert pipeline.follow_up_problems("New", "Call", "Interested", TODAY, None, TODAY) == []


def test_closing_a_lead_needs_no_next_date():
    assert pipeline.follow_up_problems("Qualified", "Call", "Converted", None, None, TODAY) == []


def test_lost_needs_a_reason():
    problems = pipeline.follow_up_problems("Contacted", "Call", "Lost", None, None, TODAY)
    assert problems == ["Choose why the lead was lost."]


def test_open_outcome_needs_a_future_date():
    assert pipeline.follow_up_problems("New", "Call", "No answer", None, None, TODAY)
    assert pipeline.follow_up_problems("New", "Call", "No answer", TODAY - timedelta(days=1), None, TODAY)


# ---- due dates and overdue (FR-03)
@pytest.mark.parametrize("tier, days", [("High", 1), ("Medium", 3), ("Low", 7)])
def test_first_contact_window_by_tier(tier, days):
    assigned = datetime(2026, 11, 2, 9, 0)
    assert pipeline.due_date("New", tier, assigned, None) == date(2026, 11, 2) + timedelta(days=days)


def test_next_follow_up_date_wins_and_closed_has_no_due_date():
    assigned = datetime(2026, 11, 2, 9, 0)
    assert pipeline.due_date("Contacted", "High", assigned, date(2026, 11, 20)) == date(2026, 11, 20)
    assert pipeline.due_date("Lost", "High", assigned, None) is None


def test_overdue_only_after_the_due_date():
    assert pipeline.is_overdue(TODAY - timedelta(days=1), TODAY)
    assert not pipeline.is_overdue(TODAY, TODAY)
    assert not pipeline.is_overdue(None, TODAY)


# ---- priority tiers (FR-04)
@pytest.mark.parametrize("prob, tier", [(0.70, "High"), (0.6999, "Medium"), (0.40, "Medium"), (0.3999, "Low")])
def test_tier_boundaries(prob, tier):
    assert scoring.assign_tier(prob) == tier


# ---- fair assignment (FR-05, lecturer's question about ties)
def test_fewest_open_leads_wins():
    assert pipeline.choose_owner({1: 5, 2: 3, 3: 4}, {}) == 2


def test_tie_goes_to_whoever_waited_longest():
    last = {1: datetime(2026, 11, 2, 10), 2: datetime(2026, 11, 1, 10)}
    assert pipeline.choose_owner({1: 3, 2: 3}, last) == 2


def test_someone_never_given_a_lead_goes_first():
    assert pipeline.choose_owner({1: 0, 2: 0}, {1: datetime(2026, 11, 1)}) == 2


def test_tied_marketers_take_turns():
    load, last, given = {1: 2, 2: 2}, {}, []
    for i in range(6):
        owner = pipeline.choose_owner(load, last)
        given.append(owner)
        load[owner] += 1
        last[owner] = datetime(2026, 11, 2) + timedelta(seconds=i)
    assert given == [1, 2, 1, 2, 1, 2]


def test_exclude_and_empty_team():
    assert pipeline.choose_owner({1: 0, 2: 9}, {}, exclude=1) == 2
    assert pipeline.choose_owner({1: 0}, {}, exclude=1) is None


# ---- sign-in rules
@pytest.mark.parametrize("email, ok", [
    ("jane@salesmesh.com", True), ("first.last@company.co.ke", True),
    ("bad..email@x.com", False),   # defect D04
    ("no-at-sign.com", False), ("a@b", False),
])
def test_email_format(email, ok):
    assert bool(auth.EMAIL_RE.match(email)) is ok


def test_password_rules():
    assert auth.password_problems("Strong#2026") == []
    assert set(auth.password_problems("short")) == {"at least 8 characters", "a number"}


def test_passwords_are_hashed_with_bcrypt():
    stored = auth.hash_password("Strong#2026")
    assert stored.startswith("$2") and "Strong#2026" not in stored
    assert auth.verify_password("Strong#2026", stored)
    assert not auth.verify_password("Wrong#2026", stored)
    assert not auth.verify_password("anything", auth.EXTERNAL_ONLY)


def test_who_can_invite_whom():
    assert auth.INVITABLE_BY["manager"] == ("marketer",)
    assert "manager" in auth.INVITABLE_BY["admin"]
    assert "marketer" not in auth.INVITABLE_BY
