"""DPS 360 Command centre: every student, bus, gate, visitor, class and safety check on one live screen."""
from collections import Counter
from datetime import datetime, timedelta

import altair as alt
import pandas as pd
import pydeck as pdk
import streamlit as st

import campus
import db
import engine as E
from db import TRIPS
import ui360 as U

U.css()
user = st.session_state.user
role = user["role"]

c_sp, c_view, c_live = st.columns([4, 3, 1], vertical_alignment="center")
view = c_view.segmented_control("View", ["Today", "Last school days"], default="Today", key="d360_view", label_visibility="collapsed") or "Today"
live = c_live.toggle("Live", value=True, help="Refresh every 20 seconds (TV mode)")


def hero():
    n, t, state = U.session_now()
    now = db.now()
    off = db.clock_offset()
    if True:
        st.markdown(
            f"<div class='hero'><div><div class='ttl'>DPS 360 Command Centre</div>"
            f"<div class='sub'>{db.setting('school_name', db.SCHOOL_NAME)} · students, buses, gates, classes and safety in one view</div>"
            f"<div class='sess'>🚌 Trip {n} · {t['short']} · {t['window'][0]}–{t['window'][1]} · {'running now' if state == 'running' else 'next'}</div></div>"
            f"<div class='clock'><b>{now:%H:%M}</b>{now:%A %d %b %Y}"
            + (f"<div class='sess'>⏱ demo clock</div>" if abs(off) > 60 else "") + "</div></div>", unsafe_allow_html=True)


def series_today(D, now):
    """Students on campus and on buses through the day (10-minute steps)."""
    mv = pd.DataFrame(db.q("SELECT pid, ts, dir FROM campus_moves WHERE ptype='STUDENT' AND date=? AND ts IS NOT NULL", (D,)))
    bus = pd.DataFrame(db.q("""SELECT m.entry_ts AS a, m.exit_ts AS b FROM manifest m JOIN trip_runs r ON r.id=m.run_id
                               WHERE r.date=? AND m.entry_ts IS NOT NULL""", (D,)))
    start = datetime.strptime(f"{D} 05:30", "%Y-%m-%d %H:%M")
    end = min(now, datetime.strptime(f"{D} 20:00", "%Y-%m-%d %H:%M"))
    if end <= start:
        return pd.DataFrame()
    slots = pd.date_range(start, end, freq="10min")
    out = []
    if not mv.empty:
        mv["ts"] = pd.to_datetime(mv["ts"])
        mv = mv.sort_values("ts")
        mv["d"] = mv["dir"].map({"IN": 1, "OUT": -1})
        # count each student's state change only once per direction (repeat scans ignored)
        mv["prev"] = mv.groupby("pid")["dir"].shift()
        mv = mv[mv["dir"] != mv["prev"]]
        mv.loc[(mv["prev"].isna()) & (mv["dir"] == "OUT"), "d"] = 0
    if not bus.empty:
        bus["a"] = pd.to_datetime(bus["a"])
        bus["b"] = pd.to_datetime(bus["b"])
    for s in slots:
        oc = int(mv.loc[mv["ts"] <= s, "d"].sum()) if not mv.empty else 0
        ob = int(((bus["a"] <= s) & ((bus["b"].isna()) | (bus["b"] > s))).sum()) if not bus.empty else 0
        out += [{"time": s, "where": "On campus", "students": max(0, oc)}, {"time": s, "where": "On buses", "students": ob}]
    return pd.DataFrame(out)


