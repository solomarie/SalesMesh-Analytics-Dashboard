"""
Lead pipeline: stages, follow-ups, assignment, overdue rules and team performance.

FR-02 lead capture and instant scoring, FR-03 automatic assignment and the call list,
FR-04 follow-up tracking, FR-05 handover and requests, FR-06 team performance.
Every function that reads or changes data checks the user's role on the server side.
"""
from datetime import date, datetime, timedelta

import mysql.connector
import pandas as pd

from . import db, notify, scoring
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
    """FR-03: open leads that need attention today or earlier, overdue first, then by score."""
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
        "   AND l.stage IN ('New', 'Contacted', 'Qualified')) AS open_leads, "
        "  (SELECT MAX(l.assigned_at) FROM leads l WHERE l.assigned_to = tm.user_id "
        "   AND l.record_type = 'pipeline') AS last_assigned_at "
        "FROM team_members tm WHERE tm.role = 'marketer' AND tm.is_active = 1 ORDER BY tm.user_id"))


def choose_owner(load: dict, last_assigned: dict, exclude=None):
    """
    FR-03 assignment rule: the active marketer with the fewest open leads. When several are tied,
    the one who has waited longest since their last new lead goes first (fair round-robin), so the
    same person is not always picked. Returns None when nobody is available.
    """
    candidates = [uid for uid in load if uid != exclude]
    if not candidates:
        return None
    return min(candidates, key=lambda uid: (load[uid], last_assigned.get(uid) or datetime.min, uid))


def _load_tables(marketers: pd.DataFrame):
    load = {int(u): int(n) for u, n in zip(marketers["user_id"], marketers["open_leads"])}
    last = {int(u): (None if pd.isna(t) else pd.Timestamp(t).to_pydatetime())
            for u, t in zip(marketers["user_id"], marketers["last_assigned_at"])}
    return load, last


def log_event(lead_id, actor_id, event_type, note=None, from_user=None, to_user=None, conn=None):
    db.execute("INSERT INTO lead_events (lead_id, user_id, event_type, from_user, to_user, note) "
               "VALUES (%s, %s, %s, %s, %s, %s)",
               (lead_id, actor_id, event_type, from_user, to_user, (note or "").strip()[:500] or None), conn)


def team_performance(user: dict) -> pd.DataFrame:
    require_role(user, "admin", "manager")
    return pd.DataFrame(db.query("SELECT * FROM team_performance ORDER BY full_name"))


# ---------------------------------------------------------------- changing
def log_follow_up(user: dict, lead_id: int, follow_up_type: str, outcome: str, notes: str,
                  next_date, lost_reason=None, today: date = None) -> str:
    """FR-04: record a contact and move the lead to its new stage in one transaction."""
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


def reassign_lead(user: dict, lead_id: int, new_marketer_id: int, note: str = None):
    """FR-05: a manager moves an open (or lost) lead to another active marketer."""
    require_role(user, "admin", "manager")
    _move_lead(user, lead_id, new_marketer_id, "reassigned", note or "Reassigned by the manager")


def _move_lead(user, lead_id, new_owner, event_type, note, conn=None):
    """Change a lead's owner, record it on the timeline and tell the people involved."""
    target = db.query_one("SELECT user_id, full_name FROM team_members WHERE user_id = %s AND role = 'marketer' "
                          "AND is_active = 1", (new_owner,), conn)
    if target is None:
        raise ValueError("Choose an active marketer.")
    lead = db.query_one("SELECT stage, assigned_to, prospect_id FROM leads WHERE lead_id = %s "
                        "AND record_type = 'pipeline'", (lead_id,), conn)
    if lead is None:
        raise LookupError("Lead not found.")
    if lead["stage"] == "Converted":
        raise ValueError("Converted leads are finished and cannot be moved.")
    if lead["assigned_to"] == new_owner:
        raise ValueError(f"{target['full_name']} already owns this lead.")
    own = conn is None
    conn = conn or db.connect()
    try:
        db.execute("UPDATE leads SET assigned_to = %s, assigned_at = NOW() WHERE lead_id = %s",
                   (new_owner, lead_id), conn)
        log_event(lead_id, user["user_id"], event_type, note, from_user=lead["assigned_to"], to_user=new_owner,
                  conn=conn)
        db.execute("UPDATE handover_requests SET status = 'cancelled', decided_at = NOW() WHERE lead_id = %s "
                   "AND status = 'pending' AND requested_by <> %s", (lead_id, new_owner), conn)
        who = user.get("full_name") or "A manager"
        if new_owner != user["user_id"]:
            notify.send(new_owner, f"{who} gave you lead {lead['prospect_id']}" + (f": {note}" if note else "."),
                        lead_id=lead_id, conn=conn)
        if lead["assigned_to"] and lead["assigned_to"] not in (user["user_id"], new_owner):
            notify.send(lead["assigned_to"], f"Lead {lead['prospect_id']} was moved to {target['full_name']} "
                        f"by {who}.", lead_id=lead_id, conn=conn)
        if own:
            conn.commit()
    except Exception:
        if own:
            conn.rollback()
        raise
    finally:
        if own:
            conn.close()


