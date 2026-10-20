"""DPS 360 campus layer: gates and card scans, visitors, staff, class attendance, early pickup, campus alerts,
vehicle checklists, security patrol rounds and the per-trip bus assignment board.

Everything a person does on campus ends up in one stream (view campus_moves): bus hand-overs at the bus bay,
gate card scans of private-transport students, staff and visitors, and early-pickup exits. The 360 dashboard,
the Security app, the Teacher app and the parent's "campus in / out" times all read that stream."""
import base64
import hashlib
import json
import random
from datetime import datetime, timedelta

import db
from db import q, q1, x, ts, TRIPS

SCHEMA = """
CREATE TABLE IF NOT EXISTS classes (id INTEGER PRIMARY KEY, name TEXT, section TEXT, shift INTEGER, teacher_id INTEGER, room TEXT,
  UNIQUE(name, section));
CREATE TABLE IF NOT EXISTS gates (id INTEGER PRIMARY KEY, name TEXT, kind TEXT, lat REAL, lon REAL);
CREATE TABLE IF NOT EXISTS gate_scans (id INTEGER PRIMARY KEY, ts TEXT, date TEXT, ptype TEXT, pid INTEGER, dir TEXT, gate_id INTEGER,
  method TEXT, operator TEXT, result TEXT, reason TEXT, ref TEXT, code TEXT);
CREATE TABLE IF NOT EXISTS visitors (id INTEGER PRIMARY KEY, date TEXT, pass_no TEXT, name TEXT, phone TEXT, id_no TEXT, company TEXT,
  purpose TEXT, host TEXT, vehicle TEXT, in_ts TEXT, out_ts TEXT, in_gate INTEGER, out_gate INTEGER, status TEXT, registered_by TEXT,
  expected_min INTEGER DEFAULT 60, pre_registered INTEGER DEFAULT 0, gender TEXT);
CREATE TABLE IF NOT EXISTS attendance (id INTEGER PRIMARY KEY, date TEXT, class_id INTEGER, student_id INTEGER, status TEXT, source TEXT,
  marked_by TEXT, ts TEXT, note TEXT, UNIQUE(date, student_id));
CREATE TABLE IF NOT EXISTS att_submissions (id INTEGER PRIMARY KEY, date TEXT, class_id INTEGER, teacher TEXT, ts TEXT, present INTEGER,
  absent INTEGER, late INTEGER, UNIQUE(date, class_id));
CREATE TABLE IF NOT EXISTS campus_alerts (id INTEGER PRIMARY KEY, ts TEXT, date TEXT, kind TEXT, severity TEXT, student_id INTEGER,
  ptype TEXT, pid INTEGER, details TEXT, status TEXT DEFAULT 'OPEN', audience TEXT, ack_by TEXT, ack_ts TEXT, resolved_by TEXT,
  resolved_ts TEXT, resolution TEXT);
CREATE TABLE IF NOT EXISTS early_pickups (id INTEGER PRIMARY KEY, student_id INTEGER, date TEXT, pickup_time TEXT, reason TEXT,
  collector TEXT, relation TEXT, collector_phone TEXT, collector_id TEXT, requested_by TEXT, requested_ts TEXT, status TEXT,
  decided_by TEXT, decided_ts TEXT, pass_code TEXT, out_ts TEXT, out_gate INTEGER, trip_no INTEGER, note TEXT);
CREATE TABLE IF NOT EXISTS vehicle_checks (id INTEGER PRIMARY KEY, date TEXT, bus_id INTEGER, trip_no INTEGER, kind TEXT, by_user TEXT,
  by_name TEXT, ts TEXT, answers TEXT, defects TEXT, critical INTEGER DEFAULT 0, status TEXT, odometer INTEGER, fuel TEXT,
  cleared_by TEXT, cleared_ts TEXT, cleared_note TEXT);
CREATE TABLE IF NOT EXISTS patrol_rounds (id INTEGER PRIMARY KEY, date TEXT, plan TEXT, round_no INTEGER, checkpoint TEXT, guard TEXT,
  planned_ts TEXT, actual_ts TEXT, status TEXT, note TEXT, source TEXT, site TEXT);
CREATE TABLE IF NOT EXISTS sim_events (id INTEGER PRIMARY KEY, date TEXT, ts TEXT, kind TEXT, data TEXT, done INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_gs_date ON gate_scans(date, ptype, pid);
CREATE INDEX IF NOT EXISTS ix_gs_pid ON gate_scans(ptype, pid);
CREATE INDEX IF NOT EXISTS ix_att ON attendance(date, class_id);
CREATE INDEX IF NOT EXISTS ix_alert ON campus_alerts(date, status);
CREATE INDEX IF NOT EXISTS ix_vis ON visitors(date, status);
CREATE INDEX IF NOT EXISTS ix_ep ON early_pickups(date, status);
CREATE INDEX IF NOT EXISTS ix_vc ON vehicle_checks(date, bus_id);
CREATE INDEX IF NOT EXISTS ix_pr ON patrol_rounds(date);
CREATE INDEX IF NOT EXISTS ix_sim ON sim_events(done, ts);
CREATE INDEX IF NOT EXISTS ix_stu_class ON students(class_name, section);
CREATE VIEW IF NOT EXISTS campus_moves AS
  SELECT 'STUDENT' AS ptype, m.student_id AS pid, r.date AS date, m.exit_ts AS ts, 'IN' AS dir, 'Bus bay' AS gate, 'BUS' AS method,
         b.bus_no AS ref
    FROM manifest m JOIN trip_runs r ON r.id=m.run_id JOIN buses b ON b.id=r.bus_id
   WHERE m.handover IN ('SCHOOL_RECEIPT','RETURNED_TO_SCHOOL') AND m.exit_ts IS NOT NULL
  UNION ALL
  SELECT 'STUDENT', m.student_id, r.date, m.entry_ts, 'OUT', 'Bus bay', 'BUS', b.bus_no
    FROM manifest m JOIN trip_runs r ON r.id=m.run_id JOIN buses b ON b.id=r.bus_id
   WHERE m.purpose='DROPOFF' AND m.entry_ts IS NOT NULL
  UNION ALL
  SELECT gs.ptype, gs.pid, gs.date, gs.ts, gs.dir, g.name, gs.method, gs.ref
    FROM gate_scans gs LEFT JOIN gates g ON g.id=gs.gate_id WHERE gs.result='OK';
"""

GATES = [("Gate 1 · Main gate", "STUDENT"), ("Gate 2 · KG gate", "STUDENT"), ("Gate 3 · Bus bay", "BUS"),
         ("Gate 4 · Staff gate", "STAFF"), ("Gate 5 · Visitor reception", "VISITOR"), ("Gate 6 · Service gate", "SERVICE")]
BUS_BAY = 3

# school day per shift: (gates open, class start / late after, dismissal from, dismissal to)
SHIFT_TIMES = {1: ("06:15", "07:30", "13:45", "14:30"), 2: ("11:45", "12:45", "18:20", "18:50")}
KG_DISMISS = ("11:10", "11:40")
STAYBACK_DISMISS = ("16:25", "16:50")

