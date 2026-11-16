"""
One-time upgrade of the salesmesh database for lead management (Sprint III).

Run from the app folder:   python setup_database.py
It asks for the MySQL root password (never stored) and reads app/.env for the
app account password and the demo password.

What it does, in order (each step is safe to run again):
 1. Backs up users, user_biodata, leads and conversion_predictions.
 2. Adds roles, account status and login lock-out columns to users.
 3. Adds the lead pipeline columns to leads (all 14 model inputs, record type,
    stage, owner, dates) and a follow_ups table, plus indexes.
 4. Removes the six NEW- demo leads (copies of existing leads).
 5. Fills the six extra model inputs for every lead from the cleaned data.
 6. Marks the 9,240 Kaggle leads as 'historical' (used for training and
    evaluation, owned by no marketer).
 7. Creates demo accounts (Admin, Marketing Manager, 4 Marketers) with bcrypt hashes.
 8. Builds a working pipeline of 300 leads from the held-out TEST set (leads the
    model never trained on): re-scores them with the saved model, assigns them,
    and adds a simulated follow-up history clearly marked source = 'simulated'.
 9. Creates the views used by the app and Power BI, and the app's own
    least-privilege MySQL account.
"""
import getpass
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

import mysql.connector
import pandas as pd

from salesmesh import config, scoring
from salesmesh.auth import hash_password
from salesmesh.pipeline import FIRST_CONTACT_DAYS, LOST_REASONS, next_stage

APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent
REFERENCE_CSV = PROJECT_DIR / "predictions" / "leads_with_predictions.csv"
TEST_IDS_CSV = APP_DIR / "model" / "test_set_prospect_ids.csv"
PIPELINE_SIZE = 300
SEED = 42
DEMO_USERS = [  # email, role, full name, phone, job title, how diligent their simulated follow-up is (0-1)
    ("admin@salesmesh.com", "admin", "System Administrator", "0700000001", "System Admin", None),
    ("manager@salesmesh.com", "manager", "Grace Njeri", "0700000002", "Marketing Manager", None),
    ("jane@salesmesh.com", "marketer", "Jane Wanjiru", "0700000003", "Marketer", 0.95),
    ("brian@salesmesh.com", "marketer", "Brian Otieno", "0700000004", "Marketer", 0.80),
    ("aisha@salesmesh.com", "marketer", "Aisha Mohamed", "0700000005", "Marketer", 0.65),
    ("kevin@salesmesh.com", "marketer", "Kevin Mutua", "0700000006", "Marketer", 0.45),
]


def step(msg):
    print(f"\n== {msg}")


def column_exists(cur, table, column):
    cur.execute("SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() "
                "AND table_name = %s AND column_name = %s", (table, column))
    return cur.fetchone()[0] > 0


def index_exists(cur, table, index):
    cur.execute("SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema = DATABASE() "
                "AND table_name = %s AND index_name = %s", (table, index))
    return cur.fetchone()[0] > 0


def table_exists(cur, table):
    cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = %s",
                (table,))
    return cur.fetchone()[0] > 0


def constraint_exists(cur, name):
    cur.execute("SELECT COUNT(*) FROM information_schema.table_constraints WHERE constraint_schema = DATABASE() "
                "AND constraint_name = %s", (name,))
    return cur.fetchone()[0] > 0


def add_columns(cur, table, columns):
    for name, definition in columns:
        if not column_exists(cur, table, name):
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
            print(f"   added {table}.{name}")


