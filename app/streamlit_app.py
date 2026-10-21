"""
SalesMesh lead-management app.

Start it from the app folder:   streamlit run streamlit_app.py

Marketers: today's call list, their own leads, logging follow-ups, adding leads.
Marketing Manager: team performance, each marketer's activity, escalations,
reassignment and account approvals. Admin: everything a manager can do plus
roles and password resets.
"""
import html
from datetime import date, datetime, timedelta

import altair as alt
import mysql.connector
import pandas as pd
import streamlit as st

from salesmesh import auth, config, pipeline, scoring, ui
from salesmesh.scoring import CATEGORICAL_COLS, FEATURE_INPUT_COLS, FRIENDLY_NAMES, NUMERIC_COLS

st.set_page_config(page_title="SalesMesh", page_icon=":material/insights:", layout="wide")
if tuple(int(x) for x in st.__version__.split(".")[:2]) < (1, 50):
    st.error(f"This app needs Streamlit 1.50 or newer (you have {st.__version__}). "
             "Run:  pip install --upgrade streamlit")
    st.stop()
ui.apply_styles()

ROLE_LABEL = {"admin": "Admin", "manager": "Marketing Manager", "marketer": "Marketer"}
TIER_LABEL = {"High": "🟢 High", "Medium": "🟠 Medium", "Low": "⚪ Low"}
ESCALATE_AFTER_DAYS = 3


# ------------------------------------------------------------------ session
def current_user():
    return st.session_state.get("user")


def google_enabled():
    """Google sign-in is offered only when app/.streamlit/secrets.toml has an [auth.google] section."""
    try:
        return "google" in st.secrets.get("auth", {})
    except Exception:
        return False


def google_signed_in():
    return google_enabled() and bool(getattr(st.user, "is_logged_in", False))


def check_session():
    """NFR-01: sign the user out after a period without activity."""
    user = current_user()
    if not user:
        return None
    last = st.session_state.get("last_active")
    if last and datetime.now() - last > timedelta(minutes=config.SESSION_TIMEOUT_MINUTES):
        st.session_state.clear()
        if google_signed_in():
            st.logout()  # also end the Google session, or the next visit would sign straight back in
        st.session_state["flash"] = ("info", f"You were signed out after {config.SESSION_TIMEOUT_MINUTES} "
                                             "minutes without activity. Please sign in again.")
        return None
    fresh = auth.refresh_session_user(user)
    if fresh is None:
        st.session_state.clear()
        if google_signed_in():
            st.logout()
        st.session_state["flash"] = ("warning", "Your account has been deactivated. Contact your Marketing Manager.")
        return None
    if fresh["role"] != user["role"]:
        st.session_state["flash"] = ("info", f"Your role is now {ROLE_LABEL[fresh['role']]}. "
                                             "Your menu has been updated.")
    st.session_state["user"] = fresh
    st.session_state["last_active"] = datetime.now()
    return fresh


def start_session(user):
    st.session_state["user"] = user
    st.session_state["last_active"] = datetime.now()


def flash(message, kind="success"):
    st.session_state["flash"] = (kind, message)
    # Data changed: start every lead table with no row selected
    st.session_state["table_version"] = st.session_state.get("table_version", 0) + 1


def show_flash():
    if "flash" in st.session_state:
        kind, message = st.session_state.pop("flash")
        getattr(st, kind)(message)


def sign_out():
    st.session_state.clear()
    if google_signed_in():
        st.logout()
    flash("You have signed out.", "info")
    st.rerun()


# ------------------------------------------------------------------ helpers
def pct(x):
    return "–" if x is None or pd.isna(x) else f"{float(x) * 100:.0f}%"


def status_of(row, today):
    if row["stage"] == "Converted":
        return "✅ Converted"
    if row["stage"] == "Lost":
        return "✖ Lost"
    if row["is_overdue"]:
        return "🔴 Overdue"
    if row["due_date"] is not None and not pd.isna(row["due_date"]) and row["due_date"] == today:
        return "🟡 Due today"
    return "Upcoming"


def leads_view(df, show_owner):
    """The columns people see in every lead list."""
    today = date.today()
    view = pd.DataFrame({
        "Prospect ID": df["prospect_id"],
        "Score": (df["conversion_probability"].astype(float) * 100).round(0),
        "Tier": df["priority_tier"].map(TIER_LABEL),
        "Stage": df["stage"],
        "Status": [status_of(r, today) for _, r in df.iterrows()],
        "Next action due": pd.to_datetime(df["due_date"]),
        "Follow-ups": df["follow_up_count"].astype(int),
        "Owner": df["assigned_name"],
        "Source": df["lead_source"],
        "Occupation": df["occupation"],
    })
    if not show_owner:
        view = view.drop(columns="Owner")
    return view


