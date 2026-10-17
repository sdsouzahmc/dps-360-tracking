"""Shared DPS 360 UI pieces: KPI tiles, person cards with photo, status pills, the vehicle checklist form, small charts."""
import altair as alt
import pandas as pd
import streamlit as st

import campus
import db

STATE_LABEL = {"ON_CAMPUS": "On campus", "ON_BUS": "On a bus", "LEFT": "Gone home", "ABSENT": "Absent", "NOT_ARRIVED": "Not arrived yet",
               "UNACCOUNTED": "Unaccounted", "NOT_SEEN": "Not seen today"}
STATE_COLOR = {"ON_CAMPUS": "#2E9E5B", "ON_BUS": "#3D4FD6", "LEFT": "#9AA3BD", "ABSENT": "#E07A1F", "NOT_ARRIVED": "#C9CFE0",
               "UNACCOUNTED": "#D93025", "NOT_SEEN": "#B9A0E8"}
SEV_PILL = {"CRITICAL": "red", "HIGH": "red", "MEDIUM": "orange", "LOW": "grey"}

CSS = """<style>
.k360{display:grid;grid-template-columns:repeat(var(--n,6),minmax(0,1fr));gap:12px;margin:6px 0 14px}
@media (max-width:1100px){.k360{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media (max-width:640px){.k360{grid-template-columns:repeat(2,minmax(0,1fr))}}
.k360 .t{background:#fff;border:1px solid var(--line);border-radius:14px;padding:12px 14px;box-shadow:0 1px 2px rgba(16,24,64,.04);position:relative;overflow:hidden}
.k360 .t:before{content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--c,#3D4FD6)}
.k360 .l{font-size:.78rem;color:#4A5272;font-weight:600;letter-spacing:.01em}
.k360 .v{font-size:1.9rem;font-weight:800;color:#1A2036;line-height:1.15;margin-top:2px}
.k360 .s{font-size:.74rem;color:#6B7390}
.k360 .v.red{color:#D93025}.k360 .v.orange{color:#E07A1F}.k360 .v.green{color:#2E9E5B}
.hero{background:linear-gradient(120deg,#1B2559 0%,#2B3A8C 60%,#3D4FD6 100%);border-radius:18px;padding:18px 22px;color:#fff;margin:-6px 0 14px;
      display:flex;align-items:center;gap:18px;flex-wrap:wrap}
.hero .ttl{font-size:1.65rem;font-weight:800;letter-spacing:-.02em;line-height:1.1}
.hero .sub{color:#C9D2FF;font-size:.88rem}
.hero .clock{margin-left:auto;text-align:right}
.hero .clock b{font-size:1.5rem;display:block;line-height:1.1}
.hero .sess{display:inline-block;background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.25);border-radius:20px;padding:3px 12px;
            font-size:.8rem;margin-top:6px}
.pcard{display:flex;gap:14px;align-items:center;background:#fff;border:1px solid var(--line);border-radius:16px;padding:12px 14px;margin:6px 0}
.pcard .n{font-weight:800;font-size:1.1rem;color:#1A2036}.pcard .m{font-size:.84rem;color:#4A5272}
.big-result{border-radius:18px;padding:16px 18px;display:flex;gap:18px;align-items:center;margin:8px 0}
.big-result.ok{background:#E8F6EE;border:2px solid #2E9E5B}.big-result.warn{background:#FFF3E6;border:2px solid #E07A1F}
.big-result.error{background:#FDECEC;border:2px solid #D93025}
.big-result .msg{font-size:1.25rem;font-weight:800;color:#1A2036;line-height:1.3}
.big-result .dir{display:inline-block;font-size:1.5rem;font-weight:900;color:#fff;border-radius:12px;padding:2px 16px;margin-bottom:6px}
@media (max-width:640px){.big-result{flex-direction:column;align-items:flex-start;gap:10px}.big-result img{width:120px;height:120px}}
.feed{max-height:520px;overflow:auto}
.feed .it{display:flex;gap:10px;padding:8px 4px;border-bottom:1px solid #EEF0F5;font-size:.86rem}
.feed .it .tm{color:#6B7390;width:44px;flex:none;font-variant-numeric:tabular-nums}
.tl{border-left:3px solid #DCE3FB;margin-left:10px;padding-left:16px}
.tl .e{position:relative;margin:0 0 12px}
.tl .e:before{content:"";position:absolute;left:-23px;top:4px;width:11px;height:11px;border-radius:50%;background:#3D4FD6;border:2px solid #fff;
              box-shadow:0 0 0 1px #c9d2f5}
.tl .e .tm{font-weight:700;color:#1A2036;font-variant-numeric:tabular-nums}.tl .e .src{font-size:.72rem;color:#6B7390;margin-left:6px}
.legend{display:flex;flex-wrap:wrap;gap:10px;font-size:.78rem;color:#4A5272;margin:4px 0}
.legend span:before{content:"";display:inline-block;width:10px;height:10px;border-radius:3px;background:var(--c);margin-right:5px;vertical-align:-1px}
.mini{font-size:.78rem;color:#6B7390}
.sbar{height:12px;border-radius:6px;overflow:hidden;display:flex;background:#EEF0F5;margin:6px 0 4px}
.sbar div{height:12px}
</style>"""


def css():
    st.markdown(CSS, unsafe_allow_html=True)