def main():
    if not config.DB_PASSWORD or "REPLACE" in config.DB_PASSWORD:
        sys.exit("Set DB_PASSWORD in app/.env first (see .env.example).")
    demo_password = __import__("os").getenv("DEMO_PASSWORD", "")
    if len(demo_password) < 8 or "REPLACE" in demo_password:
        sys.exit("Set DEMO_PASSWORD (at least 8 characters) in app/.env first.")
    for f in (REFERENCE_CSV, TEST_IDS_CSV, APP_DIR / "model" / "salesmesh_model.pkl"):
        if not f.exists():
            sys.exit(f"Missing file: {f}")

    root_password = getpass.getpass("MySQL root password (typing is hidden, press Enter when done): ")
    try:
        conn = mysql.connector.connect(host=config.DB_HOST, port=config.DB_PORT, user="root",
                                       password=root_password, database=config.DB_NAME, autocommit=False)
    except mysql.connector.Error as e:
        sys.exit(f"Could not connect to MySQL as root: {e.msg}\n"
                 "Check that MySQL is running and that you typed the root password correctly.")
    cur = conn.cursor()

    step("1. Backups")
    for t in ("users", "user_biodata", "leads", "conversion_predictions"):
        if not table_exists(cur, f"{t}_bak_sprint3"):
            cur.execute(f"CREATE TABLE {t}_bak_sprint3 AS SELECT * FROM {t}")
            print(f"   {t}_bak_sprint3 created")
        else:
            print(f"   {t}_bak_sprint3 already exists (kept)")

    step("2. Users: roles, account status, lock-out")
    add_columns(cur, "users", [
        ("is_active", "TINYINT NOT NULL DEFAULT 1"),
        ("failed_logins", "INT NOT NULL DEFAULT 0"),
        ("locked_until", "DATETIME NULL"),
    ])
    cur.execute("UPDATE users SET role = 'admin' WHERE email = 'admin@salesmesh.com'")
    cur.execute("UPDATE users SET role = 'manager', email = 'manager@salesmesh.com' WHERE email = 'user@salesmesh.com'")
    cur.execute("ALTER TABLE users ALTER role SET DEFAULT 'marketer'")
    if not constraint_exists(cur, "chk_users_role"):
        cur.execute("ALTER TABLE users ADD CONSTRAINT chk_users_role CHECK (role IN ('admin', 'manager', 'marketer'))")
    conn.commit()

    step("3. Leads pipeline columns, follow_ups table, indexes")
    add_columns(cur, "leads", [
        ("do_not_email", "VARCHAR(5) NULL"),
        ("do_not_call", "VARCHAR(5) NULL"),
        ("page_views_per_visit", "DECIMAL(6,2) NULL"),
        ("country", "VARCHAR(100) NULL"),
        ("specialization", "VARCHAR(100) NULL"),
        ("how_heard", "VARCHAR(100) NULL"),
        ("last_notable_activity", "VARCHAR(100) NULL"),
        ("record_type", "VARCHAR(12) NOT NULL DEFAULT 'pipeline'"),
        ("stage", "VARCHAR(12) NOT NULL DEFAULT 'New'"),
        ("lost_reason", "VARCHAR(100) NULL"),
        ("assigned_to", "INT NULL"),
        ("assigned_at", "DATETIME NULL"),
        ("created_at", "DATETIME NULL"),
        ("closed_at", "DATETIME NULL"),
        ("created_by", "INT NULL"),
    ])
    for name, sql in [
        ("chk_leads_record_type", "CHECK (record_type IN ('historical', 'pipeline'))"),
        ("chk_leads_stage", "CHECK (stage IN ('New', 'Contacted', 'Qualified', 'Converted', 'Lost'))"),
        ("fk_leads_assigned_to", "FOREIGN KEY (assigned_to) REFERENCES users(user_id)"),
        ("fk_leads_created_by", "FOREIGN KEY (created_by) REFERENCES users(user_id)"),
    ]:
        if not constraint_exists(cur, name):
            cur.execute(f"ALTER TABLE leads ADD CONSTRAINT {name} {sql}")
    add_columns(cur, "conversion_predictions", [
        ("model_version", "VARCHAR(20) NULL"),
        ("top_reasons", "VARCHAR(500) NULL"),
    ])
    cur.execute("""
        CREATE TABLE IF NOT EXISTS follow_ups (
            follow_up_id         INT AUTO_INCREMENT PRIMARY KEY,
            lead_id              INT NOT NULL,
            user_id              INT NOT NULL,
            follow_up_type       VARCHAR(10) NOT NULL,
            outcome              VARCHAR(20) NOT NULL,
            notes                VARCHAR(500) NULL,
            follow_up_date       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            next_follow_up_date  DATE NULL,
            source               VARCHAR(10) NOT NULL DEFAULT 'app',
            CONSTRAINT fk_follow_ups_lead FOREIGN KEY (lead_id) REFERENCES leads(lead_id),
            CONSTRAINT fk_follow_ups_user FOREIGN KEY (user_id) REFERENCES users(user_id),
            CONSTRAINT chk_follow_up_type CHECK (follow_up_type IN ('Call', 'Email', 'Meeting', 'SMS')),
            CONSTRAINT chk_follow_up_outcome CHECK (outcome IN ('No answer', 'Needs follow-up', 'Interested', 'Converted', 'Lost')),
            CONSTRAINT chk_follow_up_source CHECK (source IN ('app', 'simulated')),
            CONSTRAINT chk_follow_up_next_date CHECK (next_follow_up_date IS NULL OR next_follow_up_date >= DATE(follow_up_date))
        )""")
    # Forgot-password links: only a SHA-256 hash of each one-time token is stored
    cur.execute("""
        CREATE TABLE IF NOT EXISTS password_resets (
            reset_id    INT AUTO_INCREMENT PRIMARY KEY,
            user_id     INT NOT NULL,
            token_hash  CHAR(64) NOT NULL UNIQUE,
            created_at  DATETIME NOT NULL,
            expires_at  DATETIME NOT NULL,
            used_at     DATETIME NULL,
            CONSTRAINT fk_password_resets_user FOREIGN KEY (user_id) REFERENCES users(user_id)
        )""")
    create_collaboration_tables(cur)
    for table, name, cols in [
        ("leads", "idx_leads_owner_stage", "assigned_to, stage"),
        ("leads", "idx_leads_record_type", "record_type"),
        ("leads", "idx_leads_prospect", "prospect_id"),
        ("follow_ups", "idx_follow_ups_lead", "lead_id"),
        ("follow_ups", "idx_follow_ups_next", "next_follow_up_date"),
        ("conversion_predictions", "idx_predictions_lead", "lead_id"),
    ]:
        if not index_exists(cur, table, name):
            cur.execute(f"CREATE INDEX {name} ON {table} ({cols})")
    conn.commit()

    step("3b. Prospect ID must be unique (the database refuses duplicates)")
    cur.execute("SET SQL_SAFE_UPDATES = 0")
    cur.execute("SELECT prospect_id FROM leads GROUP BY prospect_id HAVING COUNT(*) > 1")
    duplicates = [r[0] for r in cur.fetchall()]
    for pid in duplicates:
        # keep the copy with the most follow-up history (the earliest one if they are equal)
        cur.execute("SELECT l.lead_id FROM leads l LEFT JOIN follow_ups f ON f.lead_id = l.lead_id "
                    "WHERE l.prospect_id = %s GROUP BY l.lead_id ORDER BY COUNT(f.follow_up_id) DESC, l.lead_id "
                    "LIMIT 1", (pid,))
        keep = cur.fetchone()[0]
        cur.execute("DELETE f FROM follow_ups f JOIN leads l ON l.lead_id = f.lead_id "
                    "WHERE l.prospect_id = %s AND l.lead_id <> %s", (pid, keep))
        cur.execute("DELETE cp FROM conversion_predictions cp JOIN leads l ON l.lead_id = cp.lead_id "
                    "WHERE l.prospect_id = %s AND l.lead_id <> %s", (pid, keep))
        cur.execute("DELETE FROM leads WHERE prospect_id = %s AND lead_id <> %s", (pid, keep))
        print(f"   removed {cur.rowcount} duplicate(s) of '{pid}' (kept the copy with the most follow-ups)")
    if not index_exists(cur, "leads", "uq_leads_prospect"):
        cur.execute("ALTER TABLE leads ADD CONSTRAINT uq_leads_prospect UNIQUE (prospect_id)")
        print("   UNIQUE rule added on leads.prospect_id")
    conn.commit()

    step("4. Remove the six NEW- demo leads")
    cur.execute("SET SQL_SAFE_UPDATES = 0")
    # Only the six Sprint 2 demo leads: they were never assigned. Leads added in the app always have an
    # owner, so a lead someone added with a NEW- ID is never deleted when this script is run again.
    demo = "l.prospect_id LIKE 'NEW-%' AND l.assigned_to IS NULL AND l.prospect_id REGEXP '^NEW-000[1-6]$'"
    cur.execute(f"DELETE f FROM follow_ups f JOIN leads l ON l.lead_id = f.lead_id WHERE {demo}")
    cur.execute(f"DELETE cp FROM conversion_predictions cp JOIN leads l ON cp.lead_id = l.lead_id WHERE {demo}")
    cur.execute(f"DELETE l FROM leads l WHERE {demo}")
    print(f"   removed {cur.rowcount} demo lead(s)")
    conn.commit()

    step("5. Fill the extra model inputs for every lead")
    ref = pd.read_csv(REFERENCE_CSV)
    rows = [(r["Do Not Email"], r["Do Not Call"], float(r["Page Views Per Visit"]), r["Country"],
             r["Specialization"], r["How did you hear about X Education"], r["Last Notable Activity"],
             r["prospect_id"]) for _, r in ref.iterrows()]
    cur.executemany("UPDATE leads SET do_not_email = %s, do_not_call = %s, page_views_per_visit = %s, country = %s, "
                    "specialization = %s, how_heard = %s, last_notable_activity = %s WHERE prospect_id = %s", rows)
    conn.commit()
    print(f"   {len(rows)} leads updated")

    step("6. Mark the Kaggle leads as historical")
    cur.execute("UPDATE leads SET record_type = 'historical', assigned_to = NULL, assigned_at = NULL, "
                "stage = CASE WHEN converted = 1 THEN 'Converted' ELSE 'Lost' END "
                "WHERE prospect_id NOT LIKE 'NEW-%' AND converted IS NOT NULL AND record_type <> 'historical' "
                "AND assigned_to IS NULL")
    cur.execute("UPDATE conversion_predictions SET model_version = 'gb-v1-oof' WHERE model_version IS NULL")
    conn.commit()

    step("7. Demo accounts (bcrypt)")
    cur.execute("SELECT COUNT(*) FROM leads WHERE record_type = 'pipeline'")
    updating = cur.fetchone()[0] > 0  # the app is already set up: leave people's accounts as they are
    ids = {}
    for email, role, name, phone, title, _ in DEMO_USERS:
        cur.execute("SELECT user_id, password_hash FROM users WHERE email = %s", (email,))
        found = cur.fetchone()
        if updating:
            # Never re-create a demo account someone removed or renamed, and never undo a role change,
            # a deactivation or a password change.
            if found and not str(found[1]).startswith("$2"):
                cur.execute("UPDATE users SET password_hash = %s WHERE user_id = %s",
                            (hash_password(demo_password), found[0]))
            if found:
                ids[email] = found[0]
            continue
        if found:
            uid = found[0]
            # Only replace old Sprint 2 SHA-256 hashes. A bcrypt password someone already set is kept,
            # so running this script again never undoes a password change.
            if not str(found[1]).startswith("$2"):
                cur.execute("UPDATE users SET password_hash = %s WHERE user_id = %s",
                            (hash_password(demo_password), uid))
            cur.execute("UPDATE users SET role = %s, is_active = 1 WHERE user_id = %s", (role, uid))
        else:
            cur.execute("INSERT INTO users (email, password_hash, role, is_active) VALUES (%s, %s, %s, 1)",
                        (email, hash_password(demo_password), role))
            uid = cur.lastrowid
        cur.execute("SELECT biodata_id FROM user_biodata WHERE user_id = %s", (uid,))
        if cur.fetchone():
            cur.execute("UPDATE user_biodata SET full_name = %s, phone = %s, department = 'Marketing', job_title = %s "
                        "WHERE user_id = %s", (name, phone, title, uid))
        else:
            cur.execute("INSERT INTO user_biodata (user_id, full_name, phone, department, job_title) "
                        "VALUES (%s, %s, %s, 'Marketing', %s)", (uid, name, phone, title))
        ids[email] = uid
        print(f"   {role:<8} {email}")
    if updating:
        print("   accounts kept as they are (update run)")
    conn.commit()

    step("8. Working pipeline from the held-out test set")
    cur.execute("SELECT COUNT(*) FROM leads WHERE record_type = 'pipeline'")
    if cur.fetchone()[0] > 0:
        print("   pipeline already exists (kept)")
    else:
        build_pipeline(conn, cur, ref, ids)

    step("9. Views and database accounts")
    create_views(cur)
    cur.execute("CREATE USER IF NOT EXISTS 'salesmesh_app'@'localhost' IDENTIFIED BY %s", (config.DB_PASSWORD,))
    cur.execute("ALTER USER 'salesmesh_app'@'localhost' IDENTIFIED BY %s", (config.DB_PASSWORD,))
    for t in ("leads", "conversion_predictions", "follow_ups", "users", "user_biodata", "password_resets",
              "invitations", "lead_events", "handover_requests", "notifications"):
        cur.execute(f"GRANT SELECT, INSERT, UPDATE ON salesmesh.{t} TO 'salesmesh_app'@'localhost'")
    for v in ("team_members", "lead_pipeline_status", "team_performance"):
        cur.execute(f"GRANT SELECT ON salesmesh.{v} TO 'salesmesh_app'@'localhost'")
    cur.execute("SET SQL_SAFE_UPDATES = 1")
    conn.commit()

    step("Summary")
    for sql, label in [
        ("SELECT role, COUNT(*) FROM users GROUP BY role", "users by role"),
        ("SELECT record_type, stage, COUNT(*) FROM leads GROUP BY record_type, stage ORDER BY 1, 2", "leads"),
        ("SELECT full_name, open_leads, overdue, converted, lost FROM team_performance ORDER BY full_name", "team"),
        ("SELECT source, COUNT(*) FROM follow_ups GROUP BY source", "follow-ups"),
    ]:
        cur.execute(sql)
        print(f"   {label}: {cur.fetchall()}")
    cur.close()
    conn.close()
    print("\nDone. Demo accounts use the DEMO_PASSWORD from app/.env.")