def existing_prospect_ids() -> set:
    return {r["prospect_id"] for r in db.query("SELECT prospect_id FROM leads")}


def known(value):
    """An optional detail that was not provided is stored as NULL (unknown), never as a made-up answer."""
    return None if value == scoring.NOT_PROVIDED else value


def add_leads(user: dict, batch: pd.DataFrame):
    """
    FR-02 and FR-03: validate new leads, score them immediately, assign each one to
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
    load, last = _load_tables(marketers)
    names = {int(u): n for u, n in zip(marketers["user_id"], marketers["full_name"])}
    assigned, received = [], {}
    now = datetime.now()
    conn = db.connect()
    try:
        for i, (_, r) in enumerate(scored.iterrows()):
            owner = choose_owner(load, last)
            load[owner] += 1
            last[owner] = now + timedelta(microseconds=i)
            lead_id = db.execute(
                "INSERT INTO leads (prospect_id, lead_source, lead_origin, occupation, city, total_visits, "
                "time_on_website, last_activity, converted, do_not_email, do_not_call, page_views_per_visit, "
                "country, specialization, how_heard, last_notable_activity, record_type, stage, assigned_to, "
                "assigned_at, created_at, created_by) VALUES "
                "(%s, %s, %s, %s, %s, %s, %s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, 'pipeline', 'New', %s, NOW(), NOW(), %s)",
                (r["Prospect ID"], r["Lead Source"], r["Lead Origin"], r["What is your current occupation"], known(r["City"]),
                 int(round(r["TotalVisits"])), int(round(r["Total Time Spent on Website"])), r["Last Activity"],
                 known(r["Do Not Email"]), known(r["Do Not Call"]), float(r["Page Views Per Visit"]), known(r["Country"]),
                 known(r["Specialization"]), known(r["How did you hear about X Education"]), r["Last Notable Activity"],
                 owner, user["user_id"]), conn)
            db.execute(
                "INSERT INTO conversion_predictions (lead_id, campaign_id, user_id, conversion_probability, "
                "priority_tier, model_version, top_reasons) VALUES (%s, NULL, %s, %s, %s, %s, %s)",
                (lead_id, owner, round(float(r["conversion_probability"]), 6), r["priority_tier"],
                 scoring.MODEL_VERSION, r["top_reasons"][:500]), conn)
            log_event(lead_id, user["user_id"], "created", conn=conn)
            log_event(lead_id, None, "assigned", "Automatic: fewest open leads", to_user=owner, conn=conn)
            assigned.append(names[owner])
            received.setdefault(owner, []).append((lead_id, r["Prospect ID"], r["priority_tier"]))
        for owner, leads in received.items():
            if owner == user["user_id"]:
                continue
            if len(leads) == 1:
                lid, pid, tier = leads[0]
                notify.send(owner, f"New {tier}-priority lead {pid} was assigned to you.", lead_id=lid, conn=conn)
            else:
                notify.send(owner, f"{len(leads)} new leads were assigned to you.", conn=conn)
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
    load, last = _load_tables(marketers)
    moved = 0
    for lead_id in lead_ids:
        current = db.query_one("SELECT assigned_to FROM leads WHERE lead_id = %s", (lead_id,))
        owner_now = current["assigned_to"] if current else None
        owner = choose_owner(load, last, exclude=owner_now if exclude_owners else None)
        if owner is None or owner == owner_now:
            continue
        _move_lead(user, lead_id, owner, "reassigned", "Automatic: shared out to the least-busy marketer")
        load[owner] += 1
        last[owner] = datetime.now()
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


# ---------------------------------------------------------------- collaboration (Sprint III, after the progress review)
REOPEN_OUTCOMES = ["Interested", "Needs follow-up"]
EVENT_TEXT = {
    "created": "Lead added",
    "assigned": "Assigned to {to}",
    "reassigned": "Moved from {frm} to {to}",
    "handed_over": "Handed over from {frm} to {to}",
    "reopened": "Reopened: the customer came back",
    "requested": "{actor} asked to take over this lead",
    "request_declined": "{actor} declined a handover request",
}


def _name_map():
    return {r["user_id"]: r["full_name"] for r in db.query("SELECT user_id, full_name FROM team_members")}


def timeline(user: dict, lead_id: int) -> list:
    """FR-04: everything that happened to a lead, newest first: events and follow-ups together."""
    lead = get_lead(user, lead_id)
    names = _name_map()
    items = []
    for e in db.query("SELECT event_id, created_at, user_id, event_type, from_user, to_user, note FROM lead_events "
                      "WHERE lead_id = %s", (lead_id,)):
        text = EVENT_TEXT[e["event_type"]].format(actor=names.get(e["user_id"], "System"),
                                                  frm=names.get(e["from_user"], "nobody"),
                                                  to=names.get(e["to_user"], "nobody"))
        items.append({"when": e["created_at"], "who": names.get(e["user_id"], "System"), "what": text,
                      "note": e["note"] or "", "kind": "event", "seq": (1, e["event_id"])})
    for f in db.query("SELECT follow_up_id, follow_up_date, user_id, follow_up_type, outcome, notes, next_follow_up_date, source "
                      "FROM follow_ups WHERE lead_id = %s", (lead_id,)):
        nxt = f" · next {f['next_follow_up_date']:%d %b}" if f["next_follow_up_date"] else ""
        items.append({"when": f["follow_up_date"], "who": names.get(f["user_id"], "?"),
                      "what": f"{f['follow_up_type']}: {f['outcome']}{nxt}",
                      "note": (f["notes"] or "") + (" (simulated)" if f["source"] == "simulated" else ""),
                      "kind": "follow_up", "seq": (0, f["follow_up_id"])})
    if not any(i["what"] == "Lead added" for i in items) and lead["created_at"]:
        items.append({"when": lead["created_at"], "who": "", "what": "Lead added", "note": "", "kind": "event",
                      "seq": (-1, 0)})
    return sorted(items, key=lambda i: (i["when"], i["seq"]), reverse=True)


def previous_handlers(lead_id: int) -> list:
    """Marketers who have spoken to this lead before, most recent first (for 'ask for the same person')."""
    rows = db.query("SELECT f.user_id, tm.full_name, tm.is_active, MAX(f.follow_up_date) AS last_contact "
                    "FROM follow_ups f JOIN team_members tm ON tm.user_id = f.user_id "
                    "WHERE f.lead_id = %s AND tm.role = 'marketer' GROUP BY f.user_id, tm.full_name, tm.is_active "
                    "ORDER BY last_contact DESC", (lead_id,))
    return rows


def find_leads(user: dict, text: str) -> pd.DataFrame:
    """
    Any signed-in user can look up a lead by part of its Prospect ID to see who owns it, for example
    when a customer phones the wrong person. Only routing details are shown, not the lead's data.
    """
    require_role(user, "admin", "manager", "marketer")
    text = (text or "").strip()
    if len(text) < 3:
        raise ValueError("Type at least 3 characters of the Prospect ID.")
    return pd.DataFrame(db.query(
        "SELECT lead_id, prospect_id, stage, priority_tier, assigned_to, assigned_name, last_follow_up_date "
        "FROM lead_pipeline_status WHERE record_type = 'pipeline' AND prospect_id LIKE %s "
        "ORDER BY prospect_id LIMIT 25", (f"%{text}%",)))


def handover_lead(user: dict, lead_id: int, to_user: int, reason: str):
    """FR-05: the owner (or a manager) hands a lead to a named colleague, with a reason."""
    require_role(user, "admin", "manager", "marketer")
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise ValueError("Give a short reason for the handover, for example 'Customer asked for Jane'.")
    lead = db.query_one("SELECT assigned_to FROM leads WHERE lead_id = %s AND record_type = 'pipeline'", (lead_id,))
    if lead is None:
        raise LookupError("Lead not found.")
    if user["role"] == "marketer" and lead["assigned_to"] != user["user_id"]:
        raise PermissionError("Only the lead's owner or a manager can hand it over. You can ask for it instead.")
    _move_lead(user, lead_id, to_user, "handed_over", reason)


def request_lead(user: dict, lead_id: int, reason: str) -> int:
    """FR-05: a marketer asks to take over someone else's lead; the owner accepts or declines."""
    require_role(user, "marketer")
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise ValueError("Say why you need this lead, for example 'Customer called me back'.")
    lead = db.query_one("SELECT l.assigned_to, l.stage, l.prospect_id, tm.is_active AS owner_active "
                        "FROM leads l LEFT JOIN team_members tm ON tm.user_id = l.assigned_to "
                        "WHERE l.lead_id = %s AND l.record_type = 'pipeline'", (lead_id,))
    if lead is None:
        raise LookupError("Lead not found.")
    if lead["assigned_to"] == user["user_id"]:
        raise ValueError("This lead is already yours.")
    if lead["stage"] == "Converted":
        raise ValueError("This lead has already converted.")
    if db.query_one("SELECT request_id FROM handover_requests WHERE lead_id = %s AND requested_by = %s "
                    "AND status = 'pending'", (lead_id, user["user_id"])):
        raise ValueError("You have already asked for this lead. Wait for an answer.")
    conn = db.connect()
    try:
        request_id = db.execute("INSERT INTO handover_requests (lead_id, requested_by, owner_id, reason) "
                                "VALUES (%s, %s, %s, %s)", (lead_id, user["user_id"], lead["assigned_to"],
                                                            reason[:300]), conn)
        log_event(lead_id, user["user_id"], "requested", reason, from_user=lead["assigned_to"],
                  to_user=user["user_id"], conn=conn)
        message = f"{user['full_name']} asked for lead {lead['prospect_id']}: {reason}"
        if lead["assigned_to"] and lead["owner_active"]:
            notify.send(lead["assigned_to"], message, lead_id=lead_id, request_id=request_id, conn=conn)
        else:  # nobody active owns it: the managers decide
            managers = [r["user_id"] for r in db.query("SELECT user_id FROM users WHERE role IN ('manager', 'admin') "
                                                      "AND is_active = 1", conn=conn)]
            notify.send_many(managers, message, lead_id=lead_id, request_id=request_id, conn=conn)
        conn.commit()
        return request_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def requests_to_decide(user: dict) -> pd.DataFrame:
    """Pending requests this user can answer: for their own leads, or all of them for a manager."""
    sql = ("SELECT r.request_id, r.lead_id, l.prospect_id, l.stage, r.reason, r.created_at, "
           "rq.full_name AS requested_by_name, ow.full_name AS owner_name "
           "FROM handover_requests r JOIN leads l ON l.lead_id = r.lead_id "
           "JOIN team_members rq ON rq.user_id = r.requested_by LEFT JOIN team_members ow ON ow.user_id = r.owner_id "
           "WHERE r.status = 'pending'")
    params = ()
    if user["role"] == "marketer":
        sql += " AND l.assigned_to = %s"
        params = (user["user_id"],)
    return pd.DataFrame(db.query(sql + " ORDER BY r.created_at", params))


