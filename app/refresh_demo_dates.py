"""
Move the simulated demonstration activity forward to today.

setup_database.py gave the 300 demonstration leads a simulated follow-up history
dated around the day it was run. As the weeks pass, all of those next-contact dates
fall into the past and every open lead shows as overdue. This script shifts the
simulated dates forward by the number of days since the most recent simulated
follow-up, so the pipeline looks as it did on the day it was set up.

Only simulated data is moved. Leads with any real activity (a follow-up logged in
the app, a handover, a reopening) and leads added in the app are left exactly as
they are.

Run from the app folder:   python refresh_demo_dates.py
"""
from datetime import date

from salesmesh import db

# Demonstration leads: the dataset leads (Kaggle Prospect IDs) placed in the pipeline by setup_database.py,
# with no real activity at all. Leads added in the app have other IDs and are never touched.
DEMO_LEADS = """
    SELECT l.lead_id FROM leads l
    WHERE l.record_type = 'pipeline'
      AND l.prospect_id REGEXP '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
      AND NOT EXISTS (SELECT 1 FROM follow_ups f WHERE f.lead_id = l.lead_id AND f.source <> 'simulated')
      AND NOT EXISTS (SELECT 1 FROM lead_events e WHERE e.lead_id = l.lead_id)
"""


def main():
    ids = [r["lead_id"] for r in db.query(DEMO_LEADS)]
    if not ids:
        print("No demonstration leads without real activity were found. Nothing to do.")
        return
    marks = ", ".join(["%s"] * len(ids))
    last = db.query_one(f"SELECT MAX(DATE(follow_up_date)) AS d FROM follow_ups "
                        f"WHERE source = 'simulated' AND lead_id IN ({marks})", tuple(ids))["d"]
    days = (date.today() - last).days
    print(f"{len(ids)} demonstration leads found. Their latest simulated follow-up was on {last:%d %b %Y}.")
    if days <= 0:
        print("The simulated dates are already up to date. Nothing to do.")
        return
    answer = input(f"Move their simulated dates forward by {days} day(s)? Type yes to continue: ")
    if answer.strip().lower() != "yes":
        print("Nothing was changed.")
        return

    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute(f"""UPDATE follow_ups
                        SET follow_up_date = follow_up_date + INTERVAL %s DAY,
                            next_follow_up_date = next_follow_up_date + INTERVAL %s DAY
                        WHERE source = 'simulated' AND lead_id IN ({marks})""", (days, days, *ids))
        moved_follow_ups = cur.rowcount
        cur.execute(f"""UPDATE leads
                        SET assigned_at = assigned_at + INTERVAL %s DAY,
                            created_at = created_at + INTERVAL %s DAY,
                            closed_at = closed_at + INTERVAL %s DAY
                        WHERE lead_id IN ({marks})""", (days, days, days, *ids))
        moved_leads = cur.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print(f"Done. {moved_leads} leads and {moved_follow_ups} simulated follow-ups were moved forward by {days} day(s).")


if __name__ == "__main__":
    import mysql.connector
    try:
        main()
    except mysql.connector.Error as e:
        print("Could not reach the SalesMesh database. Check that MySQL is running and that DB_PASSWORD "
              f"in app/.env is correct.\nDetails: {e.msg}")
