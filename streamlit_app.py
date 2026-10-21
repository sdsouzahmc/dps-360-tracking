"""DPS 360 · powered by AutoTrace — 360-degree student tracking for DPS: buses (per-session bus assignment), campus gates, visitors,
staff, class attendance, early pickup, vehicle checks and security patrols in one command-centre dashboard (English / Arabic)."""
import os
import random

import streamlit as st

import db
from i18n import rtl_css, t

st.set_page_config(page_title="DPS 360", page_icon="🛡️", layout="wide", initial_sidebar_state="auto")

BASE = os.path.dirname(os.path.abspath(__file__))


@st.cache_resource(show_spinner=False)
def _boot():
    """First run: build the DPS-scale demo (≈4,800 students, 100 buses, last school days + today so far)."""
    with st.spinner("Preparing the DPS 360 demo campus (first start only, about 20 seconds)…"):
        db.init_db()
        db.seed_extras()
        import planner
        planner.ensure_schema()
        import resets
        resets.snapshot_assignment()      # original per-session bus plan, for "Reset bus assignments"
    return True


_boot()
db.ensure_demo_logins()   # class-teacher + test-parent sign-ins (also on a redeploy, without rebuilding the data)
import dps_data   # noqa: E402
dps_data.tick()   # the simulated school day moves on to the current time
dps_data.apply_maintenance()   # one-off test-support actions (maintenance.json)
import security_ext   # noqa: E402
security_ext.check_missed_inspections()   # a closed trip with no security inspection after 20 min raises an alert

