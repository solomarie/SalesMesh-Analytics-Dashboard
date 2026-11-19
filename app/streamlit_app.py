"""
SalesMesh lead-management app.

Start it from the app folder:   streamlit run streamlit_app.py

Marketers: today's call list, their own leads, logging follow-ups, adding leads.
Marketing Manager: team performance, each marketer's activity, escalations,
handovers and invitations. Admin: everything a manager can do plus roles and
password resets. Everyone: notifications, a timeline for every lead, handing a
lead over, asking for a colleague's lead and reopening a lost lead that comes back.
"""
import html
from urllib.parse import urlparse
from datetime import date, datetime, timedelta

import altair as alt
import mysql.connector
import pandas as pd
import streamlit as st

from salesmesh import auth, config, notify, pipeline, scoring, ui
from salesmesh.scoring import FEATURE_INPUT_COLS, FRIENDLY_NAMES, NUMERIC_COLS

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


def site_url():
    """The address this person used to open the app: localhost, or the public Cloudflare address."""
    try:
        parts = urlparse(st.context.url or "")
    except Exception:
        return None
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else None


def google_configured():
    """Google sign-in is set up when app/.streamlit/secrets.toml has an [auth.google] section."""
    try:
        return "google" in st.secrets.get("auth", {})
    except Exception:
        return False


def google_enabled():
    """
    Offer Google only on the address registered with Google (redirect_uri in secrets.toml).
    On another address, such as a temporary Cloudflare one, Google would send people back to the
    wrong place, so they sign in with email and password there instead.
    """
    if not google_configured():
        return False
    here = site_url()
    try:
        registered = urlparse(st.secrets["auth"]["redirect_uri"]).netloc
    except Exception:
        return True
    return here is None or urlparse(here).netloc == registered


def google_signed_in():
    return google_configured() and bool(getattr(st.user, "is_logged_in", False))


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


def show_flash(as_toast=False):
    if "flash" in st.session_state:
        kind, message = st.session_state.pop("flash")
        if as_toast:  # a pop-up in the corner: takes no space on the sign-in card
            icons = {"success": ":material/check_circle:", "info": ":material/info:",
                     "warning": ":material/warning:", "error": ":material/error:"}
            st.toast(message, icon=icons.get(kind))
        else:
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
    """
    Show leads. Clicking a row opens that lead in a pop-up window over the list, so a long list
    never has to be scrolled past to see one lead's details.
    """
    if df.empty:
        st.info("No leads to show.")
        return
    df = df.reset_index(drop=True)
    view = leads_view(df, show_owner)
    # The key changes whenever the rows change, so an old selection never opens the wrong lead
    widget_key = f"{key}_{st.session_state.get('table_version', 0)}_{hash(tuple(df['lead_id']))}"
    # Long lists scroll inside the table, so the page itself never gets longer than the screen
    extra = {"height": min(height, 38 + 35 * len(df) + 2)} if height else {}
    event = st.dataframe(
        view, key=widget_key, on_select="rerun", selection_mode="single-row", hide_index=True, width="stretch",
        **extra,
        column_config={
            "Score": st.column_config.ProgressColumn("Score", help="Chance of converting, from the model",
                                                     min_value=0, max_value=100, format="%d%%"),
            "Next action due": st.column_config.DateColumn("Next action due", format="DD MMM YYYY"),
        })
    rows = event.selection.rows
    picked = int(df.iloc[rows[0]]["lead_id"]) if rows else None
    opened = st.session_state.setdefault("opened_rows", {})
    a, b = st.columns([1, 1])
    with a:
        if picked:
            pid = df.iloc[rows[0]]["prospect_id"]
            if st.button(f"Open lead {pid}", icon=":material/open_in_new:", key=f"open_{widget_key}"):
                open_lead(picked)
        else:
            st.caption("Click a row to open the lead.")
    b.download_button("Download this list (CSV)", view.to_csv(index=False).encode("utf-8"),
                      file_name=f"salesmesh_{key}_{date.today():%Y%m%d}.csv", mime="text/csv",
                      key=f"dl_{key}", width="stretch")
    if picked != opened.get(key):  # a new row was clicked: open it once
        opened[key] = picked
        if picked:
            open_lead(picked)


def reasons_list(text):
    return [r for r in (text or "").split(" | ") if r]


def can_work_on(user, lead):
    return user["role"] in ("manager", "admin") or lead["assigned_to"] == user["user_id"]


def done(message):
    """An action in the pop-up succeeded: close it, show the message and refresh the lists."""
    flash(message)
    st.rerun()


# ------------------------------------------------------------------ lead pop-up
def open_lead(lead_id):
    user = current_user()
    try:
        lead = pipeline.get_lead(user, lead_id)
    except (PermissionError, LookupError) as e:
        st.toast(str(e), icon=":material/lock:")
        return
    st.dialog(f"Lead {lead['prospect_id']}", width="large")(lead_dialog)(lead_id)


