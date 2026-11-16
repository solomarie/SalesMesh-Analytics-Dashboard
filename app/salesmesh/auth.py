"""
FR-01 / IR-04 / NFR-01: registration and login.

* Passwords are hashed with bcrypt (salted, deliberately slow), never stored as text.
* After 5 wrong passwords an account is locked for 15 minutes.
* New registrations are Marketer accounts that stay inactive until a
  Marketing Manager or Admin approves them.
"""
import hashlib
import math
import re
import secrets
from datetime import datetime, timedelta

import bcrypt
import mysql.connector

from . import config, db, mailer

ROLES = ("admin", "manager", "marketer")
EMAIL_RE = re.compile(r"^(?!.*\.\.)[A-Za-z0-9._%+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}$")  # no ".." (defect D24)
GENERIC_LOGIN_ERROR = "Incorrect email or password."


class AuthError(Exception):
    """Shown to the user as-is."""


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash or not stored_hash.startswith("$2"):
        return False
    return bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8"))


def password_problems(password: str) -> list:
    problems = []
    if len(password) < 8:
        problems.append("at least 8 characters")
    if not re.search(r"[A-Za-z]", password):
        problems.append("a letter")
    if not re.search(r"\d", password):
        problems.append("a number")
    return problems


def authenticate(email: str, password: str, now: datetime = None) -> dict:
    """Return the signed-in user, or raise AuthError with a message safe to show."""
    now = now or datetime.now()
    email = (email or "").strip().lower()
    user = db.query_one(
        "SELECT u.user_id, u.email, u.password_hash, u.role, u.is_active, u.failed_logins, u.locked_until, "
        "COALESCE(b.full_name, u.email) AS full_name "
        "FROM users u LEFT JOIN user_biodata b ON b.user_id = u.user_id WHERE u.email = %s", (email,))
    if user is None:
        raise AuthError(GENERIC_LOGIN_ERROR)
    if user["locked_until"] and user["locked_until"] > now:
        minutes = max(1, math.ceil((user["locked_until"] - now).total_seconds() / 60))
        raise AuthError(f"Too many failed attempts. Try again in {minutes} minute(s).")
    if not verify_password(password or "", user["password_hash"]):
        failed = user["failed_logins"] + 1
        locked = now + timedelta(minutes=config.LOCKOUT_MINUTES) if failed >= config.MAX_FAILED_LOGINS else None
        db.execute("UPDATE users SET failed_logins = %s, locked_until = %s WHERE user_id = %s",
                   (0 if locked else failed, locked, user["user_id"]))
        if locked:
            raise AuthError(f"Too many failed attempts. The account is locked for {config.LOCKOUT_MINUTES} minutes.")
        raise AuthError(GENERIC_LOGIN_ERROR)
    if not user["is_active"]:
        raise AuthError("This account is not active. Contact your Marketing Manager.")
    db.execute("UPDATE users SET failed_logins = 0, locked_until = NULL WHERE user_id = %s", (user["user_id"],))
    return {k: user[k] for k in ("user_id", "email", "role", "full_name")}


def _create_account(email: str, full_name: str, phone, role: str, password_hash: str, conn) -> int:
    """Insert an active account and its profile inside the caller's transaction."""
    user_id = db.execute("INSERT INTO users (email, password_hash, role, is_active) VALUES (%s, %s, %s, 1)",
                         (email, password_hash, role), conn)
    db.execute("INSERT INTO user_biodata (user_id, full_name, phone, department, job_title) "
               "VALUES (%s, %s, %s, 'Marketing', %s)",
               (user_id, (full_name or email)[:100], phone or None,
                {"admin": "System Admin", "manager": "Marketing Manager"}.get(role, "Marketer")), conn)
    return user_id


# ------------------------------------------------------------------ invitations
# There is no open self-registration (it could be flooded with requests and needed one-by-one
# approval). People join only through an invitation that a manager or admin created, or by signing
# in with Google using an email from an approved company domain.
INVITABLE_BY = {"manager": ("marketer",), "admin": ("marketer", "manager", "admin")}


