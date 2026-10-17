"""Campus & visitors (office view): who is on campus by grade, early-pickup approvals, class attendance tracker, visitors, staff and all campus alerts."""
from collections import Counter, defaultdict

import pandas as pd
import streamlit as st

import campus
import db
import ui360 as U
from ui import msg

U.css()
user = st.session_state.user
role = user["role"]
D = db.today()
st.title("🏫 Campus & visitors")

pend = campus.early_pickups(D, "PENDING")
tabs = st.tabs(["👥 Students on campus", f"🕐 Early pickups ({len(pend)} to approve)", "✅ Attendance tracker", "🪪 Visitors", "👩‍🏫 Staff",
                "🚨 Campus alerts"])

with tabs[0]:
    pres = campus.presence(D)
    studs = db.q("SELECT id, name, code, class_name, section, transport, gender, batch, grp FROM students WHERE active=1")
    by = defaultdict(Counter)
    for s in studs:
        by[s["class_name"]][pres[s["id"]]] += 1
    order = ["KG1", "KG2"] + [f"Grade {i}" for i in range(1, 13)]
    rows = []
    for g in order:
        c = by[g]
        rows.append({"Grade": g, "On roll": sum(c.values()), "On campus": c["ON_CAMPUS"], "On a bus": c["ON_BUS"], "Gone home": c["LEFT"],
                     "Absent": c["ABSENT"], "Not arrived": c["NOT_ARRIVED"], "Unaccounted": c["UNACCOUNTED"]})
    df = pd.DataFrame(rows)
    st.dataframe(df, hide_index=True, width="stretch", height=540,
                 column_config={"On campus": st.column_config.ProgressColumn("On campus", min_value=0, max_value=int(df["On roll"].max()), format="%d")})
    un = [s for s in studs if pres[s["id"]] in ("UNACCOUNTED",)]
    st.markdown(f"**Unaccounted now: {len(un)}** — expected at school, no bus or gate scan and no absence notice.")
    for s in un[:30]:
        U.person_card(s, "STUDENT", "", size=44)

with tabs[1]:
    st.caption("Parents request early pickup in the parent app. Approving creates a gate pass for Security, takes the child off the home bus "
               "list and tells the class teacher.")
    allep = campus.early_pickups(D)
    if not allep:
        st.info("No early-pickup requests today.")
    for e in allep:
        with st.container(border=True):
            a, b = st.columns([4, 1.4], vertical_alignment="center")
            with a:
                U.person_card({"id": e["student_id"], "name": e["name"], "gender": e["gender"], "class_name": e["class"].split("-")[0],
                               "section": e["class"].split("-")[-1], "code": e["code"], "transport": e["transport"]}, "STUDENT",
                              f"<div class='mini'>🕐 {e['pickup_time']} · {e['collector']} ({e['relation']}, QID {e['collector_id'] or '—'}, {e['collector_phone']}) · "
                              f"{e['reason']} · requested {e['requested_ts'][11:16]}</div>"
                              + U.pill(e["status"].title() + (f" · pass {e['pass_code']}" if e["pass_code"] else ""),
                                       {"PENDING": "orange", "APPROVED": "blue", "COMPLETED": "green", "REJECTED": "red"}[e["status"]]), size=56)
            if e["status"] == "PENDING" and role in ("manager", "admin", "dispatcher", "principal"):
                if b.button("✅ Approve", key=f"epa{e['id']}", type="primary", width="stretch"):
                    ok, m = campus.decide_early(e["id"], True, user["display_name"], role)
                    st.session_state["ep_msg"] = ("ok" if ok else "error", m)
                    st.rerun()
                if b.button("✖ Reject", key=f"epr{e['id']}", width="stretch"):
                    ok, m = campus.decide_early(e["id"], False, user["display_name"], role, "Please contact the class teacher.")
                    st.session_state["ep_msg"] = ("warn", m)
                    st.rerun()
    if st.session_state.get("ep_msg"):
        msg(*st.session_state.pop("ep_msg"))