def lead_table(df, key, show_owner, height=None):
    """Show leads; return the lead_id of the row the user clicked, if any."""
    if df.empty:
        st.info("No leads to show.")
        return None
    view = leads_view(df, show_owner)
    # The key changes whenever the rows change, so an old selection never opens the wrong lead
    widget_key = f"{key}_{st.session_state.get('table_version', 0)}_{hash(tuple(df['lead_id']))}"
    extra = {"height": height} if height else {}
    event = st.dataframe(
        view, key=widget_key, on_select="rerun", selection_mode="single-row", hide_index=True, width="stretch",
        **extra,
        column_config={
            "Score": st.column_config.ProgressColumn("Score", help="Chance of converting, from the model",
                                                     min_value=0, max_value=100, format="%d%%"),
            "Next action due": st.column_config.DateColumn("Next action due", format="DD MMM YYYY"),
        })
    st.download_button("Download this list (CSV)", view.to_csv(index=False).encode("utf-8"),
                       file_name=f"salesmesh_{key}_{date.today():%Y%m%d}.csv", mime="text/csv",
                       key=f"dl_{key}")
    rows = event.selection.rows
    return int(df.iloc[rows[0]]["lead_id"]) if rows else None


def reasons_list(text):
    return [r for r in (text or "").split(" | ") if r]


# ------------------------------------------------------------------ lead panel
def lead_panel(user, lead_id):
    lead = pipeline.get_lead(user, lead_id)
    today = date.today()
    closed = lead["stage"] in pipeline.CLOSED_STAGES
    with st.container(border=True):
        st.subheader(f"Lead {lead['prospect_id']}")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Conversion score", pct(lead["conversion_probability"]))
        c2.metric("Priority tier", TIER_LABEL.get(lead["priority_tier"], lead["priority_tier"]))
        c3.metric("Stage", lead["stage"])
        c4.metric("Owner", lead["assigned_name"] or "Unassigned")

        if closed:
            note = f" Reason: {lead['lost_reason']}." if lead["lost_reason"] else ""
            closed_on = lead["closed_at"].strftime("%d %b %Y") if lead["closed_at"] else ""
            st.info(f"This lead was closed as **{lead['stage']}** on {closed_on}.{note}")
        elif lead["is_overdue"]:
            days = (today - lead["due_date"]).days
            st.error(f"Overdue: the next action was due on {lead['due_date']:%d %b %Y} ({days} day(s) ago).")
        elif lead["due_date"] == today:
            st.warning("Due today.")
        elif lead["due_date"]:
            st.info(f"Next action due on {lead['due_date']:%d %b %Y}.")

        left, right = st.columns(2)
        with left:
            st.markdown("**Why this score**")
            reasons = reasons_list(lead["top_reasons"])
            if reasons:
                st.markdown("\n".join(f"- {r}" for r in reasons))
            else:
                st.caption("No single input changes this score much; it is close to a typical lead.")
            st.markdown("**Lead details**")
            st.dataframe(pd.DataFrame({
                "Detail": ["Lead source", "Lead origin", "Occupation", "City", "Website visits",
                           "Time on website", "Last activity", "Added on"],
                "Value": [lead["lead_source"], lead["lead_origin"], lead["occupation"], lead["city"],
                          str(lead["total_visits"]), str(lead["time_on_website"]), lead["last_activity"],
                          lead["created_at"].strftime("%d %b %Y") if lead["created_at"] else "–"],
            }), hide_index=True, width="stretch")
        with right:
            st.markdown("**Follow-up history**")
            history = pipeline.get_follow_ups(user, lead_id)
            if history.empty:
                st.caption("No contact has been logged yet.")
            else:
                history["next_follow_up_date"] = pd.to_datetime(history["next_follow_up_date"])
                history = history.rename(columns={
                    "follow_up_date": "When", "follow_up_type": "Type", "outcome": "Outcome", "notes": "Notes",
                    "next_follow_up_date": "Next follow-up", "logged_by": "By", "source": "Source"})
                st.dataframe(history, hide_index=True, width="stretch",
                             column_config={"When": st.column_config.DatetimeColumn(format="DD MMM YYYY, HH:mm"),
                                            "Next follow-up": st.column_config.DateColumn(format="DD MMM YYYY")})
                if (history["Source"] == "simulated").any():
                    st.caption("Rows marked 'simulated' were generated for the demonstration pipeline.")

        if not closed:
            log_follow_up_form(user, lead)
            if user["role"] in ("manager", "admin"):
                reassign_form(user, lead)