def create_invitations(user: dict, people: list, now: datetime = None, base_url: str = None) -> tuple:
    """
    Invite one or many people. `people` is a list of dicts with email, optional full_name and role.
    Returns (sent, skipped): sent = [{email, role, link}], skipped = [{email, reason}].
    A new invitation for an email replaces any earlier unused one.
    """
    require_role(user, "admin", "manager")
    now = now or datetime.now()
    if len(people) > config.MAX_INVITES_PER_BATCH:
        raise AuthError(f"Invite at most {config.MAX_INVITES_PER_BATCH} people at a time.")
    allowed_roles = INVITABLE_BY[user["role"]]
    existing = {r["email"] for r in db.query("SELECT email FROM users")}
    sent, skipped, seen = [], [], set()
    for person in people:
        email = str(person.get("email") or "").strip().lower()
        role = str(person.get("role") or "marketer").strip().lower()
        name = str(person.get("full_name") or "").strip()[:100] or None
        if not EMAIL_RE.match(email):
            skipped.append({"email": email or "(blank)", "reason": "Not a valid email address"})
        elif email in seen:
            skipped.append({"email": email, "reason": "Listed more than once"})
        elif email in existing:
            skipped.append({"email": email, "reason": "Already has a SalesMesh account"})
        elif role not in allowed_roles:
            skipped.append({"email": email, "reason": f"You cannot invite a {role}"})
        else:
            seen.add(email)
            token = secrets.token_urlsafe(32)
            conn = db.connect()
            try:
                db.execute("UPDATE invitations SET revoked_at = %s WHERE email = %s AND accepted_at IS NULL "
                           "AND revoked_at IS NULL", (now, email), conn)
                db.execute("INSERT INTO invitations (email, full_name, role, token_hash, invited_by, created_at, "
                           "expires_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                           (email, name, role, _token_hash(token), user["user_id"], now,
                            now + timedelta(days=config.INVITE_DAYS)), conn)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
            link = f"{(base_url or config.APP_BASE_URL).rstrip('/')}/?invite={token}"
            try:
                mailer.send(email, "You're invited to SalesMesh",
                            f"Hi{(' ' + name) if name else ''},\n\n{user['full_name']} has invited you to join "
                            f"SalesMesh as a {role}.\nOpen this link to set up your account:\n\n{link}\n\n"
                            f"The link works once and expires in {config.INVITE_DAYS} days. You can also use "
                            "Continue with Google with this email address.\n\nSalesMesh")
            except Exception:
                pass  # the link is still shown to the inviter, who can share it another way
            sent.append({"email": email, "role": role, "link": link})
    return sent, skipped


def list_invitations(user: dict):
    require_role(user, "admin", "manager")
    return db.query(
        "SELECT i.invite_id, i.email, i.full_name, i.role, i.created_at, i.expires_at, i.accepted_at, i.revoked_at, "
        "COALESCE(b.full_name, u.email) AS invited_by FROM invitations i JOIN users u ON u.user_id = i.invited_by "
        "LEFT JOIN user_biodata b ON b.user_id = u.user_id ORDER BY i.created_at DESC, i.invite_id DESC LIMIT 1000")


def revoke_invitation(user: dict, invite_id: int):
    require_role(user, "admin", "manager")
    row = db.query_one("SELECT role FROM invitations WHERE invite_id = %s AND accepted_at IS NULL "
                       "AND revoked_at IS NULL", (invite_id,))
    if row is None:
        raise AuthError("This invitation has already been used or cancelled.")
    if row["role"] not in INVITABLE_BY[user["role"]]:
        raise PermissionError("Only an admin can cancel this invitation.")
    db.execute("UPDATE invitations SET revoked_at = NOW() WHERE invite_id = %s", (invite_id,))


def check_invitation(token: str, now: datetime = None) -> dict:
    now = now or datetime.now()
    row = db.query_one("SELECT invite_id, email, full_name, role, expires_at, accepted_at, revoked_at "
                       "FROM invitations WHERE token_hash = %s", (_token_hash(token or ""),))
    if row is None or row["accepted_at"] or row["revoked_at"]:
        raise AuthError("This invitation link is not valid or has already been used. Ask for a new invitation.")
    if row["expires_at"] < now:
        raise AuthError("This invitation has expired. Ask your Marketing Manager to send a new one.")
    if db.query_one("SELECT user_id FROM users WHERE email = %s", (row["email"],)):
        raise AuthError("An account with this email already exists. Sign in instead.")
    return row


def accept_invitation(token: str, full_name: str, phone: str, password: str, confirm: str,
                      now: datetime = None) -> dict:
    """Set up the invited account (active straight away) and return it, ready to sign in."""
    now = now or datetime.now()
    invite = check_invitation(token, now)
    full_name = (full_name or "").strip()
    if not full_name:
        raise AuthError("Please enter your full name.")
    problems = password_problems(password or "")
    if problems:
        raise AuthError("Password needs " + ", ".join(problems) + ".")
    if password != confirm:
        raise AuthError("The two passwords do not match.")
    conn = db.connect()
    try:
        user_id = _create_account(invite["email"], full_name, phone, invite["role"], hash_password(password), conn)
        db.execute("UPDATE invitations SET accepted_at = %s WHERE invite_id = %s", (now, invite["invite_id"]), conn)
        conn.commit()
    except mysql.connector.IntegrityError as e:
        conn.rollback()
        raise AuthError("An account with this email already exists. Sign in instead.") from e
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"user_id": user_id, "email": invite["email"], "role": invite["role"], "full_name": full_name}