PRE_CHECKS = [   # (section, item, critical) — from the DPS driver checklist; a critical "defect" blocks the trip start
    ("Documents", "Vehicle registration (Istimara) and insurance valid", True),
    ("Documents", "Driver licence and school transport permit carried", True),
    ("Under the bonnet", "Engine oil level", True),
    ("Under the bonnet", "Coolant / radiator water level", True),
    ("Under the bonnet", "Brake fluid level", True),
    ("Outside", "Tyres: pressure, tread, no cuts (incl. spare)", True),
    ("Outside", "Lights, indicators, hazard and STOP-arm sign", True),
    ("Outside", "Mirrors clean and adjusted", False),
    ("Outside", "No fluid leaks under the bus", True),
    ("Driver's seat", "Brakes and parking brake work", True),
    ("Driver's seat", "Horn, wipers and washer", False),
    ("Driver's seat", "Dashboard: no warning lights", True),
    ("Driver's seat", "Air-conditioning cooling", False),
    ("Driver's seat", "Fuel enough for the trip", False),
    ("Safety", "Emergency exits and door open freely", True),
    ("Safety", "Fire extinguisher charged and in date", True),
    ("Safety", "First-aid kit complete", False),
    ("Safety", "Seat belts working on every seat", True),
    ("Safety", "Tracker / CCTV / tablet powered", False),
    ("Inside", "Bus clean, floor and seats", False),
]
POST_CHECKS = [
    ("Child check", "Walked to the back: no child left on any seat", True),
    ("Child check", "Under-seat and rear-bench check done", True),
    ("Cabin", "Bus clean, no litter or lost items (logged if found)", False),
    ("Cabin", "Windows closed, AC off", False),
    ("Vehicle", "New damage or warning lights reported", False),
    ("Vehicle", "Fuel level noted", False),
    ("Vehicle", "Parked in the bay, doors locked", False),
    ("Hand-over", "Tablet and keys returned", False),
]

ALERT_TEXT = {
    "ABSENT_ON_CAMPUS": "Marked absent in class but on campus",
    "ABSENT_NOT_ON_CAMPUS": "Absent, not on campus — parents notified",
    "UNAUTHORIZED_EXIT": "Tried to leave without a pass",
    "EARLY_PICKUP": "Early pickup",
    "VISITOR_OVERSTAY": "Visitor overstay",
    "CARD_REJECTED": "Card rejected at gate",
    "UNACCOUNTED": "Not accounted for",
    "VEHICLE_DEFECT": "Vehicle defect",
    "PATROL_MISSED": "Patrol checkpoint missed",
    "PRESENT_NO_SCAN": "Present in class, no entry scan",
}