def log_follow_up_form(user, lead):
    """The fields shown depend on the outcome: closing a lead needs no next date, only Lost needs a reason."""
    if user["role"] == "marketer" and lead["assigned_to"] != user["user_id"]:
        return
    k = f"{lead['lead_id']}_{st.session_state.get('table_version', 0)}"
    with st.container(border=True):
        st.markdown("**Log a follow-up**")
        a, b = st.columns(2)
        kind = a.selectbox("How did you contact them?", pipeline.FOLLOW_UP_TYPES, key=f"fu_type_{k}")
        outcome = b.selectbox("Outcome", list(pipeline.OUTCOMES), key=f"fu_outcome_{k}",
                              help="No answer / Needs follow-up → Contacted. Interested → Qualified. "
                                   "Converted or Lost closes the lead.")
        next_date, lost_reason = None, None
        if outcome == "Converted":
            st.success("This closes the lead as **Converted** (became a customer). No further follow-up is needed.")
        elif outcome == "Lost":
            st.warning("This closes the lead as **Lost**. No further follow-up is needed.")
            lost_reason = st.selectbox("Why was it lost?", pipeline.LOST_REASONS, index=None,
                                       placeholder="Choose a reason", key=f"fu_reason_{k}")
        else:
            next_date = st.date_input("Next follow-up date", value=date.today() + timedelta(days=2),
                                      min_value=date.today(), format="DD/MM/YYYY", key=f"fu_date_{k}",
                                      help="The lead comes back on your Today list on this date.")
        notes = st.text_area("Notes", max_chars=500, placeholder="What was discussed, what was agreed",
                             key=f"fu_notes_{k}")
        label = {"Converted": "Close as Converted", "Lost": "Close as Lost"}.get(outcome, "Save follow-up")
        if st.button(label, type="primary", key=f"fu_save_{k}"):
            try:
                stage = pipeline.log_follow_up(user, lead["lead_id"], kind, outcome, notes, next_date, lost_reason)
            except (ValueError, PermissionError, LookupError) as e:
                st.error(str(e))
            else:
                flash(f"Follow-up saved. Lead {lead['prospect_id']} is now **{stage}**.")
                st.rerun()


def reassign_form(user, lead):
    marketers = pipeline.active_marketers()
    others = marketers[marketers["user_id"] != lead["assigned_to"]]
    if others.empty:
        return
    st.markdown("**Reassign this lead**")
    a, b = st.columns([3, 1], vertical_alignment="bottom")
    labels = {int(r.user_id): f"{r.full_name} ({int(r.open_leads)} open leads)" for r in others.itertuples()}
    target = a.selectbox("Give it to", list(labels), format_func=labels.get, key=f"re_{lead['lead_id']}")
    if b.button("Reassign", key=f"re_btn_{lead['lead_id']}"):
        pipeline.reassign_lead(user, lead["lead_id"], target)
        flash(f"Lead {lead['prospect_id']} reassigned to {labels[target].split(' (')[0]}.")
        st.rerun()


# ------------------------------------------------------------------ pages
def login_page():
    token = st.query_params.get("reset")
    brand, form = st.columns([1.12, 1], gap="large", vertical_alignment="center")
    with brand:
        with st.container(key="brand"):
            st.html(ui.BRAND_PANEL)
    with form:
        with st.container(key="signin"):
            if token:
                reset_view(token)
            elif st.session_state.get("auth_view") == "forgot":
                forgot_view()
            else:
                signin_view()


def go_to(view):
    st.session_state["auth_view"] = view
    st.query_params.clear()


def heading(title, sub):
    st.html(f'<div class="signin-head">{title}</div><div class="signin-sub">{sub}</div>')