def lead_dialog(lead_id):
    user = current_user()
    try:
        lead = pipeline.get_lead(user, lead_id)
    except (PermissionError, LookupError) as e:
        st.error(str(e))
        return
    today = date.today()
    stage = lead["stage"]
    closed = stage in pipeline.CLOSED_STAGES
    mine = can_work_on(user, lead)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Conversion score", pct(lead["conversion_probability"]))
    c2.metric("Priority tier", TIER_LABEL.get(lead["priority_tier"], lead["priority_tier"]))
    c3.metric("Stage", stage)
    c4.metric("Owner", lead["assigned_name"] or "Unassigned")
    if closed:
        note = f" Reason: {lead['lost_reason']}." if lead["lost_reason"] else ""
        closed_on = lead["closed_at"].strftime("%d %b %Y") if lead["closed_at"] else ""
        st.info(f"Closed as **{stage}** on {closed_on}.{note}"
                + (" If the customer comes back, use **Customer came back**." if stage == "Lost" and mine else ""))
    elif lead["is_overdue"]:
        days = (today - lead["due_date"]).days
        st.error(f"Overdue: the next action was due on {lead['due_date']:%d %b %Y} ({days} day(s) ago).")
    elif lead["due_date"] == today:
        st.warning("Due today.")
    elif lead["due_date"]:
        st.info(f"Next action due on {lead['due_date']:%d %b %Y}.")

    waiting = pipeline.requests_to_decide(user)
    waiting = waiting[waiting["lead_id"] == lead_id] if not waiting.empty else waiting
    for r in waiting.itertuples():
        request_card(user, r, key=f"dlg_{r.request_id}")

    names = ["Overview", "Activity"]
    if mine and not closed:
        names.append("Log follow-up")
    if mine and stage == "Lost":
        names.append("Customer came back")
    if mine and stage != "Converted":
        names.append("Hand over")
    tabs = dict(zip(names, st.tabs(names)))

    with tabs["Overview"]:
        left, right = st.columns(2)
        with left:
            st.markdown("**Why this score**")
            reasons = reasons_list(lead["top_reasons"])
            if reasons:
                st.markdown("\n".join(f"- {r}" for r in reasons))
            else:
                st.caption("No single input changes this score much; it is close to a typical lead.")
            spoke = pipeline.previous_handlers(lead_id)
            if spoke:
                st.markdown("**Has spoken to this customer**")
                st.markdown("\n".join(
                    f"- {h['full_name']}" + ("" if h["is_active"] else " (no longer active)")
                    + f" · last contact {h['last_contact']:%d %b %Y}" for h in spoke))
        with right:
            st.markdown("**Lead details**")
            st.dataframe(pd.DataFrame({
                "Detail": ["Lead source", "Lead origin", "Occupation", "City", "Website visits",
                           "Time on website", "Last activity", "Added on"],
                "Value": [lead["lead_source"], lead["lead_origin"], lead["occupation"], lead["city"] or "Not provided",
                          str(lead["total_visits"]), str(lead["time_on_website"]), lead["last_activity"],
                          lead["created_at"].strftime("%d %b %Y") if lead["created_at"] else "–"],
            }), hide_index=True, width="stretch")

    with tabs["Activity"]:
        activity_timeline(user, lead_id)

    if "Log follow-up" in tabs:
        with tabs["Log follow-up"]:
            log_follow_up_form(user, lead)
    if "Customer came back" in tabs:
        with tabs["Customer came back"]:
            reopen_form(user, lead)
    if "Hand over" in tabs:
        with tabs["Hand over"]:
            handover_form(user, lead)


def activity_timeline(user, lead_id):
    items = pipeline.timeline(user, lead_id)
    if not items:
        st.caption("Nothing has happened to this lead yet.")
        return
    parts = []
    for i in items:
        who = f" · {html.escape(i['who'])}" if i["who"] else ""
        note = f'<div class="tl-note">{html.escape(i["note"])}</div>' if i["note"] else ""
        cls = "tl-item fu" if i["kind"] == "follow_up" else "tl-item"
        parts.append(f'<div class="{cls}"><div class="tl-when">{i["when"]:%d %b %Y, %H:%M}{who}</div>'
                     f'<div class="tl-what">{html.escape(i["what"])}</div>{note}</div>')
    box = st.container(height=360, border=False) if len(items) > 6 else st.container()
    with box:
        st.html("".join(parts))
    st.caption("Blue dots are changes of owner or stage; light dots are contacts with the customer.")


def log_follow_up_form(user, lead):
    """The fields shown depend on the outcome: closing a lead needs no next date, only Lost needs a reason."""
    k = f"{lead['lead_id']}_{st.session_state.get('table_version', 0)}"
    a, b = st.columns(2)
    kind = a.selectbox("How did you contact them?", pipeline.FOLLOW_UP_TYPES, key=f"fu_type_{k}")
    outcome = b.selectbox("Outcome", list(pipeline.OUTCOMES), key=f"fu_outcome_{k}",
                          help="No answer / Needs follow-up → Contacted. Interested → Qualified. "
                               "Converted or Lost closes the lead.")
    next_date, lost_reason = None, None
    if outcome == "Converted":
        st.success("This closes the lead as **Converted** (became a customer). No further follow-up is needed.")
    elif outcome == "Lost":
        st.warning("This closes the lead as **Lost**. If the customer comes back later, it can be reopened.")
        lost_reason = st.selectbox("Why was it lost?", pipeline.LOST_REASONS, index=None,
                                   placeholder="Choose a reason", key=f"fu_reason_{k}")
    else:
        next_date = st.date_input("Next follow-up date", value=date.today() + timedelta(days=2),
                                  min_value=date.today(), format="DD/MM/YYYY", key=f"fu_date_{k}",
                                  help="The lead comes back on the Today list on this date.")
    notes = st.text_area("Notes", max_chars=500, placeholder="What was discussed, what was agreed",
                         key=f"fu_notes_{k}", height=90)
    label = {"Converted": "Close as Converted", "Lost": "Close as Lost"}.get(outcome, "Save follow-up")
    if st.button(label, type="primary", key=f"fu_save_{k}"):
        try:
            stage = pipeline.log_follow_up(user, lead["lead_id"], kind, outcome, notes, next_date, lost_reason)
        except (ValueError, PermissionError, LookupError) as e:
            st.error(str(e))
        else:
            done(f"Follow-up saved. Lead {lead['prospect_id']} is now **{stage}**.")


def reopen_form(user, lead):
    """FR-04: a Lost customer calls back or replies; reopen the lead and record the contact."""
    k = f"ro_{lead['lead_id']}"
    st.caption("Use this when a lost customer gets back in touch. The lead reopens with the marketer who "
               "handled it before (if they are still on the team) and goes back on the call list.")
    a, b = st.columns(2)
    kind = a.selectbox("How did they get in touch?", pipeline.FOLLOW_UP_TYPES, key=f"{k}_type")
    outcome = b.selectbox("How did it go?", pipeline.REOPEN_OUTCOMES, key=f"{k}_out",
                          help="Interested → Qualified. Needs follow-up → Contacted.")
    next_date = st.date_input("Next follow-up date", value=date.today() + timedelta(days=1),
                              min_value=date.today(), format="DD/MM/YYYY", key=f"{k}_date")
    notes = st.text_area("What did they say?", max_chars=500, key=f"{k}_notes", height=80,
                         placeholder="For example: budget approved, wants a call next week")
    if st.button("Reopen lead", type="primary", key=f"{k}_go", icon=":material/restart_alt:"):
        try:
            stage = pipeline.reopen_lead(user, lead["lead_id"], kind, outcome, notes, next_date)
        except (ValueError, PermissionError, LookupError) as e:
            st.error(str(e))
        else:
            done(f"Lead {lead['prospect_id']} reopened. It is now **{stage}** and due on {next_date:%d %b %Y}.")