def ensure_schema(con=None):
    with db.tx(con) as c:
        c.executescript(SCHEMA)
        if not c.execute("SELECT COUNT(*) FROM gates").fetchone()[0]:
            lat, lon = db.SCHOOL_LAT, db.SCHOOL_LON
            for i, (n, k) in enumerate(GATES):
                c.execute("INSERT INTO gates(id, name, kind, lat, lon) VALUES (?,?,?,?,?)", (i + 1, n, k, lat + .0006 * (i % 3), lon + .0007 * (i // 3)))


def gates(con=None):
    return q("SELECT * FROM gates ORDER BY id", (), con)


def _hm(s):
    return s[11:16] if s and len(s) >= 16 else (s or "")


def at(date, hm):
    return f"{date} {hm}:00"


# ------------------------------------------------------------------ photos (generated avatars for the demo)
SKIN = ["#F1C27D", "#E0AC69", "#C68642", "#8D5524", "#FFDBAC", "#D9A066", "#B47A4B"]
HAIR = ["#1B1B1B", "#2C1B10", "#3B2416", "#4A3121", "#151515", "#5A3A22"]
UNIFORM = {"STUDENT": "#3D4FD6", "STAFF": "#1B2559", "VISITOR": "#7A869A"}


def avatar_svg(seed, gender="M", kind="STUDENT", size=120):
    """A friendly generated portrait (stand-in for the school's ID photo) — the same person always gets the same face."""
    h = int(hashlib.md5(f"{kind}{seed}".encode()).hexdigest(), 16)
    skin = SKIN[h % len(SKIN)]
    hair = HAIR[(h >> 4) % len(HAIR)]
    bg = ["#E9ECFC", "#E3F4EA", "#FDEBD8", "#F3E8FD", "#E2F3F8", "#FDF6D8"][(h >> 8) % 6]
    shirt = UNIFORM.get(kind, "#3D4FD6")
    eye_y = 50 + (h >> 12) % 3
    glasses = (h >> 16) % 9 == 0
    smile = (h >> 20) % 3
    hijab = gender == "F" and (h >> 24) % 4 == 0
    beard = gender == "M" and kind != "STUDENT" and (h >> 28) % 3 == 0
    parts = [f"<rect width='100' height='100' rx='14' fill='{bg}'/>",
             f"<path d='M14 100c2-20 16-30 36-30s34 10 36 30Z' fill='{shirt}'/>",
             "<path d='M42 70l8 10 8-10' fill='#fff'/>" if kind == "STUDENT" else "",
             f"<rect x='44' y='60' width='12' height='12' rx='4' fill='{skin}'/>"]
    if hijab:
        parts.append(f"<path d='M24 58c0-22 11-36 26-36s26 14 26 36c0 14-6 22-12 26H36c-6-4-12-12-12-26Z' fill='{['#1B2559','#6B2E5C','#2E5E4E','#3A3A3A'][(h >> 5) % 4]}'/>")
    elif gender == "F":
        parts.append(f"<path d='M26 56c0-20 10-34 24-34s24 14 24 34v20c-4 4-10 4-12 0V52H38v24c-2 4-8 4-12 0Z' fill='{hair}'/>")
    parts.append(f"<ellipse cx='50' cy='50' rx='17' ry='20' fill='{skin}'/>")
    if not hijab:
        if gender == "F":
            parts.append(f"<path d='M33 46c2-12 9-18 17-18s15 6 17 18c-6-6-12-9-17-9s-11 3-17 9Z' fill='{hair}'/>")
        else:
            style = (h >> 6) % 3
            parts.append(f"<path d='M32 48c0-14 8-21 18-21s18 7 18 21c-2-5-6-9-9-10-5 3-14 4-27 10Z' fill='{hair}'/>" if style == 0 else
                         f"<path d='M32 47c1-13 8-20 18-20s17 7 18 20c-3-7-9-10-18-10s-15 3-18 10Z' fill='{hair}'/>" if style == 1 else
                         f"<path d='M31 50c-1-15 7-24 19-24s20 9 19 24c-2-8-5-11-8-12-6 2-16 2-24 0-3 1-5 5-6 12Z' fill='{hair}'/>")
    parts.append(f"<circle cx='43' cy='{eye_y}' r='2.2' fill='#1A1A1A'/><circle cx='57' cy='{eye_y}' r='2.2' fill='#1A1A1A'/>")
    if glasses:
        parts.append(f"<g fill='none' stroke='#333' stroke-width='1.4'><circle cx='43' cy='{eye_y}' r='5'/><circle cx='57' cy='{eye_y}' r='5'/>"
                     f"<path d='M48 {eye_y}h4'/></g>")
    if beard:
        parts.append(f"<path d='M34 54c2 12 8 16 16 16s14-4 16-16c-4 6-9 7-16 7s-12-1-16-7Z' fill='{hair}' opacity='.85'/>")
    parts.append(["<path d='M44 60q6 5 12 0' stroke='#7A2E2E' stroke-width='2' fill='none' stroke-linecap='round'/>",
                  "<path d='M45 61q5 3 10 0' stroke='#7A2E2E' stroke-width='2' fill='none' stroke-linecap='round'/>",
                  "<path d='M44 59q6 7 12 0Z' fill='#7A2E2E'/>"][smile])
    svg = f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100' width='{size}' height='{size}'>{''.join(parts)}</svg>"
    return svg


def avatar_img(seed, gender="M", kind="STUDENT", size=120, style=""):
    b64 = base64.b64encode(avatar_svg(seed, gender, kind, size).encode()).decode()
    return f"<img src='data:image/svg+xml;base64,{b64}' width='{size}' height='{size}' style='border-radius:14px;{style}'/>"


# ------------------------------------------------------------------ who is where
def student_group_times(batch, grp):
    """(arrive by, dismissal window) for a student group."""
    s = SHIFT_TIMES[batch]
    if grp == "KG":
        return s[1], KG_DISMISS
    if grp == "StayBack":
        return s[1], STAYBACK_DISMISS
    return s[1], (s[2], s[3])


def drop_trip(batch, grp):
    for no, t in TRIPS.items():
        for b, g, p in t["movements"]:
            if b == batch and g == grp and p == "DROPOFF":
                return no
    return None


def pickup_trip(batch, grp):
    for no, t in TRIPS.items():
        for b, g, p in t["movements"]:
            if b == batch and g == grp and p == "PICKUP":
                return no
    return None


def last_moves(date, ptype="STUDENT", con=None):
    """pid -> last campus move today (dir, gate, method, ts, ref)."""
    rows = q("""SELECT pid, dir, gate, method, ref, MAX(ts) AS ts FROM campus_moves WHERE ptype=? AND date=? GROUP BY pid""",
             (ptype, date), con)
    return {r["pid"]: r for r in rows}


def first_in(date, ptype="STUDENT", con=None):
    return {r["pid"]: r for r in q("""SELECT pid, gate, method, ref, MIN(ts) AS ts FROM campus_moves WHERE ptype=? AND date=? AND dir='IN'
                                      GROUP BY pid""", (ptype, date), con)}


def onboard_now(date, con=None):
    """student_id -> (bus_no, purpose, trip_no) for children on a bus right now."""
    return {r["student_id"]: r for r in q("""SELECT m.student_id, b.bus_no, m.purpose, r.trip_no FROM manifest m JOIN trip_runs r ON r.id=m.run_id
                                              JOIN buses b ON b.id=r.bus_id WHERE r.date=? AND m.status IN ('ONBOARD','TRANSFER_PENDING')
                                              AND r.status IN ('IN_PROGRESS','PENDING_SYNC')""", (date,), con)}


def day_absent(date, con=None):
    """Students declared absent for the whole day (parent / office) — student_id -> reason."""
    return {r["student_id"]: r["reason"] for r in q("""SELECT student_id, reason FROM absences WHERE date=? AND status='APPROVED' AND trip_no IS NULL""",
                                                   (date,), con)}


def presence(date=None, now=None, con=None):
    """Every active student's state right now: ON_CAMPUS, ON_BUS, LEFT (went home / collected), ABSENT, NOT_ARRIVED, UNACCOUNTED."""
    date = date or db.today()
    now = now or db.now()
    nowhm = now.strftime("%H:%M") if date == db.today() else "23:59"
    studs = q("SELECT id, batch, grp, transport FROM students WHERE active=1", (), con)
    # test mode (today started from zero): only children whose pickup bus has finished its trip can be 'unaccounted'
    test_mode = db.setting("sim_off_day", None, con) == date
    closed_pick = set()
    if test_mode:
        closed_pick = {r["student_id"] for r in q("""SELECT m.student_id FROM manifest m JOIN trip_runs r ON r.id=m.run_id
                                                     WHERE r.date=? AND r.status='CLOSED' AND m.purpose='PICKUP'""", (date,), con)}
    moves, bus, absent = last_moves(date, con=con), onboard_now(date, con), day_absent(date, con)
    att = {r["student_id"]: r["status"] for r in q("SELECT student_id, status FROM attendance WHERE date=?", (date,), con)}
    att_abs = {k for k, v in att.items() if v == "ABSENT"}
    out = {}
    for s in studs:
        sid = s["id"]
        m = moves.get(sid)
        if sid in bus:
            st = "ON_BUS"
        elif m and m["dir"] == "IN":
            st = "ON_CAMPUS"
        elif m and m["dir"] == "OUT":
            st = "LEFT"
        elif sid in absent or sid in att_abs:
            st = "ABSENT"
        else:
            arrive_by, (d0, d1) = student_group_times(s["batch"], s["grp"])
            late_cut = (datetime.strptime(arrive_by, "%H:%M") + timedelta(minutes=20)).strftime("%H:%M")
            if att.get(sid) in ("PRESENT", "LATE"):          # in class but no card scan (forgot the card)
                st = "ON_CAMPUS" if nowhm < d1 else "LEFT"
            else:
                st = "UNACCOUNTED" if late_cut <= nowhm < d0 else "NOT_ARRIVED" if nowhm < late_cut else "NOT_SEEN"
                if test_mode and sid not in closed_pick:
                    st = "NOT_ARRIVED"
        out[sid] = st
    return out


def student_card(student_id, con=None):
    return q1("""SELECT s.*, b.bus_no, c.id AS class_id, c.teacher_id, st.name AS teacher, bd.code AS card
                 FROM students s LEFT JOIN buses b ON b.id=s.bus_id LEFT JOIN classes c ON c.name=s.class_name AND c.section=s.section
                 LEFT JOIN staff st ON st.id=c.teacher_id LEFT JOIN badges bd ON bd.student_id=s.id AND bd.status='ACTIVE'
                 WHERE s.id=?""", (student_id,), con)


def timeline(student_id, date, con=None):
    """One student's day, in order: bus scans, gate scans, class attendance, early pickup, alerts and parent messages."""
    ev = []
    for r in q("""SELECT e.captured_ts AS ts, e.kind, e.result, e.reason, e.stop_id, b.bus_no, r.trip_no, m.purpose, m.handover, m.recipient_name,
                         m.received_by
                  FROM events e JOIN trip_runs r ON r.id=e.run_id JOIN buses b ON b.id=r.bus_id LEFT JOIN manifest m ON m.id=e.manifest_id
                  WHERE e.student_id=? AND r.date=? ORDER BY e.captured_ts""", (student_id, date), con):
        stop = db.stop(r["stop_id"], con) if r["stop_id"] is not None else None
        where = stop["name"] if stop else ""
        if r["result"] != "ACCEPTED":
            txt, icon = f"Scan rejected on {r['bus_no']}: {r['reason']}", "⛔"
        elif r["kind"] == "CHECK_IN":
            txt, icon = f"Boarded {r['bus_no']} (Trip {r['trip_no']}) at {where}", "🚌"
        elif r["kind"] == "NO_SHOW":
            txt, icon = f"Not at the stop for {r['bus_no']} (Trip {r['trip_no']})", "⚠️"
        elif r["stop_id"] == db.SCHOOL_STOP:
            txt, icon = f"Got off {r['bus_no']} at school — received by {r['received_by'] or 'school staff'}", "🏫"
        else:
            txt, icon = f"Got off {r['bus_no']} at {where} — handed to {r['recipient_name'] or 'guardian'}", "🏠"
        ev.append({"ts": r["ts"], "icon": icon, "what": txt, "src": "Bus"})
    for r in q("""SELECT gs.*, g.name AS gate FROM gate_scans gs LEFT JOIN gates g ON g.id=gs.gate_id
                  WHERE gs.ptype='STUDENT' AND gs.pid=? AND gs.date=? ORDER BY gs.ts""", (student_id, date), con):
        if r["result"] != "OK":
            ev.append({"ts": r["ts"], "icon": "⛔", "what": f"Stopped at {r['gate']}: {r['reason']}", "src": "Security"})
        else:
            ev.append({"ts": r["ts"], "icon": "🟢" if r["dir"] == "IN" else "🔴",
                       "what": f"{'Entered' if r['dir'] == 'IN' else 'Left'} campus — {r['gate']}"
                               + (f" ({r['ref']})" if r["ref"] else "") + (" · early pickup" if r["method"] == "EARLY_PICKUP" else ""),
                       "src": "Security"})
    a = q1("SELECT * FROM attendance WHERE student_id=? AND date=?", (student_id, date), con)
    if a:
        ev.append({"ts": a["ts"], "icon": {"PRESENT": "✅", "LATE": "🕘", "ABSENT": "❌", "EXCUSED": "📝"}.get(a["status"], "•"),
                   "what": f"Class attendance: {a['status'].title()} — marked by {a['marked_by']}" + (f" ({a['note']})" if a["note"] else ""),
                   "src": "Teacher"})
    for r in q("SELECT * FROM absences WHERE student_id=? AND date=?", (student_id, date), con):
        ev.append({"ts": r["created_at"], "icon": "📝", "what": f"Absence declared ({'whole day' if r['trip_no'] is None else 'Trip ' + str(r['trip_no'])})"
                   f": {r['reason']} — by {r['declared_by']}", "src": "Parent" if r["source"] == "GUARDIAN" else "Office"})
    for r in q("SELECT * FROM early_pickups WHERE student_id=? AND date=?", (student_id, date), con):
        ev.append({"ts": r["requested_ts"], "icon": "🕐", "what": f"Early pickup requested for {r['pickup_time']} — {r['collector']} ({r['relation']}): {r['reason']}",
                   "src": "Parent"})
        if r["decided_ts"]:
            ev.append({"ts": r["decided_ts"], "icon": "✔️" if r["status"] != "REJECTED" else "✖️",
                       "what": f"Early pickup {'approved' if r['status'] != 'REJECTED' else 'rejected'} by {r['decided_by']}"
                               + (f" · gate pass {r['pass_code']}" if r["pass_code"] else ""), "src": "School"})
    for r in q("SELECT * FROM campus_alerts WHERE student_id=? AND date=?", (student_id, date), con):
        info = r["kind"] == "EARLY_PICKUP"
        ev.append({"ts": r["ts"], "icon": "ℹ️" if info else "🚨", "what": (f"Class teacher informed: {r['details']}" if info else
                   f"Alert: {r['details']} ({r['status'].lower()})"), "src": "Teacher" if info else "Alert"})
    ev.sort(key=lambda e: e["ts"] or "")
    return ev


# ------------------------------------------------------------------ alerts
def raise_alert(kind, severity, details, date=None, student_id=None, ptype="STUDENT", pid=None, audience="security", when=None, con=None):
    with db.tx(con) as c:
        if student_id and q1("SELECT id FROM campus_alerts WHERE kind=? AND student_id=? AND date=? AND status<>'RESOLVED'",
                             (kind, student_id, date or db.today()), c):
            return None
        return c.execute("""INSERT INTO campus_alerts(ts, date, kind, severity, student_id, ptype, pid, details, status, audience)
                            VALUES (?,?,?,?,?,?,?,?,?,?)""",
                         (ts(when), date or db.today(), kind, severity, student_id, ptype, pid if pid is not None else student_id, details, "OPEN",
                          audience)).lastrowid


def alerts(date=None, status=None, audience=None, con=None):
    sql = """SELECT a.*, s.name AS student, s.code, s.class_name || '-' || s.section AS class, s.gender
             FROM campus_alerts a LEFT JOIN students s ON s.id=a.student_id WHERE a.date=?"""
    p = [date or db.today()]
    if status == "OPEN":
        sql += " AND a.status<>'RESOLVED'"
    if audience:
        sql += " AND a.audience=?"; p.append(audience)
    sql += " ORDER BY a.status='RESOLVED', CASE a.severity WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3 END, a.id DESC"
    return q(sql, p, con)


def ack_alert(alert_id, user):
    x("UPDATE campus_alerts SET status='ACKNOWLEDGED', ack_by=?, ack_ts=? WHERE id=? AND status='OPEN'", (user, ts(), alert_id))


def resolve_alert(alert_id, user, resolution):
    x("UPDATE campus_alerts SET status='RESOLVED', resolved_by=?, resolved_ts=?, resolution=? WHERE id=?", (user, ts(), resolution, alert_id))
    db.audit(user, "", "CAMPUS_ALERT_RESOLVED", "campus_alert", alert_id, resolution)


# ------------------------------------------------------------------ card lookup + gate scans
def lookup(code, date=None, con=None):
    """What a scanned card / pass number belongs to."""
    code = (code or "").strip().upper()
    if not code:
        return None
    date = date or db.today()
    b = q1("SELECT * FROM badges WHERE UPPER(code)=?", (code,), con)
    if b:
        s = student_card(b["student_id"], con)
        return {"type": "STUDENT", "id": s["id"], "name": s["name"], "row": s, "card_status": b["status"], "code": code,
                "reason": b["reason"]}
    s = q1("SELECT id FROM students WHERE UPPER(code)=?", (code,), con)
    if s:
        r = student_card(s["id"], con)
        return {"type": "STUDENT", "id": r["id"], "name": r["name"], "row": r, "card_status": "ACTIVE", "code": code, "by_id": True}
    st_ = q1("SELECT * FROM staff WHERE UPPER(card_no)=?", (code,), con)
    if st_:
        return {"type": "STAFF", "id": st_["id"], "name": st_["name"], "row": st_, "card_status": "ACTIVE", "code": code}
    v = q1("SELECT * FROM visitors WHERE UPPER(pass_no)=? AND date=?", (code, date), con)
    if v:
        return {"type": "VISITOR", "id": v["id"], "name": v["name"], "row": v, "card_status": "ACTIVE" if v["status"] != "LEFT" else "USED",
                "code": code}
    ep = q1("SELECT * FROM early_pickups WHERE UPPER(pass_code)=? AND date=?", (code, date), con)
    if ep:
        r = student_card(ep["student_id"], con)
        return {"type": "EARLY_PASS", "id": r["id"], "name": r["name"], "row": r, "pass": ep, "card_status": "ACTIVE", "code": code}
    return None


def _person_state(ptype, pid, date, con=None):
    r = q1("SELECT dir, ts, gate, method FROM campus_moves WHERE ptype=? AND pid=? AND date=? ORDER BY ts DESC LIMIT 1", (ptype, pid, date), con)
    return r


def gate_scan(code, gate_id, operator, direction=None, method="CARD", when=None, con=None, notify_parent=True):
    """Security app: one card / pass scan at a gate. Returns a result for the big screen (ok/warn/error, message, person, direction)."""
    when = when or db.now()
    date = when.strftime("%Y-%m-%d")
    nowhm = when.strftime("%H:%M")
    with db.tx(con) as c:
        hit = lookup(code, date, c)

        def log(ptype, pid, dir_, result, reason="", ref=None, meth=None):
            c.execute("""INSERT INTO gate_scans(ts, date, ptype, pid, dir, gate_id, method, operator, result, reason, ref, code)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (ts(when), date, ptype, pid, dir_, gate_id, meth or method, operator, result, reason, ref, (code or "").strip().upper()))

        if not hit:
            log("UNKNOWN", None, direction or "IN", "REJECTED", "Unknown card")
            return {"ok": False, "level": "error", "message": f"Unknown card {code} — not a DPS student, staff or visitor pass."}
        if hit["card_status"] == "REVOKED":
            log(hit["type"], hit["id"], direction or "IN", "REJECTED", "Revoked card")
            raise_alert("CARD_REJECTED", "MEDIUM", f"Revoked card {code} ({hit['name']}) used at gate", date, hit["id"], when=when, con=c)
            return {"ok": False, "level": "error", "message": f"Card {code} was revoked ({hit.get('reason') or 'replaced'}). Ask for the new card.",
                    "person": hit}
        ptype = "STUDENT" if hit["type"] in ("STUDENT", "EARLY_PASS") else hit["type"]
        last = _person_state(ptype, hit["id"], date, c)
        dir_ = direction or ("OUT" if last and last["dir"] == "IN" else "IN")
        res = {"ok": True, "level": "ok", "person": hit, "dir": dir_}

        if hit["type"] == "VISITOR":
            v = hit["row"]
            if dir_ == "OUT" or v["status"] == "ON_CAMPUS":
                if v["status"] == "LEFT":
                    log("VISITOR", v["id"], "OUT", "REJECTED", "Pass already used")
                    return {"ok": False, "level": "error", "message": f"Visitor pass {v['pass_no']} already checked out at {_hm(v['out_ts'])}.", "person": hit}
                c.execute("UPDATE visitors SET status='LEFT', out_ts=?, out_gate=? WHERE id=?", (ts(when), gate_id, v["id"]))
                c.execute("UPDATE campus_alerts SET status='RESOLVED', resolved_by=?, resolved_ts=?, resolution='Visitor checked out' "
                          "WHERE kind='VISITOR_OVERSTAY' AND ptype='VISITOR' AND pid=? AND status<>'RESOLVED'", (operator, ts(when), v["id"]))
                log("VISITOR", v["id"], "OUT", "OK", ref=v["pass_no"])
                mins = int((when - datetime.strptime(v["in_ts"], "%Y-%m-%d %H:%M:%S")).total_seconds() // 60) if v["in_ts"] else 0
                res.update(dir="OUT", message=f"VISITOR OUT: {v['name']} — on campus {mins} min (host {v['host']}).")
                return res
            c.execute("UPDATE visitors SET status='ON_CAMPUS', in_ts=?, in_gate=? WHERE id=?", (ts(when), gate_id, v["id"]))
            log("VISITOR", v["id"], "IN", "OK", ref=v["pass_no"])
            res.update(dir="IN", message=f"VISITOR IN: {v['name']} → {v['host']} ({v['purpose']}).")
            return res

        if hit["type"] == "STAFF":
            log("STAFF", hit["id"], dir_, "OK", ref=hit["row"]["dept"])
            res["message"] = f"STAFF {dir_}: {hit['name']} · {hit['row']['dept'] or hit['row']['role'].title()}"
            return res

        # students (own card or early-pickup pass)
        s = hit["row"]
        arrive_by, (d0, d1) = student_group_times(s["batch"], s["grp"])
        ep = hit.get("pass") or q1("SELECT * FROM early_pickups WHERE student_id=? AND date=? AND status='APPROVED'", (s["id"], date), c)
        if dir_ == "IN":
            if last and last["dir"] == "IN":
                log("STUDENT", s["id"], "IN", "OK", "Already on campus — repeat scan", ref=None)
                res.update(level="warn", message=f"{s['name']} is already on campus (since {_hm(last['ts'])}, {last['gate']}).")
                return res
            late = nowhm > arrive_by and nowhm < d0
            note = "Late arrival" if late else ""
            if s["transport"] == "BUS":
                note = (note + " · " if note else "") + "Bus student came privately today"
            log("STUDENT", s["id"], "IN", "OK", note, ref="Late" if late else None)
            res.update(level="warn" if late else "ok",
                       message=f"IN: {s['name']} · {s['class_name']}-{s['section']}" + (f" — LATE (after {arrive_by})" if late else "")
                       + (" · bus student, came privately" if s["transport"] == "BUS" else ""))
            return res
        # OUT
        if ep and ep["status"] == "APPROVED":
            c.execute("UPDATE early_pickups SET status='COMPLETED', out_ts=?, out_gate=? WHERE id=?", (ts(when), gate_id, ep["id"]))
            log("STUDENT", s["id"], "OUT", "OK", f"Early pickup by {ep['collector']}", ref=ep["pass_code"], meth="EARLY_PICKUP")
            if notify_parent:
                _notify(c, s["id"], "EARLY_PICKUP", f"{s['name']} left school at {nowhm} with {ep['collector']} ({ep['relation']}) — early pickup pass "
                        f"{ep['pass_code']}.", when)
            res.update(message=f"EARLY PICKUP: release {s['name']} to {ep['collector']} ({ep['relation']}, ID {ep['collector_id'] or '—'}). "
                               "Check the collector's ID against the pass.", early=ep)
            return res
        if d0 > nowhm >= arrive_by:           # during school hours: not allowed without a pass
            log("STUDENT", s["id"], "OUT", "REJECTED", "No early-pickup pass during school hours")
            raise_alert("UNAUTHORIZED_EXIT", "HIGH", f"{s['name']} ({s['class_name']}-{s['section']}) tried to leave at {nowhm} without a pass — "
                        "held at the gate.", date, s["id"], when=when, con=c)
            return {"ok": False, "level": "error", "person": hit, "dir": "OUT",
                    "message": f"STOP — {s['name']} has no early-pickup pass. School hours until {d0}. Keep the student at the gate and call the office."}
        if s["transport"] == "BUS" and nowhm < d1:
            log("STUDENT", s["id"], "OUT", "OK", "Bus student leaving privately", ref=None)
            res.update(level="warn", message=f"OUT: {s['name']} — normally goes home by bus; confirm who is collecting.")
            return res
        log("STUDENT", s["id"], "OUT", "OK")
        res["message"] = f"OUT: {s['name']} · {s['class_name']}-{s['section']}"
        return res


def _notify(c, student_id, kind, message, when=None):
    for g in q("SELECT g.id FROM guardians g JOIN student_guardians sg ON sg.guardian_id=g.id WHERE sg.student_id=?", (student_id,), c):
        c.execute("""INSERT INTO notifications(ts, guardian_id, student_id, run_id, channel, kind, message, status, attempts, sent_ts)
                     VALUES (?,?,?,?,?,?,?,?,?,?)""", (ts(when), g["id"], student_id, None, "IN_APP", kind, message, "SENT", 1, ts(when)))


def gate_log(date, gate_id=None, limit=200, con=None):
    sql = """SELECT gs.*, g.name AS gate,
                    CASE gs.ptype WHEN 'STUDENT' THEN (SELECT name FROM students WHERE id=gs.pid)
                                  WHEN 'STAFF' THEN (SELECT name FROM staff WHERE id=gs.pid)
                                  WHEN 'VISITOR' THEN (SELECT name FROM visitors WHERE id=gs.pid) END AS person
             FROM gate_scans gs LEFT JOIN gates g ON g.id=gs.gate_id WHERE gs.date=?"""
    p = [date]
    if gate_id:
        sql += " AND gs.gate_id=?"; p.append(gate_id)
    return q(sql + " ORDER BY gs.ts DESC, gs.id DESC LIMIT ?", p + [limit], con)


# ------------------------------------------------------------------ visitors
def next_pass(date, con=None):
    n = q1("SELECT COUNT(*) n FROM visitors WHERE date=?", (date,), con)["n"] + 1
    return f"V{date[2:4]}{date[5:7]}{date[8:10]}-{n:03d}"


def register_visitor(name, phone, id_no, company, purpose, host, vehicle, gate_id, user, expected_min=60, check_in=True, when=None,
                     gender="M", con=None):
    when = when or db.now()
    date = when.strftime("%Y-%m-%d")
    with db.tx(con) as c:
        pno = next_pass(date, c)
        vid = c.execute("""INSERT INTO visitors(date, pass_no, name, phone, id_no, company, purpose, host, vehicle, in_ts, in_gate, status,
                           registered_by, expected_min, pre_registered, gender) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (date, pno, name, phone, id_no, company, purpose, host, vehicle, ts(when) if check_in else None, gate_id if check_in else None,
                         "ON_CAMPUS" if check_in else "EXPECTED", user, expected_min, 0 if check_in else 1, gender)).lastrowid
        if check_in:
            c.execute("""INSERT INTO gate_scans(ts, date, ptype, pid, dir, gate_id, method, operator, result, ref, code)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (ts(when), date, "VISITOR", vid, "IN", gate_id, "PASS", user, "OK", pno, pno))
        db.audit(user, "security", "VISITOR_IN" if check_in else "VISITOR_PREREGISTERED", "visitor", vid, f"{name} → {host}", c)
        return vid, pno


def checkout_visitor(visitor_id, gate_id, user, when=None, con=None):
    v = q1("SELECT pass_no FROM visitors WHERE id=?", (visitor_id,), con)
    return gate_scan(v["pass_no"], gate_id, user, direction="OUT", method="PASS", when=when, con=con)


def visitors(date, status=None, con=None):
    sql = "SELECT * FROM visitors WHERE date=?"
    p = [date]
    if status:
        sql += " AND status=?"; p.append(status)
    return q(sql + " ORDER BY COALESCE(in_ts, '9') DESC", p, con)


def check_overstays(when=None, con=None):
    when = when or db.now()
    date = when.strftime("%Y-%m-%d")
    n = 0
    for v in q("SELECT * FROM visitors WHERE date=? AND status='ON_CAMPUS' AND in_ts IS NOT NULL", (date,), con):
        mins = (when - datetime.strptime(v["in_ts"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
        if mins > (v["expected_min"] or 60) + 30:
            if not q1("SELECT id FROM campus_alerts WHERE kind='VISITOR_OVERSTAY' AND ptype='VISITOR' AND pid=?", (v["id"],), con):
                raise_alert("VISITOR_OVERSTAY", "MEDIUM", f"Visitor {v['name']} ({v['pass_no']}) on campus {int(mins)} min — expected "
                            f"{v['expected_min']} min, host {v['host']}", date, None, "VISITOR", v["id"], when=when, con=con)
                n += 1
    return n


# ------------------------------------------------------------------ early pickup
def request_early(student_id, date, pickup_time, reason, collector, relation, collector_phone, collector_id, by, when=None, con=None):
    with db.tx(con) as c:
        s = q1("SELECT batch, grp FROM students WHERE id=?", (student_id,), c)
        eid = c.execute("""INSERT INTO early_pickups(student_id, date, pickup_time, reason, collector, relation, collector_phone, collector_id,
                           requested_by, requested_ts, status, trip_no) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (student_id, date, pickup_time, reason, collector, relation, collector_phone, collector_id, by, ts(when), "PENDING",
                         drop_trip(s["batch"], s["grp"]))).lastrowid
        db.audit(by, "guardian", "EARLY_PICKUP_REQUESTED", "student", student_id, f"{date} {pickup_time} {collector}", c)
        return eid


def decide_early(eid, approve, user, role="manager", note="", when=None, con=None):
    """Approve → gate pass for security, child removed from the home bus trip, class teacher informed, parent notified."""
    with db.tx(con) as c:
        e = q1("SELECT * FROM early_pickups WHERE id=?", (eid,), c)
        if not e or e["status"] != "PENDING":
            return False, "Request is no longer pending."
        s = q1("SELECT * FROM students WHERE id=?", (e["student_id"],), c)
        if not approve:
            c.execute("UPDATE early_pickups SET status='REJECTED', decided_by=?, decided_ts=?, note=? WHERE id=?", (user, ts(when), note, eid))
            _notify(c, s["id"], "EARLY_PICKUP", f"Early pickup for {s['name']} on {e['date']} was not approved. {note}".strip(), when)
            return True, "Rejected; parent informed."
        code = f"EP{e['date'][5:7]}{e['date'][8:10]}{eid:04d}"
        c.execute("UPDATE early_pickups SET status='APPROVED', decided_by=?, decided_ts=?, pass_code=?, note=? WHERE id=?",
                  (user, ts(when), code, note, eid))
        removed = ""
        if s["transport"] == "BUS" and e["trip_no"]:
            import engine as E
            if not q1("SELECT id FROM absences WHERE student_id=? AND date=? AND (trip_no IS NULL OR trip_no=?) AND status='APPROVED'",
                      (s["id"], e["date"], e["trip_no"]), c):
                E.declare_absence(s["id"], e["date"], e["trip_no"], f"Early pickup {e['pickup_time']} by {e['collector']}", user, "EARLY_PICKUP", c)
            removed = f" Removed from Trip {e['trip_no']} home bus list."
        _notify(c, s["id"], "EARLY_PICKUP", f"Early pickup approved: {s['name']} can be collected at {e['pickup_time']} by {e['collector']}. "
                f"Gate pass {code} — bring the collector's QID.", when)
        raise_alert("EARLY_PICKUP", "LOW", f"{s['name']} ({s['class_name']}-{s['section']}) leaves at {e['pickup_time']} with {e['collector']} "
                    f"({e['relation']}) — pass {code}", e["date"], s["id"], audience="teacher", when=when, con=c)
        db.audit(user, role, "EARLY_PICKUP_APPROVED", "student", s["id"], f"{e['date']} {e['pickup_time']} pass {code}", c)
        return True, f"Approved — gate pass {code}.{removed} Class teacher and security informed."


def early_pickups(date, status=None, con=None):
    sql = """SELECT e.*, s.name, s.code, s.class_name || '-' || s.section AS class, s.gender, s.transport
             FROM early_pickups e JOIN students s ON s.id=e.student_id WHERE e.date=?"""
    p = [date]
    if status:
        sql += " AND e.status=?"; p.append(status)
    return q(sql + " ORDER BY e.pickup_time", p, con)


# ------------------------------------------------------------------ class attendance (Teacher app)
def teacher_class(staff_id, con=None):
    return q1("SELECT * FROM classes WHERE teacher_id=?", (staff_id,), con)


def class_roster(class_id, date, con=None):
    """Class list pre-filled from bus arrivals and gate scans: suggested PRESENT / LATE / ABSENT with the reason."""
    c_ = q1("SELECT * FROM classes WHERE id=?", (class_id,), con)
    studs = q("""SELECT s.*, bd.code AS card FROM students s LEFT JOIN badges bd ON bd.student_id=s.id AND bd.status='ACTIVE'
                 WHERE s.active=1 AND s.class_name=? AND s.section=? ORDER BY s.name""", (c_["name"], c_["section"]), con)
    ids = [s["id"] for s in studs] or [-1]
    ph = ",".join("?" * len(ids))
    fin = {r["pid"]: r for r in q(f"""SELECT pid, gate, method, ref, MIN(ts) ts FROM campus_moves WHERE ptype='STUDENT' AND date=? AND dir='IN'
                                      AND pid IN ({ph}) GROUP BY pid""", [date] + ids, con)}
    last = {r["pid"]: r for r in q(f"""SELECT pid, dir, gate, MAX(ts) ts FROM campus_moves WHERE ptype='STUDENT' AND date=? AND pid IN ({ph})
                                       GROUP BY pid""", [date] + ids, con)}
    absn = {r["student_id"]: r for r in q(f"""SELECT student_id, reason FROM absences WHERE date=? AND status='APPROVED' AND trip_no IS NULL
                                              AND student_id IN ({ph})""", [date] + ids, con)}
    onbus = {r["student_id"]: r for r in q(f"""SELECT m.student_id, b.bus_no FROM manifest m JOIN trip_runs r ON r.id=m.run_id JOIN buses b ON b.id=r.bus_id
                                               WHERE r.date=? AND m.purpose='PICKUP' AND m.status IN ('ONBOARD','TRANSFER_PENDING')
                                               AND m.student_id IN ({ph})""", [date] + ids, con)}
    marked = {r["student_id"]: r for r in q(f"SELECT * FROM attendance WHERE date=? AND student_id IN ({ph})", [date] + ids, con)}
    arrive_by = SHIFT_TIMES[c_["shift"]][1]
    out = []
    for s in studs:
        f, l_ = fin.get(s["id"]), last.get(s["id"])
        if f:
            late = _hm(f["ts"]) > arrive_by
            sug = "LATE" if late else "PRESENT"
            src = (f"Bus {f['ref']} at {_hm(f['ts'])}" if f["method"] == "BUS" else f"{f['gate']} at {_hm(f['ts'])}")
            if l_ and l_["dir"] == "OUT":
                src += f" · left {_hm(l_['ts'])}"
        elif s["id"] in onbus:
            sug, src = "PRESENT", f"On {onbus[s['id']]['bus_no']}, arriving"
        elif s["id"] in absn:
            sug, src = "ABSENT", f"Parent: {absn[s['id']]['reason']}"
        else:
            sug, src = "ABSENT", "No bus or gate scan today"
        m = marked.get(s["id"])
        out.append({"student_id": s["id"], "name": s["name"], "code": s["code"], "gender": s["gender"], "transport": s["transport"],
                    "suggested": sug, "source": src, "on_campus": bool(l_ and l_["dir"] == "IN"), "arrived": bool(f), "declared": s["id"] in absn,
                    "status": m["status"] if m else None, "marked_by": m["marked_by"] if m else None})
    return c_, out


def submit_attendance(class_id, date, marks, teacher, when=None, con=None, notify_parents=True, roster=None):
    """marks: {student_id: PRESENT|LATE|ABSENT}. Applies DPS rules: absent + not on campus → parents notified;
    absent but on campus → security alert to find the child."""
    when = when or db.now()
    with db.tx(con) as c:
        c_, roster = roster or class_roster(class_id, date, c)
        by = {r["student_id"]: r for r in roster}
        counts = {"PRESENT": 0, "ABSENT": 0, "LATE": 0}
        notified, alerted = 0, 0
        for sid, stt in marks.items():
            r = by[sid]
            note = None
            if stt == "ABSENT" and r["declared"]:
                note = "Parent declared"
            c.execute("""INSERT INTO attendance(date, class_id, student_id, status, source, marked_by, ts, note) VALUES (?,?,?,?,?,?,?,?)
                         ON CONFLICT(date, student_id) DO UPDATE SET status=excluded.status, marked_by=excluded.marked_by, ts=excluded.ts,
                         note=excluded.note""",
                      (date, class_id, sid, stt, "TEACHER", teacher, ts(when), note))
            counts[stt] = counts.get(stt, 0) + 1
            if stt == "ABSENT" and r.get("arrived", r["on_campus"]):
                lm = _person_state("STUDENT", sid, date, c)
                where = (f"arrived today ({r['source']})" if not r["on_campus"] else
                         f"is on campus (last scan {lm['gate'] if lm else '?'} at {_hm(lm['ts']) if lm else '?'})")
                if raise_alert("ABSENT_ON_CAMPUS", "HIGH" if r["on_campus"] else "MEDIUM",
                               f"{r['name']} ({c_['name']}-{c_['section']}) is marked absent by {teacher} but {where} — "
                               + ("locate the child." if r["on_campus"] else "check with the class teacher."), date, sid, when=when, con=c):
                    alerted += 1
            elif stt == "ABSENT" and not r["declared"]:
                if r["transport"] == "BUS":       # not in school → off the home bus list too (driver list updates by itself)
                    st_ = q1("SELECT batch, grp FROM students WHERE id=?", (sid,), c)
                    dt = drop_trip(st_["batch"], st_["grp"])
                    if dt and not q1("SELECT id FROM absences WHERE student_id=? AND date=? AND (trip_no IS NULL OR trip_no=?)", (sid, date, dt), c):
                        c.execute("""INSERT INTO absences(student_id, date, trip_no, reason, declared_by, source, status, created_at)
                                     VALUES (?,?,?,?,?,?,?,?)""", (sid, date, dt, "Absent in class (teacher)", teacher, "TEACHER", "APPROVED", ts(when)))
                        c.execute("""UPDATE manifest SET status='ABSENT_DECLARED' WHERE student_id=? AND status='EXPECTED'
                                     AND run_id IN (SELECT id FROM trip_runs WHERE date=? AND trip_no=?)""", (sid, date, dt))
                if notify_parents:
                    _notify(c, sid, "ABSENT_ALERT", f"{r['name']} was marked absent in class today ({c_['name']}-{c_['section']}) and has not entered "
                            f"the school campus. Please contact the school on {db.TRANSPORT_PHONE} if this is unexpected.", when)
                raise_alert("ABSENT_NOT_ON_CAMPUS", "MEDIUM", f"{r['name']} ({c_['name']}-{c_['section']}) absent, not on campus, no parent notice — "
                            "parents notified", date, sid, audience="office", when=when, con=c)
                notified += 1
            elif stt in ("PRESENT", "LATE") and not r["on_campus"] and "On " not in r["source"]:
                raise_alert("PRESENT_NO_SCAN", "LOW", f"{r['name']} marked present but has no entry scan today — card not scanned", date, sid,
                            audience="office", when=when, con=c)
        c.execute("""INSERT INTO att_submissions(date, class_id, teacher, ts, present, absent, late) VALUES (?,?,?,?,?,?,?)
                     ON CONFLICT(date, class_id) DO UPDATE SET teacher=excluded.teacher, ts=excluded.ts, present=excluded.present,
                     absent=excluded.absent, late=excluded.late""",
                  (date, class_id, teacher, ts(when), counts["PRESENT"], counts["ABSENT"], counts["LATE"]))
        db.audit(teacher, "teacher", "ATTENDANCE_SUBMITTED", "class", class_id,
                 f"{date}: {counts['PRESENT']} present, {counts['LATE']} late, {counts['ABSENT']} absent", c)
        return counts, notified, alerted


# ------------------------------------------------------------------ vehicle checklists
def save_check(bus_id, date, trip_no, kind, answers, user, name, odometer=None, fuel=None, when=None, con=None):
    """answers: {item: 'OK' | 'DEFECT' | 'N/A'} (+ '_note'). A critical defect → BLOCKED until cleared by the transport office."""
    items = PRE_CHECKS if kind == "PRE" else POST_CHECKS
    crit = {i for _, i, c in items if c}
    defects = [k for k, v in answers.items() if v == "DEFECT"]
    critical = any(d in crit for d in defects)
    status = "BLOCKED" if (critical and kind == "PRE") else "DEFECT" if defects else "PASS"
    with db.tx(con) as c:
        cid = c.execute("""INSERT INTO vehicle_checks(date, bus_id, trip_no, kind, by_user, by_name, ts, answers, defects, critical, status, odometer, fuel)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (date, bus_id, trip_no, kind, user, name, ts(when), json.dumps(answers), "; ".join(defects), 1 if critical else 0, status,
                         odometer, fuel)).lastrowid
        if defects:
            b = db.bus(bus_id, c)
            raise_alert("VEHICLE_DEFECT", "HIGH" if critical else "LOW",
                        f"{b['bus_no']} {'pre' if kind == 'PRE' else 'post'}-trip check: {'; '.join(defects)}"
                        + (" — trip start BLOCKED until cleared" if status == "BLOCKED" else ""), date, None, "BUS", bus_id,
                        audience="transport", when=when, con=c)
        db.audit(user, "crew", f"VEHICLE_CHECK_{kind}", "bus", bus_id, f"{status}: {'; '.join(defects) or 'all OK'}", c)
        return cid, status


def clear_check(check_id, user, note, con=None):
    with db.tx(con) as c:
        c.execute("UPDATE vehicle_checks SET status='CLEARED', cleared_by=?, cleared_ts=?, cleared_note=? WHERE id=?", (user, ts(), note, check_id))
        v = q1("SELECT bus_id, date FROM vehicle_checks WHERE id=?", (check_id,), c)
        c.execute("""UPDATE campus_alerts SET status='RESOLVED', resolved_by=?, resolved_ts=?, resolution=? WHERE kind='VEHICLE_DEFECT' AND ptype='BUS'
                     AND pid=? AND date=? AND status<>'RESOLVED'""", (user, ts(), note, v["bus_id"], v["date"]))
        db.audit(user, "manager", "VEHICLE_DEFECT_CLEARED", "vehicle_check", check_id, note, c)


def precheck_status(bus_id, date, con=None):
    r = q1("SELECT * FROM vehicle_checks WHERE bus_id=? AND date=? AND kind='PRE' ORDER BY id DESC LIMIT 1", (bus_id, date), con)
    if not r:
        return False, "Pre-trip vehicle check not done today — the driver completes it in the app before the first trip."
    if r["status"] == "BLOCKED":
        return False, f"Trip start blocked: critical defect on the pre-trip check ({r['defects']}). The transport office must clear it."
    return True, "Pre-trip check " + ("passed" if r["status"] == "PASS" else "done (minor defects reported)" if r["status"] == "DEFECT" else "cleared")


def checks(date, kind=None, con=None):
    sql = "SELECT v.*, b.bus_no FROM vehicle_checks v JOIN buses b ON b.id=v.bus_id WHERE v.date=?"
    p = [date]
    if kind:
        sql += " AND v.kind=?"; p.append(kind)
    return q(sql + " ORDER BY v.ts DESC", p, con)


# ------------------------------------------------------------------ patrol rounds (guard tour) — JWM patrol export or demo data
PATROL_COLUMNS = {   # accepted column names in a patrol-system export (lower-case)
    "checkpoint": ["checkpoint", "checkpoint name", "point", "patrol point", "location"],
    "guard": ["guard", "patrolman", "patrol man", "staff", "inspector", "user", "device"],
    "plan": ["plan", "plan name", "route", "patrol plan"],
    "planned_ts": ["planned time", "plan time", "scheduled", "scheduled time", "start time"],
    "actual_ts": ["arrival time", "actual time", "patrol time", "check time", "time", "scan time"],
    "status": ["status", "result", "patrol result", "state"],
    "site": ["site", "department", "company"],
    "note": ["note", "remark", "remarks", "event", "content"],
}


def import_patrol(df, source="JWM patrol export", con=None):
    cols = {c.lower().strip(): c for c in df.columns}
    m = {}
    for k, names in PATROL_COLUMNS.items():
        for n in names:
            if n in cols:
                m[k] = cols[n]
                break
    if "checkpoint" not in m or ("actual_ts" not in m and "planned_ts" not in m):
        return 0, f"Need at least a checkpoint column and a time column. Found: {', '.join(df.columns)}"
    rows = []
    for _, r in df.iterrows():
        def g(k):
            v = r[m[k]] if k in m else None
            return None if v is None or (isinstance(v, float) and v != v) else str(v)
        t_ = g("actual_ts") or g("planned_ts")
        try:
            d = datetime.fromisoformat(str(t_).replace("/", "-")[:19])
        except ValueError:
            continue
        stt = (g("status") or ("ARRIVED" if g("actual_ts") else "OMITTED")).upper()
        stt = "ARRIVED" if stt in ("NORMAL", "ARRIVED", "OK", "QUALIFIED", "DONE", "ON TIME") else "OMITTED" if stt in ("OMITTED", "MISSED", "MISS") \
            else "LATE" if "LATE" in stt else stt
        rows.append((d.strftime("%Y-%m-%d"), g("plan") or "Imported", None, g("checkpoint"), g("guard"), g("planned_ts"),
                     g("actual_ts") if stt != "OMITTED" else None, stt, g("note"), source, g("site")))
    with db.tx(con) as c:
        c.executemany("""INSERT INTO patrol_rounds(date, plan, round_no, checkpoint, guard, planned_ts, actual_ts, status, note, source, site)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?)""", rows)
    return len(rows), f"Imported {len(rows)} patrol records."


def patrol(date, con=None):
    return q("SELECT * FROM patrol_rounds WHERE date=? ORDER BY COALESCE(planned_ts, actual_ts)", (date,), con)


# ------------------------------------------------------------------ assignment board (per trip)
def bus_loads(trip_no, date, con=None):
    """Planned students per bus for a trip today (after approved one-day changes)."""
    import engine as E
    rows = []
    bus_ids = [r["bus_id"] for r in q("SELECT DISTINCT bus_id FROM trip_assign WHERE trip_no=?", (trip_no,), con)]
    bus_ids += [r["new_bus_id"] for r in q("""SELECT DISTINCT new_bus_id FROM change_requests WHERE date=? AND status='APPROVED' AND kind='BUS'
                                              AND (trip_no IS NULL OR trip_no=?)""", (date, trip_no), con) if r["new_bus_id"] not in bus_ids]
    for bid in bus_ids:
        b = db.bus(bid, con)
        lines = E.manifest_for(bid, date, trip_no, con)
        rows.append({"bus_id": bid, "bus_no": b["bus_no"], "capacity": b["capacity"], "planned": len(lines),
                     "absent": sum(1 for ln in lines if ln["status"] == "ABSENT_DECLARED"), "route": b["route_name"],
                     "stops": len({ln["stop_id"] for ln in lines})})
    return sorted(rows, key=lambda r: r["bus_no"])


def _stop_on_bus(c, bus_id, trip_no, src_stop_id):
    """Stop of the target bus (this trip) nearest to the student's stop; a new stop is added to that route if none is within 800 m."""
    import planner
    src = db.stop(src_stop_id, c)
    cand = db.stops_of_bus(bus_id, c, trip_no)
    best = min(cand, key=lambda s: planner.dist_m(src["lat"], src["lon"], s["lat"], s["lon"])) if cand else None
    if best and planner.dist_m(src["lat"], src["lon"], best["lat"], best["lon"]) <= 800:
        return best["id"]
    seq = (max([s["seq"] for s in cand]) + 1) if cand else 1
    return c.execute("INSERT INTO stops(bus_id, seq, name, area, lat, lon, trip_no) VALUES (?,?,?,?,?,?,?)",
                     (bus_id, seq, src["name"], src["area"], src["lat"], src["lon"], trip_no)).lastrowid


def move_students(student_ids, trip_no, date, to_bus, user, role, permanent=False, note="Assignment board", con=None):
    """Move children to another bus for one trip (today only = approved one-day bus change; permanent = new per-trip assignment)."""
    import engine as E
    moved = 0
    with db.tx(con) as c:
        for sid in student_ids:
            stu = q1("SELECT * FROM students WHERE id=?", (sid,), c)
            b, st_ = E.effective_assignment(stu, date, trip_no, c)
            if b == to_bus:
                continue
            new_stop = _stop_on_bus(c, to_bus, trip_no, st_)
            if permanent:
                c.execute("INSERT OR REPLACE INTO trip_assign(student_id, trip_no, bus_id, stop_id) VALUES (?,?,?,?)", (sid, trip_no, to_bus, new_stop))
                if trip_no == pickup_trip(stu["batch"], stu["grp"]):
                    c.execute("UPDATE students SET bus_id=?, stop_id=? WHERE id=?", (to_bus, new_stop, sid))
            else:
                c.execute("""INSERT INTO change_requests(student_id, date, trip_no, kind, new_stop_id, new_bus_id, note, requested_by, status,
                             decided_by, decided_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (sid, date, trip_no, "BUS", new_stop, to_bus, note, user, "APPROVED", user, ts(), ts()))
            moved += 1
        db.audit(user, role, "ASSIGNMENT_MOVE", "trip", trip_no, f"{moved} student(s) → bus {to_bus} {'permanent' if permanent else date}: {note}", c)
    return moved


def merge_routes(from_bus, to_bus, trip_no, date, user, role, con=None):
    import engine as E
    ids = [ln["student_id"] for ln in E.manifest_for(from_bus, date, trip_no, con)]
    return move_students(ids, trip_no, date, to_bus, user, role, note=f"Route merge {db.bus(from_bus)['bus_no']} → {db.bus(to_bus)['bus_no']}", con=con)


def swap_students(a, b, trip_no, date, user, role, con=None):
    import engine as E
    sa, sb = q1("SELECT * FROM students WHERE id=?", (a,), con), q1("SELECT * FROM students WHERE id=?", (b,), con)
    ba, _ = E.effective_assignment(sa, date, trip_no, con)
    bb, _ = E.effective_assignment(sb, date, trip_no, con)
    n = move_students([a], trip_no, date, bb, user, role, note=f"Swap with {sb['name']}", con=con)
    n += move_students([b], trip_no, date, ba, user, role, note=f"Swap with {sa['name']}", con=con)
    return n


def bus_trips(bus_id, con=None):
    """Trips this bus runs (it has a route for them)."""
    return [r["trip_no"] for r in q("SELECT DISTINCT trip_no FROM stops WHERE bus_id=? AND trip_no IS NOT NULL ORDER BY trip_no", (bus_id,), con)] \
        or list(TRIPS)