def build_pipeline(conn, cur, ref, ids):
    rng = random.Random(SEED)
    test_ids = set(pd.read_csv(TEST_IDS_CSV)["prospect_id"])
    pool = ref[ref["prospect_id"].isin(test_ids)]
    chosen = pool.sample(n=PIPELINE_SIZE, random_state=SEED).reset_index(drop=True)
    leads = chosen.rename(columns={"prospect_id": "Prospect ID"})
    scored = scoring.score_leads(leads)
    marketers = [(ids[e], d) for e, r, *_, d in DEMO_USERS if r == "marketer"]
    now = datetime.now().replace(microsecond=0)
    today = now.date()
    stats = {"Converted": 0, "Lost": 0, "open": 0, "follow_ups": 0}
    for i, r in scored.iterrows():
        owner, diligence = marketers[i % len(marketers)]
        created = now - timedelta(days=rng.uniform(0, 21), hours=rng.uniform(0, 8))
        truth = int(r["Converted"])
        stage = "New"
        history = []
        # First contact: a diligent marketer usually calls within the tier's deadline, others often late.
        sla = FIRST_CONTACT_DAYS[r["priority_tier"]]
        on_time = rng.random() < diligence
        delay = rng.uniform(0.1, sla) if on_time else rng.uniform(sla + 0.5, sla + 8)
        when = created + timedelta(days=delay)
        contact = 0
        while when <= now and contact < 6:
            close_chance = 0 if contact == 0 else min(0.2 + 0.15 * contact, 0.8)
            if rng.random() < close_chance:
                outcome = "Converted" if truth == 1 else "Lost"   # the real result from the dataset
            elif r["conversion_probability"] >= 0.55 and rng.random() < 0.6:
                outcome = "Interested"
            else:
                outcome = rng.choice(["No answer", "Needs follow-up"])
            stage = next_stage(stage, outcome)
            next_date = None
            if stage not in ("Converted", "Lost"):
                next_date = (when + timedelta(days=rng.randint(1, 5))).date()
            history.append((when, outcome, next_date))
            if next_date is None:
                break
            # The next contact happens on the agreed date if the marketer is diligent, otherwise days late.
            late = 0 if rng.random() < diligence else rng.uniform(1, 7)
            when = datetime.combine(next_date, datetime.min.time()) + timedelta(hours=rng.uniform(8, 17), days=late)
            contact += 1
        closed_at = history[-1][0] if stage in ("Converted", "Lost") else None
        cur.execute("UPDATE leads SET record_type = 'pipeline', stage = %s, converted = %s, lost_reason = %s, "
                    "assigned_to = %s, assigned_at = %s, created_at = %s, closed_at = %s, created_by = %s "
                    "WHERE prospect_id = %s",
                    (stage, 1 if stage == "Converted" else (0 if stage == "Lost" else None),
                     rng.choice(LOST_REASONS) if stage == "Lost" else None,
                     owner, created, created, closed_at, ids["manager@salesmesh.com"], r["Prospect ID"]))
        cur.execute("UPDATE conversion_predictions cp JOIN leads l ON l.lead_id = cp.lead_id "
                    "SET cp.conversion_probability = %s, cp.priority_tier = %s, cp.model_version = %s, "
                    "cp.top_reasons = %s, cp.user_id = %s, cp.predicted_at = %s WHERE l.prospect_id = %s",
                    (round(float(r["conversion_probability"]), 6), r["priority_tier"], scoring.MODEL_VERSION,
                     r["top_reasons"][:500], owner, created, r["Prospect ID"]))
        for when, outcome, next_date in history:
            cur.execute("INSERT INTO follow_ups (lead_id, user_id, follow_up_type, outcome, notes, follow_up_date, "
                        "next_follow_up_date, source) SELECT lead_id, %s, %s, %s, %s, %s, %s, 'simulated' "
                        "FROM leads WHERE prospect_id = %s",
                        (owner, rng.choice(["Call", "Call", "Email", "SMS", "Meeting"]), outcome,
                         "Simulated follow-up for demonstration", when, next_date, r["Prospect ID"]))
            stats["follow_ups"] += 1
        stats[stage if stage in ("Converted", "Lost") else "open"] += 1
    conn.commit()
    print(f"   {PIPELINE_SIZE} pipeline leads: {stats}")


