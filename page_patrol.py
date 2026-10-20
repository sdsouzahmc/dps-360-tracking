"""Security patrols dashboard: active rounds, completion, checkpoint coverage map, exceptions, activity, incidents (last 7 days)."""
from datetime import datetime, timedelta

import altair as alt
import pandas as pd
import pydeck as pdk
import streamlit as st

import db
import ui360 as U

U.css()
st.markdown("""<style>
.pex{display:flex;gap:10px;align-items:flex-start;padding:8px 2px;border-bottom:1px solid #EEF0F5}
.pex:last-child{border-bottom:none}
.pex .ic{width:30px;height:30px;border-radius:8px;display:flex;align-items:center;justify-content:center;flex:none;font-size:1rem}
.pex .tt{font-weight:700;font-size:.9rem;color:#1A2036} .pex .dd{font-size:.8rem;color:#4A5272}
.pex .tm{margin-left:auto;font-size:.75rem;color:#8A90A6;white-space:nowrap}
.plegend span{display:inline-flex;align-items:center;gap:5px;margin-right:14px;font-size:.8rem;color:#4A5272}
.plegend i{width:10px;height:10px;border-radius:50%;display:inline-block}
</style>""", unsafe_allow_html=True)

NOW = db.now()
TODAY = NOW.strftime("%Y-%m-%d")
GREEN, AMBER, RED, BLUE, GREY = "#2E9E5B", "#E0B01F", "#D93025", "#3D4FD6", "#B8BDCC"
SEC_KINDS = ("PATROL_MISSED", "UNAUTHORIZED_EXIT", "ABSENT_ON_CAMPUS", "VISITOR_OVERSTAY", "INTRUSION", "SECURITY")

h1, h2, h3, h4 = st.columns([3, 1.3, 1.3, .8], vertical_alignment="bottom")
h1.title("🛡️ Security patrols")
day = h2.date_input("Day", value=NOW.date(), max_value=NOW.date(), key="pat_day").strftime("%Y-%m-%d")
plans = [r["plan"] for r in db.q("SELECT DISTINCT plan FROM patrol_rounds WHERE date=? ORDER BY plan", (day,))]
plan_f = h3.selectbox("Patrol plan", ["All plans"] + plans, key="pat_plan")
if h4.button("🔄", help="Refresh", width="stretch"):
    st.rerun()
st.caption(("Live · " if day == TODAY else "Snapshot · ") + f"{datetime.strptime(day, '%Y-%m-%d'):%A %d %b %Y} · night rounds after midnight "
           "count for the evening the shift started · upload guard-tour exports in Security app → Patrol")


def cp_key(name):
    """'Gate 1 · Main gate' and 'Gate 1' are the same checkpoint."""
    return (name or "?").split("·")[0].strip()


GATES = {cp_key(g["name"]): g for g in db.q("SELECT * FROM gates ORDER BY id")}


def pts(s):
    try:
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S") if s else None
    except ValueError:
        return None


rows = db.q("SELECT * FROM patrol_rounds WHERE date=? ORDER BY COALESCE(planned_ts, actual_ts)", (day,))
if plan_f != "All plans":
    rows = [r for r in rows if r["plan"] == plan_f]

# ------------------------------------------------------------------ rounds
plan_cps = {}
for r in rows:
    plan_cps.setdefault(r["plan"], set()).add(cp_key(r["checkpoint"]))
rounds = {}
for r in rows:
    k = (r["plan"], "R", r["round_no"]) if r["round_no"] is not None else (r["plan"], r["guard"], (r["planned_ts"] or r["actual_ts"] or "")[:13])
    rounds.setdefault(k, []).append(r)

R = []
for k, rs in rounds.items():
    plan = k[0]
    y = max(len(plan_cps.get(plan, ())), 1)
    done = [r for r in rs if r["status"] in ("ARRIVED", "LATE")]
    missed = [r for r in rs if r["status"] == "OMITTED"]
    late = [r for r in rs if r["status"] == "LATE"]
    seen = len({cp_key(r["checkpoint"]) for r in rs})
    times = [t for t in (pts(r["actual_ts"]) or pts(r["planned_ts"]) for r in rs) if t]
    first_plan = min((t for t in (pts(r["planned_ts"]) for r in rs) if t), default=None)
    first_act = min((t for t in (pts(r["actual_ts"]) for r in rs) if t), default=None)
    last = max(times) if times else None
    active = day == TODAY and seen < y and last is not None and timedelta(0) <= NOW - last <= timedelta(minutes=45)
    if active:
        status = "In progress"
    elif missed or late or seen < y:
        status = "Review"
    else:
        status = "Complete"
    start = first_plan or first_act
    R.append({"key": k, "plan": plan, "guard": ", ".join(sorted({r["guard"] or "—" for r in rs})), "start": start, "last": last,
              "done": len(done), "missed": len(missed), "late": len(late), "seen": seen, "y": y, "status": status, "rows": rs,
              "pending": y - seen if active else 0,
              "delay": (first_act - first_plan).total_seconds() / 60 if first_act and first_plan else 0})
