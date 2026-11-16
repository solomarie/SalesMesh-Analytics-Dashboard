"""
Database and permission checks. These only read, or try actions that must be refused,
so they never change a lead or an account.
"""
import pytest

from salesmesh import auth, notify, pipeline


def test_no_plain_text_passwords(database):
    rows = database.query("SELECT password_hash FROM users")
    assert rows and all(r["password_hash"].startswith("$2") or r["password_hash"] == auth.EXTERNAL_ONLY
                        for r in rows)


def test_prospect_ids_are_unique(database):
    dupes = database.query("SELECT prospect_id FROM leads GROUP BY prospect_id HAVING COUNT(*) > 1")
    assert dupes == []


def test_team_view_has_no_passwords(database):
    columns = {r["Field"] for r in database.query("SHOW COLUMNS FROM team_members")}
    assert "password_hash" not in columns


def test_marketer_sees_only_own_leads(people):
    leads = pipeline.get_leads(people["m1"])
    assert leads.empty or set(leads["assigned_to"]) == {people["m1"]["user_id"]}


def test_marketer_cannot_open_a_colleagues_lead(people, open_lead_of):
    with pytest.raises(PermissionError):
        pipeline.get_lead(people["m1"], open_lead_of(people["m2"]["user_id"]))


def test_manager_sees_everyone(people):
    owners = set(pipeline.get_leads(people["manager"])["assigned_to"])
    assert {people["m1"]["user_id"], people["m2"]["user_id"]} <= owners


# ---- handover, requests, reopen (FR-14, FR-15): refusals only
def test_only_owner_or_manager_can_hand_over(people, open_lead_of):
    with pytest.raises(PermissionError):
        pipeline.handover_lead(people["m1"], open_lead_of(people["m2"]["user_id"]), people["m1"]["user_id"],
                               "Customer asked for me")


def test_handover_needs_a_reason(people, open_lead_of):
    with pytest.raises(ValueError):
        pipeline.handover_lead(people["m1"], open_lead_of(people["m1"]["user_id"]), people["m2"]["user_id"], "")


def test_cannot_ask_for_your_own_lead(people, open_lead_of):
    with pytest.raises(ValueError):
        pipeline.request_lead(people["m1"], open_lead_of(people["m1"]["user_id"]), "Customer called me")


def test_managers_hand_over_rather_than_ask(people, open_lead_of):
    with pytest.raises(PermissionError):
        pipeline.request_lead(people["manager"], open_lead_of(people["m1"]["user_id"]), "Please")


def test_only_lost_leads_can_be_reopened(people, open_lead_of):
    from datetime import date
    with pytest.raises(ValueError):
        pipeline.reopen_lead(people["m1"], open_lead_of(people["m1"]["user_id"]), "Call", "Interested", "",
                             date.today())


def test_reopen_needs_a_valid_outcome(people, open_lead_of):
    from datetime import date
    with pytest.raises(ValueError):
        pipeline.reopen_lead(people["m1"], open_lead_of(people["m1"]["user_id"]), "Call", "Converted", "",
                             date.today())


def test_answering_a_missing_request(people):
    with pytest.raises(LookupError):
        pipeline.decide_request(people["manager"], 999999999, True)


def test_find_shows_routing_details_only(people, database):
    pid = database.query_one("SELECT prospect_id FROM leads WHERE record_type = 'pipeline' LIMIT 1")["prospect_id"]
    found = pipeline.find_leads(people["m1"], pid[:8])
    assert pid in set(found["prospect_id"])
    assert "lead_source" not in found.columns and "top_reasons" not in found.columns
    with pytest.raises(ValueError):
        pipeline.find_leads(people["m1"], "ab")


# ---- invitations instead of approvals
def test_marketers_cannot_invite(people):
    with pytest.raises(PermissionError):
        auth.create_invitations(people["m1"], [{"email": "someone@company.com"}])


def test_invalid_invitations_are_skipped_not_sent(people):
    sent, skipped = auth.create_invitations(people["manager"], [
        {"email": "bad..email@company.com"},
        {"email": people["m1"]["email"]},
        {"email": "boss@company.com", "role": "manager"},
    ])
    assert sent == []
    assert [s["reason"] for s in skipped] == ["Not a valid email address", "Already has a SalesMesh account",
                                              "You cannot invite a manager"]


def test_unknown_invitation_link_is_refused(database):
    with pytest.raises(auth.AuthError):
        auth.check_invitation("not-a-real-token")


def test_google_stranger_is_refused_without_creating_an_account(database):
    email = "stranger@not-a-salesmesh-company.example"
    with pytest.raises(auth.AuthError):
        auth.external_sign_in(email, "Stranger", True)
    assert database.query_one("SELECT user_id FROM users WHERE email = %s", (email,)) is None


def test_unverified_google_email_is_refused(database):
    with pytest.raises(auth.AuthError):
        auth.external_sign_in("someone@gmail.com", "Someone", False)


def test_wrong_login_gives_the_same_message(database):
    with pytest.raises(auth.AuthError, match="Incorrect email or password"):
        auth.authenticate("nobody-here@salesmesh.com", "Whatever#123")


def test_unread_count_is_a_number(people):
    assert notify.unread_count(people["m1"]) >= 0


def test_sql_injection_in_the_email_field_is_refused(database):
    for attempt in ["' OR '1'='1", "admin@salesmesh.com' -- "]:
        with pytest.raises(auth.AuthError, match="Incorrect email or password"):
            auth.authenticate(attempt, "anything")