def handover_form(user, lead):
    """FR-05: give the lead to a named colleague, for example because the customer asked for them."""
    k = f"ho_{lead['lead_id']}"
    marketers = pipeline.active_marketers()
    others = marketers[marketers["user_id"] != lead["assigned_to"]] if not marketers.empty else marketers
    if others.empty:
        st.caption("There is no other active marketer to hand this lead to.")
        return
    spoke = {h["user_id"]: h["last_contact"] for h in pipeline.previous_handlers(lead["lead_id"])}
    ids = sorted((int(r.user_id) for r in others.itertuples()), key=lambda u: (u not in spoke, u))
    labels = {}
    for r in others.itertuples():
        extra = f" · spoke to them on {spoke[r.user_id]:%d %b}" if r.user_id in spoke else ""
        labels[int(r.user_id)] = f"{r.full_name} ({int(r.open_leads)} open leads){extra}"
    st.caption("Marketers who have already spoken to this customer are listed first.")
    target = st.selectbox("Hand over to", ids, format_func=labels.get, key=f"{k}_to")
    reason = st.text_input("Reason", max_chars=300, key=f"{k}_why",
                           placeholder="For example: customer asked for Jane / I'm on leave next week")
    if st.button("Hand over", type="primary", key=f"{k}_go", icon=":material/swap_horiz:"):
        try:
            pipeline.handover_lead(user, lead["lead_id"], target, reason)
        except (ValueError, PermissionError, LookupError) as e:
            st.error(str(e))
        else:
            done(f"Lead {lead['prospect_id']} handed over to {labels[target].split(' (')[0]}. They have been notified.")


def request_card(user, r, key):
    """A colleague has asked for a lead: the owner (or a manager) accepts or declines."""
    with st.container(border=True):
        st.markdown(f"**{html.escape(r.requested_by_name)}** asks for lead **{r.prospect_id}** "
                    f"({r.stage}, owned by {r.owner_name or 'nobody'})  \n“{html.escape(r.reason)}”")
        st.caption(f"Asked on {r.created_at:%d %b %Y, %H:%M}")
        note = st.text_input("Note (optional, sent if you decline)", key=f"{key}_note", max_chars=300)
        a, b, _ = st.columns([1, 1, 2])
        if a.button("Accept", type="primary", key=f"{key}_yes", icon=":material/check:"):
            decide(user, r, True, note)
        if b.button("Decline", key=f"{key}_no", icon=":material/close:"):
            decide(user, r, False, note)


def decide(user, r, accept, note):
    try:
        status = pipeline.decide_request(user, int(r.request_id), accept, note)
    except (ValueError, PermissionError, LookupError) as e:
        st.error(str(e))
    else:
        done(f"Lead {r.prospect_id} handed over to {r.requested_by_name}." if status == "accepted"
             else f"Request declined. {r.requested_by_name} has been told.")


# ------------------------------------------------------------------ pages
def login_page():
    reset_token, invite_token = st.query_params.get("reset"), st.query_params.get("invite")
    brand, form = st.columns([1.05, 1], gap="large", vertical_alignment="top")
    with brand:
        with st.container(key="brand"):
            st.html(ui.BRAND_PANEL)
    with form:
        with st.container(key="signin"):
            if reset_token:
                reset_view(reset_token)
            elif invite_token:
                invite_view(invite_token)
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
    """
    Everything fits on a small laptop screen without scrolling: one-click Google at the top
    (as in Gmail or Notion), then email and password. Messages appear as pop-ups, not boxes.
    """
    heading("Sign in", "Welcome back. Sign in to see today's calls.")
    show_flash(as_toast=True)
    if google_signed_in() and st.session_state.get("google_problem"):
        st.warning(f"Signed in with Google as **{st.user.email}**. {st.session_state['google_problem']}")
    if google_enabled():
        google_block()
        st.html('<div class="or-rule">or sign in with email</div>')
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
    st.button("Forgot password?", type="tertiary", on_click=go_to, args=("forgot",), key="forgot_link")
    if google_configured() and not google_enabled():
        st.caption("Continue with Google works on the main SalesMesh address. Here, use your email and password.")
    st.caption("New to SalesMesh? Ask your Marketing Manager to send you an invitation.")


def invite_view(token):
    """Someone opened the link in their invitation email: they set up their own account."""
    try:
        invite = auth.check_invitation(token)
    except auth.AuthError as e:
        heading("This invitation can't be used", "Invitation links work once and expire after "
                f"{config.INVITE_DAYS} days.")
        st.error(str(e))
        st.button("Go to sign in", type="primary", width="stretch", on_click=go_to, args=("signin",))
        return
    heading("Join SalesMesh", f"You've been invited as a <b>{ROLE_LABEL[invite['role']]}</b> "
                              f"with {html.escape(invite['email'])}.")
    with st.form("accept_invite", border=False):
        name = st.text_input("Full name", value=invite["full_name"] or "")
        phone = st.text_input("Phone (optional)")
        a, b = st.columns(2)
        pw1 = a.text_input("Password", type="password", help="At least 8 characters, with a letter and a number.")
        pw2 = b.text_input("Confirm password", type="password")
        created = st.form_submit_button("Create my account", type="primary", width="stretch")
    if created:
        try:
            user = auth.accept_invitation(token, name, phone, pw1, pw2)
        except auth.AuthError as e:
            st.error(str(e))
        else:
            st.query_params.clear()
            start_session(user)
            flash(f"Welcome to SalesMesh, {user['full_name']}!")
            st.rerun()
    if google_enabled():
        st.html('<div class="or-rule">or</div>')
        st.caption(f"Use Continue with Google with **{html.escape(invite['email'])}** and you won't need a password.")
        google_block()