def kpis(items, n=None):
    """items: (label, value, sub, colour, value_class)."""
    html = "".join(f"<div class='t' style='--c:{c}'><div class='l'>{l}</div><div class='v {vc}'>{v if isinstance(v, str) else f'{v:,}'}</div>"
                   f"<div class='s'>{s}&nbsp;</div></div>" for l, v, s, c, vc in items)
    st.markdown(f"<div class='k360' style='--n:{n or len(items)}'>{html}</div>", unsafe_allow_html=True)


def pill(text, cls=""):
    return f"<span class='pill {cls}'>{text}</span>"


def person_card(row, kind="STUDENT", extra="", size=72):
    if kind == "STUDENT":
        meta = (f"{row.get('class_name', '')}-{row.get('section', '')} · {row.get('code', '')} · "
                + ("🚌 " + (row.get("bus_no") or "Bus") if row.get("transport") == "BUS" else "🚗 Private transport"))
    elif kind == "STAFF":
        meta = f"{(row.get('role') or '').title()} · {row.get('dept') or ''} · {row.get('card_no') or ''}"
    else:
        meta = f"Visitor · {row.get('purpose', '')} → {row.get('host', '')} · pass {row.get('pass_no', '')}"
    img = campus.avatar_img(row["id"], row.get("gender") or "M", kind, size)
    st.markdown(f"<div class='pcard'>{img}<div><div class='n'>{row['name']}</div><div class='m'>{meta}</div>{extra}</div></div>",
                unsafe_allow_html=True)


def bar_chart(df, x, y, color=None, height=230, horizontal=False, colors=None, stack=True, title=None, sort=None):
    enc_x = alt.X(f"{x}:N", title=None, sort=sort) if not horizontal else alt.X(f"{y}:Q", title=None)
    enc_y = alt.Y(f"{y}:Q", title=None, stack=stack) if not horizontal else alt.Y(f"{x}:N", title=None, sort=sort or "-x")
    ch = alt.Chart(df).mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(x=enc_x, y=enc_y, tooltip=list(df.columns))
    if color:
        sc = alt.Scale(domain=list(colors), range=list(colors.values())) if colors else alt.Undefined
        ch = ch.encode(color=alt.Color(f"{color}:N", scale=sc, legend=alt.Legend(orient="bottom", title=None)))
    return ch.properties(height=height, title=title or "").configure_view(strokeWidth=0).configure_axis(grid=False, labelColor="#4A5272",
                                                                                                       domainColor="#E3E7F0")


def donut(value, total, color="#2E9E5B", label="", size=130):
    pct = 0 if not total else value / total
    df = pd.DataFrame({"k": ["done", "left"], "v": [value, max(0, total - value)]})
    base = alt.Chart(df).mark_arc(innerRadius=size * 0.32, outerRadius=size * 0.46).encode(
        theta="v:Q", color=alt.Color("k:N", scale=alt.Scale(domain=["done", "left"], range=[color, "#EEF0F5"]), legend=None))
    txt = alt.Chart(pd.DataFrame({"t": [f"{pct:.0%}"]})).mark_text(fontSize=size * 0.16, fontWeight="bold", color="#1A2036").encode(text="t:N")
    return (base + txt).properties(width=size, height=size, title=alt.TitleParams(label, fontSize=12, color="#4A5272", anchor="middle")) \
        .configure_view(strokeWidth=0)


def vehicle_check_form(bus_id, kind, trip_no, user, key):
    """In-app pre-trip / post-trip checklist (DPS driver checklist). A critical defect blocks the trip start."""
    items = campus.PRE_CHECKS if kind == "PRE" else campus.POST_CHECKS
    with st.form(f"vc_{key}_{kind}"):
        st.markdown(f"**{'🔧 Pre-trip vehicle check' if kind == 'PRE' else '🧹 Post-trip check'}** — tap *Defect* for anything not OK. "
                    + ("Items marked ⛔ block the trip start." if kind == "PRE" else ""))
        ans = {}
        sec = None
        for s_, item, crit in items:
            if s_ != sec:
                st.markdown(f"<div class='mini' style='margin-top:6px;font-weight:700'>{s_.upper()}</div>", unsafe_allow_html=True)
                sec = s_
            ans[item] = st.radio(("⛔ " if crit and kind == "PRE" else "") + item, ["OK", "Defect", "N/A"], horizontal=True,
                                 key=f"vc_{key}_{kind}_{item}")
        c1, c2 = st.columns(2)
        odo = c1.number_input("Odometer (km)", min_value=0, value=0, step=1, key=f"vc_{key}_{kind}_odo")
        fuel = c2.selectbox("Fuel", ["Full", "3/4", "1/2", "1/4", "Reserve"], key=f"vc_{key}_{kind}_fuel")
        ok = st.form_submit_button("Submit check", type="primary", width="stretch")
    if ok:
        answers = {k: ("DEFECT" if v == "Defect" else "N/A" if v == "N/A" else "OK") for k, v in ans.items()}
        cid, status = campus.save_check(bus_id, db.today(), trip_no, kind, answers, user["username"], user["display_name"], odo or None, fuel)
        return status
    return None


def session_now():
    """Which transport session is running / next."""
    import engine as E
    n = E.trip_for_time()
    t = db.TRIPS[n]
    now = db.now().strftime("%H:%M")
    state = "running" if t["window"][0] <= now <= t["window"][1] else "next"
    return n, t, state