# Accounts created through Google have no SalesMesh password. This marker can never
# match a bcrypt hash, so password sign-in is impossible for them until they set one.
EXTERNAL_ONLY = "!external-sign-in"


def external_sign_in(email: str, full_name: str, email_verified: bool, provider: str = "Google",
                     now: datetime = None) -> dict:
    """
    Sign in with an identity already confirmed by Google (OpenID Connect).
    Known, active account -> signed in with its SalesMesh role.
    Unknown email with a valid invitation -> account created with the invited role.
    Unknown email from an approved company domain -> account created as a Marketer.
    Anyone else is refused: Google proves who the person is; SalesMesh decides whether they belong.
    """
    now = now or datetime.now()
    email = (email or "").strip().lower()
    if not email or not email_verified:
        raise AuthError(f"Your {provider} email address is not verified, so it cannot be used here.")
    user = db.query_one(
        "SELECT u.user_id, u.email, u.role, u.is_active, COALESCE(b.full_name, u.email) AS full_name "
        "FROM users u LEFT JOIN user_biodata b ON b.user_id = u.user_id WHERE u.email = %s", (email,))
    if user is not None:
        if not user["is_active"]:
            raise AuthError("This account has been deactivated. Contact your Marketing Manager.")
        db.execute("UPDATE users SET failed_logins = 0, locked_until = NULL WHERE user_id = %s", (user["user_id"],))
        return {k: user[k] for k in ("user_id", "email", "role", "full_name")}
    invite = db.query_one("SELECT invite_id, role FROM invitations WHERE email = %s AND accepted_at IS NULL "
                          "AND revoked_at IS NULL AND expires_at > %s ORDER BY created_at DESC LIMIT 1", (email, now))
    domain = email.rsplit("@", 1)[-1]
    if invite is None and domain not in config.ALLOWED_SIGNUP_DOMAINS:
        raise AuthError("There is no SalesMesh account for this email. Ask your Marketing Manager for an "
                        "invitation.")
    role = invite["role"] if invite else "marketer"
    conn = db.connect()
    try:
        user_id = _create_account(email, full_name, None, role, EXTERNAL_ONLY, conn)
        if invite:
            db.execute("UPDATE invitations SET accepted_at = %s WHERE invite_id = %s", (now, invite["invite_id"]), conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"user_id": user_id, "email": email, "role": role, "full_name": (full_name or email)[:100]}


def change_password(user: dict, current: str, new: str, confirm: str):
    """Any signed-in user can change their own password."""
    row = db.query_one("SELECT password_hash FROM users WHERE user_id = %s", (user["user_id"],))
    if row is None or not verify_password(current or "", row["password_hash"]):
        raise AuthError("Your current password is incorrect.")
    problems = password_problems(new or "")
    if problems:
        raise AuthError("New password needs " + ", ".join(problems) + ".")
    if new != confirm:
        raise AuthError("The two new passwords do not match.")
    if new == current:
        raise AuthError("Choose a password different from the current one.")
    db.execute("UPDATE users SET password_hash = %s WHERE user_id = %s", (hash_password(new), user["user_id"]))
    mailer.send(user["email"], "Your SalesMesh password was changed",
                "The password for your SalesMesh account was just changed from the My account page.\n\n"
                "If this was you, there is nothing else to do.\n"
                "If it wasn't you, use 'Forgot password?' on the sign-in page straight away and tell "
                "your SalesMesh administrator.\n\nSalesMesh")


def admin_reset_password(user: dict, target_id: int, new: str):
    """An admin sets a temporary password for someone who forgot theirs, and unlocks the account."""
    require_role(user, "admin")
    problems = password_problems(new or "")
    if problems:
        raise AuthError("Temporary password needs " + ", ".join(problems) + ".")
    db.execute("UPDATE users SET password_hash = %s, failed_logins = 0, locked_until = NULL WHERE user_id = %s",
               (hash_password(new), target_id))


