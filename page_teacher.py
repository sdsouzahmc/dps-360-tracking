"""Teacher app: the class list is pre-filled from bus arrivals and gate scans; the teacher verifies, marks and submits.
On submit: absent and not on campus → parents notified; absent but on campus → security alerted to find the child."""
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

cls = campus.teacher_class(user["staff_id"]) if role == "teacher" else None
if not cls:
    allc = db.q("SELECT c.*, s.name AS teacher FROM classes c LEFT JOIN staff s ON s.id=c.teacher_id ORDER BY c.shift, c.id")
    if role == "teacher":
        st.warning("You are not a class teacher — choose a class to take attendance for.")
    cid = st.selectbox("Class", [c["id"] for c in allc], format_func=lambda i: next(f"{c['name']}-{c['section']} · {c['teacher']}" for c in allc
                                                                                 if c["id"] == i), key="t_cls")
    cls = db.q1("SELECT * FROM classes WHERE id=?", (cid,))
teacher = db.staff_name(cls["teacher_id"])
arrive_by = campus.SHIFT_TIMES[cls["shift"]][1]
c_, roster = campus.class_roster(cls["id"], D)
sub = db.q1("SELECT * FROM att_submissions WHERE date=? AND class_id=?", (D, cls["id"]))

st.title(f"✅ {cls['name']}-{cls['section']} attendance")
st.markdown(f"<div class='datebar'>📅 <b>{db.now():%A %d %b %Y}</b> · {db.now():%H:%M} <span>· {'Morning' if cls['shift'] == 1 else 'Afternoon'} shift · "
            f"classes start {arrive_by} · room {cls['room']} · class teacher {teacher}</span></div>", unsafe_allow_html=True)
if sub:
    msg("ok", f"Submitted at {sub['ts'][11:16]} by {sub['teacher']} — {sub['present']} present, {sub['late']} late, {sub['absent']} absent. "
        "You can correct and submit again.")

oncamp = sum(1 for r in roster if r["on_campus"])
arrived = sum(1 for r in roster if r["suggested"] in ("PRESENT", "LATE") and not r["source"].startswith("On "))
onbus = sum(1 for r in roster if r["source"].startswith("On "))
decl = sum(1 for r in roster if r["declared"])
noscan = sum(1 for r in roster if r["source"].startswith("No bus"))
U.kpis([("ON ROLL", len(roster), "", "#1B2559", ""), ("ARRIVED TODAY", arrived, f"{oncamp} still on campus", "#2E9E5B", "green"),
        ("ON A BUS", onbus, "arriving", "#3D4FD6", ""), ("PARENT SAID ABSENT", decl, "", "#E07A1F", "orange"),
        ("NO SCAN YET", noscan, "not on a bus, not through a gate", "#D93025", "red" if noscan else "")], n=5)

ep = [e for e in campus.early_pickups(D) if e["class"] == f"{cls['name']}-{cls['section']}" and e["status"] in ("APPROVED", "COMPLETED")]
for e in ep:
    st.markdown(f"<div class='warn'>🕐 <b>{e['name']}</b> leaves early at {e['pickup_time']} with {e['collector']} ({e['relation']}) — "
                f"{'already collected at ' + e['out_ts'][11:16] if e['status'] == 'COMPLETED' else 'pass ' + e['pass_code'] + ', security will release'}.</div>",
                unsafe_allow_html=True)

st.markdown("##### Verify the list")
st.caption("Suggested from today's scans: arrived by bus or through a gate → Present (Late after " + arrive_by + "); parent absence → Absent. "
           "Change anything that is not right, then submit.")
OPTS = ["PRESENT", "LATE", "ABSENT"]
LAB = {"PRESENT": "Present", "LATE": "Late", "ABSENT": "Absent"}
marks = {}
for r in roster:
    a, b, c = st.columns([2.4, 2.4, 2.2], vertical_alignment="center")
    img = campus.avatar_img(r["student_id"], r["gender"], "STUDENT", 44)
    tag = "🚌" if r["transport"] == "BUS" else "🚗"
    a.markdown(f"<div style='display:flex;gap:10px;align-items:center'>{img}<div><b>{r['name']}</b><div class='mini'>{r['code']} · {tag}</div></div></div>",
               unsafe_allow_html=True)
    col = "green" if r["suggested"] in ("PRESENT",) else "orange" if r["suggested"] == "LATE" else "red" if not r["declared"] else "grey"
    b.markdown(U.pill(r["source"], col), unsafe_allow_html=True)
    default = r["status"] or r["suggested"]
    marks[r["student_id"]] = c.segmented_control("Mark", OPTS, default=default, format_func=LAB.get, key=f"att_{cls['id']}_{r['student_id']}",
                                                  label_visibility="collapsed") or default
st.divider()
chg = sum(1 for r in roster if marks[r["student_id"]] != r["suggested"])
p_, l_, a_ = (sum(1 for v in marks.values() if v == k) for k in OPTS)
st.markdown(f"**{p_} present · {l_} late · {a_} absent** · {chg} changed from the suggestion")
if st.button("📤 Submit attendance", type="primary", width="stretch"):
    counts, notified, alerted = campus.submit_attendance(cls["id"], D, marks, teacher if role == "teacher" else f"{user['display_name']} for {teacher}")
    st.session_state["att_done"] = (counts, notified, alerted)
    st.rerun()
if st.session_state.get("att_done"):
    counts, notified, alerted = st.session_state.pop("att_done")
    msg("ok", f"Submitted: {counts['PRESENT']} present, {counts.get('LATE', 0)} late, {counts['ABSENT']} absent.")
    if notified:
        msg("warn", f"📩 {notified} parent(s) notified: child absent and not on campus. Bus students were taken off today's home bus list.")
    if alerted:
        msg("error", f"🚨 {alerted} child(ren) marked absent but scanned on campus — Security has been alerted with the photo and last scan.")

with st.expander("This class over the last school days"):
    import engine as E
    days = E.school_days_back(5)
    ph = ",".join("?" * len(days))
    h = pd.DataFrame(db.q(f"""SELECT a.date, s.name, a.status FROM attendance a JOIN students s ON s.id=a.student_id WHERE a.class_id=? AND a.date IN ({ph})""",
                          [cls["id"]] + days))
    if not h.empty:
        pv = h.pivot_table(index="name", columns="date", values="status", aggfunc="first").fillna("")
        pv = pv.replace({"PRESENT": "✓", "LATE": "L", "ABSENT": "✗"})
        pv.columns = [pd.to_datetime(c).strftime("%a %d") for c in pv.columns]
        st.dataframe(pv, width="stretch")