def board():
    hero()
    D = db.today()
    now = db.now()
    pres = campus.presence(D, now)
    cnt = Counter(pres.values())
    tot = len(pres)
    stu = db.q1("SELECT COUNT(*) n, SUM(transport='BUS') b FROM students WHERE active=1")
    decl = db.q1("SELECT COUNT(DISTINCT student_id) n FROM absences WHERE date=? AND status='APPROVED' AND trip_no IS NULL", (D,))["n"]
    runs = db.q("SELECT id, bus_id, trip_no, status, start_ts FROM trip_runs WHERE date=?", (D,))
    planned_runs = db.q("SELECT trip_no, COUNT(DISTINCT bus_id) n FROM trip_assign GROUP BY trip_no")
    planned_total = sum(r["n"] for r in planned_runs)
    closed = sum(1 for r in runs if r["status"] == "CLOSED")
    active = [r for r in runs if r["status"] in ("IN_PROGRESS", "PENDING_SYNC")]
    ontime = sum(1 for r in runs if r["start_ts"] and r["start_ts"][11:16] <= (datetime.strptime(TRIPS[r["trip_no"]]["window"][0], "%H:%M")
                                                                               + timedelta(minutes=10)).strftime("%H:%M"))
    subm = db.q1("SELECT COUNT(*) n FROM att_submissions WHERE date=?", (D,))["n"]
    ncls = db.q1("SELECT COUNT(*) n FROM classes")["n"]
    due_cls = db.q1("SELECT COUNT(*) n FROM classes WHERE shift=1")["n"] + (db.q1("SELECT COUNT(*) n FROM classes WHERE shift=2")["n"]
                                                                           if now.strftime("%H:%M") >= "13:10" else 0)
    vis = campus.visitors(D)
    vis_on = [v for v in vis if v["status"] == "ON_CAMPUS"]
    staff_in = sum(1 for m in campus.last_moves(D, "STAFF").values() if m["dir"] == "IN")
    al = campus.alerts(D, "OPEN")
    al_hi = [a for a in al if a["severity"] in ("HIGH", "CRITICAL")]

    U.kpis([
        ("STUDENTS ENROLLED", tot, f"{stu['b']:,} by bus · {tot - stu['b']:,} private", "#1B2559", ""),
        ("ON CAMPUS NOW", cnt["ON_CAMPUS"], f"{cnt['ON_CAMPUS'] / max(1, tot):.0%} of students", "#2E9E5B", "green"),
        ("ON BUSES NOW", cnt["ON_BUS"], f"{len(active)} buses on the road", "#3D4FD6", ""),
        ("GONE HOME", cnt["LEFT"], "dropped / collected / left", "#9AA3BD", ""),
        ("ABSENT TODAY", cnt["ABSENT"], f"{decl} declared by parents", "#E07A1F", "orange"),
        ("UNACCOUNTED", cnt["UNACCOUNTED"], "not on campus, bus or absence list", "#D93025", "red" if cnt["UNACCOUNTED"] else "green"),
    ])
    U.kpis([
        ("BUS TRIPS TODAY", f"{closed}/{planned_total}", f"{len(active)} running · {planned_total - closed - len(active)} to go", "#3D4FD6", ""),
        ("ON-TIME STARTS", f"{ontime / max(1, len(runs)):.0%}", f"{len(runs) - ontime} late of {len(runs)} started", "#2E9E5B", ""),
        ("ATTENDANCE SUBMITTED", f"{subm}/{ncls}", f"{max(0, due_cls - subm)} classes due, not in" if due_cls > subm else "all due classes in",
         "#7B5CD6", "orange" if due_cls - subm > 3 else ""),
        ("VISITORS ON CAMPUS", len(vis_on), f"{len(vis)} today · {sum(1 for a in al if a['kind'] == 'VISITOR_OVERSTAY')} overstay",
         "#0F9BB5", ""),
        ("STAFF ON CAMPUS", staff_in, "card in at staff gate", "#1B2559", ""),
        ("OPEN ALERTS", len(al), f"{len(al_hi)} high priority", "#D93025", "red" if al_hi else ""),
    ])

    # ---------------------------------------------------------------- where are the students + headcount curve | alerts
    left, right = st.columns([1.75, 1])
    with left, st.container(border=True):
        st.markdown("<div class='sect'>👥 Where every student is right now</div>", unsafe_allow_html=True)
        order = ["ON_CAMPUS", "ON_BUS", "LEFT", "ABSENT", "NOT_ARRIVED", "UNACCOUNTED", "NOT_SEEN"]
        st.markdown("<div class='sbar'>" + "".join(f"<div style='width:{cnt[k] / max(1, tot) * 100:.2f}%;background:{U.STATE_COLOR[k]}' "
                                                    f"title='{U.STATE_LABEL[k]}: {cnt[k]}'></div>" for k in order if cnt[k]) + "</div>"
                    + "<div class='legend'>" + "".join(f"<span style='--c:{U.STATE_COLOR[k]}'>{U.STATE_LABEL[k]} <b>{cnt[k]:,}</b></span>"
                                                       for k in order if cnt[k]) + "</div>", unsafe_allow_html=True)
        sr = series_today(D, now)
        if not sr.empty:
            ch = alt.Chart(sr).mark_area(opacity=.55, interpolate="monotone").encode(
                x=alt.X("time:T", title=None, axis=alt.Axis(format="%H:%M", labelColor="#4A5272")),
                y=alt.Y("students:Q", title=None, stack=None),
                color=alt.Color("where:N", scale=alt.Scale(domain=["On campus", "On buses"], range=["#2E9E5B", "#3D4FD6"]),
                                legend=alt.Legend(orient="top-left", title=None)),
                tooltip=[alt.Tooltip("time:T", format="%H:%M"), "where", "students"])
            rules = alt.Chart(pd.DataFrame([{"t": datetime.strptime(f"{D} {TRIPS[n]['window'][0]}", "%Y-%m-%d %H:%M"), "lab": f"T{n}"}
                                            for n in TRIPS])).mark_rule(color="#C9CFE0", strokeDash=[3, 3]).encode(x="t:T")
            st.altair_chart((ch + rules).properties(height=250).configure_view(strokeWidth=0).configure_axis(grid=False, domainColor="#E3E7F0"),
                            width="stretch")
            st.caption("Students on campus (gate and bus-bay scans) and on buses (boarded, not yet off), in 10-minute steps. Dashed lines: trip start times.")
    with right, st.container(border=True):
        st.markdown(f"<div class='sect'>🚨 Live alerts <span class='muted' style='margin-left:auto;font-weight:400'>{len(al)} open</span></div>",
                    unsafe_allow_html=True)
        kinds = Counter(a["kind"] for a in al)
        if kinds:
            st.markdown(" ".join(U.pill(f"{campus.ALERT_TEXT.get(k, k)} · {v}", "red" if k in ("ABSENT_ON_CAMPUS", "UNAUTHORIZED_EXIT", "UNACCOUNTED")
                                         else "orange" if k in ("VEHICLE_DEFECT", "VISITOR_OVERSTAY", "ABSENT_NOT_ON_CAMPUS") else "grey")
                                 for k, v in kinds.most_common()), unsafe_allow_html=True)
        inc = db.q("""SELECT i.ts, i.kind, i.severity, i.details FROM incidents i WHERE i.status<>'RESOLVED' AND i.ts LIKE ? ORDER BY i.id DESC LIMIT 6""",
                   (D + "%",))
        items = [(a["ts"], a["severity"], a["details"]) for a in al[:40]] + [(i["ts"], i["severity"], "🚌 " + i["details"]) for i in inc]
        items.sort(key=lambda x: ({"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2}.get(x[1], 3), -int((x[0] or "0")[11:13] + (x[0] or "0000000000000000")[14:16])))
        st.markdown("<div class='feed' style='max-height:330px'>" + "".join(
            f"<div class='it'><span class='tm'>{(t_ or '')[11:16]}</span><span>{U.pill(sv.title(), U.SEV_PILL.get(sv, 'grey'))} {dt}</span></div>"
            for t_, sv, dt in items[:30]) + "</div>" if items else "<div class='muted'>No open alerts.</div>", unsafe_allow_html=True)

    # ---------------------------------------------------------------- transport sessions | live bus map
    l2, r2 = st.columns([1.1, 1])
    with l2, st.container(border=True):
        st.markdown("<div class='sect'>🚌 Transport sessions today</div>", unsafe_allow_html=True)
        pl = {r["trip_no"]: r["n"] for r in planned_runs}
        lines = {r["trip_no"]: r for r in db.q("""SELECT r.trip_no, COUNT(*) n, SUM(m.status IN ('COMPLETED')) done, SUM(m.status='ONBOARD') onb,
                                                  SUM(m.status IN ('ABSENT_DECLARED','NO_SHOW')) abs FROM manifest m JOIN trip_runs r ON r.id=m.run_id
                                                  WHERE r.date=? GROUP BY r.trip_no""", (D,))}
        rows = []
        for n, t in TRIPS.items():
            rr = [r for r in runs if r["trip_no"] == n]
            c_ = sum(1 for r in rr if r["status"] == "CLOSED")
            a_ = sum(1 for r in rr if r["status"] in ("IN_PROGRESS", "PENDING_SYNC"))
            tot_b = pl.get(n, t["buses"])
            ln = lines.get(n, {})
            pct_c, pct_a = c_ / max(1, tot_b) * 100, a_ / max(1, tot_b) * 100
            state = "Done" if c_ >= tot_b else "Running" if a_ else "Planned"
            pc = {"Done": "green", "Running": "blue", "Planned": "grey"}[state]
            rows.append(f"<div style='margin:8px 0 12px'><div style='display:flex;align-items:center;gap:8px'>"
                        f"<b style='min-width:52px'>Trip {n}</b><span style='font-size:.86rem'>{t['short']}</span>"
                        f"<span class='muted'>{t['window'][0]}–{t['window'][1]}</span>{U.pill(state, pc)}"
                        f"<span class='muted' style='margin-left:auto'>{c_ + a_}/{tot_b} buses · {(ln.get('done') or 0):,} delivered"
                        + (f" · {ln.get('onb') or 0} on board" if a_ else "") + "</span></div>"
                        f"<div class='sbar'><div style='width:{pct_c:.1f}%;background:#2E9E5B'></div><div style='width:{pct_a:.1f}%;background:#3D4FD6'></div></div></div>")
        st.markdown("".join(rows), unsafe_allow_html=True)
        chk = db.q1("SELECT COUNT(DISTINCT bus_id) n, SUM(status='BLOCKED') bl FROM vehicle_checks WHERE date=? AND kind='PRE'", (D,))
        op = len({r["bus_id"] for r in runs})
        st.caption(f"Pre-trip vehicle checks: {chk['n']} of {op} operating buses · {chk['bl'] or 0} blocked now · "
                   f"late starts today: {len(runs) - ontime}")
    with r2, st.container(border=True):
        st.markdown(f"<div class='sect'>🗺 Buses on the road <span class='muted' style='margin-left:auto;font-weight:400'>{len(active)} live</span></div>",
                    unsafe_allow_html=True)
        pts = []
        ids = [r["id"] for r in active] or [-1]
        ph = ",".join("?" * len(ids))
        last = {r["run_id"]: r for r in db.q(f"""SELECT run_id, lat, lon, MAX(captured_ts) ts FROM events WHERE run_id IN ({ph}) AND lat IS NOT NULL
                                                 GROUP BY run_id""", ids)}
        onb = {r["run_id"]: r["n"] for r in db.q(f"SELECT run_id, COUNT(*) n FROM manifest WHERE run_id IN ({ph}) AND status='ONBOARD' GROUP BY run_id", ids)}
        busno = {b["id"]: b["bus_no"] for b in db.buses()}
        for r in active:
            e = last.get(r["id"])
            if e:
                pts.append({"lat": e["lat"], "lon": e["lon"], "label": f"{busno[r['bus_id']]} · Trip {r['trip_no']} · {onb.get(r['id'], 0)} on board",
                            "bus": busno[r["bus_id"]][4:]})
        lat, lon = db.school_pos()
        layers = [pdk.Layer("ScatterplotLayer", pd.DataFrame([{"lat": lat, "lon": lon, "label": "DPS campus"}]), get_position="[lon, lat]",
                            get_radius=260, get_fill_color=[217, 48, 37, 200], pickable=True)]
        if pts:
            df = pd.DataFrame(pts)
            layers += [pdk.Layer("ScatterplotLayer", df, get_position="[lon, lat]", get_radius=150, radius_min_pixels=6,
                                 get_fill_color=[61, 79, 214, 220], get_line_color=[255, 255, 255], line_width_min_pixels=1, stroked=True,
                                 pickable=True)]
            if len(df) <= 25:
                layers.append(pdk.Layer("TextLayer", df, get_position="[lon, lat]", get_text="bus", get_size=11, get_pixel_offset=[0, -14],
                                        get_color=[27, 37, 89]))
        st.pydeck_chart(pdk.Deck(layers=layers, initial_view_state=pdk.ViewState(latitude=lat - .01, longitude=lon + .03, zoom=10.4),
                                 map_style="light", tooltip={"text": "{label}"}), height=330)

    # ---------------------------------------------------------------- arrivals by mode | attendance by grade | gates
    a1, a2, a3 = st.columns(3)
    with a1, st.container(border=True):
        st.markdown("<div class='sect'>🕖 How students arrived</div>", unsafe_allow_html=True)
        fi = pd.DataFrame(db.q("""SELECT pid, method, MIN(ts) ts FROM campus_moves WHERE ptype='STUDENT' AND date=? AND dir='IN'
                                  GROUP BY pid""", (D,)))
        if not fi.empty:
            fi["ts"] = pd.to_datetime(fi["ts"])
            fi["slot"] = fi["ts"].dt.floor("15min").dt.strftime("%H:%M")
            fi["Mode"] = fi["method"].map(lambda m: "School bus" if m == "BUS" else "Private (gate)")
            g = fi.groupby(["slot", "Mode"]).size().reset_index(name="Students")
            st.altair_chart(U.bar_chart(g, "slot", "Students", "Mode", 220, colors={"School bus": "#3D4FD6", "Private (gate)": "#0F9BB5"}),
                            width="stretch")
            late = db.q1("SELECT COUNT(*) n FROM attendance WHERE date=? AND status='LATE'", (D,))["n"]
            st.caption(f"First entry per student, 15-minute steps · {late} late arrivals marked by teachers")
        else:
            st.caption("No arrivals yet today.")
    with a2, st.container(border=True):
        st.markdown("<div class='sect'>✅ Class attendance by grade</div>", unsafe_allow_html=True)
        at = pd.DataFrame(db.q("""SELECT c.name AS grade, a.status, COUNT(*) n FROM attendance a JOIN classes c ON c.id=a.class_id
                                  WHERE a.date=? GROUP BY c.name, a.status""", (D,)))
        if not at.empty:
            at["Status"] = at["status"].str.title()
            ordr = ["KG1", "KG2"] + [f"Grade {i}" for i in range(1, 13)]
            at["grade"] = at["grade"].str.replace("Grade ", "G")
            st.altair_chart(U.bar_chart(at.rename(columns={"n": "Students"}), "grade", "Students", "Status", 220,
                                        colors={"Present": "#2E9E5B", "Late": "#E0B01F", "Absent": "#E07A1F"},
                                        sort=[o.replace("Grade ", "G") for o in ordr]), width="stretch")
            p_ = at.loc[at["status"].isin(["PRESENT", "LATE"]), "n"].sum()
            st.caption(f"{p_ / max(1, at['n'].sum()):.1%} present in submitted classes · {subm}/{ncls} classes submitted")
        else:
            st.caption("No class has submitted attendance yet.")
    with a3, st.container(border=True):
        st.markdown("<div class='sect'>🚪 Gate activity today</div>", unsafe_allow_html=True)
        ga = pd.DataFrame(db.q("""SELECT g.name AS gate, gs.ptype, COUNT(*) n FROM gate_scans gs JOIN gates g ON g.id=gs.gate_id
                                  WHERE gs.date=? AND gs.result='OK' GROUP BY g.name, gs.ptype""", (D,)))
        bb = db.q1("""SELECT COUNT(*) n FROM manifest m JOIN trip_runs r ON r.id=m.run_id WHERE r.date=? AND
                      (m.handover='SCHOOL_RECEIPT' OR (m.purpose='DROPOFF' AND m.entry_ts IS NOT NULL))""", (D,))["n"]
        rows = ga.to_dict("records") if not ga.empty else []
        rows.append({"gate": "Gate 3 · Bus bay", "ptype": "STUDENT", "n": bb})
        g = pd.DataFrame(rows)
        g["Who"] = g["ptype"].str.title()
        g["gate"] = g["gate"].str.replace("Gate ", "G").str.replace(" · ", " ")
        st.altair_chart(U.bar_chart(g.rename(columns={"n": "Scans"}), "gate", "Scans", "Who", 220, horizontal=True,
                                    colors={"Student": "#3D4FD6", "Staff": "#1B2559", "Visitor": "#0F9BB5"}), width="stretch")
        rej = db.q1("SELECT COUNT(*) n FROM gate_scans WHERE date=? AND result='REJECTED'", (D,))["n"]
        st.caption(f"Card and pass scans by gate (bus bay = bus hand-overs) · {rej} rejected")

    # ---------------------------------------------------------------- compliance
    with st.container(border=True):
        st.markdown("<div class='sect'>🛡 Safety & compliance today</div>", unsafe_allow_html=True)
        pre = db.q1("SELECT COUNT(DISTINCT bus_id) n FROM vehicle_checks WHERE date=? AND kind='PRE'", (D,))["n"]
        done_buses = {r["bus_id"] for r in runs}
        last_trip_done = {b for b in done_buses if all(r["status"] == "CLOSED" for r in runs if r["bus_id"] == b)
                          and max(campus.bus_trips(b)) in {r["trip_no"] for r in runs if r["bus_id"] == b}}
        post = db.q1("SELECT COUNT(DISTINCT bus_id) n FROM vehicle_checks WHERE date=? AND kind='POST'", (D,))["n"]
        pr = db.q1("SELECT COUNT(*) n, SUM(status='ARRIVED') ok FROM patrol_rounds WHERE date=? AND planned_ts<=?", (D, db.ts(now)))
        sweep = db.q1("SELECT COUNT(*) n, SUM(sweep_by IS NOT NULL AND sweep_by<>'') ok FROM trip_runs WHERE date=? AND status='CLOSED'", (D,))
        oncamp_scan = sum(1 for k, v in pres.items() if v == "ON_CAMPUS")
        d = st.columns(5)
        with d[0]:
            st.altair_chart(U.donut(pre, max(1, len(done_buses)), "#3D4FD6", "Pre-trip checks"), width="content")
            st.caption(f"{pre} of {len(done_buses)} buses")
        with d[1]:
            st.altair_chart(U.donut(post, max(1, len(last_trip_done)), "#7B5CD6", "Post-trip checks"), width="content")
            st.caption(f"{post} of {len(last_trip_done)} buses finished for the day")
        with d[2]:
            st.altair_chart(U.donut(sweep["ok"] or 0, max(1, sweep["n"]), "#2E9E5B", "Child-left-behind sweep"), width="content")
            st.caption(f"{sweep['ok'] or 0} of {sweep['n']} closed trips")
        with d[3]:
            st.altair_chart(U.donut(pr["ok"] or 0, max(1, pr["n"]), "#0F9BB5", "Patrol on time"), width="content")
            st.caption(f"{pr['ok'] or 0} of {pr['n']} checkpoints so far")
        with d[4]:
            st.altair_chart(U.donut(subm, max(1, due_cls), "#E07A1F", "Attendance in"), width="content")
            st.caption(f"{subm} of {due_cls} classes due")


def trends():
    hero()
    days = E.school_days_back(5) + [db.today()]
    ph = ",".join("?" * len(days))
    att = pd.DataFrame(db.q(f"""SELECT date, SUM(status IN ('PRESENT','LATE')) p, SUM(status='LATE') late, COUNT(*) n FROM attendance
                                WHERE date IN ({ph}) GROUP BY date""", days))
    runs = pd.DataFrame(db.q(f"SELECT date, trip_no, start_ts FROM trip_runs WHERE date IN ({ph}) AND start_ts IS NOT NULL", days))
    vis = pd.DataFrame(db.q(f"SELECT date, COUNT(*) n FROM visitors WHERE date IN ({ph}) GROUP BY date", days))
    al = pd.DataFrame(db.q(f"SELECT date, kind, COUNT(*) n FROM campus_alerts WHERE date IN ({ph}) GROUP BY date, kind", days))
    pr = pd.DataFrame(db.q(f"SELECT date, SUM(status='ARRIVED') ok, COUNT(*) n FROM patrol_rounds WHERE date IN ({ph}) GROUP BY date", days))
    ep = pd.DataFrame(db.q(f"SELECT date, COUNT(*) n FROM early_pickups WHERE date IN ({ph}) GROUP BY date", days))
    lbl = lambda d: datetime.strptime(d, "%Y-%m-%d").strftime("%a %d")
    c1, c2 = st.columns(2)
    with c1, st.container(border=True):
        st.markdown("<div class='sect'>✅ Attendance rate</div>", unsafe_allow_html=True)
        if not att.empty:
            att["Day"] = att["date"].map(lbl)
            att["Present %"] = (att["p"] / att["n"] * 100).round(1)
            st.altair_chart(alt.Chart(att).mark_line(point=True, color="#2E9E5B").encode(
                x=alt.X("Day:N", sort=None, title=None), y=alt.Y("Present %:Q", scale=alt.Scale(zero=False), title=None),
                tooltip=["Day", "Present %", "late"]).properties(height=220).configure_view(strokeWidth=0), width="stretch")
    with c2, st.container(border=True):
        st.markdown("<div class='sect'>🚌 On-time trip starts</div>", unsafe_allow_html=True)
        if not runs.empty:
            runs["late"] = runs.apply(lambda r: r["start_ts"][11:16] > (datetime.strptime(TRIPS[r["trip_no"]]["window"][0], "%H:%M")
                                                                         + timedelta(minutes=10)).strftime("%H:%M"), axis=1)
            g = runs.groupby("date").agg(n=("late", "size"), late=("late", "sum")).reset_index()
            g["Day"] = g["date"].map(lbl)
            g["On time %"] = ((1 - g["late"] / g["n"]) * 100).round(1)
            st.altair_chart(alt.Chart(g).mark_bar(color="#3D4FD6", cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
                x=alt.X("Day:N", sort=None, title=None), y=alt.Y("On time %:Q", scale=alt.Scale(domain=[80, 100]), title=None),
                tooltip=["Day", "On time %", "late", "n"]).properties(height=220).configure_view(strokeWidth=0), width="stretch")
    c3, c4 = st.columns(2)
    with c3, st.container(border=True):
        st.markdown("<div class='sect'>🚨 Alerts by type</div>", unsafe_allow_html=True)
        if not al.empty:
            al["Day"] = al["date"].map(lbl)
            al["Type"] = al["kind"].map(lambda k: campus.ALERT_TEXT.get(k, k))
            st.altair_chart(alt.Chart(al).mark_bar().encode(x=alt.X("Day:N", sort=None, title=None), y=alt.Y("n:Q", title=None),
                                                            color=alt.Color("Type:N", legend=alt.Legend(orient="bottom", columns=2, title=None)),
                                                            tooltip=["Day", "Type", "n"]).properties(height=260).configure_view(strokeWidth=0),
                            width="stretch")
    with c4, st.container(border=True):
        st.markdown("<div class='sect'>🚪 Visitors · early pickups · patrol</div>", unsafe_allow_html=True)
        rows = []
        for d in days:
            rows.append({"Day": lbl(d), "Visitors": int(vis.loc[vis["date"] == d, "n"].sum()) if not vis.empty else 0,
                         "Early pickups": int(ep.loc[ep["date"] == d, "n"].sum()) if not ep.empty else 0,
                         "Patrol on time %": round(float((pr.loc[pr["date"] == d, "ok"].sum() / max(1, pr.loc[pr["date"] == d, "n"].sum())) * 100), 1)
                         if not pr.empty else 0})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption("Today counts up to the current time.")


@st.fragment(run_every=20 if live else None)
def live_board():
    import dps_data
    dps_data.tick()
    if view == "Today":
        board()
    else:
        trends()


live_board()
