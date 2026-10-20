"""Student 360: one child's whole day — buses per session, gate scans, class attendance, early pickup, alerts and parent messages."""
from datetime import datetime

import pandas as pd
import streamlit as st

import campus
import db
import engine as E
from db import TRIPS
import ui360 as U

U.css()
D = db.today()
st.title("🔎 Student 360")

if "p360_sid" not in st.session_state:
    ex = db.q1("""SELECT pid FROM gate_scans WHERE date=? AND ptype='STUDENT' AND method='EARLY_PICKUP' LIMIT 1""", (D,)) or \
        db.q1("SELECT student_id AS pid FROM campus_alerts WHERE date=? AND kind='ABSENT_ON_CAMPUS' LIMIT 1", (D,)) or {"pid": 1}
    st.session_state.p360_sid = ex["pid"]

c1, c2 = st.columns([3, 2], vertical_alignment="bottom")
qtxt = c1.text_input("Search", placeholder="Name, student ID (DPS…), card number or class e.g. 5-A", key="p360_q")
day = c2.date_input("Day", value=datetime.strptime(D, "%Y-%m-%d"), key="p360_day").strftime("%Y-%m-%d")
if qtxt.strip():
    t = qtxt.strip()
    like = f"%{t}%"
    hits = db.q("""SELECT s.id, s.name, s.code, s.class_name, s.section, s.transport FROM students s LEFT JOIN badges b ON b.student_id=s.id
                   WHERE s.name LIKE ? OR s.code LIKE ? OR b.code=? OR (s.class_name || '-' || s.section) LIKE ? OR (REPLACE(s.class_name,'Grade ','') || '-' || s.section)=?
                   GROUP BY s.id ORDER BY s.name LIMIT 40""", (like, like, t, like, t))
    if hits:
        sel = st.selectbox(f"{len(hits)} match(es)", [h["id"] for h in hits], key="p360_pick",
                           format_func=lambda i: next(f"{h['name']} · {h['class_name']}-{h['section']} · {h['code']} · {h['transport'].title()}" for h in hits if h["id"] == i))
        st.session_state.p360_sid = sel
    else:
        st.info("No student found.")

sid = st.session_state.p360_sid
s = campus.student_card(sid)
if not s:
    st.stop()
pres = campus.presence(D).get(sid) if day == D else None
left, right = st.columns([1.05, 1.6])
with left:
    with st.container(border=True):
        st.markdown(f"<div style='display:flex;gap:16px;align-items:center'>{campus.avatar_img(s['id'], s['gender'], 'STUDENT', 120)}"
                    f"<div><div style='font-size:1.45rem;font-weight:800'>{s['name']}</div>"
                    f"<div class='muted'>{s['class_name']}-{s['section']} · {s['code']} · card {s['card'] or '—'}</div>"
                    f"<div class='muted'>{db.mlabel(s['batch'], s['grp'])} · class teacher {s['teacher'] or '—'}</div>"
                    + (f"<div style='margin-top:6px'>{U.pill(U.STATE_LABEL.get(pres, pres), 'green' if pres == 'ON_CAMPUS' else 'blue' if pres == 'ON_BUS' else 'red' if pres == 'UNACCOUNTED' else 'grey')}</div>" if pres else "")
                    + "</div></div>", unsafe_allow_html=True)
        st.markdown("**Transport**")
        if s["transport"] == "BUS":
            rows = []
            for n, t in TRIPS.items():
                mv = [p for b, g, p in t["movements"] if b == s["batch"] and g == s["grp"]]
                if not mv:
                    continue
                b, st_ = E.effective_assignment(s, day, n, None)
                rows.append({"Trip": f"{n} · {t['short']}", "Bus": db.bus(b)["bus_no"] if b else "—", "Stop": (db.stop(st_) or {}).get("name", "—"),
                             "": "Pickup" if mv[0] == "PICKUP" else "Drop-off"})
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
            st.caption("DPS assigns a bus per session — the morning and afternoon buses can differ.")
        else:
            st.markdown("🚗 Private transport — tracked at the campus gates.")
        g = db.q("""SELECT g.name, g.relation, g.phone FROM guardians g JOIN student_guardians sg ON sg.guardian_id=g.id WHERE sg.student_id=?""", (sid,))
        rec = db.q("SELECT name, relation FROM recipients WHERE student_id=? AND active=1", (sid,))
        import security_ext as SX
        st.markdown("**Parents and allowed collectors** (photo check at the gate)")
        st.markdown(SX.collectors_html(sid, 52), unsafe_allow_html=True)
        _gp = SX.gate_passes(True, sid)
        if _gp:
            st.markdown("**Standing gate pass** · " + " · ".join(f"{x['pass_code']} {x['days']} → {x['collector']}" for x in _gp))
