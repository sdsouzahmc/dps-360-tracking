"""Vehicle & patrol checks: in-app pre-/post-trip vehicle checklists (critical defect blocks the trip), defect clearing,
compliance over the last school days, and security patrol rounds (guard tour) on the same screen."""
from datetime import datetime

import pandas as pd
import streamlit as st

import campus
import db
import engine as E
import ui360 as U
from ui import msg

U.css()
user = st.session_state.user
role = user["role"]
D = db.today()
st.title("🛡 Vehicle & patrol checks")

runs = db.q("SELECT bus_id, trip_no, status FROM trip_runs WHERE date=?", (D,))
op = sorted({r["bus_id"] for r in runs})
pre = {r["bus_id"]: r for r in db.q("SELECT * FROM vehicle_checks WHERE date=? AND kind='PRE' ORDER BY id", (D,))}
post = {r["bus_id"]: r for r in db.q("SELECT * FROM vehicle_checks WHERE date=? AND kind='POST' ORDER BY id", (D,))}
blocked = [v for v in pre.values() if v["status"] == "BLOCKED"]
U.kpis([("BUSES OPERATING", len(op), "today so far", "#1B2559", ""),
        ("PRE-TRIP DONE", len([b for b in op if b in pre]), f"{len([b for b in op if b in pre]) / max(1, len(op)):.0%}", "#2E9E5B", "green"),
        ("DEFECTS REPORTED", sum(1 for v in pre.values() if v["defects"]) + sum(1 for v in post.values() if v["defects"]), "minor + critical", "#E07A1F", "orange"),
        ("BLOCKED NOW", len(blocked), "critical defect, trip cannot start", "#D93025", "red" if blocked else ""),
        ("POST-TRIP DONE", len(post), "after the last trip", "#7B5CD6", "")], n=5)

tabs = st.tabs(["🔧 Today's vehicle checks", "📅 Compliance (last school days)", "🛡 Security patrol", "📋 Checklist items"])
with tabs[0]:
    for v in blocked:
        with st.container(border=True):
            st.markdown(f"⛔ **{db.bus(v['bus_id'])['bus_no']}** — critical defect at {v['ts'][11:16]} by {v['by_name']}: **{v['defects']}**")
            if role in ("manager", "admin", "dispatcher"):
                note = st.text_input("Clearance note (mechanic / replacement)", key=f"clr_n{v['id']}", placeholder="e.g. mechanic checked, sensor refitted")
                if st.button("Clear defect — bus may run", key=f"clr{v['id']}", type="primary"):
                    campus.clear_check(v["id"], user["display_name"], note.strip() or "Cleared by transport office")
                    st.rerun()
    rows = []
    for b in db.buses():
        if b["id"] not in op and b["id"] not in pre:
            continue
        p, q_ = pre.get(b["id"]), post.get(b["id"])
        rows.append({"Bus": b["bus_no"], "Driver": db.staff_name(b["driver_id"]), "Pre-trip": (p["ts"][11:16] if p else "— missing"),
                     "Result": (p["status"].title() if p else ""), "Defects": (p["defects"] if p else ""),
                     "Cleared": (f"{p['cleared_by']} {(p['cleared_ts'] or '')[11:16]}" if p and p["cleared_by"] else ""),
                     "Post-trip": (q_["ts"][11:16] if q_ else ""), "Post result": (q_["status"].title() if q_ else "")})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=460)

with tabs[1]:
    days = E.school_days_back(5) + [D]
    out = []
    for d in days:
        rb = {r["bus_id"] for r in db.q("SELECT bus_id FROM trip_runs WHERE date=?", (d,))}
        pc = {r["bus_id"] for r in db.q("SELECT bus_id FROM vehicle_checks WHERE date=? AND kind='PRE'", (d,))}
        po = {r["bus_id"] for r in db.q("SELECT bus_id FROM vehicle_checks WHERE date=? AND kind='POST'", (d,))}
        sw = db.q1("SELECT COUNT(*) n, SUM(sweep_by IS NOT NULL AND sweep_by<>'') ok FROM trip_runs WHERE date=? AND status='CLOSED'", (d,))
        pr = db.q1("SELECT COUNT(*) n, SUM(status='ARRIVED') ok FROM patrol_rounds WHERE date=?", (d,))
        df_ = db.q1("SELECT COUNT(*) n FROM vehicle_checks WHERE date=? AND defects<>''", (d,))
        out.append({"Day": datetime.strptime(d, "%Y-%m-%d").strftime("%a %d %b"), "Buses": len(rb),
                    "Pre-trip %": round(len(pc & rb) / max(1, len(rb)) * 100, 1), "Post-trip %": round(len(po & rb) / max(1, len(rb)) * 100, 1),
                    "Defects": df_["n"], "Sweep %": round((sw["ok"] or 0) / max(1, sw["n"]) * 100, 1),
                    "Patrol on time %": round((pr["ok"] or 0) / max(1, pr["n"]) * 100, 1)})
    st.dataframe(pd.DataFrame(out), hide_index=True, width="stretch",
                 column_config={k: st.column_config.ProgressColumn(k, min_value=0, max_value=100, format="%.0f%%")
                                for k in ("Pre-trip %", "Post-trip %", "Sweep %", "Patrol on time %")})
    st.caption("Today counts up to the current time (post-trip checks happen after each bus's last trip).")

with tabs[2]:
    pr = campus.patrol(D)
    if pr:
        df = pd.DataFrame(pr)
        g = df.groupby(["checkpoint", "status"]).size().reset_index(name="n")
        g["Status"] = g["status"].str.title()
        st.altair_chart(U.bar_chart(g, "checkpoint", "n", "Status", 230, colors={"Arrived": "#2E9E5B", "Late": "#E0B01F", "Omitted": "#D93025"}),
                        width="stretch")
        st.dataframe(pd.DataFrame([{"Plan": p["plan"], "Round": p["round_no"], "Checkpoint": p["checkpoint"], "Guard": p["guard"],
                                    "Planned": (p["planned_ts"] or "")[11:16], "Scanned": (p["actual_ts"] or "")[11:16], "Status": p["status"].title()}
                                   for p in pr]), hide_index=True, width="stretch", height=320)
    st.caption("Feed from the guard-tour app (JWM patrol). Security can upload the patrol export in the Security app.")

with tabs[3]:
    a, b = st.columns(2)
    with a:
        st.markdown("**Pre-trip (driver, before the first trip)**")
        st.dataframe(pd.DataFrame([{"Section": s, "Item": i, "Blocks trip": "⛔ yes" if c else ""} for s, i, c in campus.PRE_CHECKS]),
                     hide_index=True, width="stretch", height=560)
    with b:
        st.markdown("**Post-trip (after the last trip)**")
        st.dataframe(pd.DataFrame([{"Section": s, "Item": i} for s, i, c in campus.POST_CHECKS]), hide_index=True, width="stretch")
        st.toggle("Require the pre-trip check before a trip can start", value=db.setting("require_precheck", "1") == "1", key="req_pc",
                  disabled=role not in ("manager", "admin"),
                  on_change=lambda: db.set_setting("require_precheck", "1" if st.session_state.req_pc else "0"))
