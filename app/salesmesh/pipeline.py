"""
Lead pipeline: stages, follow-ups, assignment, overdue rules and team performance.

FR-07 lead capture and instant scoring, FR-08 automatic assignment, FR-09 follow-up
tracking, FR-10 call list and overdue alerts, FR-11 team performance.
Every function that reads or changes data checks the user's role on the server side.
"""
from datetime import date, datetime, timedelta

import mysql.connector
import pandas as pd

from . import db, scoring
from .auth import require_role

STAGES = ["New", "Contacted", "Qualified", "Converted", "Lost"]
OPEN_STAGES = ["New", "Contacted", "Qualified"]
CLOSED_STAGES = ["Converted", "Lost"]
FOLLOW_UP_TYPES = ["Call", "Email", "Meeting", "SMS"]
# Outcome of a contact -> the stage it moves the lead to
OUTCOMES = {
    "No answer": "Contacted",
    "Needs follow-up": "Contacted",
    "Interested": "Qualified",
    "Converted": "Converted",
    "Lost": "Lost",
}
LOST_REASONS = ["Not interested", "Chose a competitor", "Price or budget", "Could not be reached", "Not a good fit"]
# A new, untouched lead becomes overdue if nobody contacts it within this many days
FIRST_CONTACT_DAYS = {"High": 1, "Medium": 3, "Low": 7}


# ---------------------------------------------------------------- pure rules
def next_stage(current: str, outcome: str) -> str:
    """Stage after a follow-up. Open leads never move backwards."""
    if current in CLOSED_STAGES:
        raise ValueError("This lead is already closed.")
    if outcome not in OUTCOMES:
        raise ValueError(f"Unknown outcome '{outcome}'.")
    target = OUTCOMES[outcome]
    if target in CLOSED_STAGES:
        return target
    return max(current, target, key=OPEN_STAGES.index)


def follow_up_problems(current: str, follow_up_type: str, outcome: str, next_date, lost_reason, today: date) -> list:
    problems = []
    if current in CLOSED_STAGES:
        problems.append("This lead is already closed.")
    if follow_up_type not in FOLLOW_UP_TYPES:
        problems.append("Choose how you contacted the lead.")
    if outcome not in OUTCOMES:
        problems.append("Choose the outcome of the contact.")
    closing = OUTCOMES.get(outcome) in CLOSED_STAGES
    if not closing:
        if next_date is None:
            problems.append("Set the date of the next follow-up.")
        elif next_date < today:
            problems.append("The next follow-up date cannot be in the past.")
    if outcome == "Lost" and not lost_reason:
        problems.append("Choose why the lead was lost.")
    return problems


def due_date(stage: str, tier: str, assigned_at, next_follow_up):
    """When the lead next needs attention (same rule as the lead_pipeline_status view)."""
    if stage in CLOSED_STAGES or assigned_at is None:
        return None
    if next_follow_up is not None:
        return next_follow_up
    return (assigned_at + timedelta(days=FIRST_CONTACT_DAYS[tier])).date()


def is_overdue(due, today: date) -> bool:
    return due is not None and due < today


# ---------------------------------------------------------------- reading
LEAD_COLUMNS = ("lead_id, prospect_id, lead_source, lead_origin, occupation, city, total_visits, time_on_website, "
                "last_activity, stage, lost_reason, assigned_to, assigned_name, assigned_at, created_at, closed_at, "
                "conversion_probability, priority_tier, top_reasons, follow_up_count, last_follow_up_date, "
                "next_follow_up_date, due_date, is_overdue")


def get_leads(user: dict, stages=None, marketer_id=None) -> pd.DataFrame:
    """Pipeline leads the user may see: a marketer only their own, a manager or admin everyone's."""
    require_role(user, "admin", "manager", "marketer")
    sql = f"SELECT {LEAD_COLUMNS} FROM lead_pipeline_status WHERE record_type = 'pipeline'"
    params = []
    if user["role"] == "marketer":
        sql += " AND assigned_to = %s"
        params.append(user["user_id"])
    elif marketer_id:
        sql += " AND assigned_to = %s"
        params.append(marketer_id)
    if stages:
        sql += " AND stage IN (" + ", ".join(["%s"] * len(stages)) + ")"
        params.extend(stages)
    sql += " ORDER BY is_overdue DESC, due_date IS NULL, due_date, conversion_probability DESC"
    return pd.DataFrame(db.query(sql, tuple(params)))


