"""
Creates app/.streamlit/secrets.toml for "Continue with Google".
Run from the app folder:   python make_secrets.py
You need the Client ID and Client secret from Google Cloud Console (Google Auth Platform > Clients).
The file is listed in .gitignore, so it is never uploaded to GitHub.
"""
import getpass
import secrets
from pathlib import Path

FILE = Path(__file__).resolve().parent / ".streamlit" / "secrets.toml"

if FILE.exists() and input("secrets.toml already exists. Replace it? (y/n): ").strip().lower() != "y":
    raise SystemExit("Kept the existing secrets.toml.")

client_id = input("Paste the Client ID (ends with .apps.googleusercontent.com): ").strip()
if not client_id.endswith(".apps.googleusercontent.com"):
    raise SystemExit("That does not look like a Google Client ID. Copy it again from Google Cloud Console.")
client_secret = getpass.getpass("Paste the Client secret (typing is hidden): ").strip()
if len(client_secret) < 10:
    raise SystemExit("That Client secret looks too short. Copy it again from Google Cloud Console.")
port = input("Port the app runs on (press Enter for 8501): ").strip() or "8501"

FILE.parent.mkdir(exist_ok=True)
FILE.write_text(f'''# Sign in with Google (OpenID Connect). Never upload this file.
[auth]
redirect_uri = "http://localhost:{port}/oauth2callback"
cookie_secret = "{secrets.token_hex(32)}"

[auth.google]
client_id = "{client_id}"
client_secret = "{client_secret}"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
''', encoding="utf-8")
print(f"Saved {FILE}")
print("Restart the app (Ctrl+C, then: streamlit run streamlit_app.py) to see 'Continue with Google'.")
