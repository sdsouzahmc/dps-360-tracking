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
import qr_utils
import ui360 as U
from ui import msg

user = st.session_state.user
role = user["role"]
st.title("⚙️ Setup & data")
if "su_last" in st.session_state:
    lv, m = st.session_state.pop("su_last")
    msg(lv, m)

tabs = st.tabs(["School & data source", "Students", "Badges & ID cards", "Buses & crew", "Stops", "CSV import", "Devices", "Users", "Backup", "Demo tools", "Reset trip data", "Test resets", "Integrations & API"])


def new_badge(sid, code):
    """New DPS card number (7 digits, unused)."""
    while True:
        c = f"27{random.randint(0, 99999):05d}"
        if not db.q1("SELECT id FROM badges WHERE code=?", (c,)):
            return c


# ------------------------------------------------------------------ data source
with tabs[0]:
    st.markdown(f"**Current data:** {db.setting('data_source', '—')}")
    k = db.q1("""SELECT (SELECT COUNT(*) FROM students) s, (SELECT COUNT(*) FROM buses) b, (SELECT COUNT(*) FROM trip_runs) t,
                 (SELECT COUNT(*) FROM events) e, (SELECT MIN(date) FROM trip_runs) d1, (SELECT MAX(date) FROM trip_runs) d2""")
    st.caption(f"{k['s']} students · {k['b']} buses · {k['t']} trips · {k['e']} scan events · trip dates {k['d1'] or '—'} to {k['d2'] or '—'}")
    with st.form("school_name_form", border=False):
        cN1, cN2 = st.columns([3, 1], vertical_alignment="bottom")
        nm = cN1.text_input("School name (shown on the login page, dashboard, sidebar, reports, passes and ID cards)", value=db.setting("school_name", db.SCHOOL_NAME),
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
            st.markdown("**Full-scale demo** — about 4,800 students (KG, main school, stay-back, afternoon shift), 100 buses, 160 classes, "
                        "gates, visitors and patrols; the last school days plus today up to now.")
            if st.button("Regenerate the demo (about 20 s)"):
                with st.spinner("Generating…"):
                    db.init_db(reset=True)
                st.session_state.su_last = ("ok", "Demo regenerated.")
                st.rerun()
        with st.container(border=True):
            st.markdown("**Parent app policy**")
            st.toggle("Show the live bus location to parents", value=db.setting("parent_bus_location", "0") == "1", key="pbl",
                      on_change=lambda: db.set_setting("parent_bus_location", "1" if st.session_state.pbl else "0"),
                      help="The school asked that parents see campus entry and exit times only.")
# ------------------------------------------------------------------ students
with tabs[1]:
    q_ = st.text_input("Search student (name, ID or class e.g. Grade 5-A)")
    st.caption(f"{db.q1('SELECT COUNT(*) n FROM students')['n']:,} students · showing up to 300 matches. Bus per session: Student 360 or the Bus assignment board.")
    rows = db.q("""SELECT s.id, s.code AS ID, s.name AS Name, s.class_name||'-'||s.section AS Class, s.grp AS "Group", s.transport AS Transport, b.bus_no AS "Bus (pickup)",
                   st.name AS Stop, CASE s.active WHEN 1 THEN 'Active' ELSE 'Inactive' END AS Status,
                   (SELECT COUNT(*) FROM recipients r WHERE r.student_id=s.id AND r.active=1) AS Recipients
                   FROM students s LEFT JOIN buses b ON b.id=s.bus_id LEFT JOIN stops st ON st.id=s.stop_id
                   WHERE s.name LIKE ? OR s.code LIKE ? OR (s.class_name||'-'||s.section) LIKE ? ORDER BY s.code LIMIT 300""", (f"%{q_}%", f"%{q_}%", f"%{q_}%"))
    st.dataframe(pd.DataFrame(rows).drop(columns=["id"]) if rows else pd.DataFrame(), hide_index=True, width="stretch", height=260)
    if rows:
        sid = st.selectbox("Edit student", [r["id"] for r in rows], format_func=lambda i, rows=rows: next(f"{r['ID']} — {r['Name']}" for r in rows if r["id"] == i))
        s = db.q1("SELECT * FROM students WHERE id=?", (sid,))
        with st.container(border=True):
            import campus as _cp
            _GROUPS = {"KG": (1, "KG"), "Main school": (1, "Main"), "Stay-back": (1, "StayBack"), "Afternoon shift": (2, "Afternoon")}
            _cur = next((k for k, v in _GROUPS.items() if v == (s["batch"], s["grp"])), "Main school")
            ta = db.q("SELECT trip_no, bus_id, stop_id FROM trip_assign WHERE student_id=? ORDER BY trip_no", (sid,))
            if ta:
                st.caption("Bus per session: " + " · ".join(f"Trip {a['trip_no']} {db.bus(a['bus_id'])['bus_no']} ({(db.stop(a['stop_id']) or {}).get('name', '—')})"
                                                           for a in ta))
            c1, c2, c3, c4 = st.columns(4)
            bl = db.buses()
            ptrip = _cp.pickup_trip(*_GROUPS[_cur])
            nb = c1.selectbox(f"Pickup bus (Trip {ptrip})", [b["id"] for b in bl], index=[b["id"] for b in bl].index(s["bus_id"]) if s["bus_id"] in [b["id"] for b in bl] else 0,
                              format_func=lambda i: next(b["bus_no"] for b in bl if b["id"] == i), key=f"eb{sid}", disabled=s["transport"] != "BUS")
            sl = db.stops_of_bus(nb, None, ptrip)
            ns = c2.selectbox("Pickup stop", [x["id"] for x in sl], index=[x["id"] for x in sl].index(s["stop_id"]) if s["stop_id"] in [x["id"] for x in sl] else 0,
                              format_func=lambda i: next(x["name"] for x in sl if x["id"] == i), key=f"es{sid}", disabled=s["transport"] != "BUS") if sl else None
            ngl = c3.selectbox("Group", list(_GROUPS), index=list(_GROUPS).index(_cur), key=f"eg{sid}")
            ntr = c4.selectbox("Transport", ["BUS", "PRIVATE"], index=0 if s["transport"] == "BUS" else 1, key=f"etr{sid}")
            act = st.checkbox("Active", value=bool(s["active"]), key=f"ea{sid}")
            if st.button("Save student", key=f"esv{sid}"):
                nbat, ngrp = _GROUPS[ngl]
                if ntr == "BUS":
                    db.x("UPDATE students SET bus_id=?, stop_id=?, batch=?, grp=?, active=?, transport=? WHERE id=?", (nb, ns, nbat, ngrp, int(act), ntr, sid))
                    db.x("INSERT OR REPLACE INTO trip_assign(student_id, trip_no, bus_id, stop_id) VALUES (?,?,?,?)", (sid, ptrip, nb, ns))
                else:
                    db.x("UPDATE students SET batch=?, grp=?, active=?, transport=? WHERE id=?", (nbat, ngrp, int(act), ntr, sid))
                db.audit(user["username"], role, "STUDENT_UPDATED", "student", s["code"], f"pickup bus {nb} stop {ns} group {ngl} {ntr} active {act}")
                st.session_state.su_last = ("ok", f"{s['name']} updated.")
                st.rerun()
            st.markdown("**Guardians**")
            st.dataframe(db.q("""SELECT g.name AS Name, g.relation AS Relation, g.phone AS Phone, g.email AS "E-mail",
                                 CASE g.verified WHEN 1 THEN 'Verified' ELSE 'Not verified' END AS Status, u.username AS Login
                                 FROM guardians g JOIN student_guardians sg ON sg.guardian_id=g.id LEFT JOIN users u ON u.guardian_id=g.id
                                 WHERE sg.student_id=?""", (sid,)), hide_index=True, width="stretch")
            st.markdown("**Authorized recipients** (only these people can receive the child at the stop)")
            recs = db.q("SELECT id, name AS Name, relation AS Relation, phone AS Phone, id_number AS \"ID no.\", active FROM recipients WHERE student_id=?", (sid,))
            st.dataframe([{k: v for k, v in r.items() if k not in ("id",)} for r in recs], hide_index=True, width="stretch")
            r1, r2 = st.columns(2)
            with r1.popover("➕ Add recipient"):
                rn = st.text_input("Name", key=f"rn{sid}")
                rr = st.selectbox("Relation", ["Father", "Mother", "Guardian", "Nanny", "Driver", "Relative"], key=f"rr{sid}")
                rp = st.text_input("Phone", key=f"rp{sid}")
                ri = st.text_input("QID / ID number", key=f"ri{sid}")
                if st.button("Add", key=f"ra{sid}", disabled=not rn.strip()):
                    db.x("INSERT INTO recipients(student_id, name, relation, phone, id_number) VALUES (?,?,?,?,?)", (sid, rn, rr, rp, ri))
                    db.audit(user["username"], role, "RECIPIENT_ADDED", "student", s["code"], f"{rn} ({rr})")
                    st.rerun()
            act_recs = [r for r in recs if r["active"]]
            if act_recs:
                with r2.popover("➖ Remove recipient"):
                    rid = st.selectbox("Recipient", [r["id"] for r in act_recs], format_func=lambda i: next(r["Name"] for r in act_recs if r["id"] == i), key=f"rx{sid}")
                    if st.button("Remove", key=f"rxb{sid}"):
                        db.x("UPDATE recipients SET active=0 WHERE id=?", (rid,))
                        db.audit(user["username"], role, "RECIPIENT_REMOVED", "student", s["code"], str(rid))
                        st.rerun()
            with st.popover("👨‍👩‍👧 Link an existing guardian (siblings)"):
                gq = st.text_input("Guardian name", key=f"lg{sid}", placeholder="e.g. Guardian 051")
                gl = db.q("SELECT id, name, email FROM guardians WHERE name LIKE ? ORDER BY name LIMIT 20", (f"%{gq}%",)) if gq else []
                if gl:
                    gpick = st.selectbox("Guardian", [g["id"] for g in gl], format_func=lambda i: next(f"{g['name']} · {g['email']}" for g in gl if g["id"] == i), key=f"lgs{sid}")
                    if st.button("Link as guardian + authorized recipient", key=f"lgb{sid}"):
                        E.link_guardian(sid, gpick, user["username"], role)
                        st.session_state.su_last = ("ok", "Guardian linked — they can now receive both children and see both in the parent portal.")
                        st.rerun()
            st.markdown("**Emergency contacts**")
            st.dataframe(db.q("SELECT name AS Name, relation AS Relation, phone AS Phone FROM emergency_contacts WHERE student_id=?", (sid,)),
                         hide_index=True, width="stretch")

# ------------------------------------------------------------------ badges
with tabs[2]:
    with st.expander("🏷 Rear-door bus tags (QR) — print, laminate and fix inside the rear door of each bus"):
        st.caption("The crew scans the tag in the pre- and post-trip check, and security scans it in the after-trip inspection — proof that "
                   "someone walked to the back of the bus.")
        _bl = db.q("SELECT bus_no FROM buses ORDER BY bus_no")
        _sel = st.multiselect("Buses (empty = all)", [b_["bus_no"] for b_ in _bl], key="rt_sel")
        st.download_button("⬇ Rear-door tags (PDF)", qr_utils.rear_tags_pdf([b_ for b_ in _bl if not _sel or b_["bus_no"] in _sel],
                                                                          db.setting("school_name", db.SCHOOL_NAME)),
                           "Flexitrack360_rear_door_tags.pdf", "application/pdf", key="rt_dl")
    q2 = st.text_input("Find student", key="bq")
    rows = db.q("""SELECT s.id, s.code, s.name, b.code AS badge, b.status FROM students s LEFT JOIN badges b ON b.student_id=s.id AND b.status='ACTIVE'
                   WHERE s.name LIKE ? OR s.code LIKE ? ORDER BY s.code LIMIT 200""", (f"%{q2}%", f"%{q2}%"))
    if rows:
        sid = st.selectbox("Student", [r["id"] for r in rows], format_func=lambda i, rows=rows: next(f"{r['code']} — {r['name']} · badge {r['badge'] or 'NONE'}" for r in rows if r["id"] == i))
        r = next(r for r in rows if r["id"] == sid)
        hist = db.q("SELECT code AS Badge, status AS Status, issued_at AS Issued, revoked_at AS Revoked, reason AS Reason, replaced_by AS \"Replaced by\" "
                    "FROM badges WHERE student_id=? ORDER BY id DESC", (sid,))
        st.dataframe(hist, hide_index=True, width="stretch")
        c1, c2, c3 = st.columns(3)
        why = c1.selectbox("Reason", ["Lost card", "Damaged", "Stolen", "Left school", "Security"], key="bwhy")
        if c2.button("🔁 Replace badge (revoke + issue new)", disabled=not r["badge"]):
            nc = new_badge(sid, r["code"])
            db.x("UPDATE badges SET status='REVOKED', revoked_at=?, reason=?, replaced_by=? WHERE code=?", (db.ts(), why, nc, r["badge"]))
            db.x("INSERT INTO badges(code, student_id, status, issued_at) VALUES (?,?,?,?)", (nc, sid, "ACTIVE", db.ts()))
            db.audit(user["username"], role, "BADGE_REPLACED", "badge", r["badge"], f"-> {nc} ({why})")
            st.session_state.su_last = ("ok", f"Badge {r['badge']} revoked; new badge {nc} issued. The old one is now rejected at scan.")
            st.rerun()
        if r["badge"] and c3.button("⛔ Revoke only"):
            db.x("UPDATE badges SET status='REVOKED', revoked_at=?, reason=? WHERE code=?", (db.ts(), why, r["badge"]))
            db.audit(user["username"], role, "BADGE_REVOKED", "badge", r["badge"], why)
            st.rerun()
        if not r["badge"] and c3.button("➕ Issue badge"):
            nc = new_badge(sid, r["code"])
            db.x("INSERT INTO badges(code, student_id, status, issued_at) VALUES (?,?,?,?)", (nc, sid, "ACTIVE", db.ts()))
            db.audit(user["username"], role, "BADGE_ISSUED", "badge", nc, r["code"])
            st.rerun()
    st.divider()
    st.markdown("**Printable ID cards** (barcode + card number, 10 per A4 page)")
    by = st.radio("Cards for", ["Pickup bus", "Class"], horizontal=True, key="idby")
    _q = """SELECT s.code, s.name, s.class_name, s.section, s.batch, s.grp, bu.bus_no, st.name AS stop_name, NULL AS drop_stop_name,
            b.code AS badge FROM students s JOIN badges b ON b.student_id=s.id AND b.status='ACTIVE' LEFT JOIN buses bu ON bu.id=s.bus_id
            LEFT JOIN stops st ON st.id=s.stop_id WHERE s.active=1 AND """
    if by == "Pickup bus":
        bl = db.buses()
        bsel = st.selectbox("Bus", [b["id"] for b in bl], format_func=lambda i: next(b["bus_no"] for b in bl if b["id"] == i), key="idbus")
        cards = db.q(_q + "s.bus_id=? ORDER BY st.seq, s.name", (bsel,))
        fname = f"ID_cards_{db.bus(bsel)['bus_no']}.pdf"
    else:
        cl = db.q("SELECT name, section FROM classes ORDER BY shift, id")
        csel = st.selectbox("Class", [f"{c['name']}-{c['section']}" for c in cl], key="idcls")
        n_, s_ = csel.rsplit("-", 1)
        cards = db.q(_q + "s.class_name=? AND s.section=? ORDER BY s.name", (n_, s_))
        fname = f"ID_cards_{csel.replace(' ', '_')}.pdf"
    if cards:
        st.download_button(f"⬇ ID cards PDF ({len(cards)} cards)", id_cards_pdf(cards, db.setting("school_name", db.SCHOOL_NAME)),
                           file_name=fname, mime="application/pdf")

# ------------------------------------------------------------------ buses & crew
with tabs[3]:
    rows = db.q("""SELECT b.id, b.bus_no AS Bus, b.plate AS Plate, b.capacity AS Capacity, b.route_name AS Route, b.fvts_vehicle AS "Autotrace vehicle",
                   d.name AS Driver, s.name AS Supervisor, c.name AS "Care-taker",
                   (SELECT COUNT(*) FROM students x WHERE x.bus_id=b.id AND x.active=1) AS Students
                   FROM buses b LEFT JOIN staff d ON d.id=b.driver_id LEFT JOIN staff s ON s.id=b.supervisor_id LEFT JOIN staff c ON c.id=b.caretaker_id
                   ORDER BY b.bus_no""")
    st.dataframe(pd.DataFrame(rows).drop(columns=["id"]), hide_index=True, width="stretch")
    bid = st.selectbox("Edit bus", [r["id"] for r in rows], format_func=lambda i, rows=rows: next(r["Bus"] for r in rows if r["id"] == i))
    b = db.bus(bid)
    c1, c2, c3, c4 = st.columns(4)
    cap = c1.number_input("Capacity", 10, 80, b["capacity"])

    def pick(col, label, r, cur):
        opts = db.q("SELECT id, name FROM staff WHERE role=? ORDER BY name", (r,))
        ids = [o["id"] for o in opts]
        return col.selectbox(label, ids, index=ids.index(cur) if cur in ids else 0, format_func=lambda i: next(o["name"] for o in opts if o["id"] == i), key=f"bc{r}{bid}")
    nd = pick(c2, "Driver", "driver", b["driver_id"])
    ns = pick(c3, "Supervisor", "supervisor", b["supervisor_id"])
    nc = pick(c4, "Care-taker", "caretaker", b["caretaker_id"])
    fv = st.text_input("Autotrace (FVTS) vehicle name — used to pull live GPS in production", value=b["fvts_vehicle"] or "")
    if st.button("Save bus"):
        db.x("UPDATE buses SET capacity=?, driver_id=?, supervisor_id=?, caretaker_id=?, fvts_vehicle=? WHERE id=?", (cap, nd, ns, nc, fv, bid))
        db.audit(user["username"], role, "BUS_UPDATED", "bus", b["bus_no"], f"cap {cap} crew {nd}/{ns}/{nc}")
        st.session_state.su_last = ("ok", f"{b['bus_no']} saved.")
        st.rerun()

# ------------------------------------------------------------------ stops
with tabs[4]:
    _bl = db.buses()
    _sb = st.selectbox("Bus", [None] + [b["id"] for b in _bl], format_func=lambda i: "All buses" if i is None else next(b["bus_no"] for b in _bl if b["id"] == i),
                       key="stp_bus")
    st.dataframe(db.q("""SELECT b.bus_no AS Bus, s.trip_no AS Trip, s.seq AS Seq, s.name AS Stop, s.area AS Area, ROUND(s.lat,5) AS Lat, ROUND(s.lon,5) AS Lon,
                         (SELECT COUNT(*) FROM trip_assign x WHERE x.stop_id=s.id) AS Students
                         FROM stops s LEFT JOIN buses b ON b.id=s.bus_id WHERE (? IS NULL OR s.bus_id=?) ORDER BY b.bus_no, s.trip_no, s.seq""", (_sb, _sb)),
                 hide_index=True, width="stretch", height=420)
    st.caption(f"School location: {db.school_pos()}. Schedules: " + " · ".join(f"Trip {n} {v['window'][0]}–{v['window'][1]}" for n, v in TRIPS.items()))

# ------------------------------------------------------------------ CSV import
with tabs[5]:
    st.markdown("**Student import (CSV)** — adds new students or updates existing ones (matched by ID). Every row is validated first; "
                "nothing is saved if any row has an error.")
    cols = ["code", "name", "class_name", "section", "batch", "grp", "bus_no", "stop_name", "guardian_name", "guardian_email", "guardian_phone",
            "recipient_name", "recipient_relation", "recipient_phone"]
    ex_bus = db.q1("SELECT b.bus_no, s.name FROM buses b JOIN stops s ON s.bus_id=b.id LIMIT 1") or {"bus_no": "BUS-01", "name": "Stop 1"}
    tmpl = pd.DataFrame([["STU9001", "New Student", "Grade 2", "A", 1, "Main", ex_bus["bus_no"], ex_bus["name"], "Parent Name", "parent@example.com",
                          "+974 5555 0000", "Parent Name", "Father", "+974 5555 0000"]], columns=cols)
    st.download_button("⬇ Template CSV", tmpl.to_csv(index=False).encode(), file_name="students_template.csv", mime="text/csv")
    up = st.file_uploader("Students CSV", type=["csv"], key="csvup")
    if up:
        try:
            df = pd.read_csv(up, dtype=str).fillna("")
        except Exception as e:      # noqa: BLE001
            st.error(f"Cannot read CSV: {e}")
            df = None
        if df is not None:
            errs = []
            miss = [c for c in cols[:8] if c not in df.columns]
            if miss:
                errs.append(f"Missing columns: {', '.join(miss)}")
            else:
                buses = {b["bus_no"]: b["id"] for b in db.buses()}
                stops = {(s["bus_id"], s["name"]): s["id"] for s in db.q("SELECT id, bus_id, name FROM stops")}
                dupe = df["code"][df["code"].duplicated()].tolist()
                if dupe:
                    errs.append(f"Duplicate IDs in file: {dupe[:5]}")
                for i, r in df.iterrows():
                    n = i + 2
                    if not r["code"].strip() or not r["name"].strip():
                        errs.append(f"Row {n}: code and name are required")
                    if r["batch"] not in ("1", "2"):
                        errs.append(f"Row {n}: batch must be 1 or 2")
                    if (r["batch"], r["grp"]) not in (("1", "KG"), ("1", "Main"), ("1", "StayBack"), ("2", "Afternoon")):
                        errs.append(f"Row {n}: batch/grp must be 1/KG, 1/Main, 1/StayBack or 2/Afternoon")
                    if r["bus_no"] not in buses:
                        errs.append(f"Row {n}: unknown bus {r['bus_no']}")
                    elif (buses[r["bus_no"]], r["stop_name"]) not in stops:
                        errs.append(f"Row {n}: stop '{r['stop_name']}' is not on {r['bus_no']}")
                    if "guardian_email" in df and r.get("guardian_email") and "@" not in r["guardian_email"]:
                        errs.append(f"Row {n}: invalid e-mail")
            st.dataframe(df.head(50), hide_index=True, width="stretch")
            if errs:
                st.error(f"{len(errs)} problem(s) — fix the file and upload again:")
                st.write(errs[:40])
            else:
                existing = {s["code"] for s in db.q("SELECT code FROM students")}
                n_new = sum(1 for c in df["code"] if c not in existing)
                st.success(f"Valid: {n_new} new, {len(df) - n_new} updates.")
                if st.button("Import", type="primary"):
                    with db.tx() as c:
                        for _, r in df.iterrows():
                            bid, sid_ = buses[r["bus_no"]], stops[(buses[r["bus_no"]], r["stop_name"])]
                            if r["code"] in existing:
                                c.execute("UPDATE students SET name=?, class_name=?, section=?, batch=?, grp=?, bus_id=?, stop_id=? WHERE code=?",
                                          (r["name"], r["class_name"], r["section"], int(r["batch"]), r["grp"], bid, sid_, r["code"]))
                                continue
                            new_id = c.execute("INSERT INTO students(code, name, class_name, section, batch, grp, bus_id, stop_id) VALUES (?,?,?,?,?,?,?,?)",
                                               (r["code"], r["name"], r["class_name"], r["section"], int(r["batch"]), r["grp"], bid, sid_)).lastrowid
                            if r.get("guardian_name"):
                                gid = c.execute("INSERT INTO guardians(name, phone, email, relation, verified) VALUES (?,?,?,?,0)",
                                                (r["guardian_name"], r.get("guardian_phone", ""), r.get("guardian_email", ""), "Guardian")).lastrowid
                                c.execute("INSERT INTO student_guardians VALUES (?,?,1)", (new_id, gid))
                            if r.get("recipient_name"):
                                c.execute("INSERT INTO recipients(student_id, name, relation, phone) VALUES (?,?,?,?)",
                                          (new_id, r["recipient_name"], r.get("recipient_relation", ""), r.get("recipient_phone", "")))
                            c.execute("INSERT INTO badges(code, student_id, status, issued_at) VALUES (?,?,?,?)",
                                      (new_badge(new_id, r["code"]), new_id, "ACTIVE", db.ts()))
                        db.audit(user["username"], role, "CSV_IMPORT", "students", up.name, f"{n_new} new, {len(df) - n_new} updated", c)
                    st.session_state.su_last = ("ok", f"Imported {n_new} new and updated {len(df) - n_new} students. New guardians start as "
                                                      "'not verified' until the school verifies them.")
                    st.rerun()

# ------------------------------------------------------------------ devices
with tabs[6]:
    devs = db.q("SELECT d.*, b.bus_no FROM devices d LEFT JOIN buses b ON b.id=d.bus_id ORDER BY b.bus_no")
    st.dataframe([{"Device": d["name"], "Bus": d["bus_no"], "Status": d["status"], "Registered": d["registered_at"], "Revoked": d["revoked_at"],
                   "Reason": d["reason"]} for d in devs], hide_index=True, width="stretch")
    did = st.selectbox("Device", [d["id"] for d in devs], format_func=lambda i: next(f"{d['name']} ({d['status']})" for d in devs if d["id"] == i))
    d = next(d for d in devs if d["id"] == did)
    if d["status"] == "ACTIVE":
        why = st.text_input("Reason for revoking", placeholder="Lost / stolen / replaced")
        if st.button("⛔ Revoke device", disabled=not why.strip()):
            db.x("UPDATE devices SET status='REVOKED', revoked_at=?, reason=? WHERE id=?", (db.ts(), why, did))
            db.audit(user["username"], role, "DEVICE_REVOKED", "device", did, why)
            st.session_state.su_last = ("warn", f"{d['name']} revoked — it can no longer start trips or submit scans.")
            st.rerun()
    elif st.button("Re-activate device"):
        db.x("UPDATE devices SET status='ACTIVE', revoked_at=NULL, reason=NULL WHERE id=?", (did,))
        db.audit(user["username"], role, "DEVICE_REACTIVATED", "device", did)
        st.rerun()

# ------------------------------------------------------------------ users
with tabs[7]:
    st.caption("Roles: administrator, transport manager, principal, dispatcher, security, teacher, driver, supervisor, care-taker, receiving staff, verified guardian. "
               "Administrator and transport manager sign in with a second factor (MFA). PINs are stored hashed.")
    us = db.q("SELECT username, role, display_name, active FROM users ORDER BY role, username")
    st.dataframe([{"Login": u["username"], "Role": u["role"], "Name": u["display_name"], "MFA": "Yes" if u["role"] in db.MFA_ROLES else "",
                   "Active": "Yes" if u["active"] else "No"} for u in us], hide_index=True, width="stretch", height=300)
    if role == "admin":
        un = st.selectbox("User", [u["username"] for u in us])
        c1, c2 = st.columns(2)
        newpin = c1.text_input("New PIN / password", type="password")
        if c1.button("Reset PIN", disabled=len(newpin) < 4):
            db.x("UPDATE users SET pin_hash=? WHERE username=?", (db.pin_hash(newpin), un))
            db.audit(user["username"], role, "PIN_RESET", "user", un)
            st.session_state.su_last = ("ok", f"PIN reset for {un}.")
            st.rerun()
        cur = next(u for u in us if u["username"] == un)
        if c2.button("Deactivate" if cur["active"] else "Activate", disabled=un == user["username"]):
            db.x("UPDATE users SET active=? WHERE username=?", (0 if cur["active"] else 1, un))
            db.audit(user["username"], role, "USER_" + ("DEACTIVATED" if cur["active"] else "ACTIVATED"), "user", un)
            st.rerun()
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
        rdate = r1.date_input("Date *", value=date.fromisoformat(db.today()), min_value=date.fromisoformat(db.today()),
                              max_value=date.fromisoformat(db.today()), key="rs_date", format="YYYY/MM/DD",
                              help="Only today's trips can be reset. Previous days are kept as a record.")
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


# ------------------------------------------------------------------ test resets (Flexitrack 360)
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
        _D = db.today()
        st.markdown(f"<span class='pill'>Today {_D}</span> &nbsp;<span class='muted'>Resets only change today. Previous days are kept exactly as they were.</span>",
                    unsafe_allow_html=True)
        what = st.segmented_control("Reset", ["Student", "Class attendance", "Bus (whole day)", "Vehicle check", "Staff", "Visitors",
                                              "Early pickups", "Security patrol", "Alerts", "Parent requests", "Bus assignments", "Whole day"],
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
            elif what == "Security patrol":
                pv_ = R_.patrol_preview(_D)
                st.caption(f"{sum(r['n'] for r in pv_)} patrol record(s) on {_D}"
                           + (": " + " · ".join(f"{r['plan']} ({r['source'] or 'app'}) {r['n']}" for r in pv_) if pv_ else ".")
                           + " Night rounds after midnight belong to the evening the shift started.")
                pl_ = st.selectbox("Plan", [None] + sorted({r["plan"] for r in pv_}), format_func=lambda x: "All plans" if x is None else x, key="tr_ppl")
                so_ = st.selectbox("Source", [None] + sorted({r["source"] for r in pv_}),
                                   format_func=lambda x: "All sources (app rounds and uploaded files)" if x is None else (x or "Patrol app"), key="tr_pso")
                st.caption("Removes the checkpoint scans and their 'patrol checkpoint missed' alerts, so the Security patrols dashboard starts from zero "
                           "(or only the chosen plan / upload). You can upload the patrol file again afterwards.")
                if st.button("Reset security patrol", type="primary", disabled=not _confirm("pt"), key="tr_ptgo"):
                    n_, a_ = R_.reset_patrol(_D, user["username"], pl_, so_)
                    st.session_state.tr_msg = ("ok", f"{n_} patrol record(s) and {a_} missed-checkpoint alert(s) removed.")
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
                mode_ = st.radio("How", ["zero", "replay"], key="tr_wdmode", format_func={
                    "zero": "Start today from ZERO — remove all of today's activity; every total is 0 except students enrolled (simulation off for today)",
                    "replay": "Rebuild today with simulated activity up to now"}.get)
                st.caption("Removes all of today's trips, scans, gate scans, attendance, visitors, early pickups, alerts, checks, patrol rounds, "
                           "absences, requests and messages. Previous days are not touched. Affects BOTH teams — agree it first.")
                rel = st.checkbox("Also release buses that were reset for manual testing today (reserved test buses BUS-001 / BUS-026 stay manual)",
                                  key="tr_rel")
                if st.button("Reset today", type="primary", disabled=not _confirm("wd"), key="tr_wdgo"):
                    if rel:
                        R_.release_manual_buses(user["username"])
                    with st.spinner("Resetting today…"):
                        if mode_ == "zero":
                            _dd.clear_today(user["username"])
                        else:
                            _dd.replay_today_at("now")
                            db.audit(user["username"], role, "TEST_RESET_DAY", "day", _D, "rebuilt to now")
                    st.session_state.tr_msg = ("ok", "Today starts from zero — only students enrolled are counted. Use 'Rebuild today' to bring the "
                                               "simulated day back." if mode_ == "zero" else "Today rebuilt up to now.")
                    st.rerun()
                st.caption("Everything from scratch (all days, all data): Data source tab → Regenerate the demo.")


with tabs[12]:
    import integrations as I
    import security_ext as SX
    if role not in ("admin", "manager"):
        st.info("Only the administrator or transport lead can manage integrations.")
    else:
        st.markdown("**Connections to other systems.** Flexitrack 360 exposes a REST API (run `api.py` next to the app; documentation at `/docs`). "
                    "The CSMS ERP, the vehicle tracker, RFID readers and Woqod push to it or are imported here.")
        st.dataframe(pd.DataFrame(I.status()), hide_index=True, width="stretch")
        it = st.segmented_control("Section", ["API keys", "CSMS ERP", "Woqod fuel", "Vehicle tracker", "RFID readers", "Scan speed"], default="API keys",
                                  key="int_sec") or "API keys"
        with st.container(border=True):
            if it == "API keys":
                st.caption("Each system gets its own key with only the scopes it needs. The key is shown once — store it in that system's settings.")
                a_, b_ = st.columns([2, 3])
                kn = a_.text_input("Key name", placeholder="e.g. CSMS ERP, Tracker vendor, Gate readers", key="ak_n")
                ks = b_.multiselect("Scopes", I.SCOPES, default=["read"], key="ak_s")
                if st.button("Create API key", type="primary", disabled=not (kn.strip() and ks), key="ak_go"):
                    st.session_state.ak_new = I.create_api_key(kn.strip(), ks, user["username"])
                if st.session_state.get("ak_new"):
                    st.code(st.session_state.ak_new)
                    st.warning("Copy this key now — it will not be shown again.")
                keys = I.api_keys()
                st.dataframe(pd.DataFrame([{"Name": k_["name"], "Key starts": k_["prefix"] + "…", "Scopes": k_["scopes"], "Created": k_["ts"][:16],
                                            "Last used": (k_["last_used"] or "")[:16], "Active": "yes" if k_["active"] else "revoked"} for k_ in keys]),
                             hide_index=True, width="stretch")
                act_ = [k_ for k_ in keys if k_["active"]]
                if act_:
                    rk = st.selectbox("Revoke", [None] + [k_["id"] for k_ in act_], key="ak_rv",
                                      format_func=lambda i: "—" if i is None else next(f"{k_['name']} ({k_['prefix']}…)" for k_ in act_ if k_["id"] == i))
                    if rk and st.button("Revoke key", key="ak_rv_go"):
                        I.revoke_api_key(rk, user["username"])
                        st.rerun()
                st.caption("Endpoints: GET /v1/students, /v1/students/{code}, /v1/presence, /v1/buses, /v1/trips, /v1/trips/{id}/manifest, /v1/attendance, "
                           "/v1/alerts, /v1/inspections, /v1/fleet/job-orders, /v1/fleet/documents, /v1/eta · POST /v1/scans/bus, /v1/scans/gate, /v1/rfid, "
                           "/v1/gps/positions, /v1/telematics/events, /v1/fuel/transactions, /v1/csms/sync.")
            elif it == "CSMS ERP":
                cfg = I.csms_config()
                st.caption("Students, parents, staff, vehicles, routes and stops come from CSMS so nobody types them twice. Until school IT shares the CSMS "
                           "API, upload a CSMS export here: Flexitrack 360 shows exactly what would change before anything is written.")
                a_, b_ = st.columns(2)
                cu = a_.text_input("CSMS API base URL", value=cfg["base_url"], placeholder="https://csms.dpsmisdoha.com/api", key="cs_u")
                ck = b_.text_input("CSMS API key", type="password", placeholder="(stored)" if cfg["has_key"] else "", key="cs_k")
                if st.button("Save CSMS connection", key="cs_save"):
                    db.set_setting("csms_base_url", cu.strip())
                    if ck.strip():
                        db.set_setting("csms_api_key", ck.strip())
                    st.success("Saved.")
                ent = st.radio("Data", ["students", "staff", "buses"], horizontal=True, key="cs_e")
                up = st.file_uploader("CSMS export (CSV, Excel or JSON)", type=["csv", "xlsx", "json"], key="cs_f")
                rows_ = None
                if up is not None:
                    import json as _j
                    rows_ = (_j.loads(up.getvalue()) if up.name.endswith(".json") else
                             (pd.read_csv(up, dtype=str) if up.name.endswith(".csv") else pd.read_excel(up, dtype=str)).to_dict("records"))
                elif cfg["base_url"] and st.button(f"Pull {ent} from the CSMS API", key="cs_pull"):
                    try:
                        rows_ = I.csms_pull(ent)
                    except Exception as ex:
                        st.error(str(ex))
                if rows_:
                    d_ = I.csms_diff(ent, rows_)
                    st.session_state.cs_diff = d_
                d_ = st.session_state.get("cs_diff")
                if d_ and d_["entity"] == ent:
                    U.kpis([("NEW", len(d_["new"]), f"{ent} to add", "#2E9E5B", "green"), ("CHANGED", len(d_["changed"]), "fields to update", "#3D4FD6", ""),
                            ("NOT IN CSMS", len(d_["missing"]), "kept unless you deactivate", "#E07A1F", "")], n=3)
                    if d_["changed"]:
                        st.dataframe(pd.DataFrame([{"Key": c_["key"], "Field": f_, "Flexitrack 360": v[0], "CSMS": v[1]} for c_ in d_["changed"][:300]
                                                   for f_, v in c_["changes"].items()]), hide_index=True, width="stretch", height=220)
                    if d_["new"]:
                        st.dataframe(pd.DataFrame(d_["new"][:300]), hide_index=True, width="stretch", height=180)
                    deact = st.checkbox("Deactivate students missing from CSMS (left school)", key="cs_deact") if ent == "students" else False
                    if st.button("Apply these changes", type="primary", key="cs_apply"):
                        r_ = I.csms_apply(d_, user["username"], deact)
                        st.session_state.pop("cs_diff", None)
                        st.success(f"Applied: {r_['new']} new, {r_['updated']} updated, {r_['deactivated']} deactivated.")
                st.caption("Accepted column names include student_id / admission_no, name, class / grade, section, gender, transport, shift; "
                           "employee_id, designation, mobile, department; vehicle_no, plate_no, seats, route.")
            elif it == "Woqod fuel":
                st.caption("Upload the monthly / weekly Woqod fuel-card statement. Lines are matched to buses by plate or card number; lines already "
                           "imported (same transaction id) are skipped; suspicious fills are flagged in Fleet → Fuel. When the school gets Woqod API access, "
                           "the same import runs automatically through POST /v1/fuel/transactions.")
                st.download_button("Statement template (CSV)", pd.DataFrame(I.woqod_template()).to_csv(index=False).encode(), "woqod_statement_template.csv",
                                   "text/csv", key="wq_t")
                wf = st.file_uploader("Woqod statement (CSV or Excel)", type=["csv", "xlsx", "xls"], key="wq_f")
                if wf is not None and st.button("Import statement", type="primary", key="wq_go"):
                    df_ = pd.read_csv(wf) if wf.name.lower().endswith(".csv") else pd.read_excel(wf)
                    n_, fl_, pb_ = I.woqod_import(df_, user["username"])
                    st.success(f"{n_} fuel line(s) imported, {len(fl_)} flagged.") if n_ else st.warning("Nothing imported.")
                    if pb_:
                        st.warning(pb_)
            elif it == "Vehicle tracker":
                st.caption("The tracker (FVTS / GPS box on each bus) pushes positions and events to the API. Positions give the live map, the odometer "
                           "for maintenance and parents' ETA; speeds above the limit and harsh braking / acceleration raise alerts and feed driver "
                           "behaviour in Fleet.")
                sl = st.number_input("Speed limit (km/h)", value=I.speed_limit(), step=5.0, key="tk_sl")
                if st.button("Save limit", key="tk_sl_go"):
                    db.set_setting("speed_limit_kmh", sl)
                    st.success("Saved.")
                bl_ = db.q("SELECT id, bus_no FROM buses ORDER BY bus_no")
                dbb = st.selectbox("Demo: send a 6-minute GPS track for", [b_["id"] for b_ in bl_], format_func=lambda i: next(b_["bus_no"] for b_ in bl_ if b_["id"] == i),
                                   key="tk_b")
                if st.button("Send demo track (includes one over-speed point)", key="tk_go"):
                    n_, a_ = I.demo_drive(dbb)
                    st.success(f"{n_} positions stored, {a_} speeding alert(s).")
                pos = db.q("SELECT g.ts, b.bus_no, g.lat, g.lon, g.speed_kmh, g.source FROM gps_positions g JOIN buses b ON b.id=g.bus_id ORDER BY g.ts DESC LIMIT 50")
                ev = db.q("SELECT t.ts, b.bus_no, t.kind, t.value, t.source FROM telematics_events t JOIN buses b ON b.id=t.bus_id ORDER BY t.ts DESC LIMIT 50")
                a_, b_ = st.columns(2)
                a_.markdown("**Latest positions**")
                a_.dataframe(pd.DataFrame(pos), hide_index=True, width="stretch", height=260)
                b_.markdown("**Latest events**")
                b_.dataframe(pd.DataFrame(ev), hide_index=True, width="stretch", height=260)
            elif it == "RFID readers":
                st.caption("Each reader is registered to a bus door or a gate. A tag read is processed like a card scan — wrong bus, unknown tag and "
                           "exit-without-pass checks stay the same. Map tag UIDs to card numbers if the tags carry their own UID.")
                a_, b_, c_ = st.columns(3)
                rid_ = a_.text_input("Reader id", placeholder="e.g. BUS-001-DOOR", key="rf_id")
                pt_ = b_.selectbox("Point", ["BUS_IN", "BUS_OUT", "GATE"], key="rf_pt")
                if pt_ == "GATE":
                    gl_ = db.q("SELECT id, name FROM gates")
                    tgt = c_.selectbox("Gate", [g_["id"] for g_ in gl_], format_func=lambda i: next(g_["name"] for g_ in gl_ if g_["id"] == i), key="rf_g")
                else:
                    bl_ = db.q("SELECT id, bus_no FROM buses ORDER BY bus_no")
                    tgt = c_.selectbox("Bus", [b_x["id"] for b_x in bl_], format_func=lambda i: next(b_x["bus_no"] for b_x in bl_ if b_x["id"] == i), key="rf_b")
                if st.button("Register reader", key="rf_go", disabled=not rid_.strip()):
                    I.register_reader(rid_.strip(), pt_, None if pt_ == "GATE" else tgt, tgt if pt_ == "GATE" else None)
                    st.success("Reader registered.")
                st.dataframe(pd.DataFrame(db.q("SELECT reader_id, point, bus_id, gate_id FROM rfid_readers")), hide_index=True, width="stretch")
                tu = st.text_input("Test a tag read: reader id | tag", placeholder="BUS-001-DOOR | 27xxxxx", key="rf_test")
                if "|" in tu and st.button("Send test read", key="rf_t_go"):
                    a1, a2 = [x_.strip() for x_ in tu.split("|", 1)]
                    st.json(I.rfid_scan(a1, a2))
            elif it == "Scan speed":
                st.caption("school IT asked how fast the bus and gate points register a scan. Every scan is timed from the read to the result on screen "
                           "(server side).")
                a_, b_ = st.columns(2)
                f_ = a_.date_input("From", value=pd.Timestamp(db.today()) - pd.Timedelta(days=7), key="ss_f")
                t_ = b_.date_input("To", value=pd.Timestamp(db.today()), key="ss_t")
                sp = SX.scan_speed(f_.strftime("%Y-%m-%d"), t_.strftime("%Y-%m-%d"))
                if sp:
                    st.dataframe(pd.DataFrame([{"Scan point": k_, "Scans": v["scans"], "Median (ms)": round(v["median_ms"]), "95% under (ms)": round(v["p95_ms"]),
                                                "Slowest (ms)": round(v["max_ms"]), "Under 1 second": f"{v['under_1s']:.1%}"} for k_, v in sp.items()]),
                                 hide_index=True, width="stretch")
                else:
                    st.caption("No scans in this period.")