st.markdown("""<style>
  :root { --navy:#1B2559; --indigo:#3D4FD6; --indigo-soft:#E9ECFC; --ink:#1A2036; --muted:#6B7390; --line:#E3E7F0;
          --green:#2E9E5B; --orange:#E07A1F; --red:#D93025; }
  .block-container {padding-top: 4.2rem !important; max-width: 1400px;}
  header[data-testid="stHeader"] {background: rgba(244,246,250,.92); backdrop-filter: blur(4px);}
  h1, h2, h3 {color: var(--ink); letter-spacing: -0.01em;}
  h1 {font-weight: 800 !important;}
  /* white rounded cards for bordered containers */
  div[data-testid="stVerticalBlockBorderWrapper"], div[data-testid="stContainer"][data-border="true"] {
      background:#FFFFFF; border:1px solid var(--line) !important; border-radius:14px !important;
      box-shadow: 0 1px 2px rgba(16,24,64,.04), 0 2px 8px rgba(16,24,64,.04); }
  div[data-testid="stExpander"] details {background:#FFFFFF; border-radius:12px; border:1px solid var(--line);}
  div[data-testid="stMetric"] {background:#FFFFFF; border:1px solid var(--line); border-radius:14px; padding:12px 16px;
      box-shadow: 0 1px 2px rgba(16,24,64,.04);}
  div[data-testid="stMetricValue"] {font-weight:800; color:var(--ink);}
  div[data-testid="stDataFrame"] {border-radius:12px; overflow:hidden;}
  /* sidebar */
  section[data-testid="stSidebar"] {background: var(--navy);}
  section[data-testid="stSidebar"] a[data-testid="stSidebarNavLink"] {border-radius:10px; margin:2px 6px; padding:8px 12px;}
  section[data-testid="stSidebar"] a[data-testid="stSidebarNavLink"][aria-current="page"] {background: var(--indigo) !important;}
  section[data-testid="stSidebar"] a[data-testid="stSidebarNavLink"] span {color:#E8ECFF !important; font-weight:500;}
  .brand {font-size:1.7rem; font-weight:800; color:#FFFFFF; letter-spacing:-0.02em; margin:0 0 2px 4px;}
  .brand b {color:#8EA2FF;}
  .brand-sub {color:#AEB8E8; font-size:.8rem; margin:-6px 0 12px 2px;}
  [data-testid='stSidebarHeader'] img, [data-testid='stLogo'] {height:2.4rem !important; max-width:220px;}
  .nowrap{white-space:nowrap}
  .kpi .lab{font-size:.8rem;line-height:1.2;min-height:2.1em}
  .bc .top{flex-wrap:wrap}
  /* status messages */
  .ok   {background:#E8F6EE;border-left:6px solid var(--green);padding:10px 14px;border-radius:10px;font-size:1.02rem;margin:6px 0}
  .warn {background:#FFF3E6;border-left:6px solid var(--orange);padding:10px 14px;border-radius:10px;font-size:1.02rem;margin:6px 0}
  .error{background:#FDECEC;border-left:6px solid var(--red);padding:10px 14px;border-radius:10px;font-size:1.02rem;margin:6px 0}
  .card {background:#FFFFFF;border:1px solid var(--line);border-radius:14px;padding:12px 16px;margin:6px 0}
  .muted{color:var(--muted);font-size:.85rem}
  .pill {display:inline-block;padding:2px 10px;border-radius:12px;font-size:.78rem;font-weight:600;background:var(--indigo-soft);color:var(--indigo)}
  .pill.green{background:#E3F4EA;color:var(--green)} .pill.blue{background:var(--indigo);color:#fff}
  .pill.grey{background:#EEF0F5;color:#5B6378} .pill.orange{background:#FDEBD8;color:var(--orange)} .pill.red{background:#FDE3E1;color:var(--red)}
  .badge-sample{background:#FDE3E1;color:#C62828;font-weight:700;font-size:.75rem;padding:6px 12px;border-radius:8px;letter-spacing:.04em}
  /* KPI cards */
  .kpis{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:14px;margin:8px 0 16px}
  @media (max-width: 1100px){.kpis{grid-template-columns:repeat(3,minmax(0,1fr));}}
  @media (max-width: 640px){.kpis{grid-template-columns:repeat(2,minmax(0,1fr));}}
  .kpi{background:#fff;border:1px solid var(--line);border-radius:14px;padding:14px 16px;box-shadow:0 1px 2px rgba(16,24,64,.04)}
  .kpi .lab{display:flex;align-items:center;gap:8px;color:#3A4160;font-size:.86rem;font-weight:500}
  .kpi .val{font-size:2.1rem;font-weight:800;color:var(--ink);line-height:1.15;margin-top:4px}
  .kpi .val.orange{color:var(--orange)} .kpi .sub{color:var(--muted);font-size:.75rem}
  .kpi svg{width:20px;height:20px;flex:none}
  .sect{display:flex;align-items:center;gap:8px;font-weight:700;color:var(--ink);font-size:1.02rem;margin:2px 0 8px}
  .sect svg{width:18px;height:18px}
  .bar{display:flex;align-items:center;gap:10px;margin:10px 0}
  .bar .name{width:44%;font-size:.88rem;color:#2A3150}
  .bar .track{flex:1;height:22px;background:transparent}
  .bar .fill{height:22px;border-radius:3px}
  .bar .num{width:36px;text-align:right;font-weight:800}
  .safety{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
  .safety .lab{color:var(--muted);font-size:.8rem} .safety .v{font-size:1.5rem;font-weight:800;display:flex;align-items:center;gap:8px}
  .att{display:flex;justify-content:space-between;align-items:center;background:#F7F8FC;border-radius:10px;padding:8px 12px;margin:6px 0;font-size:.88rem}
  .att .dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--orange);margin-right:8px}
  /* live trip cards */
  .buscards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}
  @media (max-width: 1100px){.buscards{grid-template-columns:repeat(2,minmax(0,1fr));}}
  @media (max-width: 640px){.buscards{grid-template-columns:1fr;}}
  .bc{background:#fff;border:1px solid var(--line);border-radius:14px;padding:14px 16px;box-shadow:0 1px 2px rgba(16,24,64,.04)}
  .bc .top{display:flex;align-items:center;gap:8px} .bc .no{font-weight:800;font-size:1.05rem}
  .bc .area{border:1px solid var(--line);border-radius:8px;padding:1px 8px;font-size:.75rem;color:#4A5272}
  .bc .prog{position:relative;height:6px;background:#DCE3FB;border-radius:4px;margin:16px 0 4px}
  .bc .prog .f{height:6px;background:#9DB0F7;border-radius:4px}
  .bc .prog .k{position:absolute;top:-5px;width:16px;height:16px;border-radius:50%;background:var(--indigo);border:3px solid #fff;box-shadow:0 0 0 1px #c9d2f5}
  .bc .ends{display:flex;justify-content:space-between;font-size:.72rem;color:var(--muted)}
  .bc .grid{display:grid;grid-template-columns:1fr 1fr;gap:6px 12px;margin-top:10px}
  .bc .l{font-size:.72rem;color:var(--muted)} .bc .v{font-size:.86rem;font-weight:600;color:var(--ink)}
  .crewcnt{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin:6px 0 10px}
  .crewcnt div{background:#fff;border:1px solid var(--line);border-radius:12px;padding:8px 10px;text-align:center}
  .crewcnt b{display:block;font-size:1.6rem;line-height:1.1} .crewcnt span{font-size:.72rem;color:var(--muted)}
  @media (max-width: 640px){ h1{font-size:1.7rem !important} h3{font-size:1.15rem !important} .block-container{padding-left:.8rem;padding-right:.8rem} }
  div[data-testid='stPopover'] button[kind='primary']{background:#D93025 !important;border-color:#D93025 !important;color:#fff !important;font-weight:700}
  .datebar{display:inline-block;background:#fff;border:1px solid var(--line);border-radius:10px;padding:6px 12px;margin:-4px 0 10px;font-size:1rem;color:var(--ink)}
  .datebar span{color:var(--muted);font-size:.8rem}
  [data-testid='stToolbarActions']{display:none !important}
  /* touch-friendly buttons */
  div[data-testid='stPills'] button, div[data-testid='stButtonGroup'] button{min-height:44px;padding:8px 14px;font-size:.98rem}
  div[data-testid='stButton'] button{min-height:44px}
  .bc.flat{border:none;box-shadow:none;padding:2px 2px 6px} .bc.closed .prog .f{background:#9ED3B0} .bc.closed .prog .k{background:var(--green)}
""" + rtl_css(), unsafe_allow_html=True)


