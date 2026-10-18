"""Setup & data (spec §2, §9): data source / workbook import, students & contacts, badges (issue / revoke / replace) and ID cards,
buses & crew, stops, validated CSV import, devices, users, backups, demo tools."""
import io
import os
import random
import sqlite3
import tempfile
from datetime import date

import pandas as pd
import streamlit as st

import db
import engine as E
from db import TRIPS
from qr_utils import id_cards_pdf
from ui import msg

user = st.session_state.user
role = user["role"]
st.title("⚙️ Setup & data")
if "su_last" in st.session_state:
    lv, m = st.session_state.pop("su_last")
    msg(lv, m)

tabs = st.tabs(["Data source", "Students", "Badges & ID cards", "Buses & crew", "Stops", "CSV import", "Devices", "Users", "Backup", "Demo tools", "Reset trip data"])


def new_badge(sid, code):
    return f"BDG-{code[-4:] if code[-4:].isdigit() else sid:0>4}-{random.randint(1000, 9999)}"


# ------------------------------------------------------------------ data source
with tabs[0]:
    st.markdown(f"**Current data:** {db.setting('data_source', '—')}")
    k = db.q1("""SELECT (SELECT COUNT(*) FROM students) s, (SELECT COUNT(*) FROM buses) b, (SELECT COUNT(*) FROM trip_runs) t,
                 (SELECT COUNT(*) FROM events) e, (SELECT MIN(date) FROM trip_runs) d1, (SELECT MAX(date) FROM trip_runs) d2""")
    st.caption(f"{k['s']} students · {k['b']} buses · {k['t']} trips · {k['e']} scan events · trip dates {k['d1'] or '—'} to {k['d2'] or '—'}")
    with st.form("school_name_form", border=False):
        cN1, cN2 = st.columns([3, 1], vertical_alignment="bottom")
        nm = cN1.text_input("School name (shown on dashboard, sidebar and ID cards)", value=db.setting("school_name", db.SCHOOL_NAME),
                            disabled=role != "admin")
        if cN2.form_submit_button("Save name", disabled=role != "admin") and nm.strip():
            db.set_setting("school_name", nm.strip())
            db.audit(user["username"], role, "SETTING", "setting", "school_name", nm.strip())
            st.rerun()
    if role != "admin":
        st.info("Only the administrator can replace the data.")
    else:
        st.warning("Regenerating replaces everything in the database (trips, scans, users). Download a backup first if needed.")
        with st.container(border=True):
            st.markdown("**DPS-scale demo** — about 4,800 students (KG, main school, stay-back, afternoon shift), 100 buses, 160 classes, "
                        "gates, visitors and patrols; the last school days plus today up to now.")
            if st.button("Regenerate the DPS demo (about 20 s)"):
                with st.spinner("Generating…"):
                    db.init_db(reset=True)
                st.session_state.su_last = ("ok", "DPS demo regenerated.")
                st.rerun()
        with st.container(border=True):
            st.markdown("**Parent app policy**")
            st.toggle("Show the live bus location to parents", value=db.setting("parent_bus_location", "0") == "1", key="pbl",
                      on_change=lambda: db.set_setting("parent_bus_location", "1" if st.session_state.pbl else "0"),
                      help="DPS asked that parents see campus entry and exit times only.")
        unv = db.q("SELECT id, name, email FROM guardians WHERE verified=0")
        if unv:
            st.markdown(f"**Guardians awaiting verification ({len(unv)})**")
            gv = st.selectbox("Guardian", [g["id"] for g in unv], format_func=lambda i: next(f"{g['name']} · {g['email']}" for g in unv if g["id"] == i))
            if st.button("Mark verified (identity checked by school)"):
                db.x("UPDATE guardians SET verified=1 WHERE id=?", (gv,))
                db.audit(user["username"], role, "GUARDIAN_VERIFIED", "guardian", gv)
                st.rerun()