R.sort(key=lambda x: x["start"] or datetime.min, reverse=True)

verified = sum(x["done"] for x in R)
missed_n = sum(x["missed"] for x in R)
pending_n = sum(x["pending"] for x in R)
late_n = sum(x["late"] for x in R)
active_n = sum(1 for x in R if x["status"] == "In progress")
den = verified + missed_n + pending_n
alerts = db.q(f"""SELECT * FROM campus_alerts WHERE date=? AND (audience='security' OR kind IN ({','.join('?' * len(SEC_KINDS))}))
                  ORDER BY ts DESC""", (day, *SEC_KINDS))
open_al = [a for a in alerts if a["status"] != "RESOLVED"]
high_open = sum(1 for a in open_al if a["severity"] == "HIGH")

U.kpis([("ACTIVE PATROLS", active_n, f"{len({x['guard'] for x in R if x['status'] == 'In progress'})} guard(s) on a round" if active_n else
         f"{len(R)} round(s) on this day", BLUE, ""),
        ("PATROL COMPLETION", f"{verified / den:.0%}" if den else "—", f"{verified} of {den} checkpoints", GREEN, "green"),
        ("CHECKPOINTS VERIFIED", verified, f"{late_n} late · {missed_n} missed", "#1B2559", ""),
        ("OPEN INCIDENTS", len(open_al), f"{high_open} high priority", RED, "red" if open_al else "")], n=4)

if not R:
    st.info("No patrol records for this day yet. Rounds from the guard-tour app appear here as they are scanned, or upload an export "
            "(CSV / Excel) in Security app → Patrol.")

# ------------------------------------------------------------------ map | exceptions
c1, c2 = st.columns([1.55, 1])
with c1, st.container(border=True):
    st.markdown("<div class='sect'>🗺️ Patrol coverage map</div>", unsafe_allow_html=True)
    sel_opts = {f"{x['plan']} · {x['start']:%H:%M} · {x['guard']}" if x["start"] else f"{x['plan']} · {x['guard']}": i for i, x in enumerate(R)}
    pick = st.selectbox("Round", ["Whole day"] + list(sel_opts), key="pat_round", label_visibility="collapsed") if R else "Whole day"
    focus = R[sel_opts[pick]]["rows"] if pick != "Whole day" else rows
    last_state = {}
    for r in sorted(focus, key=lambda r: r["actual_ts"] or r["planned_ts"] or ""):
        last_state.setdefault(cp_key(r["checkpoint"]), []).append(r["status"])
    pts_ = []
    for key, g in GATES.items():
        sts = last_state.get(key)
        if not sts:
            col, lab = ([184, 189, 204] if not R else [224, 176, 31]), "Not visited" if day != TODAY else "Pending"
        elif "OMITTED" in sts:
            col, lab = [217, 48, 37], f"Missed {sts.count('OMITTED')}×"
        elif "LATE" in sts:
            col, lab = [224, 176, 31], f"Verified ({sts.count('LATE')} late)"
        else:
            col, lab = [46, 158, 91], "Verified"
        pts_.append({"lat": g["lat"], "lon": g["lon"], "name": key, "label": f"{g['name']} · {lab} · {len(sts or [])} scan(s)",
                     "col": col})
    route = []
    if pick != "Whole day":
        for r in sorted(focus, key=lambda r: r["actual_ts"] or r["planned_ts"] or ""):
            g = GATES.get(cp_key(r["checkpoint"]))
            if g:
                route.append([g["lon"], g["lat"]])
    else:   # usual patrol route: around the campus perimeter, gate to gate, back to the start
        import math
        cx = sum(g["lon"] for g in GATES.values()) / max(1, len(GATES))
        cy = sum(g["lat"] for g in GATES.values()) / max(1, len(GATES))
        route = sorted(([g["lon"], g["lat"]] for g in GATES.values()), key=lambda p: math.atan2(p[1] - cy, p[0] - cx))
        route += route[:1]
    lat0 = sum(g["lat"] for g in GATES.values()) / max(1, len(GATES)) if GATES else db.school_pos()[0]
    lon0 = sum(g["lon"] for g in GATES.values()) / max(1, len(GATES)) if GATES else db.school_pos()[1]
    if GATES:
        la = [g["lat"] for g in GATES.values()]
        lo = [g["lon"] for g in GATES.values()]
        pad = .00045
        poly = [[[min(lo) - pad, min(la) - pad], [max(lo) + pad, min(la) - pad], [max(lo) + pad, max(la) + pad], [min(lo) - pad, max(la) + pad]]]
    layers = []
    if GATES:
        layers.append(pdk.Layer("PolygonLayer", [{"poly": poly[0]}], get_polygon="poly", get_fill_color=[61, 79, 214, 18],
                                get_line_color=[61, 79, 214, 120], line_width_min_pixels=1, stroked=True))
    if len(route) > 1:
        layers.append(pdk.Layer("PathLayer", [{"path": route}], get_path="path", get_color=[61, 79, 214, 200], width_min_pixels=3,
                                get_width=4, rounded=True))
    if pts_:
        df = pd.DataFrame(pts_)
        layers += [pdk.Layer("ScatterplotLayer", df, get_position="[lon, lat]", get_radius=9, radius_min_pixels=9, get_fill_color="col",
                             get_line_color=[255, 255, 255], line_width_min_pixels=2, stroked=True, pickable=True),
                   pdk.Layer("TextLayer", df, get_position="[lon, lat]", get_text="name", get_size=13, get_pixel_offset=[0, -30], get_text_anchor="'middle'",
                             get_color=[27, 37, 89])]
    st.pydeck_chart(pdk.Deck(layers=layers, initial_view_state=pdk.ViewState(latitude=lat0, longitude=lon0, zoom=17.2),
                             map_style="light", tooltip={"text": "{label}"}), height=340)
    st.markdown(f"<div class='plegend'><span><i style='background:{GREEN}'></i>Verified</span><span><i style='background:{AMBER}'></i>"
                f"Late / pending</span><span><i style='background:{RED}'></i>Missed</span><span><i style='background:#3D4FD6;border-radius:2px;"
                f"height:3px;width:16px'></i>Route</span></div>", unsafe_allow_html=True)