APPS = {"parent": ("Parent app", ("guardian",)), "driver": ("Driver app", ("driver",)),
        "supervisor": ("Supervisor app", ("supervisor", "caretaker")), "security": ("Security app", ("security",)),
        "teacher": ("Teacher app", ("teacher",))}
if "app_mode" not in st.session_state:          # set by the Android apps (…/?app=parent|driver|supervisor|security|teacher)
    st.session_state.app_mode = (st.query_params.get("app") or "").lower() if (st.query_params.get("app") or "").lower() in APPS else ""


def login():
    st.markdown("<div style='display:flex;align-items:center;gap:14px;margin-bottom:4px'>"
                "<div style='width:58px;height:58px;border-radius:16px;background:linear-gradient(135deg,#1B2559,#3D4FD6);display:flex;"
                "align-items:center;justify-content:center;color:#fff;font-weight:900;font-size:1.25rem;letter-spacing:-.03em'>360</div>"
                "<div><div style='font-size:2.2rem;font-weight:800;letter-spacing:-0.02em;color:#1B2559;line-height:1.1'>DPS <span style='color:#3D4FD6'>360</span></div>"
                "<div style='font-size:.95rem;color:#6B7390;font-weight:600'>Student safety &amp; tracking · powered by "
                "<b style='color:#1B2559'>Auto</b><b style='color:#3D4FD6'>Trace</b></div></div></div>", unsafe_allow_html=True)
    st.caption(db.setting("school_name", db.SCHOOL_NAME))
    _am = st.session_state.get("app_mode")
    if _am:
        st.markdown(f"<span class='pill'>📱 {t(APPS[_am][0])}</span>", unsafe_allow_html=True)
    c1, c2 = st.columns([1, 1])
    with c1:
        lang = st.radio(t("Language"), ["English", "العربية"], horizontal=True, index=1 if st.session_state.get("lang") == "ar" else 0)
        if (lang == "العربية") != (st.session_state.get("lang") == "ar"):
            st.session_state.lang = "ar" if lang == "العربية" else "en"
            st.rerun()
        pend = st.session_state.get("mfa_pending")
        if pend:
            st.info(f"Two-step verification for **{pend['display_name']}**. Enter the 6-digit code sent to your registered mobile "
                    f"(**{st.session_state.mfa_code}**).")
            with st.form("mfa"):
                code = st.text_input(t("Verification code"), max_chars=6)
                ok = st.form_submit_button(t("Verify"), type="primary")
            if ok:
                if code.strip() == st.session_state.mfa_code:
                    st.session_state.user = pend
                    st.session_state.pop("mfa_pending")
                    db.audit(pend["username"], pend["role"], "LOGIN", "user", pend["username"], "password + MFA")
                    st.rerun()
                else:
                    db.audit(pend["username"], pend["role"], "MFA_FAILED", "user", pend["username"])
                    st.error(t("Wrong code."))
            if st.button("Cancel"):
                st.session_state.pop("mfa_pending")
                st.rerun()
            return
        with st.form("login"):
            u = st.text_input(t("Username"))
            p = st.text_input(t("PIN / password"), type="password")
            ok = st.form_submit_button(t("Sign in"), type="primary")
        if ok:
            user = db.get_user(u, p)
            if not user:
                db.audit(u, "", "LOGIN_FAILED", "user", u)
                st.error(t("Wrong username or PIN."))
            elif st.session_state.get("app_mode") and user["role"] not in APPS[st.session_state.app_mode][1]:
                st.error(f"{t('This is the')} {t(APPS[st.session_state.app_mode][0])} — "
                         + t("please sign in with the app for your role."))
            elif user["role"] in db.MFA_ROLES:
                st.session_state.mfa_pending = user
                st.session_state.mfa_code = f"{random.randint(0, 999999):06d}"
                st.rerun()
            else:
                st.session_state.user = user
                db.audit(user["username"], user["role"], "LOGIN", "user", user["username"])
                st.rerun()
    with c2:
        st.markdown("<div class='card' style='margin-top:2.2rem'><b>One view of every child, all day</b><br><span class='muted'>"
                    "Bus pickup and drop-off for ~100 buses in five sessions · campus gate in / out for private-transport students, staff "
                    "and visitors · class attendance pre-filled from bus and gate scans · early pickup passes · vehicle checks and "
                    "security patrols — on one live dashboard.</span></div>", unsafe_allow_html=True)
        with st.expander("Demo sign-ins"):
            st.markdown("""| App | Username | PIN |
|---|---|---|
| 360 dashboard (principal) | `principal` | 1234 |
| Transport lead (all modules) | `manager` | manager123 |
| Security / gate | `security`, `guard1` | 1234 |
| Teacher (Grade 5-A) | `teacher1` | 1234 |
| Any class teacher: t + grade + section | `t5a`, `tkg1a`, `t12j` … | 1234 |
| Parent | `g001` … `g040` | 1234 |
| Any parent: p + 5-digit number (Reports → Parent logins) | `p00001` … | 1234 |
| Test parents per tester (children on that tester's bus / classes) | `pa1-01…`, `pa2-01…`, `pb1-01…`, `pb2-01…` | 1234 |
| Bus supervisor / driver | `sup001` / `drv001` | 1234 |
| Fleet approvers: supervisor, senior manager, VP | `fleetsup`, `srmanager`, `vp` | 1234 |""")


