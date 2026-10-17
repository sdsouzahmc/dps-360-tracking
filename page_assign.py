"""Bus assignment board: DPS gives each student a bus per session. Move students between buses, swap two students,
merge two routes or switch a whole route to a spare bus — with a capacity check. Changes go straight to the bus tablets."""
import pandas as pd
import streamlit as st

import campus
import db
import engine as E
from db import TRIPS
import ui360 as U
from ui import msg

U.css()
user = st.session_state.user
role = user["role"]
D = db.today()
st.title("🔀 Bus assignment board")
st.caption("One list per session replaces the manual merging and sharing of bus lists. Today-only changes are approved one-day bus changes; "
           "permanent changes update the student's bus for that session. The tablets pick them up automatically.")

trip = st.pills("Session", list(TRIPS), default=st.session_state.get("asg_trip", 1), key="asg_trip",
                format_func=lambda n: f"Trip {n} · {TRIPS[n]['short']} · {TRIPS[n]['window'][0]}") or 1
started = {r["bus_id"]: r["status"] for r in db.q("SELECT bus_id, status FROM trip_runs WHERE date=? AND trip_no=?", (D, trip))}
loads = campus.bus_loads(trip, D)
for r in loads:
    r["Load"] = round(r["planned"] / max(1, r["capacity"]) * 100)
    r["Status"] = {"CLOSED": "Done", "IN_PROGRESS": "Running"}.get(started.get(r["bus_id"]), "Planned")
tot = sum(r["planned"] for r in loads)
over = [r for r in loads if r["planned"] > r["capacity"]]
U.kpis([("BUSES THIS SESSION", len(loads), TRIPS[trip]["label"], "#3D4FD6", ""), ("STUDENTS", tot, f"avg {tot / max(1, len(loads)):.0f} per bus", "#1B2559", ""),
        ("OVER CAPACITY", len(over), ", ".join(r["bus_no"] for r in over) or "none", "#D93025", "red" if over else "green"),
        ("ALREADY RUN / RUNNING", sum(1 for r in loads if r["Status"] != "Planned"), "changes apply to buses not yet started", "#9AA3BD", "")], n=4)

left, right = st.columns([1.35, 1])
with left:
    df = pd.DataFrame([{"Bus": r["bus_no"], "Route": r["route"], "Students": r["planned"], "Absent": r["absent"], "Seats": r["capacity"],
                        "Load %": r["Load"], "Stops": r["stops"], "Status": r["Status"]} for r in loads])
    st.dataframe(df, hide_index=True, width="stretch", height=560,
                 column_config={"Load %": st.column_config.ProgressColumn("Load %", min_value=0, max_value=120, format="%d%%")})
