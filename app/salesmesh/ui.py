"""Look and feel: page styles, the sign-in brand panel and small HTML pieces."""
import base64

import streamlit as st

INK = "#12263A"        # sidebar and brand panel
BLUE = "#185FA5"       # actions and links (same blue as the Power BI report)
LINE = "#D8E0EA"       # borders
RED, AMBER, GREEN = "#C0392B", "#D9902F", "#2E8B57"

CSS = f"""
<style>
header[data-testid="stHeader"] {{ background: transparent; }}
[data-testid="stDecoration"] {{ display: none; }}
[data-testid="stMainBlockContainer"] {{ padding-top: 2.2rem; max-width: 1280px; }}
h1 {{ letter-spacing: -0.02em; }}

/* Sign-in page: compact so the form is visible without scrolling */
.st-key-brand {{
  background: {INK}; border-radius: 18px; padding: 30px 34px 26px;
}}
.st-key-brand * {{ color: #E6EDF5; }}
.st-key-signin {{
  background: #FFFFFF; border: 1px solid {LINE}; border-radius: 18px; padding: 20px 26px 10px;
}}
.st-key-signin [data-testid="stVerticalBlock"] {{ gap: 0.55rem; }}
.st-key-signin [data-testid="stForm"] [data-testid="stVerticalBlock"] {{ gap: 0.5rem; }}
.st-key-forgot_link {{ margin-top: -6px; }}
.st-key-forgot_link button {{ min-height: 0; padding: 0; }}
/* The sign-in page starts near the top of the window */
[data-testid="stMainBlockContainer"]:has(.st-key-signin) {{ padding-top: 1rem; padding-bottom: 1rem; }}
/* No empty sidebar on the sign-in page (it can linger for a moment after signing out) */
.stApp:has(.st-key-signin) [data-testid="stSidebar"],
.stApp:has(.st-key-signin) [data-testid="stSidebarCollapsedControl"],
.stApp:has(.st-key-signin) [data-testid="stExpandSidebarButton"] {{ display: none !important; }}
.brand-points {{ margin-top: 24px; display: flex; flex-direction: column; gap: 16px; }}
.brand-point {{ display: flex; gap: 12px; align-items: flex-start; font-size: 1rem; line-height: 1.45;
  color: #C9D5E2 !important; }}
.brand-point img {{ flex: none; margin-top: 1px; }}
.brand-point b {{ color: #FFFFFF !important; }}
.st-key-google button {{
  background: #FFFFFF; border: 1px solid #C3CEDB; color: #1F1F1F; font-weight: 600; min-height: 44px;
}}
.st-key-google button:hover {{ border-color: {BLUE}; background: #F7FAFD; color: #1F1F1F; }}
.st-key-google button p::before {{
  content: ""; display: inline-block; width: 18px; height: 18px; margin-right: 10px; vertical-align: -3px;
  background: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 48 48'><path fill='%23EA4335' d='M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z'/><path fill='%234285F4' d='M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z'/><path fill='%23FBBC05' d='M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z'/><path fill='%2334A853' d='M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z'/></svg>") no-repeat center / contain;
}}
.signin-head {{ font-size: 1.45rem; font-weight: 800; letter-spacing: -0.02em; color: #16202C; }}
.signin-sub {{ color: #5B6B80; font-size: 0.95rem; margin-top: 2px; }}
.or-rule {{ display: flex; align-items: center; gap: 12px; color: #6B7A8C; font-size: 0.85rem; margin: 0; }}
.or-rule::before, .or-rule::after {{ content: ""; flex: 1; border-top: 1px solid {LINE}; }}

/* Brand panel content */
.wordmark {{ display: flex; align-items: center; gap: 10px; font-weight: 800; font-size: 1.15rem; letter-spacing: -0.01em; }}
.brand-head {{ font-size: 1.95rem; line-height: 1.12; font-weight: 800; letter-spacing: -0.025em; margin: 26px 0 10px; max-width: 16ch; }}
.brand-sub {{ font-size: 1.02rem; line-height: 1.6; color: #B9C7D6 !important; max-width: 44ch; }}
.lead-card {{
  margin-top: 22px; background: #1A3249; border: 1px solid #2A4763; border-radius: 14px;
  padding: 20px 22px; display: flex; gap: 20px; align-items: flex-start; max-width: 470px;
}}
.lead-card .who {{ font-weight: 700; font-size: 0.98rem; }}
.lead-card .tier {{ display: inline-block; margin-left: 8px; padding: 1px 9px; border-radius: 999px;
  background: rgba(46,139,87,.22); color: #8FD9AE !important; font-size: 0.78rem; font-weight: 700; }}
.lead-card ul {{ margin: 10px 0 10px 0; padding-left: 18px; }}
.lead-card li {{ font-size: 0.9rem; line-height: 1.5; color: #C9D5E2 !important; }}
.lead-card .due {{ font-size: 0.82rem; color: #F2C27B !important; font-weight: 600; }}
.brand-foot {{ font-size: 0.8rem; color: #8DA1B6 !important; margin-top: 18px; }}

/* Notifications and timeline */
.note-card {{ border: 1px solid {LINE}; border-radius: 10px; padding: 10px 14px; background: #FFFFFF; margin-bottom: 6px; }}
.note-card.unread {{ border-left: 4px solid {BLUE}; }}
.note-when {{ color: #6B7A8C; font-size: 0.8rem; }}
.tl-item {{ border-left: 2px solid {LINE}; padding: 2px 0 10px 14px; margin-left: 6px; position: relative; }}
.tl-item::before {{ content: ""; position: absolute; left: -6px; top: 6px; width: 10px; height: 10px; border-radius: 50%;
  background: {BLUE}; }}
.tl-item.fu::before {{ background: #9DB7D5; }}
.tl-when {{ color: #6B7A8C; font-size: 0.78rem; }}
.tl-what {{ font-weight: 600; font-size: 0.92rem; }}
.tl-note {{ color: #3D4B5C; font-size: 0.86rem; }}

/* KPI tiles */
[data-testid="stMetric"] {{ background: #FFFFFF; border: 1px solid {LINE}; border-radius: 12px; padding: 14px 16px 12px; }}
[data-testid="stMetricLabel"] p {{ color: #51627A; font-weight: 600; }}

/* Tabs: more space between them so each one is easy to see and click */
[role="tablist"] {{ gap: 2.25rem !important; margin-top: 0.5rem; }}
[data-testid="stTab"] p {{ font-size: 0.95rem; }}

/* Lead pop-up: smaller figures so the tabs are visible without scrolling */
[role="dialog"] [data-testid="stMetric"] {{ padding: 8px 12px 6px; }}
[role="dialog"] [data-testid="stMetricValue"] {{ font-size: 1.35rem; }}

/* Live score preview on Add leads */
.score-big {{ font-size: 2.6rem; font-weight: 800; letter-spacing: -0.02em; line-height: 1.1; color: #16202C; }}
.score-of {{ font-size: 0.9rem; font-weight: 500; color: #5B6B80; margin-left: 8px; letter-spacing: 0; }}
.tier-pill {{ display: inline-block; margin-top: 6px; padding: 3px 12px; border-radius: 999px; font-weight: 700; font-size: 0.9rem; }}
.tier-high {{ background: #E3F4EA; color: {GREEN}; }}
.tier-medium {{ background: #FBF0DF; color: #A86A12; }}
.tier-low {{ background: #EEF1F5; color: #51627A; }}

/* Sidebar */
.side-brand {{ font-weight: 800; font-size: 1.1rem; letter-spacing: -0.01em; margin: -6px 0 6px; }}
.side-user {{ font-size: 0.92rem; line-height: 1.4; }}
.side-user .role {{ color: #9FB3C8; font-size: 0.82rem; }}
</style>
"""