# ------------------------------------------------------------------ backup
with tabs[8]:
    st.caption("Full database backup (spec §9). In production: automated encrypted nightly backups with off-site copies and restore tests.")
    if st.button("Prepare backup"):
        src = sqlite3.connect(db.DB_PATH)
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
            path = tf.name
        dst = sqlite3.connect(path)
        src.backup(dst)
        dst.close()
        src.close()
        st.session_state.backup = open(path, "rb").read()
        db.audit(user["username"], role, "BACKUP", "database", "", f"{len(st.session_state.backup)} bytes")
    if st.session_state.get("backup"):
        st.download_button("⬇ Download backup", st.session_state.backup, file_name=f"school_bus_backup_{db.today()}.db", mime="application/octet-stream")

# ------------------------------------------------------------------ demo tools
with tabs[9]:
    import dps_data
    st.markdown("**Demo clock** — replay today as it stood at a chosen time (rebuilds today's data from the day script, then the day "
                "carries on from there in real time). History days are not touched.")
    off = db.clock_offset()
    st.caption(f"Now showing: {db.now():%H:%M}" + (f" (demo clock, {off / 3600:+.1f} h from real time)" if abs(off) > 60 else " (real time)"))
    PRESETS = {"06:20": "Morning pickup running", "07:40": "Classes starting, attendance coming in", "10:30": "School day, visitors, early pickups",
               "11:30": "KG drop-off + afternoon pickup", "14:10": "2:00 pm drop-off running", "16:45": "Stay-back drop-off",
               "18:50": "Afternoon-shift drop-off"}
    cols = st.columns(4)
    for i, (hm, lab) in enumerate(PRESETS.items()):
        if cols[i % 4].button(f"{hm} · {lab}", key=f"clk{hm}", width="stretch", disabled=role not in ("admin", "manager")):
            with st.spinner("Rebuilding today…"):
                dps_data.replay_today_at(hm)
            st.session_state.su_last = ("ok", f"Today replayed to {hm}.")
            st.rerun()
    if cols[3].button("⏱ Back to real time", key="clk_real", width="stretch", type="primary", disabled=abs(off) < 60):
        if off > 0:
            dps_data.replay_today_at("now")
        else:
            dps_data.real_time()
        st.session_state.su_last = ("ok", "Real time.")
        st.rerun()
    st.toggle("Pause the simulated day (only live demo actions change the data)", value=db.setting("sim_paused") == "1", key="simp",
              on_change=lambda: db.set_setting("sim_paused", "1" if st.session_state.simp else "0"))
    pend = db.q1("SELECT COUNT(*) n, MIN(ts) nxt FROM sim_events WHERE done=0")
    st.caption(f"Simulated events still to come today: {pend['n']:,}" + (f" · next at {pend['nxt'][11:16]}" if pend["nxt"] else ""))
    if db.setting("sim_last_error"):
        st.caption(f"Last simulation note: {db.setting('sim_last_error')}")