def call_list(user: dict) -> pd.DataFrame:
    """FR-10: open leads that need attention today or earlier, overdue first, then by score."""
    leads = get_leads(user, stages=OPEN_STAGES)
    if leads.empty:
        return leads
    due = leads[pd.to_datetime(leads["due_date"]).dt.date <= date.today()]
    return due.sort_values(["is_overdue", "conversion_probability"], ascending=[False, False])


def get_lead(user: dict, lead_id: int) -> dict:
    require_role(user, "admin", "manager", "marketer")
    lead = db.query_one(f"SELECT {LEAD_COLUMNS}, record_type FROM lead_pipeline_status WHERE lead_id = %s", (lead_id,))
    if lead is None:
        raise LookupError("Lead not found.")
    if user["role"] == "marketer" and lead["assigned_to"] != user["user_id"]:
        raise PermissionError("This lead is assigned to another marketer.")
    return lead


def get_follow_ups(user: dict, lead_id: int) -> pd.DataFrame:
    get_lead(user, lead_id)  # permission check
    return pd.DataFrame(db.query(
        "SELECT f.follow_up_date, f.follow_up_type, f.outcome, f.notes, f.next_follow_up_date, "
        "COALESCE(b.full_name, u.email) AS logged_by, f.source "
        "FROM follow_ups f JOIN users u ON u.user_id = f.user_id "
        "LEFT JOIN user_biodata b ON b.user_id = u.user_id "
        "WHERE f.lead_id = %s ORDER BY f.follow_up_date DESC, f.follow_up_id DESC", (lead_id,)))


def active_marketers() -> pd.DataFrame:
    return pd.DataFrame(db.query(
        "SELECT tm.user_id, tm.full_name, "
        "  (SELECT COUNT(*) FROM leads l WHERE l.assigned_to = tm.user_id AND l.record_type = 'pipeline' "
        "   AND l.stage IN ('New', 'Contacted', 'Qualified')) AS open_leads "
        "FROM team_members tm WHERE tm.role = 'marketer' AND tm.is_active = 1 ORDER BY tm.user_id"))


def team_performance(user: dict) -> pd.DataFrame:
    require_role(user, "admin", "manager")
    return pd.DataFrame(db.query("SELECT * FROM team_performance ORDER BY full_name"))


# ---------------------------------------------------------------- changing
def log_follow_up(user: dict, lead_id: int, follow_up_type: str, outcome: str, notes: str,
                  next_date, lost_reason=None, today: date = None) -> str:
    """FR-09: record a contact and move the lead to its new stage in one transaction."""
    require_role(user, "admin", "manager", "marketer")
    today = today or date.today()
    conn = db.connect()
    try:
        lead = db.query_one("SELECT stage, assigned_to FROM leads WHERE lead_id = %s AND record_type = 'pipeline' "
                            "FOR UPDATE", (lead_id,), conn)
        if lead is None:
            raise LookupError("Lead not found.")
        if user["role"] == "marketer" and lead["assigned_to"] != user["user_id"]:
            raise PermissionError("This lead is assigned to another marketer.")
        problems = follow_up_problems(lead["stage"], follow_up_type, outcome, next_date, lost_reason, today)
        if problems:
            raise ValueError(" ".join(problems))
        new_stage = next_stage(lead["stage"], outcome)
        closing = new_stage in CLOSED_STAGES
        db.execute(
            "INSERT INTO follow_ups (lead_id, user_id, follow_up_type, outcome, notes, next_follow_up_date, source) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'app')",
            (lead_id, user["user_id"], follow_up_type, outcome, (notes or "").strip()[:500] or None,
             None if closing else next_date), conn)
        if closing:
            db.execute("UPDATE leads SET stage = %s, converted = %s, lost_reason = %s, closed_at = NOW() "
                       "WHERE lead_id = %s",
                       (new_stage, 1 if new_stage == "Converted" else 0,
                        lost_reason if new_stage == "Lost" else None, lead_id), conn)
        else:
            db.execute("UPDATE leads SET stage = %s WHERE lead_id = %s", (new_stage, lead_id), conn)
        conn.commit()
        return new_stage
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reassign_lead(user: dict, lead_id: int, new_marketer_id: int):
    """FR-08: a manager moves an open lead to another active marketer."""
    require_role(user, "admin", "manager")
    target = db.query_one("SELECT user_id FROM team_members WHERE user_id = %s AND role = 'marketer' AND is_active = 1",
                          (new_marketer_id,))
    if target is None:
        raise ValueError("Choose an active marketer.")
    lead = db.query_one("SELECT stage FROM leads WHERE lead_id = %s AND record_type = 'pipeline'", (lead_id,))
    if lead is None:
        raise LookupError("Lead not found.")
    if lead["stage"] in CLOSED_STAGES:
        raise ValueError("Closed leads cannot be reassigned.")
    db.execute("UPDATE leads SET assigned_to = %s, assigned_at = NOW() WHERE lead_id = %s", (new_marketer_id, lead_id))