def forgot_view():
    heading("Reset your password",
            "Enter the email you use for SalesMesh. We'll send you a link to choose a new password.")
    with st.form("forgot", border=False):
        email = st.text_input("Email", placeholder="you@company.com")
        sent = st.form_submit_button("Send reset link", type="primary", width="stretch")
    if sent:
        try:
            st.success(auth.request_password_reset(email, base_url=site_url()))
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
    """Continue with Google, or let someone who picked the wrong Google account try another."""
    if not google_signed_in():
        if st.button("Continue with Google", key="google", width="stretch"):
            st.login("google")
        return
    if st.button("Use a different Google account", key="google", width="stretch"):
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
    waiting = pipeline.requests_to_decide(user)
    if not waiting.empty:
        st.info(f"{len(waiting)} colleague request(s) for your leads are waiting for your answer.",
                icon=":material/swap_horiz:")
        st.page_link(NOTIFICATIONS_PAGE, label="Answer them in Notifications", icon=":material/arrow_forward:")
    if calls.empty:
        st.success("Nothing is due today. Well done! Check **My leads** for upcoming follow-ups.")
        return
    lead_table(calls, "calls", show_owner=False, height=420)


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
    st.caption(f"{len(df)} lead(s). Click a row to open it in a pop-up window. "
               "Tick **Lost** under Stage to find a customer who has come back.")
    lead_table(df, "leads", show_owner=manager, height=420)


# The form asks only for the details that drive the score. They start empty, so a lead is never saved
# with made-up answers. The six optional details (scoring.OPTIONAL_COLS) are saved as unknown.
KEY_FIELDS = ["Lead Origin", "Lead Source", "What is your current occupation", "TotalVisits",
              "Total Time Spent on Website", "Page Views Per Visit", "Last Activity", "Last Notable Activity"]
FIELD_HELP = {
    "Total Time Spent on Website": "From the website tracker. A typical lead has about 250; serious leads often have 700 or more.",
    "TotalVisits": "How many times they visited the website.",
    "Page Views Per Visit": "Average pages looked at per visit.",
    "Last Activity": "The last thing they did, for example opened an email or replied by SMS.",
    "Lead Source": "Where the lead came from. 'Reference' means an existing customer referred them.",
}
# Example leads for demonstrations (scores from the model: about 99%, 53% and 4%)
EXAMPLES = {
    "Hot lead": {"Lead Origin": "Lead Add Form", "Lead Source": "Reference",
                 "What is your current occupation": "Working Professional", "TotalVisits": 4.0,
                 "Total Time Spent on Website": 1100.0, "Page Views Per Visit": 3.0,
                 "Last Activity": "SMS Sent", "Last Notable Activity": "SMS Sent"},
    "Warm lead": {"Lead Origin": "Landing Page Submission", "Lead Source": "Direct Traffic",
                  "What is your current occupation": "Unemployed", "TotalVisits": 5.0,
                  "Total Time Spent on Website": 700.0, "Page Views Per Visit": 3.0,
                  "Last Activity": "Email Opened", "Last Notable Activity": "Email Opened"},
    "Cold lead": {"Lead Origin": "Landing Page Submission", "Lead Source": "Google",
                  "What is your current occupation": "Unemployed", "TotalVisits": 2.0,
                  "Total Time Spent on Website": 120.0, "Page Views Per Visit": 2.0,
                  "Last Activity": "Olark Chat Conversation", "Last Notable Activity": "Modified"},
}


def _fill(values):
    for col, v in values.items():
        st.session_state[f"new_{col}"] = v


def _clear_lead_form():
    """Runs before the form is drawn (widgets cannot be reset after they are on the page)."""
    for col in FEATURE_INPUT_COLS:
        st.session_state.pop(f"new_{col}", None)
    st.session_state["new_pid"] = f"APP-{datetime.now():%Y%m%d-%H%M%S}"
    st.session_state.pop("clear_lead_form", None)