# ------------------------------------------------------------------ reset trip data
with tabs[10]:
    st.markdown("**Reset trip data** — permanently remove trips for a date so they can be run again. "
                "Removes each trip's children list, scans, parent notices and incidents. The reset is recorded in the audit log.  \n"
                "🔒 **Closed trips are final and are never reset.** Only trips that are planned, in progress or pending sync can be removed.")
    if "rs_result" in st.session_state:
        _lv, _m = st.session_state.pop("rs_result")
        msg(_lv, _m)
    if role != "admin":
        st.info("Only the administrator can reset trip data.")
    else:
        bl_ = db.buses()
        r1, r2, r3 = st.columns([1, 1, 1.4])
        rdate = r1.date_input("Date *", value=None, key="rs_date", format="YYYY/MM/DD",
                              help="Required. Pick the date whose trips you want to reset.")
        bopt = [None] + [b["id"] for b in bl_]
        rbus = r2.selectbox("Bus", bopt, key="rs_bus",
                            format_func=lambda i: "All buses" if i is None else next(b["bus_no"] for b in bl_ if b["id"] == i))
        topt = [None] + list(TRIPS)
        rtrip = r3.selectbox("Trip", topt, key="rs_trip",
                             format_func=lambda n: "All trips" if n is None else f"Trip {n} · {TRIPS[n]['window'][0]}–{TRIPS[n]['window'][1]} · {TRIPS[n]['label']}")
        if rdate is None:
            st.warning("Select a date to continue — the date is mandatory.")
        else:
            D_ = rdate.isoformat()
            all_ = E.reset_scope(D_, rbus, rtrip)
            force = st.toggle("Testing only: also reset closed trips (overrides the lock — recorded in the audit log)", key="rs_force")
            runs_ = [r for r in all_ if force or r["status"] != "CLOSED"]
            locked_ = [] if force else [r for r in all_ if r["status"] == "CLOSED"]
            pc_ = E.parent_changes_scope(D_, rbus, rtrip)
            n_pc = len(pc_["absences"]) + len(pc_["change_requests"])
            if locked_:
                st.markdown(f"**🔒 {len(locked_)} closed trip(s) — locked, will be kept:**")
                st.dataframe(pd.DataFrame([{"Bus": r["bus_no"], "Trip": r["trip_no"], "Status": "Closed (locked)",
                                            "Started": (r["start_ts"] or "")[11:16], "Ended": (r["end_ts"] or "")[11:16],
                                            "Children": r["children"], "Scans": r["scans"]} for r in locked_]),
                             hide_index=True, width="stretch")
            if not runs_ and locked_:
                st.info("Every matching trip is closed, so there is nothing that can be reset. To reset closed trips for testing, "
                        "turn on 'Testing only: also reset closed trips' above.")
            elif not runs_:
                st.info(f"No trips found for {D_}" + (f" · {next(b['bus_no'] for b in bl_ if b['id'] == rbus)}" if rbus else "")
                        + (f" · Trip {rtrip}" if rtrip else "") + ". Nothing to reset.")
            else:
                st.markdown(f"**{len(runs_)} trip(s) will be removed:**")
                st.dataframe(pd.DataFrame([{"Bus": r["bus_no"], "Trip": r["trip_no"], "Status": r["status"].replace("_", " ").title(),
                                            "Started": (r["start_ts"] or "")[11:16], "Ended": (r["end_ts"] or "")[11:16],
                                            "Children": r["children"], "On board now": r["onboard"], "Scans": r["scans"],
                                            "Notices": r["notices"], "Incidents": r["incidents"]} for r in runs_]),
                             hide_index=True, width="stretch")
                if any(r["onboard"] for r in runs_):
                    st.warning("Some of these trips still show children on board. Reset only if this is test data.")
            also = False
            if n_pc:   # only offered when there is something to remove
                also = st.checkbox(f"Optional: also remove parent absences and change requests for this date ({n_pc} found)", value=False,
                                   key="rs_pc")
            can = bool(runs_) or bool(also and n_pc)
            if can:
                st.markdown("**Step 2 — confirm:** tick the box below, then press **Reset trip data**.")
            sure = st.checkbox("I understand this permanently deletes the selected trip data", key="rs_ok", disabled=not can)
            if st.button("Reset trip data", type="primary", disabled=not sure or (not runs_ and not (also and n_pc)), key="rs_go"):
                n, m, k = E.reset_trips(D_, user["username"], role, bus_id=rbus, trip_no=rtrip, parent_changes=also, include_closed=force)
                st.session_state.rs_result = ("ok", f"Reset done for {D_}: {n} trip(s) removed"
                                            + (f", {m} absence/change request(s) removed" if also else "") + (f", {k} closed trip(s) kept" if k else "")
                                            + ". The removed trip(s) can now be started again."
                                            + (" This bus is now kept for manual testing today: the simulated day will not start its "
                                               "remaining trips." if rbus and D_ == db.today() else ""))
                for k_ in ("rs_ok", "rs_pc", "rs_force"):
                    st.session_state.pop(k_, None)
                st.rerun()