bus_opts = {r["bus_id"]: f"{r['bus_no']} · {r['planned']}/{r['capacity']} · {r['Status']}" for r in loads}
spares = [b for b in db.buses() if b["id"] not in bus_opts]
all_opts = {**bus_opts, **{b["id"]: f"{b['bus_no']} · spare (not on this session)" for b in spares}}
can = role in ("manager", "admin", "dispatcher")
with right:
    t1, t2, t3, t4 = st.tabs(["Move", "Swap", "Merge", "Switch to spare"])
    with t1:
        src = st.selectbox("From bus", list(bus_opts), format_func=bus_opts.get, key="mv_src")
        lines = E.manifest_for(src, D, trip) if src else []
        pick = st.multiselect("Students", [ln["student_id"] for ln in lines], key="mv_stu",
                              format_func=lambda i: next(f"{ln['name']} · {ln['class']} · {db.stop(ln['stop_id'])['name']}" for ln in lines if ln["student_id"] == i))
        dst = st.selectbox("To bus", [b for b in all_opts if b != src], format_func=all_opts.get, key="mv_dst")
        perm = st.toggle("Permanent (every day for this session)", key="mv_perm")
        if dst:
            dl = next((r for r in loads if r["bus_id"] == dst), {"planned": 0, "capacity": db.bus(dst)["capacity"]})
            after = dl["planned"] + len(pick)
            st.caption(f"Target after move: {after}/{dl['capacity']} seats" + (" — ⚠ over capacity" if after > dl["capacity"] else ""))
        if st.button("Move students", type="primary", disabled=not (can and pick and dst), key="mv_go"):
            n = campus.move_students(pick, trip, D, dst, user["display_name"], role, permanent=perm)
            st.session_state["asg_msg"] = ("ok", f"{n} student(s) moved to {db.bus(dst)['bus_no']} for Trip {trip}" + (" (permanent)." if perm else " today."))
            st.rerun()
    with t2:
        a = st.selectbox("Bus A", list(bus_opts), format_func=bus_opts.get, key="sw_a")
        la = E.manifest_for(a, D, trip) if a else []
        sa = st.selectbox("Student on bus A", [ln["student_id"] for ln in la], key="sw_sa", index=None,
                          format_func=lambda i: next(f"{ln['name']} · {ln['class']}" for ln in la if ln["student_id"] == i))
        b = st.selectbox("Bus B", [x for x in bus_opts if x != a], format_func=bus_opts.get, key="sw_b")
        lb = E.manifest_for(b, D, trip) if b else []
        sb = st.selectbox("Student on bus B", [ln["student_id"] for ln in lb], key="sw_sb", index=None,
                          format_func=lambda i: next(f"{ln['name']} · {ln['class']}" for ln in lb if ln["student_id"] == i))
        if st.button("Swap for today", type="primary", disabled=not (can and sa and sb), key="sw_go"):
            campus.swap_students(sa, sb, trip, D, user["display_name"], role)
            st.session_state["asg_msg"] = ("ok", "Students swapped for today; both buses' lists are updated.")
            st.rerun()
    with t3:
        fa = st.selectbox("Merge route of", list(bus_opts), format_func=bus_opts.get, key="mg_a")
        fb = st.selectbox("into bus", [x for x in bus_opts if x != fa], format_func=bus_opts.get, key="mg_b")
        if fa and fb:
            ra = next(r for r in loads if r["bus_id"] == fa)
            rb = next(r for r in loads if r["bus_id"] == fb)
            tot_ = ra["planned"] - ra["absent"] + rb["planned"] - rb["absent"]
            ok_cap = tot_ <= rb["capacity"]
            msg("ok" if ok_cap else "error", f"Merged bus would carry {tot_} students (absences excluded) on {rb['capacity']} seats"
                + ("" if ok_cap else " — too many: move some students to a third bus instead."))
        if st.button("Merge routes for today", type="primary", disabled=not (can and fa and fb), key="mg_go"):
            n = campus.merge_routes(fa, fb, trip, D, user["display_name"], role)
            st.session_state["asg_msg"] = ("ok", f"Route merged: {n} students now on {db.bus(fb)['bus_no']}. Its route is re-planned with the extra stops.")
            st.rerun()
    with t4:
        st.caption("Breakdown or driver absent: give the whole route to a spare bus for this session.")
        fa = st.selectbox("Route of bus", list(bus_opts), format_func=bus_opts.get, key="sp_a")
        sp = st.selectbox("Spare bus", [b["id"] for b in spares], format_func=lambda i: db.bus(i)["bus_no"], key="sp_b")
        if st.button("Switch route to spare bus", type="primary", disabled=not (can and fa and sp), key="sp_go"):
            n = campus.merge_routes(fa, sp, trip, D, user["display_name"], role)
            st.session_state["asg_msg"] = ("ok", f"{n} students switched to {db.bus(sp)['bus_no']} for Trip {trip} today.")
            st.rerun()
    if st.session_state.get("asg_msg"):
        msg(*st.session_state.pop("asg_msg"))
    ch = db.q("""SELECT c.created_at, s.name, b.bus_no, c.trip_no, c.note, c.requested_by FROM change_requests c JOIN students s ON s.id=c.student_id
                 LEFT JOIN buses b ON b.id=c.new_bus_id WHERE c.date=? AND c.kind='BUS' ORDER BY c.id DESC LIMIT 100""", (D,))
    with st.expander(f"Bus changes today ({len(ch)})"):
        st.dataframe(pd.DataFrame([{"Time": c["created_at"][11:16], "Student": c["name"], "To": c["bus_no"], "Trip": c["trip_no"], "Why": c["note"],
                                    "By": c["requested_by"]} for c in ch]), hide_index=True, width="stretch")
