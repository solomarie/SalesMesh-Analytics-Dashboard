"""
Creates app/.env by asking for the two passwords (typing is hidden).
Run from the app folder:   python make_env.py
"""
import getpass
import re
from pathlib import Path

ENV = Path(__file__).resolve().parent / ".env"


def ask(label, min_len, need_symbol):
    while True:
        first = getpass.getpass(f"{label} (typing is hidden): ")
        problems = []
        if len(first) < min_len:
            problems.append(f"at least {min_len} characters")
        if not re.search(r"[A-Za-z]", first) or not re.search(r"\d", first):
            problems.append("letters and numbers")
        if need_symbol and not re.search(r"[^A-Za-z0-9]", first):
            problems.append("a symbol such as ! or @")
        if re.search(r"[\s#'\"=]", first):
            problems.append("no spaces, #, = or quote marks")
        if problems:
            print("   Not accepted. It needs: " + ", ".join(problems) + ". Try again.")
            continue
        if getpass.getpass("Type it again to confirm: ") != first:
            print("   The two did not match. Try again.")
            continue
        return first


if ENV.exists() and input(".env already exists. Replace it? (y/n): ").strip().lower() != "y":
    raise SystemExit("Kept the existing .env.")

print("1) App database password: used by the app to connect to MySQL. NOT your root password.")
app_pw = ask("App database password", 12, True)
print("2) Demo password: what you type to log in as manager, jane, brian, aisha, kevin or admin.")
demo_pw = ask("Demo login password", 8, False)

ENV.write_text(
    "DB_HOST=localhost\nDB_PORT=3306\nDB_NAME=salesmesh\nDB_USER=salesmesh_app\n"
    f"DB_PASSWORD={app_pw}\nDEMO_PASSWORD={demo_pw}\nSESSION_TIMEOUT_MINUTES=30\n",
    encoding="utf-8")
print(f"Saved {ENV}")
