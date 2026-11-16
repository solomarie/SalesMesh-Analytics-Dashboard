"""Settings read from app/.env (never committed to GitHub)."""
import os
from pathlib import Path

from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent.parent
load_dotenv(APP_DIR / ".env")

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_NAME = os.getenv("DB_NAME", "salesmesh")
DB_USER = os.getenv("DB_USER", "salesmesh_app")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

SESSION_TIMEOUT_MINUTES = int(os.getenv("SESSION_TIMEOUT_MINUTES", "30"))
MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15

# Email (forgot password). With no SMTP settings the email is printed in the
# terminal instead of being sent: useful for testing, never for real users.
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
MAIL_FROM = os.getenv("MAIL_FROM", SMTP_USER)
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8501").rstrip("/")
RESET_LINK_MINUTES = 30
MAX_RESET_REQUESTS_PER_HOUR = 3

# Joining SalesMesh (Sprint III): there is no open self-registration.
# People join through an invitation link, or by signing in with Google using an email
# from one of these company domains (comma-separated, e.g. "salesmesh.com,company.co.ke").
ALLOWED_SIGNUP_DOMAINS = [d.strip().lower().lstrip("@") for d in os.getenv("ALLOWED_SIGNUP_DOMAINS", "").split(",")
                          if d.strip()]
INVITE_DAYS = 7
MAX_INVITES_PER_BATCH = 500

