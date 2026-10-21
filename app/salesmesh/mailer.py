"""Sends email through SMTP (for example Gmail with an app password)."""
import smtplib
import ssl
from email.message import EmailMessage

from . import config


def email_enabled() -> bool:
    return bool(config.SMTP_HOST and config.SMTP_USER and config.SMTP_PASSWORD)


def send(to: str, subject: str, text: str) -> None:
    msg = EmailMessage()
    msg["From"] = f"SalesMesh <{config.MAIL_FROM or config.SMTP_USER}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    if not email_enabled():
        # Development mode: show the email in the terminal instead of sending it
        print("\n" + "=" * 70 + f"\nEMAIL (not sent: SMTP is not set up)\nTo: {to}\nSubject: {subject}\n\n{text}\n"
              + "=" * 70 + "\n", flush=True)
        return
    deliver(msg, config.SMTP_HOST, config.SMTP_PORT, config.SMTP_USER, config.SMTP_PASSWORD)


def deliver(msg, host, port, user, password):
    """Port 465 uses SSL from the start; port 587 upgrades with STARTTLS. Gmail accepts both."""
    context = ssl.create_default_context()
    if int(port) == 465:
        with smtplib.SMTP_SSL(host, 465, timeout=20, context=context) as server:
            server.login(user, password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(host, int(port), timeout=20) as server:
            server.starttls(context=context)
            server.login(user, password)
            server.send_message(msg)