def add_leads_page():
    user = current_user()
    st.title("Add leads")
    st.caption("Each new lead is checked, scored by the model straight away, explained, and given to the "
               "marketer with the fewest open leads.")
    show_flash()
    reference = scoring.load_reference()
    allowed = reference["allowed_values"]
    one, upload = st.tabs(["Enter one lead", "Upload a CSV file"])

    with one:
        enter_one_lead(user, allowed)

    with upload:
        rows = [{"Prospect ID": f"NEW-{name.split()[0].upper()}-001", **values} for name, values in EXAMPLES.items()]
        template = pd.DataFrame(rows)[["Prospect ID"] + KEY_FIELDS]
        st.caption("The file needs a Prospect ID and the eight details in the template. "
                   "Columns for city, country, specialisation, how they heard about us, do-not-email and "
                   "do-not-call may be added; blanks are saved as not provided.")
        st.download_button("Download the CSV template (3 example rows)", template.to_csv(index=False).encode("utf-8"),
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


def lead_input(box, col, allowed, default=None):
    key = f"new_{col}"
    if key not in st.session_state:
        st.session_state[key] = default
    if col in NUMERIC_COLS:
        return box.number_input(FRIENDLY_NAMES[col], min_value=0.0, step=1.0, format="%g", key=key,
                                placeholder="Enter a number", help=FIELD_HELP.get(col))
    options = [v for v in allowed[col] if v != "Select"]
    return box.selectbox(FRIENDLY_NAMES[col], options, key=key, placeholder="Choose", help=FIELD_HELP.get(col))


def enter_one_lead(user, allowed):
    """FR-02: the score and its reasons update live while the lead's details are filled in."""
    if st.session_state.get("clear_lead_form"):
        _clear_lead_form()
    if "new_pid" not in st.session_state:
        st.session_state["new_pid"] = f"APP-{datetime.now():%Y%m%d-%H%M%S}"
    pid = st.columns([1.6, 1])[0].text_input("Prospect ID", key="new_pid")

    form, preview = st.columns([1.6, 1], gap="large")
    with form:
        values = {}
        cols = st.columns(2)
        for i, col in enumerate(KEY_FIELDS):
            values[col] = lead_input(cols[i % 2], col, allowed)
        for col in scoring.OPTIONAL_COLS:
            values[col] = scoring.NOT_PROVIDED
        with st.expander("Example leads"):
            st.caption("Fill the form with a typical High, Medium or Low priority lead, for example to show "
                       "how the score changes.")
            labels = {"Hot lead": "High priority", "Warm lead": "Medium priority",
                      "Cold lead": "Low priority"}
            for box, name in zip(st.columns(3), EXAMPLES):
                box.button(labels[name], on_click=_fill, args=(EXAMPLES[name],), key=f"example_{name}",
                           width="stretch")
    with preview:
        missing = [FRIENDLY_NAMES[c] for c in KEY_FIELDS if values[c] is None or values[c] == ""]
        with st.container(border=True):
            st.markdown("**Score preview**")
            if missing:
                st.caption("The score appears here as you fill in the lead. Still needed: " + ", ".join(missing) + ".")
                return
            batch = pd.DataFrame([{"Prospect ID": pid, **values}]).astype(str)
            valid, rejected = scoring.validate(batch, pipeline.existing_prospect_ids(), scoring.load_reference())
            if valid.empty:
                st.error(rejected["Reason"].iloc[0])
                return
            scored = scoring.score_leads(valid).iloc[0]
            tier = scored["priority_tier"]
            st.html(f'<div class="score-big">{pct(scored["conversion_probability"])}'
                    f'<span class="score-of">chance of converting</span></div>'
                    f'<span class="tier-pill tier-{tier.lower()}">{tier} priority</span>')
            st.caption(f"First contact due within {pipeline.FIRST_CONTACT_DAYS[tier]} day(s). "
                       "High ≥ 70%, Medium 40–69%, Low under 40%.")
            reasons = reasons_list(scored["top_reasons"])
            st.markdown("**Why**")
            st.markdown("\n".join(f"- {r}" for r in reasons) if reasons
                        else "- Close to a typical lead: nothing stands out.")
            if st.button("Save and assign lead", type="primary", width="stretch"):
                try:
                    saved, rejected = pipeline.add_leads(user, pd.DataFrame([{"Prospect ID": pid, **values}]))
                except (scoring.InputFileError, ValueError) as e:
                    st.error(str(e))
                    return
                if saved.empty:
                    st.error(rejected["Reason"].iloc[0])
                    return
                r = saved.iloc[0]
                st.session_state["clear_lead_form"] = True
                flash(f"Lead {r['Prospect ID']} saved: {pct(r['conversion_probability'])} chance, "
                      f"{r['priority_tier']} priority, assigned to {r['assigned_to']}.")
                st.rerun()


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
    c3.caption(f"Model expected {expected:.0f}")
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
    late = pipeline.escalations(user, ESCALATE_AFTER_DAYS)
    attention = int((table["Needs attention"] != "").sum())
    marketers_tab, charts_tab, tiers_tab, escalations_tab, one_tab = st.tabs([
        "Marketers", "Pipeline", "Priority tiers", f"Escalations ({len(late)})", "One marketer"])

    with marketers_tab:
        if attention:
            st.warning(f"{attention} marketer(s) need attention.", icon=":material/warning:")
        st.dataframe(table, hide_index=True, width="stretch", column_config={
            "Overdue %": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d%%"),
        })
        st.caption("Needs attention = 20% or more of open leads overdue, or no follow-ups in the last 7 days.")

    with charts_tab:
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
                tooltip=["Marketer", "Leads", "Count"]).properties(height=220), width="stretch")
        with right:
            st.markdown("**Pipeline by stage**")
            funnel = pipeline.stage_counts(user)
            st.altair_chart(alt.Chart(funnel).mark_bar(color="#185FA5").encode(
                y=alt.Y("stage:N", sort=pipeline.STAGES, title=None),
                x=alt.X("leads:Q", title="Leads"),
                tooltip=[alt.Tooltip("stage:N", title="Stage"), alt.Tooltip("leads:Q", title="Leads")])
                .properties(height=220), width="stretch")

    with tiers_tab:
        tier_charts(user)

    with escalations_tab:
        if late.empty:
            st.success(f"No lead has been overdue for {ESCALATE_AFTER_DAYS} days or more.")
        else:
            st.caption(f"These leads have been overdue for {ESCALATE_AFTER_DAYS} days or more. Reassigning gives each "
                       "one to the least-busy other marketer.")
            lead_table(late, "escalations", show_owner=True, height=230)
            if st.button(f"Reassign all {len(late)} automatically", type="primary"):
                moved = pipeline.redistribute(user, late["lead_id"].astype(int).tolist(), exclude_owners=True)
                flash(f"{moved} escalated lead(s) reassigned to the least-busy marketers.")
                st.rerun()

    with one_tab:
        one_marketer(user, perf)


def one_marketer(user, perf):
    """Drill-down: one marketer's figures, open leads and recent follow-ups."""
    names = {int(r.user_id): r.full_name for r in perf.itertuples()}
    if not names:
        st.caption("There are no active marketers.")
        return
    who = st.selectbox("Marketer", list(names), format_func=names.get)
    row = perf[perf["user_id"] == who].iloc[0]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Open leads", f"{row['open_leads']:.0f}")
    m2.metric("Overdue", f"{row['overdue']:.0f}")
    m3.metric("Follow-ups in last 7 days", f"{row['follow_ups_last_7_days']:.0f}")
    m4.metric("Converted / model expected", f"{row['converted']:.0f} / {row['expected_conversions_closed']:.1f}")
    leads_view_tab, activity_tab = st.tabs(["Open leads", "Follow-ups in the last 14 days"])
    with leads_view_tab:
        their_open = pipeline.get_leads(user, stages=pipeline.OPEN_STAGES, marketer_id=who)
        lead_table(their_open, f"marketer_{who}", show_owner=False, height=220)
    with activity_tab:
        activity = pipeline.recent_activity(user, who, 14)
        if activity.empty:
            st.warning("No follow-ups logged in the last 14 days.")
            return
        activity["next_follow_up_date"] = pd.to_datetime(activity["next_follow_up_date"])
        st.dataframe(activity.drop(columns=["lead_id", "marketer"]).rename(columns={
            "follow_up_date": "When", "prospect_id": "Prospect ID", "follow_up_type": "Type", "outcome": "Outcome",
            "notes": "Notes", "next_follow_up_date": "Next follow-up", "source": "Source"}),
            hide_index=True, width="stretch", height=220,
            column_config={"When": st.column_config.DatetimeColumn(format="DD MMM YYYY, HH:mm"),
                           "Next follow-up": st.column_config.DateColumn(format="DD MMM YYYY")})