def signin_view():
    heading("Sign in", "Use the account your marketing manager approved.")
    show_flash()
    if google_enabled():
        google_block()
        st.html('<div class="or-rule">or use your SalesMesh email</div>')
    sign_in, sign_up = st.tabs(["Sign in", "Create an account"])
    with sign_in:
        with st.form("login", border=False):
            email = st.text_input("Email", placeholder="you@company.com")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign in", type="primary", width="stretch")
        if submitted:
            try:
                start_session(auth.authenticate(email, password))
            except auth.AuthError as e:
                st.error(str(e))
            else:
                st.rerun()
        st.button("Forgot password?", type="tertiary", on_click=go_to, args=("forgot",))
    with sign_up:
        st.caption("New accounts are Marketer accounts. A Marketing Manager approves them before first sign-in.")
        with st.form("register", border=False):
            name = st.text_input("Full name")
            email = st.text_input("Work email")
            phone = st.text_input("Phone (optional)")
            pw1 = st.text_input("Password", type="password", help="At least 8 characters, with a letter and a number.")
            pw2 = st.text_input("Confirm password", type="password")
            created = st.form_submit_button("Create account", width="stretch")
        if created:
            try:
                auth.register(name, email, pw1, pw2, phone)
            except auth.AuthError as e:
                st.error(str(e))
            else:
                st.success("Account created. A Marketing Manager must approve it before you can sign in.")


def forgot_view():
    heading("Reset your password",
            "Enter the email you use for SalesMesh. We'll send you a link to choose a new password.")
    with st.form("forgot", border=False):
        email = st.text_input("Email", placeholder="you@company.com")
        sent = st.form_submit_button("Send reset link", type="primary", width="stretch")
    if sent:
        try:
            st.success(auth.request_password_reset(email))
        except auth.AuthError as e:
            st.error(str(e))
        except Exception:
            st.error("The email could not be sent right now. Try again in a few minutes, "
                     "or ask your administrator to reset your password.")
    st.button("Back to sign in", type="tertiary", icon=":material/arrow_back:", on_click=go_to, args=("signin",))


def reset_view(token):
    try:
        account = auth.check_reset_token(token)
    except auth.AuthError as e:
        heading("This link can't be used", "Reset links expire after "
                f"{config.RESET_LINK_MINUTES} minutes and work only once.")
        st.error(str(e))
        st.button("Request a new link", type="primary", width="stretch", on_click=go_to, args=("forgot",))
        st.button("Back to sign in", type="tertiary", icon=":material/arrow_back:", on_click=go_to, args=("signin",))
        return
    heading("Choose a new password", f"For {html.escape(account['email'])}")
    with st.form("reset", border=False):
        new = st.text_input("New password", type="password", help="At least 8 characters, with a letter and a number.")
        confirm = st.text_input("Confirm new password", type="password")
        saved = st.form_submit_button("Save new password", type="primary", width="stretch")
    if saved:
        try:
            auth.reset_password_with_token(token, new, confirm)
        except auth.AuthError as e:
            st.error(str(e))
        else:
            go_to("signin")
            flash("Your password has been changed. Sign in with your new password.")
            st.rerun()


def google_block():
    """Continue with Google, or explain why a Google account cannot get in yet."""
    if not google_signed_in():
        if st.button("Continue with Google", key="google", width="stretch"):
            st.login("google")
        return
    problem = st.session_state.get("google_problem")
    st.warning(f"Signed in with Google as **{st.user.email}**. {problem or ''}")
    if st.button("Use a different Google account", width="stretch"):
        st.session_state.pop("google_problem", None)
        st.logout()