def as_img(svg: str, size: int, alt: str = "") -> str:
    """Embed an SVG as an image (Streamlit removes raw <svg> from HTML)."""
    data = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f'<img src="data:image/svg+xml;base64,{data}" width="{size}" height="{size}" alt="{alt}">'


LOGO_SVG = f"""<svg xmlns="http://www.w3.org/2000/svg" width="26" height="26" viewBox="0 0 26 26">
<rect width="26" height="26" rx="7" fill="{BLUE}"/>
<circle cx="8" cy="9" r="2.4" fill="#fff"/><circle cx="18" cy="9" r="2.4" fill="#fff" opacity=".75"/>
<circle cx="13" cy="18" r="2.4" fill="#fff"/>
<path d="M8 9 13 18 18 9" stroke="#fff" stroke-width="1.6" fill="none" opacity=".8"/></svg>"""
LOGO = as_img(LOGO_SVG, 26)


def score_ring(pct: int, size: int = 84) -> str:
    r = 34
    circ = 2 * 3.14159 * r
    on = circ * pct / 100
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="84" height="84" viewBox="0 0 84 84">
<circle cx="42" cy="42" r="{r}" fill="none" stroke="#2A4763" stroke-width="8"/>
<circle cx="42" cy="42" r="{r}" fill="none" stroke="#4FB37E" stroke-width="8" stroke-linecap="round"
  stroke-dasharray="{on:.1f} {circ:.1f}" transform="rotate(-90 42 42)"/>
<text x="42" y="47" text-anchor="middle" font-size="19" font-weight="800" fill="#FFFFFF"
  font-family="Manrope, Arial, sans-serif">{pct}%</text></svg>"""
    return as_img(svg, size, f"Score {pct}%")


CHECK_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 22 22">
<circle cx="11" cy="11" r="11" fill="#2E8B57"/>
<path d="M6.5 11.3 9.6 14.3 15.6 8.2" stroke="#fff" stroke-width="2.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>"""
CHECK = as_img(CHECK_SVG, 22)

BRAND_PANEL = f"""
<div class="wordmark">{LOGO}<span>SalesMesh</span></div>
<div class="brand-head">Know which lead to call next.</div>
<div class="brand-sub">SalesMesh helps marketing teams focus on the leads most likely to become customers.</div>
<div class="brand-points">
  <div class="brand-point">{CHECK}<div><b>Scores every new lead</b> with machine learning and explains why</div></div>
  <div class="brand-point">{CHECK}<div><b>Assigns it fairly</b> to the marketer with the fewest open leads</div></div>
  <div class="brand-point">{CHECK}<div><b>Tracks every follow-up</b> so no lead is forgotten</div></div>
</div>
"""


def apply_styles():
    st.markdown(CSS, unsafe_allow_html=True)


def sidebar_brand():
    st.html(f'<div class="side-brand wordmark">{LOGO}<span>SalesMesh</span></div>')
