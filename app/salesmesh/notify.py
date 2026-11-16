"""In-app notifications (the bell): who received a lead, handover requests and their answers."""
from . import db


def send(user_id: int, message: str, lead_id: int = None, request_id: int = None, conn=None):
    if user_id is None:
        return
    db.execute("INSERT INTO notifications (user_id, message, lead_id, request_id) VALUES (%s, %s, %s, %s)",
               (user_id, message[:300], lead_id, request_id), conn)


def send_many(user_ids, message: str, lead_id: int = None, request_id: int = None, conn=None):
    for uid in {u for u in user_ids if u is not None}:
        send(uid, message, lead_id, request_id, conn)


def unread_count(user: dict) -> int:
    return db.query_one("SELECT COUNT(*) AS n FROM notifications WHERE user_id = %s AND read_at IS NULL",
                        (user["user_id"],))["n"]


def recent(user: dict, limit: int = 50):
    return db.query(
        "SELECT n.notification_id, n.message, n.lead_id, n.request_id, n.created_at, n.read_at, "
        "l.prospect_id, r.status AS request_status "
        "FROM notifications n LEFT JOIN leads l ON l.lead_id = n.lead_id "
        "LEFT JOIN handover_requests r ON r.request_id = n.request_id "
        "WHERE n.user_id = %s ORDER BY n.read_at IS NOT NULL, n.created_at DESC, n.notification_id DESC LIMIT %s",
        (user["user_id"], limit))


def mark_all_read(user: dict):
    db.execute("UPDATE notifications SET read_at = NOW() WHERE user_id = %s AND read_at IS NULL", (user["user_id"],))