TIER_COLOURS = alt.Scale(domain=["High", "Medium", "Low"], range=["#2E8B57", "#D9902F", "#8C9BAB"])


def tier_charts(user):
    """How the pipeline splits by priority tier, and whether higher tiers really convert more often."""
    leads = pipeline.get_leads(user)
    if leads.empty:
        return
    left, right = st.columns(2)
    with left:
        st.markdown("**Leads by priority tier**")
        counts = (leads.assign(Status=leads["stage"].map(
                      lambda s: "Open" if s in pipeline.OPEN_STAGES else "Closed"))
                  .groupby(["priority_tier", "Status"]).size().reset_index(name="Leads"))
        st.altair_chart(alt.Chart(counts).mark_bar().encode(
            y=alt.Y("priority_tier:N", sort=["High", "Medium", "Low"], title=None),
            x=alt.X("Leads:Q", title="Leads", stack=True),
            color=alt.Color("priority_tier:N", scale=TIER_COLOURS, legend=None),
            opacity=alt.Opacity("Status:N", scale=alt.Scale(domain=["Open", "Closed"], range=[1, 0.45]),
                                legend=alt.Legend(orient="bottom", title=None)),
            tooltip=[alt.Tooltip("priority_tier:N", title="Tier"), "Status", "Leads"]
        ).properties(height=200), width="stretch")
    with right:
        st.markdown("**Conversion rate by priority tier (closed leads)**")
        closed = leads[leads["stage"].isin(pipeline.CLOSED_STAGES)]
        if closed.empty:
            st.caption("No leads have closed yet.")
            return
        rates = (closed.assign(converted=closed["stage"].eq("Converted"))
                 .groupby("priority_tier")["converted"].agg(["mean", "size"]).reset_index())
        rates["Conversion rate"] = (rates["mean"] * 100).round(1)
        st.altair_chart(alt.Chart(rates).mark_bar().encode(
            y=alt.Y("priority_tier:N", sort=["High", "Medium", "Low"], title=None),
            x=alt.X("Conversion rate:Q", title="Converted (%)", scale=alt.Scale(domain=[0, 100])),
            color=alt.Color("priority_tier:N", scale=TIER_COLOURS, legend=None),
            tooltip=[alt.Tooltip("priority_tier:N", title="Tier"),
                     alt.Tooltip("Conversion rate:Q", title="Converted (%)"),
                     alt.Tooltip("size:Q", title="Closed leads")]
        ).properties(height=220), width="stretch")
        st.caption("If the model works, High priority leads should convert most often and Low least often.")


def users_page():
    user = current_user()
    st.title("Accounts")
    show_flash()
    invite, invitations, accounts = st.tabs(["Invite people", "Invitations sent", "Accounts"])
    with invite:
        invite_tab(user)
    with invitations:
        invitations_tab(user)
    with accounts:
        accounts_tab(user)


def invite_tab(user):
    roles = auth.INVITABLE_BY[user["role"]]
    st.caption("There is no open sign-up: only people you invite can join, so nobody can flood the system "
               f"with requests. Each person gets a link that works once and expires after {config.INVITE_DAYS} days. "
               "Their account is active as soon as they set it up, with nothing to approve.")
    if config.ALLOWED_SIGNUP_DOMAINS:
        st.success("Company sign-in is on: anyone with a verified Google account at "
                   + ", ".join(f"**@{d}**" for d in config.ALLOWED_SIGNUP_DOMAINS)
                   + " can join as a Marketer without an invitation.", icon=":material/domain:")
    one, many = st.columns(2, gap="large")
    with one:
        with st.container(border=True):
            st.markdown("**Invite one person**")
            with st.form("invite_one", clear_on_submit=True, border=False):
                email = st.text_input("Email")
                name = st.text_input("Full name (optional)")
                role = st.selectbox("Role", roles, format_func=ROLE_LABEL.get)
                sent = st.form_submit_button("Send invitation", type="primary")
            if sent:
                show_invite_results(*auth.create_invitations(user, [{"email": email, "full_name": name,
                                                                      "role": role}], base_url=site_url()))
    with many:
        with st.container(border=True):
            st.markdown("**Invite many people from a CSV file**")
            st.caption(f"Columns: email, full_name, role. Up to {config.MAX_INVITES_PER_BATCH} people per file.")
            template = pd.DataFrame([{"email": "jane.doe@company.com", "full_name": "Jane Doe", "role": "marketer"}])
            st.download_button("Download the template", template.to_csv(index=False).encode("utf-8"),
                               file_name="salesmesh_invitations_template.csv", mime="text/csv")
            file = st.file_uploader("CSV file", type=["csv"], key="invite_csv", label_visibility="collapsed")
            if file is not None:
                try:
                    people = pd.read_csv(file, dtype=str, keep_default_na=False)
                    people.columns = [c.strip().lower() for c in people.columns]
                except Exception:
                    st.error("This file could not be read as a CSV.")
                    return
                if "email" not in people.columns:
                    st.error("The file needs an 'email' column.")
                    return
                st.write(f"{len(people)} row(s) found.")
                if st.button(f"Send {len(people)} invitation(s)", type="primary"):
                    try:
                        show_invite_results(*auth.create_invitations(user, people.to_dict("records"),
                                                                     base_url=site_url()))
                    except auth.AuthError as e:
                        st.error(str(e))