if "user" not in st.session_state:
    login()
    st.stop()

user = st.session_state.user
role = user["role"]

st.logo(os.path.join(BASE, "dps360_logo.svg"), size="large", icon_image=os.path.join(BASE, "dps360_icon.svg"))
with st.sidebar:
    st.markdown("<div class='brand-sub'>DPS 360 · powered by AutoTrace · " + db.setting("school_name", db.SCHOOL_NAME) + "</div>", unsafe_allow_html=True)
    st.markdown(f"**{user['display_name']}**  \n<span class='muted'>{ {'vp': 'Vice President', 'fleet_supervisor': 'Fleet supervisor', 'senior_manager': 'Senior Manager'}.get(role, role.title())} · {user['username']}</span>", unsafe_allow_html=True)
    lang = st.radio(t("Language"), ["English", "العربية"], horizontal=True, index=1 if st.session_state.get("lang") == "ar" else 0)
    if (lang == "العربية") != (st.session_state.get("lang") == "ar"):
        st.session_state.lang = "ar" if lang == "العربية" else "en"
        st.rerun()
    if st.button(t("Sign out"), width="stretch"):
        db.audit(user["username"], role, "LOGOUT", "user", user["username"])
        for k in list(st.session_state):
            if k != "lang":
                del st.session_state[k]
        st.rerun()