def create_collaboration_tables(cur):
    """Sprint III (after the progress review): invitations, lead activity timeline, handover requests and notifications."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS invitations (
            invite_id    INT AUTO_INCREMENT PRIMARY KEY,
            email        VARCHAR(100) NOT NULL,
            full_name    VARCHAR(100) NULL,
            role         VARCHAR(10) NOT NULL DEFAULT 'marketer',
            token_hash   CHAR(64) NOT NULL UNIQUE,
            invited_by   INT NOT NULL,
            created_at   DATETIME NOT NULL,
            expires_at   DATETIME NOT NULL,
            accepted_at  DATETIME NULL,
            revoked_at   DATETIME NULL,
            CONSTRAINT fk_invitations_by FOREIGN KEY (invited_by) REFERENCES users(user_id),
            CONSTRAINT chk_invitations_role CHECK (role IN ('admin', 'manager', 'marketer')),
            INDEX idx_invitations_email (email)
        )""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS lead_events (
            event_id    INT AUTO_INCREMENT PRIMARY KEY,
            lead_id     INT NOT NULL,
            user_id     INT NULL,
            event_type  VARCHAR(20) NOT NULL,
            from_user   INT NULL,
            to_user     INT NULL,
            note        VARCHAR(500) NULL,
            created_at  DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            CONSTRAINT fk_lead_events_lead FOREIGN KEY (lead_id) REFERENCES leads(lead_id),
            CONSTRAINT fk_lead_events_user FOREIGN KEY (user_id) REFERENCES users(user_id),
            CONSTRAINT fk_lead_events_from FOREIGN KEY (from_user) REFERENCES users(user_id),
            CONSTRAINT fk_lead_events_to FOREIGN KEY (to_user) REFERENCES users(user_id),
            CONSTRAINT chk_lead_events_type CHECK (event_type IN ('created', 'assigned', 'reassigned',
                'handed_over', 'reopened', 'requested', 'request_declined')),
            INDEX idx_lead_events_lead (lead_id)
        )""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS handover_requests (
            request_id    INT AUTO_INCREMENT PRIMARY KEY,
            lead_id       INT NOT NULL,
            requested_by  INT NOT NULL,
            owner_id      INT NULL,
            reason        VARCHAR(300) NOT NULL,
            status        VARCHAR(10) NOT NULL DEFAULT 'pending',
            created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            decided_by    INT NULL,
            decided_at    DATETIME NULL,
            decision_note VARCHAR(300) NULL,
            CONSTRAINT fk_requests_lead FOREIGN KEY (lead_id) REFERENCES leads(lead_id),
            CONSTRAINT fk_requests_by FOREIGN KEY (requested_by) REFERENCES users(user_id),
            CONSTRAINT fk_requests_owner FOREIGN KEY (owner_id) REFERENCES users(user_id),
            CONSTRAINT fk_requests_decided FOREIGN KEY (decided_by) REFERENCES users(user_id),
            CONSTRAINT chk_requests_status CHECK (status IN ('pending', 'accepted', 'declined', 'cancelled')),
            INDEX idx_requests_lead (lead_id, status)
        )""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            notification_id INT AUTO_INCREMENT PRIMARY KEY,
            user_id         INT NOT NULL,
            message         VARCHAR(300) NOT NULL,
            lead_id         INT NULL,
            request_id      INT NULL,
            created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            read_at         DATETIME NULL,
            CONSTRAINT fk_notifications_user FOREIGN KEY (user_id) REFERENCES users(user_id),
            CONSTRAINT fk_notifications_lead FOREIGN KEY (lead_id) REFERENCES leads(lead_id),
            CONSTRAINT fk_notifications_request FOREIGN KEY (request_id) REFERENCES handover_requests(request_id),
            INDEX idx_notifications_user (user_id, read_at)
        )""")
    print("   invitations, lead_events, handover_requests, notifications ready")