with c2, st.container(border=True):
    st.markdown("<div class='sect'>⚠️ Patrol exceptions</div>", unsafe_allow_html=True)
    ex = []
    for x in R:
        for r in x["rows"]:
            if r["status"] == "OMITTED":
                ex.append((pts(r["planned_ts"]) or x["start"], "📍", "#FDECEA", "Missed checkpoint",
                           f"{r['checkpoint']} · {x['plan']} · {r['guard'] or '—'}"))
        if x["delay"] > 10:
            ex.append((x["start"], "⏱️", "#FFF6DD", "Delayed patrol start",
                       f"{x['plan']} started {x['delay']:.0f} min late · {x['guard']}"))
        elif x["late"]:
            ex.append((x["last"], "⏱️", "#FFF6DD", "Late checkpoint scan", f"{x['late']} late in {x['plan']} · {x['guard']}"))
    for a in open_al:
        if a["kind"] == "PATROL_MISSED":
            continue    # already listed as a missed checkpoint
        ex.append((pts(a["ts"]), "🚨" if a["severity"] == "HIGH" else "🔔", "#EEF0FF", "Incident awaiting review",
                   f"{a['kind'].replace('_', ' ').title()} · {(a['details'] or '')[:58]}{'…' if len(a['details'] or '') > 58 else ''}"))
    ex.sort(key=lambda e: e[0] or datetime.min, reverse=True)
    if ex:
        st.markdown("".join(f"<div class='pex'><div class='ic' style='background:{bg}'>{ic}</div><div><div class='tt'>{tt}</div>"
                            f"<div class='dd'>{dd}</div></div><div class='tm'>{(t_.strftime('%H:%M') if t_ else '')}</div></div>"
                            for t_, ic, bg, tt, dd in ex[:6]), unsafe_allow_html=True)
        if len(ex) > 6:
            st.caption(f"+ {len(ex) - 6} more — see Security app → Alerts.")
    else:
        st.success("No exceptions — every checkpoint verified on time.")