# ------------------------------------------------------------------ forgot password
RESET_SENT_MESSAGE = ("If an account exists for that email, we've sent a link to reset your password. "
                      "It expires in {minutes} minutes. Check your spam folder if it doesn't arrive.")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def request_password_reset(email: str, now: datetime = None, base_url: str = None) -> str:
    """
    Email a one-time reset link. Always returns the same message, whether or not the
    email belongs to an account, so the form cannot be used to find out who is registered.
    """
    now = now or datetime.now()
    email = (email or "").strip().lower()
    message = RESET_SENT_MESSAGE.format(minutes=config.RESET_LINK_MINUTES)
    if not EMAIL_RE.match(email):
        raise AuthError("Please enter a valid email address.")
    user = db.query_one("SELECT u.user_id, COALESCE(b.full_name, u.email) AS full_name FROM users u "
                        "LEFT JOIN user_biodata b ON b.user_id = u.user_id WHERE u.email = %s", (email,))
    if user is None:
        return message
    recent = db.query_one("SELECT COUNT(*) AS n FROM password_resets WHERE user_id = %s AND created_at > %s",
                          (user["user_id"], now - timedelta(hours=1)))["n"]
    if recent >= config.MAX_RESET_REQUESTS_PER_HOUR:
        return message  # quietly limit repeated requests
    token = secrets.token_urlsafe(32)
    db.execute("INSERT INTO password_resets (user_id, token_hash, created_at, expires_at) VALUES (%s, %s, %s, %s)",
               (user["user_id"], _token_hash(token), now, now + timedelta(minutes=config.RESET_LINK_MINUTES)))
    link = f"{(base_url or config.APP_BASE_URL).rstrip('/')}/?reset={token}"
    mailer.send(email, "Reset your SalesMesh password",
                f"Hi {user['full_name']},\n\n"
                "We received a request to reset the password for your SalesMesh account.\n"
                "Open this link to choose a new password:\n\n"
                f"{link}\n\n"
                f"The link expires in {config.RESET_LINK_MINUTES} minutes and can only be used once.\n\n"
                "If you didn't ask for this, you can ignore this email. Your password will not change.\n\n"
                "SalesMesh")
    return message


def check_reset_token(token: str, now: datetime = None) -> dict:
    """Return the account a reset link belongs to, or raise AuthError if the link cannot be used."""
    now = now or datetime.now()
    row = db.query_one(
        "SELECT r.reset_id, r.user_id, r.expires_at, r.used_at, u.email FROM password_resets r "
        "JOIN users u ON u.user_id = r.user_id WHERE r.token_hash = %s", (_token_hash(token or ""),))
    if row is None or row["used_at"] is not None:
        raise AuthError("This reset link is not valid or has already been used. Request a new one.")
    if row["expires_at"] < now:
        raise AuthError("This reset link has expired. Request a new one.")
    return row


def reset_password_with_token(token: str, new: str, confirm: str, now: datetime = None) -> str:
    """Set a new password from a valid link. Every other open link for the account stops working."""
    now = now or datetime.now()
    row = check_reset_token(token, now)
    problems = password_problems(new or "")
    if problems:
        raise AuthError("Password needs " + ", ".join(problems) + ".")
    if new != confirm:
        raise AuthError("The two passwords do not match.")
    conn = db.connect()
    try:
        db.execute("UPDATE users SET password_hash = %s, failed_logins = 0, locked_until = NULL WHERE user_id = %s",
                   (hash_password(new), row["user_id"]), conn)
        db.execute("UPDATE password_resets SET used_at = %s WHERE user_id = %s AND used_at IS NULL",
                   (now, row["user_id"]), conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    mailer.send(row["email"], "Your SalesMesh password was changed",
                "The password for your SalesMesh account was just changed.\n\n"
                "If this was you, there is nothing else to do.\n"
                "If it wasn't you, contact your SalesMesh administrator straight away.\n\nSalesMesh")
    return row["email"]


def refresh_session_user(user: dict):
    """
    Re-read the signed-in person's account on every page load, so a role change or a
    deactivation takes effect immediately instead of at their next sign-in.
    Returns the up-to-date user, or None if the account no longer exists or is inactive.
    """
    row = db.query_one(
        "SELECT u.user_id, u.email, u.role, u.is_active, COALESCE(b.full_name, u.email) AS full_name "
        "FROM users u LEFT JOIN user_biodata b ON b.user_id = u.user_id WHERE u.user_id = %s", (user["user_id"],))
    if row is None or not row["is_active"]:
        return None
    return {k: row[k] for k in ("user_id", "email", "role", "full_name")}


def require_role(user: dict, *roles: str):
    """Server-side permission check used by every service function."""
    if not user or user.get("role") not in roles:
        raise PermissionError("You do not have permission to do this.")