with right:
    with st.container(border=True):
        st.markdown(f"<div class='sect'>🕓 Day timeline · {datetime.strptime(day, '%Y-%m-%d'):%A %d %b}</div>", unsafe_allow_html=True)
        tl = campus.timeline(sid, day)
        if tl:
            st.markdown("<div class='tl'>" + "".join(f"<div class='e'><span class='tm'>{(e['ts'] or '')[11:16]}</span>"
                                                     f"<span class='src'>{e['src']}</span><div>{e['icon']} {e['what']}</div></div>" for e in tl) + "</div>",
                        unsafe_allow_html=True)
        else:
            st.caption("Nothing recorded on this day.")
    with st.container(border=True):
        st.markdown("<div class='sect'>📅 Last school days</div>", unsafe_allow_html=True)
        days = E.school_days_back(5) + [D]
        rows = []
        for d in days:
            fi = db.q1("SELECT MIN(ts) ts, method, ref FROM campus_moves WHERE ptype='STUDENT' AND pid=? AND date=? AND dir='IN'", (sid, d))
            lo = db.q1("SELECT MAX(ts) ts FROM campus_moves WHERE ptype='STUDENT' AND pid=? AND date=? AND dir='OUT'", (sid, d))
            home = db.q1("""SELECT m.exit_ts FROM manifest m JOIN trip_runs r ON r.id=m.run_id WHERE m.student_id=? AND r.date=? AND m.purpose='DROPOFF'
                            AND m.handover='HOME_RELEASE'""", (sid, d))
            at = db.q1("SELECT status FROM attendance WHERE student_id=? AND date=?", (sid, d))
            rows.append({"Day": datetime.strptime(d, "%Y-%m-%d").strftime("%a %d"), "Arrived": (fi["ts"] or "")[11:16],
                         "How": ("Bus " + (fi["ref"] or "")) if fi["method"] == "BUS" else ("Gate" if fi["ts"] else ""),
                         "Class": (at or {}).get("status", "").title(), "Left campus": (lo["ts"] or "")[11:16], "Home (bus)": ((home or {}).get("exit_ts") or "")[11:16]})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    msgs = db.q("""SELECT ts, kind, message FROM notifications WHERE student_id=? AND ts LIKE ? AND channel='IN_APP' GROUP BY ts, message
                   ORDER BY ts DESC LIMIT 30""",
                (sid, day + "%"))
    with st.expander(f"📩 Messages sent to the parents ({len(msgs)})"):
        for m in msgs:
            st.markdown(f"`{m['ts'][11:16]}` {m['message']}")

if st.session_state.user["role"] in ("admin", "manager") and day == D:
    with st.expander("🧹 Test reset for this student (today)"):
        import resets as R_
        pv = R_.student_preview(sid, day)
        st.caption(f"Clean {s['name']}'s day ({day}): " + ", ".join(f"{k} {v}" for k, v in pv.items() if v) if any(pv.values()) else "Nothing recorded for this day.")
        if st.checkbox("I understand this removes this student's test data for the day", key="p360_rs_ok") and \
                st.button("Reset this student's day", type="primary", key="p360_rs"):
            R_.reset_student(sid, day, st.session_state.user["username"])
            st.success("Done — bus lines back to Expected; scans, marks, requests, alerts and messages removed.")
            st.rerun()