def create_views(cur):
    cur.execute("""
        CREATE OR REPLACE VIEW team_members AS
        SELECT u.user_id, COALESCE(b.full_name, u.email) AS full_name, u.role, u.is_active
        FROM users u LEFT JOIN user_biodata b ON b.user_id = u.user_id""")
    sla = " ".join(f"WHEN '{t}' THEN {d}" for t, d in FIRST_CONTACT_DAYS.items())
    cur.execute(f"""
        CREATE OR REPLACE VIEW lead_pipeline_status AS
        SELECT x.*, (x.due_date IS NOT NULL AND x.due_date < CURDATE()) AS is_overdue
        FROM (
            SELECT l.lead_id, l.prospect_id, l.lead_source, l.lead_origin, l.occupation, l.city,
                   l.total_visits, l.time_on_website, l.last_activity, l.record_type, l.stage, l.lost_reason,
                   l.converted, l.assigned_to, tm.full_name AS assigned_name, l.assigned_at, l.created_at,
                   l.closed_at, cp.conversion_probability, cp.priority_tier, cp.top_reasons, cp.model_version,
                   COALESCE(fa.follow_up_count, 0) AS follow_up_count, fa.last_follow_up_date,
                   lf.next_follow_up_date,
                   CASE WHEN l.stage IN ('Converted', 'Lost') OR l.assigned_at IS NULL THEN NULL
                        WHEN lf.next_follow_up_date IS NOT NULL THEN lf.next_follow_up_date
                        ELSE DATE(DATE_ADD(l.assigned_at, INTERVAL CASE cp.priority_tier {sla} ELSE 7 END DAY))
                   END AS due_date
            FROM leads l
            LEFT JOIN conversion_predictions cp ON cp.lead_id = l.lead_id
            LEFT JOIN team_members tm ON tm.user_id = l.assigned_to
            LEFT JOIN (SELECT lead_id, COUNT(*) AS follow_up_count, MAX(follow_up_date) AS last_follow_up_date,
                              MAX(follow_up_id) AS last_follow_up_id
                       FROM follow_ups GROUP BY lead_id) fa ON fa.lead_id = l.lead_id
            LEFT JOIN follow_ups lf ON lf.follow_up_id = fa.last_follow_up_id
        ) x""")
    cur.execute("""
        CREATE OR REPLACE VIEW team_performance AS
        SELECT tm.user_id, tm.full_name, tm.is_active,
               COUNT(p.lead_id) AS assigned_leads,
               COALESCE(SUM(p.stage IN ('New', 'Contacted', 'Qualified')), 0) AS open_leads,
               COALESCE(SUM(p.is_overdue), 0) AS overdue,
               COALESCE(SUM(p.stage = 'Converted'), 0) AS converted,
               COALESCE(SUM(p.stage = 'Lost'), 0) AS lost,
               ROUND(SUM(p.stage = 'Converted') / NULLIF(SUM(p.stage IN ('Converted', 'Lost')), 0), 3) AS conversion_rate,
               ROUND(COALESCE(SUM(CASE WHEN p.stage IN ('Converted', 'Lost') THEN p.conversion_probability END), 0), 1)
                   AS expected_conversions_closed,
               ROUND(COALESCE(SUM(CASE WHEN p.stage IN ('New', 'Contacted', 'Qualified') THEN p.conversion_probability END), 0), 1)
                   AS forecast_open_pipeline,
               (SELECT COUNT(*) FROM follow_ups f WHERE f.user_id = tm.user_id
                  AND f.follow_up_date >= NOW() - INTERVAL 7 DAY) AS follow_ups_last_7_days
        FROM team_members tm
        LEFT JOIN lead_pipeline_status p ON p.assigned_to = tm.user_id AND p.record_type = 'pipeline'
        WHERE tm.role = 'marketer'
        GROUP BY tm.user_id, tm.full_name, tm.is_active""")


if __name__ == "__main__":
    main()
