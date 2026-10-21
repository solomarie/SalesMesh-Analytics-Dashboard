"""
Adds the email settings for "Forgot password" to app/.env and sends a test email.
Run from the app folder:   python setup_email.py

You need a Gmail address with 2-Step Verification turned on and a 16-character
App Password (Google Account > Security > 2-Step Verification > App passwords).
It tries Gmail's two ports (587, then 465) and keeps the one your network allows.
"""
import getpass
import re
import smtplib
from email.message import EmailMessage
from pathlib import Path

from salesmesh.mailer import deliver

ENV = Path(__file__).resolve().parent / ".env"
KEYS = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "MAIL_FROM", "APP_BASE_URL")

if not ENV.exists():
    raise SystemExit("app/.env was not found. Run  python make_env.py  first.")

address = input("Gmail address that will send the emails: ").strip().lower()
if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", address):
    raise SystemExit("That is not a valid email address.")
app_password = getpass.getpass("16-character App Password (typing is hidden): ").replace(" ", "")
if len(app_password) != 16:
    raise SystemExit("A Google App Password has 16 letters. Copy it again (spaces don't matter).")
base_url = input("Address of the app (press Enter for http://localhost:8501): ").strip() or "http://localhost:8501"

msg = EmailMessage()
msg["From"], msg["To"], msg["Subject"] = f"SalesMesh <{address}>", address, "SalesMesh test email"
msg.set_content("Email from SalesMesh is working.\n\nSalesMesh")

working_port, problems = None, []
for port in (587, 465):
    print(f"Trying Gmail on port {port} ...")
    try:
        deliver(msg, "smtp.gmail.com", port, address, app_password)
        working_port = port
        break
    except smtplib.SMTPAuthenticationError:
        raise SystemExit("Gmail refused the App Password. Check the address and create a new App Password.")
    except Exception as e:
        problems.append(f"port {port}: {e}")

if working_port is None:
    raise SystemExit("Could not reach Gmail on either port:\n  " + "\n  ".join(problems) + "\n"
                     "Your network or antivirus is blocking email. Try another network (for example "
                     "your phone's hotspot), or allow Python through your antivirus, then run this again.")

lines = [l for l in ENV.read_text(encoding="utf-8").splitlines() if l.split("=", 1)[0].strip() not in KEYS]
lines += ["SMTP_HOST=smtp.gmail.com", f"SMTP_PORT={working_port}", f"SMTP_USER={address}",
          f"SMTP_PASSWORD={app_password}", f"MAIL_FROM={address}", f"APP_BASE_URL={base_url.rstrip('/')}"]
ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"Test email sent using port {working_port}. Settings saved in app/.env")
print("Check the inbox of", address, "then restart the app.")