# ------------------------------------------------------------------ activity | donut | incidents
c3, c4, c5 = st.columns([1.9, .9, 1.2])
with c3, st.container(border=True):
    st.markdown("<div class='sect'>📋 Patrol activity</div>", unsafe_allow_html=True)
    if R:
        df = pd.DataFrame([{"Route": f"{x['plan'].replace(' patrol', '')} · {x['start']:%H:%M}" if x["start"] else x["plan"], "Guard": x["guard"],
                            "Progress": f"{x['seen']} of {x['y']}", "Done": x["seen"] / x["y"],
                            "Status": {"In progress": "🔵 In progress", "Complete": "🟢 Complete", "Review": "🟠 Review"}[x["status"]]}
                           for x in R])
        st.dataframe(df, hide_index=True, width="stretch", height=330,
                     column_config={"Route": st.column_config.TextColumn(width=118), "Guard": st.column_config.TextColumn(width=118),
                                    "Progress": st.column_config.TextColumn(width=58),
                                    "Done": st.column_config.ProgressColumn("", min_value=0, max_value=1, format=" ", width=52),
                                    "Status": st.column_config.TextColumn(width=100)})
    else:
        st.caption("No rounds yet.")

with c4, st.container(border=True):
    st.markdown("<div class='sect'>🎯 Checkpoints</div>", unsafe_allow_html=True)
    tot = verified + pending_n + missed_n
    dd = pd.DataFrame({"k": ["Verified", "Pending", "Missed"], "v": [verified, pending_n, missed_n]})
    if tot == 0:
        dd = pd.DataFrame({"k": ["None"], "v": [1]})
    arc = alt.Chart(dd).mark_arc(innerRadius=52, outerRadius=78).encode(
        theta="v:Q", color=alt.Color("k:N", scale=alt.Scale(domain=["Verified", "Pending", "Missed", "None"], range=[GREEN, AMBER, RED, "#EEF0F5"]),
                                     legend=None), tooltip=["k:N", "v:Q"])
    txt = alt.Chart(pd.DataFrame({"t": [f"{tot}"], "s": ["checkpoints"]})).mark_text(fontSize=26, fontWeight="bold", color="#1A2036", dy=-6).encode(text="t:N")
    sub = alt.Chart(pd.DataFrame({"s": ["total"]})).mark_text(fontSize=11, color="#8A90A6", dy=16).encode(text="s:N")
    st.altair_chart((arc + txt + sub).properties(height=180), width="stretch")
    st.markdown(f"<div class='plegend' style='text-align:center'><span><i style='background:{GREEN}'></i>Verified {verified}</span>"
                f"<span><i style='background:{AMBER}'></i>Pending {pending_n}</span><span><i style='background:{RED}'></i>Missed {missed_n}</span></div>",
                unsafe_allow_html=True)

with c5, st.container(border=True):
    st.markdown("<div class='sect'>📊 Incident overview · last 7 days</div>", unsafe_allow_html=True)
    d1 = datetime.strptime(day, "%Y-%m-%d")
    days7 = [(d1 - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(6, -1, -1)]
    inc = db.q(f"""SELECT date, SUM(status='RESOLVED') res, SUM(status!='RESOLVED') opn FROM campus_alerts
                   WHERE date BETWEEN ? AND ? AND (audience='security' OR kind IN ({','.join('?' * len(SEC_KINDS))})) GROUP BY date""",
               (days7[0], days7[-1], *SEC_KINDS))
    m = {r["date"]: r for r in inc}
    bars = []
    for d in days7:
        lab = datetime.strptime(d, "%Y-%m-%d").strftime("%a %d")
        bars += [{"Day": lab, "State": "Resolved", "n": int((m.get(d) or {}).get("res") or 0), "o": 0},
                 {"Day": lab, "State": "Open", "n": int((m.get(d) or {}).get("opn") or 0), "o": 1}]
    bdf = pd.DataFrame(bars)
    ch = alt.Chart(bdf).mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3, size=18).encode(
        x=alt.X("Day:N", sort=[datetime.strptime(d, "%Y-%m-%d").strftime("%a %d") for d in days7], title=None,
                axis=alt.Axis(labelAngle=0, labelOverlap=False, labelExpr="split(datum.label, ' ')[0]")),
        y=alt.Y("n:Q", title=None, stack=True), order=alt.Order("o:Q"),
        color=alt.Color("State:N", scale=alt.Scale(domain=["Resolved", "Open"], range=[GREEN, RED]), legend=alt.Legend(orient="bottom", title=None)),
        tooltip=["Day", "State", "n"])
    st.altair_chart(ch.properties(height=230), width="stretch")
    st.caption(f"{int(bdf[bdf.State == 'Open'].n.sum())} open · {int(bdf[bdf.State == 'Resolved'].n.sum())} resolved (security alerts incl. missed checkpoints)")