with tabs[2]:
    cls = db.q("""SELECT c.*, s.name AS teacher, a.ts AS sub_ts, a.present, a.absent, a.late FROM classes c LEFT JOIN staff s ON s.id=c.teacher_id
                  LEFT JOIN att_submissions a ON a.class_id=c.id AND a.date=? ORDER BY c.shift, c.id""", (D,))
    due_aft = db.now().strftime("%H:%M") >= "13:10"
    pending = [c for c in cls if not c["sub_ts"] and (c["shift"] == 1 or due_aft)]
    U.kpis([("CLASSES", len(cls), "", "#1B2559", ""), ("SUBMITTED", sum(1 for c in cls if c["sub_ts"]), "", "#2E9E5B", "green"),
            ("DUE, NOT IN", len(pending), "chase these teachers", "#D93025", "red" if pending else ""),
            ("ABSENT (SUBMITTED)", sum(c["absent"] or 0 for c in cls), "", "#E07A1F", "orange")], n=4)
    if pending:
        st.markdown("**Not submitted yet:** " + " · ".join(f"{c['name']}-{c['section']} ({c['teacher']})" for c in pending))
    st.dataframe(pd.DataFrame([{"Class": f"{c['name']}-{c['section']}", "Shift": "AM" if c["shift"] == 1 else "PM", "Teacher": c["teacher"],
                                "Submitted": (c["sub_ts"] or "")[11:16], "Present": c["present"], "Late": c["late"], "Absent": c["absent"]} for c in cls]),
                 hide_index=True, width="stretch", height=420)
    ab = db.q("""SELECT s.name, s.class_name || '-' || s.section AS cls, a.note, s.transport,
                        (SELECT reason FROM absences x WHERE x.student_id=s.id AND x.date=a.date AND x.trip_no IS NULL LIMIT 1) AS reason
                 FROM attendance a JOIN students s ON s.id=a.student_id WHERE a.date=? AND a.status='ABSENT' ORDER BY cls""", (D,))
    with st.expander(f"Absent today ({len(ab)})"):
        st.dataframe(pd.DataFrame([{"Student": r["name"], "Class": r["cls"], "Transport": r["transport"].title(),
                                    "Parent notice": r["reason"] or "— none (parents notified)"} for r in ab]), hide_index=True, width="stretch")

with tabs[3]:
    v = campus.visitors(D)
    on = [x for x in v if x["status"] == "ON_CAMPUS"]
    U.kpis([("ON CAMPUS", len(on), "", "#0F9BB5", ""), ("TODAY", len(v), "", "#1B2559", ""),
            ("PARENT VISITS", sum(1 for x in v if x["company"] == "Parent"), "", "#3D4FD6", ""),
            ("CONTRACTORS / VENDORS", sum(1 for x in v if x["company"] not in ("Parent", "—", "")), "", "#7B5CD6", "")], n=4)
    st.dataframe(pd.DataFrame([{"Pass": x["pass_no"], "Name": x["name"], "Purpose": x["purpose"], "Company": x["company"], "Host": x["host"],
                                "In": (x["in_ts"] or "")[11:16], "Out": (x["out_ts"] or "")[11:16], "Status": x["status"].replace("_", " ").title(),
                                "Vehicle": x["vehicle"]} for x in v]), hide_index=True, width="stretch", height=420)

with tabs[4]:
    sm = campus.last_moves(D, "STAFF")
    staff = db.q("SELECT id, name, role, dept, gender, card_no FROM staff WHERE role IN ('teacher','staff','principal','security','receiving','manager','dispatcher')")
    on = [s for s in staff if sm.get(s["id"], {}).get("dir") == "IN"]
    left = [s for s in staff if sm.get(s["id"], {}).get("dir") == "OUT"]
    notin = [s for s in staff if s["id"] not in sm]
    U.kpis([("STAFF", len(staff), "", "#1B2559", ""), ("ON CAMPUS", len(on), "", "#2E9E5B", "green"), ("LEFT", len(left), "", "#9AA3BD", ""),
            ("NOT IN TODAY", len(notin), "leave / afternoon shift", "#E07A1F", "")], n=4)
    st.dataframe(pd.DataFrame([{"Name": s["name"], "Role": s["role"].title(), "Department": s["dept"], "Card": s["card_no"],
                                "Status": {"IN": "On campus", "OUT": "Left"}.get(sm.get(s["id"], {}).get("dir"), "Not in"),
                                "Last scan": (sm.get(s["id"], {}).get("ts") or "")[11:16]} for s in staff]), hide_index=True, width="stretch", height=420)

with tabs[5]:
    al = campus.alerts(D)
    kinds = sorted({a["kind"] for a in al})
    f = st.multiselect("Type", kinds, format_func=lambda k: campus.ALERT_TEXT.get(k, k), key="ca_f")
    sh = st.toggle("Open only", value=True, key="ca_open")
    rows = [a for a in al if (not f or a["kind"] in f) and (not sh or a["status"] != "RESOLVED")]
    st.dataframe(pd.DataFrame([{"Time": a["ts"][11:16], "Severity": a["severity"].title(), "Type": campus.ALERT_TEXT.get(a["kind"], a["kind"]),
                                "Details": a["details"], "For": (a["audience"] or "").title(), "Status": a["status"].title(),
                                "Resolution": a["resolution"] or ""} for a in rows]), hide_index=True, width="stretch", height=460)
    if role in ("manager", "admin", "dispatcher") and rows:
        with st.form("ca_bulk"):
            st.markdown("Resolve informational alerts in bulk (e.g. parents already notified)")
            k = st.selectbox("Type", sorted({a["kind"] for a in rows if a["status"] != "RESOLVED"}) or ["—"],
                             format_func=lambda k: campus.ALERT_TEXT.get(k, k))
            if st.form_submit_button("Resolve all of this type"):
                n = 0
                for a in rows:
                    if a["kind"] == k and a["status"] != "RESOLVED":
                        campus.resolve_alert(a["id"], user["display_name"], "Reviewed by office")
                        n += 1
                st.session_state["ca_msg"] = f"{n} alert(s) resolved."
                st.rerun()
    if st.session_state.get("ca_msg"):
        msg("ok", st.session_state.pop("ca_msg"))