def decide_request(user: dict, request_id: int, accept: bool, note: str = "") -> str:
    require_role(user, "admin", "manager", "marketer")
    req = db.query_one("SELECT r.request_id, r.lead_id, r.requested_by, r.reason, r.status, l.assigned_to, "
                       "l.prospect_id FROM handover_requests r JOIN leads l ON l.lead_id = r.lead_id "
                       "WHERE r.request_id = %s", (request_id,))
    if req is None:
        raise LookupError("Request not found.")
    if req["status"] != "pending":
        raise ValueError("This request has already been answered.")
    if user["role"] == "marketer" and req["assigned_to"] != user["user_id"]:
        raise PermissionError("Only the lead's owner or a manager can answer this request.")
    note = (note or "").strip()[:300]
    conn = db.connect()
    try:
        if accept:
            _move_lead(user, req["lead_id"], req["requested_by"], "handed_over",
                       f"Request accepted: {req['reason']}", conn=conn)
            status, text = "accepted", f"{user['full_name']} accepted your request: lead {req['prospect_id']} is now yours."
        else:
            log_event(req["lead_id"], user["user_id"], "request_declined", note, conn=conn)
            status = "declined"
            text = f"{user['full_name']} declined your request for lead {req['prospect_id']}" + (f": {note}" if note else ".")
            notify.send(req["requested_by"], text, lead_id=req["lead_id"], conn=conn)
        db.execute("UPDATE handover_requests SET status = %s, decided_by = %s, decided_at = NOW(), decision_note = %s "
                   "WHERE request_id = %s", (status, user["user_id"], note or None, request_id), conn)
        db.execute("UPDATE notifications SET read_at = NOW() WHERE request_id = %s AND read_at IS NULL",
                   (request_id,), conn)
        conn.commit()
        return status
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reopen_lead(user: dict, lead_id: int, follow_up_type: str, outcome: str, notes: str, next_date,
                today: date = None) -> str:
    """
    FR-04: a Lost lead whose customer comes back is reopened and the new contact is recorded.
    It stays with (or goes back to) the marketer who handled it before, if they are still active.
    """
    require_role(user, "admin", "manager", "marketer")
    today = today or date.today()
    if outcome not in REOPEN_OUTCOMES:
        raise ValueError("Choose 'Interested' or 'Needs follow-up' for the customer's return.")
    if follow_up_type not in FOLLOW_UP_TYPES:
        raise ValueError("Choose how the customer got in touch.")
    if next_date is None or next_date < today:
        raise ValueError("Set the next follow-up date (today or later).")
    conn = db.connect()
    try:
        lead = db.query_one("SELECT stage, assigned_to, prospect_id FROM leads WHERE lead_id = %s "
                            "AND record_type = 'pipeline' FOR UPDATE", (lead_id,), conn)
        if lead is None:
            raise LookupError("Lead not found.")
        owner_row = db.query_one("SELECT is_active FROM users WHERE user_id = %s", (lead["assigned_to"],), conn) \
            if lead["assigned_to"] else None
        lead["owner_active"] = bool(owner_row and owner_row["is_active"])
        if lead["stage"] != "Lost":
            raise ValueError("Only a Lost lead can be reopened.")
        if user["role"] == "marketer" and lead["assigned_to"] != user["user_id"]:
            raise PermissionError("This lead belongs to another marketer. Ask for it first.")
        owner = lead["assigned_to"]
        if not owner or not lead["owner_active"]:  # previous owner has left: share it out fairly
            load, last = _load_tables(active_marketers())
            owner = user["user_id"] if user["role"] == "marketer" else choose_owner(load, last)
            if owner is None:
                raise ValueError("There is no active marketer to give this lead to.")
        new_stage = next_stage("Contacted", outcome)
        db.execute("UPDATE leads SET stage = %s, converted = NULL, lost_reason = NULL, closed_at = NULL, "
                   "assigned_at = IF(assigned_to <=> %s, assigned_at, NOW()), assigned_to = %s WHERE lead_id = %s",
                   (new_stage, owner, owner, lead_id), conn)
        log_event(lead_id, user["user_id"], "reopened", notes, from_user=lead["assigned_to"], to_user=owner, conn=conn)
        db.execute("INSERT INTO follow_ups (lead_id, user_id, follow_up_type, outcome, notes, next_follow_up_date, "
                   "source) VALUES (%s, %s, %s, %s, %s, %s, 'app')",
                   (lead_id, user["user_id"], follow_up_type, outcome, (notes or "").strip()[:500] or None, next_date),
                   conn)
        if owner != user["user_id"]:
            notify.send(owner, f"Lead {lead['prospect_id']} came back and was reopened for you by "
                        f"{user['full_name']}.", lead_id=lead_id, conn=conn)
        conn.commit()
        return new_stage
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