def existing_prospect_ids() -> set:
    return {r["prospect_id"] for r in db.query("SELECT prospect_id FROM leads")}


def add_leads(user: dict, batch: pd.DataFrame):
    """
    FR-07 and FR-08: validate new leads, score them immediately, assign each one to
    the marketer with the fewest open leads and save everything in one transaction.
    Returns (saved leads, rejected rows).
    """
    require_role(user, "admin", "manager", "marketer")
    reference = scoring.load_reference()
    valid, rejected = scoring.validate(batch, existing_prospect_ids(), reference)
    if valid.empty:
        return valid, rejected
    scored = scoring.score_leads(valid, reference=reference)
    marketers = active_marketers()
    if marketers.empty:
        raise ValueError("There is no active marketer to assign leads to.")
    load = dict(zip(marketers["user_id"], marketers["open_leads"]))
    names = dict(zip(marketers["user_id"], marketers["full_name"]))
    assigned = []
    conn = db.connect()
    try:
        for _, r in scored.iterrows():
            owner = min(load, key=lambda uid: (load[uid], uid))
            load[owner] += 1
            lead_id = db.execute(
                "INSERT INTO leads (prospect_id, lead_source, lead_origin, occupation, city, total_visits, "
                "time_on_website, last_activity, converted, do_not_email, do_not_call, page_views_per_visit, "
                "country, specialization, how_heard, last_notable_activity, record_type, stage, assigned_to, "
                "assigned_at, created_at, created_by) VALUES "
                "(%s, %s, %s, %s, %s, %s, %s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, 'pipeline', 'New', %s, NOW(), NOW(), %s)",
                (r["Prospect ID"], r["Lead Source"], r["Lead Origin"], r["What is your current occupation"], r["City"],
                 int(round(r["TotalVisits"])), int(round(r["Total Time Spent on Website"])), r["Last Activity"],
                 r["Do Not Email"], r["Do Not Call"], float(r["Page Views Per Visit"]), r["Country"],
                 r["Specialization"], r["How did you hear about X Education"], r["Last Notable Activity"],
                 owner, user["user_id"]), conn)
            db.execute(
                "INSERT INTO conversion_predictions (lead_id, campaign_id, user_id, conversion_probability, "
                "priority_tier, model_version, top_reasons) VALUES (%s, NULL, %s, %s, %s, %s, %s)",
                (lead_id, owner, round(float(r["conversion_probability"]), 6), r["priority_tier"],
                 scoring.MODEL_VERSION, r["top_reasons"][:500]), conn)
            assigned.append(names[owner])
        conn.commit()
    except mysql.connector.IntegrityError as e:
        conn.rollback()
        if e.errno == 1062:  # duplicate key: someone saved the same Prospect ID a moment ago
            raise ValueError("Nothing was saved: one of these Prospect IDs already exists "
                             "(it may have just been added). Refresh the page and try again.") from e
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    scored["assigned_to"] = assigned
    return scored, rejected


def pending_users(user: dict) -> pd.DataFrame:
    require_role(user, "admin", "manager")
    return pd.DataFrame(db.query(
        "SELECT u.user_id, COALESCE(b.full_name, '') AS full_name, u.email, u.role, u.is_active, u.created_at "
        "FROM users u LEFT JOIN user_biodata b ON b.user_id = u.user_id ORDER BY u.is_active, u.created_at DESC"))


def set_user_active(user: dict, target_id: int, active: bool) -> int:
    """
    Managers approve or deactivate marketers; only an admin can change a manager or admin.
    When a marketer is deactivated, their open leads are shared out automatically to the
    remaining marketers so no lead is left without an owner. Returns how many were moved.
    """
    require_role(user, "admin", "manager")
    target = db.query_one("SELECT role FROM users WHERE user_id = %s", (target_id,))
    if target is None:
        raise LookupError("User not found.")
    if target_id == user["user_id"]:
        raise ValueError("You cannot change your own account.")
    if user["role"] == "manager" and target["role"] != "marketer":
        raise PermissionError("Only an admin can change manager or admin accounts.")
    db.execute("UPDATE users SET is_active = %s WHERE user_id = %s", (1 if active else 0, target_id))
    if active or target["role"] != "marketer":
        return 0
    open_ids = [r["lead_id"] for r in db.query(
        "SELECT lead_id FROM leads WHERE assigned_to = %s AND record_type = 'pipeline' "
        "AND stage IN ('New', 'Contacted', 'Qualified')", (target_id,))]
    return redistribute(user, open_ids)


