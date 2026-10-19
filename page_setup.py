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

tabs = st.tabs(["Data source", "Students", "Badges & ID cards", "Buses & crew", "Stops", "CSV import", "Devices", "Users", "Backup", "Demo tools", "Reset trip data", "Test resets"])


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


# ------------------------------------------------------------------ test resets (DPS 360)
with tabs[11]:
    import campus as _cp
    import dps_data as _dd
    import resets as R_
    st.markdown("**Test resets** — put anything back to a clean state when a test gets stuck. Every reset is recorded in the audit log. "
                "Trip resets are on the previous tab.")
    if "tr_msg" in st.session_state:
        msg(*st.session_state.pop("tr_msg"))
    if role not in ("admin", "manager"):
        st.info("Only the administrator or transport lead can use test resets.")
    else:
        _D = st.date_input("Date", value=date.fromisoformat(db.today()), key="tr_date").isoformat()
        what = st.segmented_control("Reset", ["Student", "Class attendance", "Bus (whole day)", "Vehicle check", "Staff", "Visitors",
                                              "Early pickups", "Alerts", "Parent requests", "Bus assignments", "Whole day"],
                                    default="Student", key="tr_what") or "Student"

        def _confirm(key):
            return st.checkbox("I understand this permanently removes the selected test data", key=f"tr_ok_{key}")

        with st.container(border=True):
            if what == "Student":
                st.caption("Bus lines go back to Expected (scans removed); gate scans, class mark, absences, requests, early pickups, alerts "
                           "and parent messages for that day are removed. Use it when a child is stuck On board, marked wrongly, etc.")
                qtxt = st.text_input("Student name, ID (DPS…) or card number", key="tr_sq")
                hits = db.q("""SELECT s.id, s.name, s.code, s.class_name, s.section FROM students s LEFT JOIN badges b ON b.student_id=s.id
                               WHERE s.name LIKE ? OR s.code=? OR b.code=? GROUP BY s.id ORDER BY s.name LIMIT 30""",
                            (f"%{qtxt.strip()}%", qtxt.strip().upper(), qtxt.strip())) if qtxt.strip() else []
                if hits:
                    sid_ = st.selectbox("Student", [h["id"] for h in hits], key="tr_sid",
                                        format_func=lambda i: next(f"{h['name']} · {h['class_name']}-{h['section']} · {h['code']}" for h in hits if h["id"] == i))
                    pv = R_.student_preview(sid_, _D)
                    st.dataframe(pd.DataFrame([{"Item": k, "Records": v} for k, v in pv.items()]), hide_index=True, width="stretch")
                    parts = st.multiselect("What to reset", list(pv), default=list(pv), key="tr_sparts")
                    if st.button("Reset student", type="primary", disabled=not (_confirm("s") and parts), key="tr_sgo"):
                        R_.reset_student(sid_, _D, user["username"], parts)
                        st.session_state.tr_msg = ("ok", f"Student reset for {_D}: {', '.join(parts)}.")
                        st.rerun()
                elif qtxt.strip():
                    st.caption("No student found.")
            elif what == "Class attendance":
                st.caption("Removes the class's submitted attendance, the alerts and parent messages it created and the 'absent (teacher)' bus "
                           "absences — the teacher can take attendance again.")
                cls_ = db.q("""SELECT c.id, c.name, c.section, s.name AS t, (SELECT ts FROM att_submissions a WHERE a.class_id=c.id AND a.date=?) sub
                               FROM classes c LEFT JOIN staff s ON s.id=c.teacher_id ORDER BY c.shift, c.id""", (_D,))
                cid_ = st.selectbox("Class", [c["id"] for c in cls_], key="tr_cid",
                                    format_func=lambda i: next(f"{c['name']}-{c['section']} · {c['t']} · {'submitted ' + c['sub'][11:16] if c['sub'] else 'not submitted'}"
                                                               for c in cls_ if c["id"] == i))
                if st.button("Reset class attendance", type="primary", disabled=not _confirm("c"), key="tr_cgo"):
                    n_ = R_.reset_class_attendance(cid_, _D, user["username"])
                    st.session_state.tr_msg = ("ok", f"Class attendance reset ({n_} marks removed).")
                    st.rerun()
            elif what in ("Bus (whole day)", "Vehicle check"):
                bl_ = db.buses()
                bid_ = st.selectbox("Bus", [b["id"] for b in bl_], key="tr_bid", format_func=lambda i: next(b["bus_no"] for b in bl_ if b["id"] == i))
                runs_ = db.q("SELECT trip_no, status FROM trip_runs WHERE bus_id=? AND date=? ORDER BY trip_no", (bid_, _D))
                chk_ = db.q("SELECT kind, status, ts FROM vehicle_checks WHERE bus_id=? AND date=?", (bid_, _D))
                st.caption(f"Trips on {_D}: " + (", ".join(f"Trip {r['trip_no']} {r['status'].lower()}" for r in runs_) or "none")
                           + " · Vehicle checks: " + (", ".join(f"{c['kind'].lower()} {c['status'].lower()} {c['ts'][11:16]}" for c in chk_) or "none"))
                if what == "Bus (whole day)":
                    st.caption("Removes ALL trips of this bus for the day (including closed ones) with lists, scans, messages and incidents, plus its "
                               "vehicle checks and office messages. The bus is then kept for manual testing for the rest of the day.")
                    if st.button("Reset bus day", type="primary", disabled=not _confirm("b"), key="tr_bgo"):
                        n_, nc_ = R_.reset_bus_day(bid_, _D, user["username"])
                        st.session_state.tr_msg = ("ok", f"Bus reset: {n_} trip(s) and {nc_} vehicle check(s) removed. Start again with the pre-trip check.")
                        st.rerun()
                else:
                    st.caption("Removes the bus's pre-/post-trip checks for the day (also clears a BLOCKED bus) — the driver checks again.")
                    if st.button("Reset vehicle checks", type="primary", disabled=not _confirm("v"), key="tr_vgo"):
                        n_ = R_.reset_vehicle_checks(bid_, _D, user["username"])
                        st.session_state.tr_msg = ("ok", f"{n_} vehicle check(s) removed.")
                        st.rerun()
            elif what == "Staff":
                qs = st.text_input("Staff name or card (EMP…)", key="tr_stq")
                sh = db.q("SELECT id, name, card_no, role FROM staff WHERE name LIKE ? OR card_no=? LIMIT 30", (f"%{qs.strip()}%", qs.strip().upper())) if qs.strip() else []
                if sh:
                    stid = st.selectbox("Staff", [x["id"] for x in sh], key="tr_stid", format_func=lambda i: next(f"{x['name']} · {x['role']} · {x['card_no']}" for x in sh if x["id"] == i))
                    if st.button("Reset staff gate scans", type="primary", disabled=not _confirm("st"), key="tr_stgo"):
                        n_ = R_.reset_staff_day(stid, _D, user["username"])
                        st.session_state.tr_msg = ("ok", f"{n_} gate scan(s) removed.")
                        st.rerun()
            elif what == "Visitors":
                only = st.toggle("Only visitors still on campus", value=False, key="tr_vonly")
                st.caption(f"{db.q1('SELECT COUNT(*) n FROM visitors WHERE date=?', (_D,))['n']} visitor(s) registered on {_D}.")
                if st.button("Remove visitors", type="primary", disabled=not _confirm("vi"), key="tr_vigo"):
                    n_ = R_.reset_visitors(_D, user["username"], only)
                    st.session_state.tr_msg = ("ok", f"{n_} visitor(s) removed with their passes, gate scans and alerts.")
                    st.rerun()
            elif what == "Early pickups":
                eps = _cp.early_pickups(_D)
                opts = {None: f"All early pickups on {_D} ({len(eps)})"}
                opts.update({e["student_id"]: f"{e['name']} · {e['class']} · {e['pickup_time']} · {e['status'].lower()}" for e in eps})
                epk = st.selectbox("Which", list(opts), format_func=opts.get, key="tr_ep")
                st.caption("Removes the request, its gate pass, its 'off the home bus' absence (child back on the bus list), gate exit, "
                           "teacher notice and parent messages.")
                if st.button("Reset early pickup(s)", type="primary", disabled=not _confirm("ep"), key="tr_epgo"):
                    n_ = R_.reset_early_pickups(_D, user["username"], epk)
                    st.session_state.tr_msg = ("ok", f"{n_} early-pickup request(s) removed.")
                    st.rerun()
            elif what == "Alerts":
                kinds = sorted({a["kind"] for a in db.q("SELECT DISTINCT kind FROM campus_alerts WHERE date=?", (_D,))})
                kd = st.selectbox("Type", [None] + kinds, format_func=lambda k: "All types" if k is None else _cp.ALERT_TEXT.get(k, k), key="tr_ak")
                mode = st.radio("Action", ["reopen", "delete"], horizontal=True, key="tr_am",
                                format_func={"reopen": "Re-open resolved / acknowledged alerts", "delete": "Delete the alerts"}.get)
                if st.button("Reset alerts", type="primary", disabled=not _confirm("al"), key="tr_algo"):
                    n_ = R_.reset_alerts(_D, user["username"], mode, kd)
                    st.session_state.tr_msg = ("ok", f"{n_} alert(s) {'re-opened' if mode == 'reopen' else 'deleted'}.")
                    st.rerun()
            elif what == "Parent requests":
                st.caption("Removes absences declared from parent logins, stop / recipient change requests for the day and all pending home-location "
                           "requests. (Simulated parent absences are kept.)")
                if st.button("Reset parent requests", type="primary", disabled=not _confirm("pr"), key="tr_prgo"):
                    a_, r_, l_ = R_.reset_parent_requests(_D, user["username"])
                    st.session_state.tr_msg = ("ok", f"Removed {a_} absence(s), {r_} change request(s), {l_} pending location(s).")
                    st.rerun()
            elif what == "Bus assignments":
                perm = st.toggle("Also undo permanent moves (back to the original per-session plan)", value=True, key="tr_perm")
                st.caption("Undoes Assignment-board changes: today's moves, swaps, merges and spare-bus switches"
                           + (", and permanent moves." if perm else "."))
                if st.button("Reset bus assignments", type="primary", disabled=not _confirm("as"), key="tr_asgo"):
                    n1, n2 = R_.reset_assignment(_D, user["username"], perm)
                    st.session_state.tr_msg = ("ok", f"{n1} one-day move(s) removed, {n2} permanent move(s) restored.")
                    st.rerun()
            elif what == "Whole day":
                st.caption("Rebuilds TODAY from the simulated day script up to the current time: all of today's trips, scans, gate scans, attendance, "
                           "visitors, early pickups, alerts and checks are removed and replayed. Affects BOTH teams — agree it first.")
                rel = st.checkbox("Also release buses that were reset for manual testing today (reserved test buses BUS-001 / BUS-026 stay manual)",
                                  key="tr_rel")
                if st.button("Rebuild today now", type="primary", disabled=not _confirm("wd") or _D != db.today(), key="tr_wdgo"):
                    if rel:
                        R_.release_manual_buses(user["username"])
                    with st.spinner("Rebuilding today…"):
                        _dd.replay_today_at("now")
                    db.audit(user["username"], role, "TEST_RESET_DAY", "day", _D, "rebuilt to now")
                    st.session_state.tr_msg = ("ok", "Today rebuilt up to now.")
                    st.rerun()
                st.caption("Everything from scratch (all days, all data): Data source tab → Regenerate the DPS demo.")