def show_invite_results(sent, skipped):
    if sent:
        st.success(f"{len(sent)} invitation(s) sent.")
        links = pd.DataFrame(sent).rename(columns={"email": "Email", "role": "Role", "link": "Invitation link"})
        if len(sent) == 1:
            st.caption("If the email does not arrive, copy this link and send it to them another way:")
            st.code(sent[0]["link"], language=None)
        else:
            st.download_button("Download the links (CSV)", links.to_csv(index=False).encode("utf-8"),
                               file_name="salesmesh_invitation_links.csv", mime="text/csv")
    if skipped:
        st.warning(f"{len(skipped)} not sent:")
        st.dataframe(pd.DataFrame(skipped).rename(columns={"email": "Email", "reason": "Reason"}),
                     hide_index=True, width="stretch")


def invitation_status(r, now):
    if r["accepted_at"]:
        return "✅ Joined"
    if r["revoked_at"]:
        return "Cancelled"
    if r["expires_at"] < now:
        return "Expired"
    return "⏳ Waiting"


def invitations_tab(user):
    rows = auth.list_invitations(user)
    if not rows:
        st.caption("No invitations have been sent yet.")
        return
    now = datetime.now()
    df = pd.DataFrame(rows)
    df["status"] = [invitation_status(r, now) for r in rows]
    counts = df["status"].value_counts()
    a, b, c = st.columns(3)
    a.metric("Waiting", int(counts.get("⏳ Waiting", 0)))
    b.metric("Joined", int(counts.get("✅ Joined", 0)))
    c.metric("Expired or cancelled", int(counts.get("Expired", 0) + counts.get("Cancelled", 0)))
    st.dataframe(pd.DataFrame({
        "Email": df["email"], "Name": df["full_name"], "Role": df["role"].map(ROLE_LABEL), "Status": df["status"],
        "Sent": df["created_at"], "Expires": df["expires_at"], "Invited by": df["invited_by"],
    }), hide_index=True, width="stretch", height=min(300, 38 + 35 * len(df) + 2),
        column_config={"Sent": st.column_config.DatetimeColumn(format="DD MMM YYYY, HH:mm"),
                       "Expires": st.column_config.DatetimeColumn(format="DD MMM YYYY")})
    waiting = df[df["status"] == "⏳ Waiting"]
    if waiting.empty:
        return
    a, b = st.columns([3, 1], vertical_alignment="bottom")
    labels = {int(r.invite_id): f"{r.email} ({ROLE_LABEL[r.role]})" for r in waiting.itertuples()}
    target = a.selectbox("Cancel an invitation that is still waiting", list(labels), format_func=labels.get)
    if b.button("Cancel invitation"):
        try:
            auth.revoke_invitation(user, target)
        except (auth.AuthError, PermissionError) as e:
            st.error(str(e))
        else:
            flash(f"Invitation for {labels[target]} cancelled. The link no longer works.")
            st.rerun()
    st.caption("To send someone a fresh link, invite them again: the old link stops working.")


def accounts_tab(user):
    admin = user["role"] == "admin"
    users = pipeline.pending_users(user)
    st.dataframe(pd.DataFrame({
        "Name": users["full_name"], "Email": users["email"], "Role": users["role"].map(ROLE_LABEL),
        "Status": users["is_active"].map({1: "Active", 0: "Inactive"}), "Created": users["created_at"],
    }), hide_index=True, width="stretch", height=min(300, 38 + 35 * len(users) + 2))

    st.markdown("**Change an account**")
    editable = users[users["user_id"] != user["user_id"]]
    if not admin:
        editable = editable[editable["role"] == "marketer"]
    labels = {int(r.user_id): f"{r.full_name or r.email} – {ROLE_LABEL[r.role]}"
                              + ("" if r.is_active else " (inactive)") for r in editable.itertuples()}
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


def notifications_page():
    user = current_user()
    st.title("Notifications")
    show_flash()
    waiting = pipeline.requests_to_decide(user)
    if not waiting.empty:
        st.subheader("Waiting for your answer")
        for r in waiting.itertuples():
            request_card(user, r, key=f"np_{r.request_id}")
    st.subheader("Recent")
    items = notify.recent(user, 50)
    if not items:
        st.caption("Nothing yet. You'll be told here when you receive a lead, when someone asks for one of "
                   "your leads, and when your own requests are answered.")
        return
    if user["role"] == "marketer":  # a marketer can open only leads that are theirs now
        mine = set(pipeline.get_leads(user).get("lead_id", pd.Series(dtype=int)).astype(int))
    for n in items:
        can_open = n["lead_id"] and (user["role"] != "marketer" or n["lead_id"] in mine)
        with st.container(border=True):
            a, b = st.columns([5, 1.2], vertical_alignment="center")
            new = "" if n["read_at"] else "🔵 "
            answered = f" · request {n['request_status']}" if n["request_id"] and n["request_status"] != "pending" else ""
            a.markdown(f"{new}{html.escape(n['message'])}")
            a.caption(f"{n['created_at']:%d %b %Y, %H:%M}{answered}")
            if can_open and b.button("Open lead", key=f"nl_{n['notification_id']}"):
                open_lead(n["lead_id"])
    if any(n["read_at"] is None for n in items):
        notify.mark_all_read(user)  # seen now; the count in the menu clears on the next click