def set_user_role(user: dict, target_id: int, role: str) -> int:
    """Only an admin changes roles. A marketer moved to another role hands their open leads on."""
    require_role(user, "admin")
    if role not in ("admin", "manager", "marketer"):
        raise ValueError("Unknown role.")
    if target_id == user["user_id"]:
        raise ValueError("You cannot change your own role.")
    target = db.query_one("SELECT role FROM users WHERE user_id = %s", (target_id,))
    if target is None:
        raise LookupError("User not found.")
    db.execute("UPDATE users SET role = %s WHERE user_id = %s", (role, target_id))
    if target["role"] == "marketer" and role != "marketer":
        open_ids = [r["lead_id"] for r in db.query(
            "SELECT lead_id FROM leads WHERE assigned_to = %s AND record_type = 'pipeline' "
            "AND stage IN ('New', 'Contacted', 'Qualified')", (target_id,))]
        return redistribute(user, open_ids)
    return 0


def redistribute(user: dict, lead_ids: list, exclude_owners: bool = False) -> int:
    """Give each lead to the active marketer with the fewest open leads (optionally not its current owner)."""
    require_role(user, "admin", "manager")
    marketers = active_marketers()
    if marketers.empty or not lead_ids:
        return 0
    load = dict(zip(marketers["user_id"], marketers["open_leads"]))
    moved = 0
    for lead_id in lead_ids:
        current = db.query_one("SELECT assigned_to FROM leads WHERE lead_id = %s", (lead_id,))
        choices = {uid: n for uid, n in load.items()
                   if not (exclude_owners and current and uid == current["assigned_to"])}
        if not choices:
            continue
        owner = min(choices, key=lambda uid: (choices[uid], uid))
        reassign_lead(user, lead_id, owner)
        load[owner] += 1
        moved += 1
    return moved


def escalations(user: dict, min_days_overdue: int = 3) -> pd.DataFrame:
    """Leads that have been overdue for several days: the manager should step in."""
    require_role(user, "admin", "manager")
    return pd.DataFrame(db.query(
        f"SELECT {LEAD_COLUMNS}, DATEDIFF(CURDATE(), due_date) AS days_overdue FROM lead_pipeline_status "
        "WHERE record_type = 'pipeline' AND is_overdue = 1 AND DATEDIFF(CURDATE(), due_date) >= %s "
        "ORDER BY days_overdue DESC, conversion_probability DESC", (min_days_overdue,)))


def stage_counts(user: dict, marketer_id=None) -> pd.DataFrame:
    """Funnel: how many pipeline leads are in each stage."""
    require_role(user, "admin", "manager", "marketer")
    sql = "SELECT stage, COUNT(*) AS leads FROM leads WHERE record_type = 'pipeline'"
    params = []
    if user["role"] == "marketer":
        sql += " AND assigned_to = %s"
        params.append(user["user_id"])
    elif marketer_id:
        sql += " AND assigned_to = %s"
        params.append(marketer_id)
    rows = {r["stage"]: r["leads"] for r in db.query(sql + " GROUP BY stage", tuple(params))}
    return pd.DataFrame({"stage": STAGES, "leads": [int(rows.get(s, 0)) for s in STAGES]})


def recent_activity(user: dict, marketer_id=None, days: int = 14) -> pd.DataFrame:
    """Follow-ups logged recently: a marketer sees their own, a manager anyone's."""
    require_role(user, "admin", "manager", "marketer")
    if user["role"] == "marketer":
        marketer_id = user["user_id"]
    sql = ("SELECT f.follow_up_date, COALESCE(b.full_name, u.email) AS marketer, l.prospect_id, l.lead_id, "
           "f.follow_up_type, f.outcome, f.notes, f.next_follow_up_date, f.source "
           "FROM follow_ups f JOIN leads l ON l.lead_id = f.lead_id JOIN users u ON u.user_id = f.user_id "
           "LEFT JOIN user_biodata b ON b.user_id = u.user_id "
           "WHERE f.follow_up_date >= NOW() - INTERVAL %s DAY")
    params = [days]
    if marketer_id:
        sql += " AND f.user_id = %s"
        params.append(marketer_id)
    return pd.DataFrame(db.query(sql + " ORDER BY f.follow_up_date DESC LIMIT 500", tuple(params)))