P = {
    "d360": st.Page("page_360.py", title="360 Command centre", icon=":material/radar:", default=True),
    "person": st.Page("page_person.py", title="Student 360", icon=":material/person_search:"),
    "security": st.Page("page_security.py", title=t("Security app"), icon=":material/shield_person:"),
    "patrol": st.Page("page_patrol.py", title="Security patrols", icon=":material/local_police:"),
    "teacher": st.Page("page_teacher.py", title=t("Teacher app"), icon=":material/fact_check:"),
    "campus": st.Page("page_campus.py", title="Campus & visitors", icon=":material/badge:"),
    "fleet": st.Page("page_fleet.py", title="Fleet maintenance & fuel", icon=":material/build:"),
    "safety": st.Page("page_safety.py", title="Vehicle & patrol checks", icon=":material/verified_user:"),
    "assign": st.Page("page_assign.py", title="Bus assignment board", icon=":material/swap_horiz:"),
    "dash": st.Page("page_dashboard.py", title="Transport overview", icon=":material/directions_bus:"),
    "crew": st.Page("page_crew.py", title=t("Crew app"), icon=":material/qr_code_scanner:"),
    "receipt": st.Page("page_receipt.py", title=t("School receipt"), icon=":material/school:"),
    "parent": st.Page("page_parent.py", title=t("My children"), icon=":material/family_restroom:"),
    "live": st.Page("page_live.py", title="Live trips", icon=":material/map:"),
    "lists": st.Page("page_lists.py", title="Daily bus lists", icon=":material/groups:"),
    "exceptions": st.Page("page_exceptions.py", title="Incidents & approvals", icon=":material/warning:"),
    "notices": st.Page("page_notices.py", title="Parent notices", icon=":material/mail:"),
    "reports": st.Page("page_reports.py", title="Reports", icon=":material/bar_chart:"),
    "setup": st.Page("page_setup.py", title="Setup & data", icon=":material/settings:"),
    "plan": st.Page("page_planning.py", title="Trip planning", icon=":material/route:"),
    "locs": st.Page("page_locations.py", title="Student locations", icon=":material/home_pin:"),
}
OFFICE = ["d360", "person", "patrol", "campus", "safety", "dash", "live", "assign", "lists", "plan", "locs", "exceptions", "notices", "reports"]
MENU = {
    "supervisor": ["crew"], "caretaker": ["crew"], "driver": ["crew"],
    "receiving": ["receipt"],
    "guardian": ["parent"],
    "security": ["security", "patrol"],
    "teacher": ["teacher"],
    "principal": ["d360", "person", "patrol", "campus", "safety", "fleet", "dash", "live", "exceptions", "reports"],
    "fleet_supervisor": ["fleet"],
    "senior_manager": ["d360", "fleet", "patrol", "reports"],
    "vp": ["d360", "fleet", "patrol", "campus", "reports"],
    "dispatcher": OFFICE + ["fleet", "crew", "receipt"],
    "manager": OFFICE + ["fleet", "security", "teacher", "setup", "crew", "receipt"],
    "admin": OFFICE + ["fleet", "security", "teacher", "setup", "crew", "receipt", "parent"],
}
_pages = [P[k] for k in MENU.get(role, [])]
if role in ("manager", "admin", "dispatcher", "principal", "senior_manager", "vp"):
    _groups = {"Command centre": [P[k] for k in ("d360", "person") if P[k] in _pages],
               "Campus": [P[k] for k in ("security", "patrol", "teacher", "campus", "safety") if P[k] in _pages],
               "Transport": [P[k] for k in ("dash", "live", "assign", "lists", "plan", "locs", "crew", "receipt") if P[k] in _pages],
               "Fleet": [P[k] for k in ("fleet",) if P[k] in _pages],
               "Office": [P[k] for k in ("exceptions", "notices", "reports", "setup", "parent") if P[k] in _pages]}
    st.navigation({k: v for k, v in _groups.items() if v}).run()
else:
    st.navigation(_pages).run()