def today_page():
    user = current_user()
    st.title("Today's call list")
    st.caption("Leads that need contact today or are overdue, most urgent first, then highest score.")
    show_flash()
    mine = pipeline.get_leads(user, stages=pipeline.OPEN_STAGES)
    calls = pipeline.call_list(user)
    counts = pipeline.stage_counts(user).set_index("stage")["leads"]
    closed = counts["Converted"] + counts["Lost"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("To contact today", len(calls))
    c2.metric("Overdue", int(calls["is_overdue"].sum()) if not calls.empty else 0)
    c3.metric("My open leads", len(mine))
    c4.metric("My conversion rate", pct(counts["Converted"] / closed) if closed else "–",
              help="Converted ÷ (Converted + Lost) for your closed leads")
    if calls.empty:
        st.success("Nothing is due today. Well done! Check **My leads** for upcoming follow-ups.")
        return
    lead_id = lead_table(calls, "calls", show_owner=False)
    if lead_id:
        lead_panel(user, lead_id)
    else:
        st.caption("Click a row to open the lead and log a follow-up.")


def leads_page():
    user = current_user()
    manager = user["role"] in ("manager", "admin")
    st.title("All leads" if manager else "My leads")
    show_flash()
    f1, f2, f3, f4 = st.columns([2, 2, 2, 1.4], vertical_alignment="bottom")
    marketer_id = None
    if manager:
        marketers = pipeline.active_marketers()
        options = {0: "Everyone"} | {int(r.user_id): r.full_name for r in marketers.itertuples()}
        marketer_id = f1.selectbox("Marketer", list(options), format_func=options.get) or None
    else:
        search_box = f1
    stages = f2.multiselect("Stage", pipeline.STAGES, default=pipeline.OPEN_STAGES)
    tiers = f3.multiselect("Tier", ["High", "Medium", "Low"], default=["High", "Medium", "Low"])
    overdue_only = f4.checkbox("Overdue only")
    search = (st if manager else search_box).text_input("Search Prospect ID", placeholder="Type part of an ID")

    df = pipeline.get_leads(user, stages=stages or None, marketer_id=marketer_id)
    if not df.empty:
        df = df[df["priority_tier"].isin(tiers)]
        if overdue_only:
            df = df[df["is_overdue"] == 1]
        if search:
            df = df[df["prospect_id"].str.contains(search.strip(), case=False, regex=False)]
    st.caption(f"{len(df)} lead(s). Click a row to open it.")
    lead_id = lead_table(df.reset_index(drop=True), "leads", show_owner=manager, height=420)
    if lead_id:
        lead_panel(user, lead_id)


def add_leads_page():
    user = current_user()
    st.title("Add leads")
    st.caption("Each new lead is checked, scored by the model straight away, explained, and given to the "
               "marketer with the fewest open leads.")
    show_flash()
    reference = scoring.load_reference()
    allowed, baseline = reference["allowed_values"], reference["baseline"]
    one, upload = st.tabs(["Enter one lead", "Upload a CSV file"])

    with one:
        if "new_pid" not in st.session_state:
            st.session_state["new_pid"] = f"APP-{datetime.now():%Y%m%d-%H%M%S}"
        with st.form("one_lead"):
            pid = st.text_input("Prospect ID", value=st.session_state["new_pid"])
            values = {}
            cols = st.columns(3)
            for i, col in enumerate(FEATURE_INPUT_COLS):
                box = cols[i % 3]
                if col in NUMERIC_COLS:
                    values[col] = box.number_input(FRIENDLY_NAMES[col], min_value=0.0, value=float(baseline[col]),
                                                   step=1.0, format="%g")
                else:
                    options = [v for v in allowed[col] if v != "Select"]
                    default = options.index(baseline[col]) if baseline[col] in options else 0
                    values[col] = box.selectbox(FRIENDLY_NAMES[col], options, index=default)
            submitted = st.form_submit_button("Score and save lead", type="primary")
        if submitted:
            batch = pd.DataFrame([{"Prospect ID": pid, **values}])
            save_batch(user, batch)
            st.session_state.pop("new_pid", None)

    with upload:
        template = pd.DataFrame([{"Prospect ID": "NEW-0001", **{c: baseline[c] for c in FEATURE_INPUT_COLS}}])
        st.download_button("Download the CSV template", template.to_csv(index=False).encode("utf-8"),
                           file_name="salesmesh_new_leads_template.csv", mime="text/csv")
        file = st.file_uploader("Choose a CSV file of new leads", type=["csv"])
        if file is not None:
            try:
                batch = pd.read_csv(file, dtype=str, keep_default_na=False)
            except Exception:
                st.error("This file could not be read as a CSV.")
                return
            st.write(f"{len(batch)} row(s) found.")
            st.dataframe(batch.head(20), hide_index=True, width="stretch")
            if st.button("Score and save these leads", type="primary"):
                save_batch(user, batch)


def save_batch(user, batch):
    try:
        saved, rejected = pipeline.add_leads(user, batch)
    except scoring.InputFileError as e:
        st.error(str(e))
        return
    except ValueError as e:
        st.error(str(e))
        return
    if not saved.empty:
        st.success(f"{len(saved)} lead(s) scored, saved and assigned.")
        for _, r in saved.iterrows():
            with st.container(border=True):
                st.markdown(f"**Lead {r['Prospect ID']}**")
                b, c, d = st.columns(3)
                b.metric("Score", pct(r["conversion_probability"]))
                c.metric("Tier", TIER_LABEL[r["priority_tier"]])
                d.metric("Assigned to", r["assigned_to"])
                reasons = reasons_list(r["top_reasons"])
                if reasons:
                    st.markdown("**Why:** " + "; ".join(reasons))
                else:
                    st.markdown("**Why:** no single detail moves this score much; it is close to a typical lead.")
                first = pipeline.FIRST_CONTACT_DAYS[r["priority_tier"]]
                st.caption(f"First contact due within {first} day(s).")
    if not rejected.empty:
        st.error(f"{len(rejected)} row(s) were not saved. Fix them and try again:")
        st.dataframe(rejected[["Prospect ID", "Reason"]], hide_index=True, width="stretch")
        st.download_button("Download the rejected rows", rejected.to_csv(index=False).encode("utf-8"),
                           file_name="salesmesh_rejected_leads.csv", mime="text/csv")


def team_page():
    user = current_user()
    st.title("Team performance")
    st.caption("How each marketer is following up their leads. Overdue = the agreed next contact date has passed.")
    show_flash()
    perf = pipeline.team_performance(user)
    perf = perf[perf["is_active"] == 1].copy()
    num = ["assigned_leads", "open_leads", "overdue", "converted", "lost", "expected_conversions_closed",
           "forecast_open_pipeline", "follow_ups_last_7_days"]
    perf[num] = perf[num].astype(float)
    open_, overdue = perf["open_leads"].sum(), perf["overdue"].sum()
    converted, lost = perf["converted"].sum(), perf["lost"].sum()
    expected = perf["expected_conversions_closed"].sum()

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Open leads", f"{open_:.0f}")
    c2.metric("Overdue", f"{overdue:.0f}")
    c2.caption(f"{overdue / open_ * 100:.0f}% of open leads" if open_ else "")
    c3.metric("Converted", f"{converted:.0f}",
              help="The model's expected conversions = sum of the scores of the leads that have closed.")
    c3.caption(f"The model expected {expected:.0f}")
    c4.metric("Conversion rate", pct(converted / (converted + lost)) if converted + lost else "–",
              help="Converted ÷ (Converted + Lost)")
    c5.metric("Forecast", f"{perf['forecast_open_pipeline'].sum():.0f}",
              help="Sum of the scores of all open leads: how many the model expects to convert.")
    c5.caption("from open leads")

    table = pd.DataFrame({
        "Marketer": perf["full_name"],
        "Open": perf["open_leads"].astype(int),
        "Overdue": perf["overdue"].astype(int),
        "Overdue %": (perf["overdue"] / perf["open_leads"].replace(0, pd.NA) * 100).fillna(0).round(0),
        "Follow-ups (7 days)": perf["follow_ups_last_7_days"].astype(int),
        "Converted": perf["converted"].astype(int),
        "Lost": perf["lost"].astype(int),
        "Conversion rate": [pct(v) if v is not None and not pd.isna(v) else "" for v in perf["conversion_rate"]],
        "Model expected": perf["expected_conversions_closed"].round(1),
        "Forecast (open)": perf["forecast_open_pipeline"].round(1),
    })
    table.insert(1, "Needs attention", ["⚠ Yes" if (o >= 20 or f == 0) else "" for o, f in
                                        zip(table["Overdue %"], table["Follow-ups (7 days)"])])
    st.subheader("Marketers")
    st.dataframe(table, hide_index=True, width="stretch", column_config={
        "Overdue %": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d%%"),
    })
    st.caption("Needs attention = 20% or more of open leads overdue, or no follow-ups in the last 7 days.")

    left, right = st.columns(2)
    with left:
        st.markdown("**Open vs overdue leads**")
        bars = perf.assign(full_name=perf["full_name"].str.split().str[0])[["full_name", "open_leads", "overdue"]].rename(
            columns={"full_name": "Marketer", "open_leads": "Open", "overdue": "Overdue"}).melt(
            "Marketer", var_name="Leads", value_name="Count")
        st.altair_chart(alt.Chart(bars).mark_bar().encode(
            x=alt.X("Marketer:N", title=None, axis=alt.Axis(labelAngle=0)),
            xOffset="Leads:N",
            y=alt.Y("Count:Q", title="Leads"),
            color=alt.Color("Leads:N", scale=alt.Scale(domain=["Open", "Overdue"], range=["#9DB7D5", "#C0392B"]),
                            legend=alt.Legend(orient="bottom", title=None)),
            tooltip=["Marketer", "Leads", "Count"]).properties(height=280), width="stretch")
    with right:
        st.markdown("**Pipeline by stage**")
        funnel = pipeline.stage_counts(user)
        st.altair_chart(alt.Chart(funnel).mark_bar(color="#185FA5").encode(
            y=alt.Y("stage:N", sort=pipeline.STAGES, title=None),
            x=alt.X("leads:Q", title="Leads"),
            tooltip=[alt.Tooltip("stage:N", title="Stage"), alt.Tooltip("leads:Q", title="Leads")])
            .properties(height=280), width="stretch")

    st.divider()
    st.subheader(f"Escalations: overdue by {ESCALATE_AFTER_DAYS}+ days")
    late = pipeline.escalations(user, ESCALATE_AFTER_DAYS)
    if late.empty:
        st.success("No lead has been overdue for that long.")
    else:
        st.caption("These leads have waited too long. Reassigning gives each one to the least-busy other marketer.")
        picked = lead_table(late, "escalations", show_owner=True, height=250)
        if picked:
            lead_panel(user, picked)
        if st.button(f"Reassign all {len(late)} automatically", type="primary"):
            moved = pipeline.redistribute(user, late["lead_id"].astype(int).tolist(), exclude_owners=True)
            flash(f"{moved} escalated lead(s) reassigned to the least-busy marketers.")
            st.rerun()

    st.divider()
    st.subheader("Look at one marketer")
    names = {int(r.user_id): r.full_name for r in perf.itertuples()}
    if not names:
        return
    who = st.selectbox("Marketer", list(names), format_func=names.get)
    row = perf[perf["user_id"] == who].iloc[0]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Open leads", f"{row['open_leads']:.0f}")
    m2.metric("Overdue", f"{row['overdue']:.0f}")
    m3.metric("Follow-ups in last 7 days", f"{row['follow_ups_last_7_days']:.0f}")
    m4.metric("Converted / model expected", f"{row['converted']:.0f} / {row['expected_conversions_closed']:.1f}")
    their_open = pipeline.get_leads(user, stages=pipeline.OPEN_STAGES, marketer_id=who)
    st.markdown(f"**{names[who]}'s open leads** (overdue first)")
    lead_id = lead_table(their_open, f"marketer_{who}", show_owner=False, height=300)
    if lead_id:
        lead_panel(user, lead_id)
    st.markdown(f"**{names[who]}'s follow-ups in the last 14 days**")
    activity = pipeline.recent_activity(user, who, 14)
    if not activity.empty:
        activity["next_follow_up_date"] = pd.to_datetime(activity["next_follow_up_date"])
    if activity.empty:
        st.warning("No follow-ups logged in the last 14 days.")
    else:
        st.dataframe(activity.drop(columns=["lead_id", "marketer"]).rename(columns={
            "follow_up_date": "When", "prospect_id": "Prospect ID", "follow_up_type": "Type", "outcome": "Outcome",
            "notes": "Notes", "next_follow_up_date": "Next follow-up", "source": "Source"}),
            hide_index=True, width="stretch", height=300,
            column_config={"When": st.column_config.DatetimeColumn(format="DD MMM YYYY, HH:mm"),
                           "Next follow-up": st.column_config.DateColumn(format="DD MMM YYYY")})


def users_page():
    user = current_user()
    admin = user["role"] == "admin"
    st.title("Accounts")
    show_flash()
    users = pipeline.pending_users(user)
    pending = users[(users["is_active"] == 0) & (users["role"] == "marketer")]
    st.subheader("Waiting for approval")
    if pending.empty:
        st.caption("No accounts are waiting.")
    for r in pending.itertuples():
        a, b, c = st.columns([3, 3, 1], vertical_alignment="center")
        a.write(f"**{r.full_name}**  \n{r.email}")
        b.caption(f"Registered {r.created_at:%d %b %Y %H:%M}")
        if c.button("Approve", key=f"approve_{r.user_id}", type="primary"):
            pipeline.set_user_active(user, int(r.user_id), True)
            flash(f"{r.full_name} can now sign in.")
            st.rerun()

    st.subheader("All accounts")
    st.dataframe(pd.DataFrame({
        "Name": users["full_name"], "Email": users["email"], "Role": users["role"].map(ROLE_LABEL),
        "Status": users["is_active"].map({1: "Active", 0: "Inactive"}), "Created": users["created_at"],
    }), hide_index=True, width="stretch")

    st.subheader("Change an account")
    editable = users[users["user_id"] != user["user_id"]]
    if not admin:
        editable = editable[editable["role"] == "marketer"]
    labels = {int(r.user_id): f"{r.full_name or r.email} – {ROLE_LABEL[r.role]}" for r in editable.itertuples()}
    if not labels:
        return
    target = st.selectbox("Account", list(labels), format_func=labels.get)
    row = users[users["user_id"] == target].iloc[0]
    a, b = st.columns(2)
    if row["is_active"]:
        if a.button("Deactivate", help="They can no longer sign in. Their open leads move to other marketers."):
            moved = pipeline.set_user_active(user, target, False)
            flash(f"Account deactivated. {moved} open lead(s) were shared out to other marketers.")
            st.rerun()
    elif a.button("Activate"):
        pipeline.set_user_active(user, target, True)
        flash("Account activated.")
        st.rerun()
    if admin:
        with b.popover("Admin options"):
            role = st.selectbox("Role", list(ROLE_LABEL), index=list(ROLE_LABEL).index(row["role"]),
                                format_func=ROLE_LABEL.get)
            if st.button("Save role"):
                moved = pipeline.set_user_role(user, target, role)
                flash(f"Role changed to {ROLE_LABEL[role]}." + (f" {moved} open lead(s) moved." if moved else ""))
                st.rerun()
            temp = st.text_input("Temporary password", type="password")
            if st.button("Reset password"):
                try:
                    auth.admin_reset_password(user, target, temp)
                except auth.AuthError as e:
                    st.error(str(e))
                else:
                    flash("Password reset and account unlocked. Ask them to change it after signing in.")
                    st.rerun()


def account_page():
    user = current_user()
    st.title("My account")
    show_flash()
    st.write(f"**{user['full_name']}**  \n{user['email']}  \n{ROLE_LABEL[user['role']]}")
    st.subheader("Change password")
    with st.form("change_password", clear_on_submit=True):
        current = st.text_input("Current password", type="password")
        new = st.text_input("New password", type="password", help="At least 8 characters, with a letter and a number.")
        confirm = st.text_input("Confirm new password", type="password")
        if st.form_submit_button("Change password", type="primary"):
            try:
                auth.change_password(user, current, new, confirm)
            except auth.AuthError as e:
                st.error(str(e))
            else:
                st.success("Password changed.")


# ------------------------------------------------------------------ navigation
def main():
    user = check_session()
    if not user and google_signed_in() and "google_problem" not in st.session_state:
        try:
            user = auth.external_sign_in(st.user.email, st.user.get("name"), bool(st.user.get("email_verified")))
        except auth.AuthError as e:
            st.session_state["google_problem"] = str(e)
        else:
            start_session(user)
    if not user:
        page = st.navigation([st.Page(login_page, title="Sign in", icon=":material/login:")], position="hidden")
    else:
        st.logo("assets/salesmesh_logo.svg", size="large")
        with st.sidebar.container():
            st.divider()
            st.html(f'<div class="side-user"><b>{html.escape(user["full_name"])}</b><br>'
                    f'<span class="role">{ROLE_LABEL[user["role"]]}</span></div>')
            if st.button("Sign out", icon=":material/logout:"):
                sign_out()
            st.caption(f"For security you are signed out after {config.SESSION_TIMEOUT_MINUTES} minutes "
                       "without activity.")
        account = st.Page(account_page, title="My account", icon=":material/person:")
        add = st.Page(add_leads_page, title="Add leads", icon=":material/person_add:")
        if user["role"] == "marketer":
            pages = [st.Page(today_page, title="Today", icon=":material/call:", default=True),
                     st.Page(leads_page, title="My leads", icon=":material/list:"), add, account]
        else:
            pages = [st.Page(team_page, title="Team", icon=":material/groups:", default=True),
                     st.Page(leads_page, title="All leads", icon=":material/list:"), add,
                     st.Page(users_page, title="Accounts", icon=":material/manage_accounts:"), account]
        page = st.navigation(pages)
    try:
        page.run()
    except mysql.connector.Error as e:
        st.error("The app cannot reach the database. Check that MySQL is running and that DB_PASSWORD in "
                 f"app/.env is correct.\n\nDetails: {e.msg}")
    except PermissionError as e:
        st.error(str(e))


main()