def find_page():
    """A customer phones the 'wrong' marketer: find who owns the lead and ask for it."""
    user = current_user()
    st.title("Find a lead")
    st.caption("Use this when a customer calls you about a lead that is not on your list, for example "
               "because they asked for you by name. You'll see who owns it and can ask for it.")
    show_flash()
    text = st.text_input("Prospect ID", placeholder="Type at least 3 characters")
    if not text:
        return
    try:
        found = pipeline.find_leads(user, text)
    except ValueError as e:
        st.warning(str(e))
        return
    if found.empty:
        st.info("No lead matches. Check the ID, or add it on **Add leads** if it is a new customer.")
        return
    view = pd.DataFrame({
        "Prospect ID": found["prospect_id"], "Stage": found["stage"],
        "Tier": found["priority_tier"].map(TIER_LABEL),
        "Owner": [("You" if o == user["user_id"] else n or "Nobody") for o, n in
                  zip(found["assigned_to"], found["assigned_name"])],
        "Last contact": pd.to_datetime(found["last_follow_up_date"]),
    })
    event = st.dataframe(view, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                         key=f"find_{text}_{st.session_state.get('table_version', 0)}",
                         column_config={"Last contact": st.column_config.DatetimeColumn(format="DD MMM YYYY")})
    if not event.selection.rows:
        st.caption("Click a row to see what you can do.")
        return
    lead = found.iloc[event.selection.rows[0]]
    lead_id = int(lead["lead_id"])
    with st.container(border=True):
        if lead["assigned_to"] == user["user_id"]:
            st.markdown(f"Lead **{lead['prospect_id']}** is already yours.")
            if st.button("Open lead", type="primary"):
                open_lead(lead_id)
            return
        if lead["stage"] == "Converted":
            st.markdown(f"Lead **{lead['prospect_id']}** is already a customer (Converted) and is owned by "
                        f"**{lead['assigned_name']}**.")
            return
        st.markdown(f"Lead **{lead['prospect_id']}** is owned by **{lead['assigned_name'] or 'nobody'}** "
                    f"({lead['stage']}).")
        spoke = pipeline.previous_handlers(lead_id)
        if spoke:
            st.caption("Has spoken to this customer: " + ", ".join(
                f"{h['full_name']} ({h['last_contact']:%d %b})" for h in spoke))
        reason = st.text_input("Why do you need it?", max_chars=300, key=f"ask_{lead_id}",
                               placeholder="For example: customer called me back and asked for me")
        if st.button("Ask for this lead", type="primary", icon=":material/front_hand:"):
            try:
                pipeline.request_lead(user, lead_id, reason)
            except (ValueError, PermissionError, LookupError) as e:
                st.error(str(e))
            else:
                flash(f"Request sent. {lead['assigned_name'] or 'A manager'} will accept or decline it, "
                      "and you'll see the answer in Notifications.")
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


def link_while_signed_in():
    """An invitation or reset link was opened in a browser that is already signed in."""
    user = current_user()
    kind = "invitation" if st.query_params.get("invite") else "password reset"
    _, mid, _ = st.columns([1, 1.4, 1])
    with mid:
        with st.container(key="signin"):
            heading("You're already signed in",
                    f"This browser is signed in as {html.escape(user['email'])}.")
            st.write(f"To use this {kind} link, sign out first. The link will then open.")
            if st.button("Sign out and continue", type="primary", width="stretch"):
                google = google_signed_in()
                st.session_state.clear()     # the link stays in the address bar
                if google:
                    st.logout()              # also ends the Google session; open the link again afterwards
                st.rerun()
            if st.button("Stay signed in", type="tertiary"):
                st.query_params.clear()
                st.rerun()
            st.caption("Tip: to test an invitation while signed in as the manager, open the link in a private "
                       "(incognito) window instead: Ctrl + Shift + N.")


# ------------------------------------------------------------------ navigation
NOTIFICATIONS_PAGE = st.Page(notifications_page, title="Notifications", icon=":material/notifications:",
                             url_path="notifications")


def main():
    global NOTIFICATIONS_PAGE
    user = check_session()
    opened_link = bool(st.query_params.get("invite") or st.query_params.get("reset"))
    # A browser remembers its Google sign-in. When someone opens an invitation or reset link, show that
    # link instead of signing the remembered Google account straight in (it may be someone else's account).
    if not user and google_signed_in() and "google_problem" not in st.session_state and not opened_link:
        try:
            user = auth.external_sign_in(st.user.email, st.user.get("name"), bool(st.user.get("email_verified")))
        except auth.AuthError as e:
            st.session_state["google_problem"] = str(e)
        else:
            start_session(user)
    if user and opened_link:
        # Someone already signed in opened an invitation or reset link (for example their own test invite)
        page = st.navigation([st.Page(link_while_signed_in, title="Sign in", icon=":material/login:")],
                             position="hidden")
    elif not user:
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
        try:
            unread = notify.unread_count(user)
        except mysql.connector.Error:
            unread = 0  # shown properly by the page itself
        NOTIFICATIONS_PAGE = st.Page(notifications_page, url_path="notifications",
                                     title=f"Notifications ({unread})" if unread else "Notifications",
                                     icon=":material/notifications_active:" if unread else ":material/notifications:")
        account = st.Page(account_page, title="My account", icon=":material/person:")
        add = st.Page(add_leads_page, title="Add leads", icon=":material/person_add:")
        if user["role"] == "marketer":
            pages = [st.Page(today_page, title="Today", icon=":material/call:", default=True),
                     st.Page(leads_page, title="My leads", icon=":material/list:"),
                     st.Page(find_page, title="Find a lead", icon=":material/search:"),
                     add, NOTIFICATIONS_PAGE, account]
        else:
            pages = [st.Page(team_page, title="Team", icon=":material/groups:", default=True),
                     st.Page(leads_page, title="All leads", icon=":material/list:"),
                     add,
                     st.Page(users_page, title="Accounts", icon=":material/manage_accounts:"),
                     NOTIFICATIONS_PAGE, account]
        page = st.navigation(pages)
    try:
        page.run()
    except mysql.connector.Error as e:
        if e.errno in (1142, 1146):  # table missing or no permission: the database is from an older version
            st.error("The database needs updating for this version of SalesMesh. Stop the app, run  "
                     "`python setup_database.py`  in the app folder, then start the app again.\n\n"
                     f"Details: {e.msg}")
            return
        st.error("The app cannot reach the database. Check that MySQL is running and that DB_PASSWORD in "
                 f"app/.env is correct.\n\nDetails: {e.msg}")
    except PermissionError as e:
        st.error(str(e))


main()
