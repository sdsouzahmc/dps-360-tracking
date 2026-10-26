"""Flexitrack 360 security additions (DPS requirements, Security module):
bus inspection after every trip (no child left behind), rear-door bus tags, visitor blacklist, standing gate passes,
authorised-collector check with photos, "ready at the gate" notice, and scan-speed logging for the bus and gate scan points."""
import json
import time
from datetime import datetime, timedelta

import db
from db import q, q1, ts

SCHEMA = """
CREATE TABLE IF NOT EXISTS bus_inspections (id INTEGER PRIMARY KEY, date TEXT, run_id INTEGER, bus_id INTEGER, trip_no INTEGER,
  ts TEXT, inspector TEXT, inspector_name TEXT, rear_tag TEXT, rear_tag_ok INTEGER, answers TEXT, child_found INTEGER DEFAULT 0,
  found_note TEXT, status TEXT, minutes_after INTEGER, source TEXT);
CREATE INDEX IF NOT EXISTS ix_binsp ON bus_inspections(date, run_id);
CREATE TABLE IF NOT EXISTS visitor_blacklist (id INTEGER PRIMARY KEY, name TEXT, id_no TEXT, phone TEXT, reason TEXT, added_by TEXT,
  ts TEXT, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS gate_passes (id INTEGER PRIMARY KEY, pass_code TEXT UNIQUE, student_id INTEGER, days TEXT, valid_from TEXT,
  valid_to TEXT, collector TEXT, relation TEXT, collector_id TEXT, note TEXT, created_by TEXT, ts TEXT, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS scan_perf (id INTEGER PRIMARY KEY, ts TEXT, date TEXT, point TEXT, ms REAL, result TEXT, device TEXT);
CREATE INDEX IF NOT EXISTS ix_scanperf ON scan_perf(date, point);
"""

INSPECT_ITEMS = [
    "Walked the full length of the bus to the rear door",
    "Every seat, under every seat and the floor checked",
    "No child on board",
    "No bags, lunch boxes or belongings left",
    "Emergency exit and windows closed",
    "Engine off, doors locked, bus secured",
]
GRACE_MIN = 20          # an inspection is due within 20 minutes of the trip closing
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def ensure_schema(con=None):
    with db.tx(con) as c:
        c.executescript(SCHEMA)
        cols = {r[1] for r in c.execute("PRAGMA table_info(early_pickups)").fetchall()}
        if cols and "ready_ts" not in cols:
            c.execute("ALTER TABLE early_pickups ADD COLUMN ready_ts TEXT")
            c.execute("ALTER TABLE early_pickups ADD COLUMN ready_by TEXT")


# ------------------------------------------------------------------ rear-door tag
def rear_tag_code(bus_no):
    """Code printed on the QR tag fixed inside the rear door of each bus (scanned to prove the walk to the back)."""
    return f"REAR-{bus_no}"


def rear_tag_ok(bus_id, code, con=None):
    b = db.bus(bus_id, con)
    return bool(b and code and code.strip().upper() == rear_tag_code(b["bus_no"]).upper())


# ------------------------------------------------------------------ bus inspection after every trip
def inspections_board(date, now=None, con=None):
    """Every closed trip of the day with its security inspection state: DONE / CHILD_FOUND / DUE / MISSED."""
    now = now or db.now()
    runs = q("""SELECT r.id run_id, r.bus_id, r.trip_no, r.end_ts, r.sweep_by, b.bus_no, b.route_name
                FROM trip_runs r JOIN buses b ON b.id=r.bus_id WHERE r.date=? AND r.status='CLOSED' ORDER BY r.end_ts DESC""", (date,), con)
    insp = {r["run_id"]: r for r in q("SELECT * FROM bus_inspections WHERE date=?", (date,), con)}
    out = []
    for r in runs:
        i = insp.get(r["run_id"])
        end = datetime.strptime(r["end_ts"], "%Y-%m-%d %H:%M:%S") if r["end_ts"] else None
        mins = int((now - end).total_seconds() // 60) if end else 0
        if i:
            state = "CHILD_FOUND" if i["child_found"] else ("LATE" if (i["minutes_after"] or 0) > GRACE_MIN else "DONE")
        else:
            state = "MISSED" if mins > GRACE_MIN else "DUE"
        out.append({**r, "inspection": i, "state": state, "mins_since_close": mins})
    return out


def save_inspection(run_id, inspector, inspector_name, rear_tag, answers, child_found=False, found_note="", when=None, source="Security app",
                    con=None):
    """Security confirms the bus is empty after a trip. A child found on board raises a HIGH alert to security and transport."""
    when = when or db.now()
    with db.tx(con) as c:
        r = q1("SELECT * FROM trip_runs WHERE id=?", (run_id,), c)
        if not r:
            return False, "Trip not found."
        if q1("SELECT id FROM bus_inspections WHERE run_id=?", (run_id,), c):
            return False, "This trip has already been inspected."
        b = db.bus(r["bus_id"], c)
        tag_ok = rear_tag_ok(r["bus_id"], rear_tag, c)
        if not tag_ok:
            return False, f"Rear-door tag not recognised — scan the tag inside the rear door of {b['bus_no']} ({rear_tag_code(b['bus_no'])})."
        missing = [k for k in INSPECT_ITEMS if not answers.get(k) and k != "No child on board"]
        if missing and not child_found:
            return False, "Tick every item: " + "; ".join(missing)
        end = datetime.strptime(r["end_ts"], "%Y-%m-%d %H:%M:%S") if r["end_ts"] else when
        mins = max(0, int((when - end).total_seconds() // 60))
        status = "CHILD_FOUND" if child_found else "DONE"
        c.execute("""INSERT INTO bus_inspections(date, run_id, bus_id, trip_no, ts, inspector, inspector_name, rear_tag, rear_tag_ok, answers,
                     child_found, found_note, status, minutes_after, source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (r["date"], run_id, r["bus_id"], r["trip_no"], ts(when), inspector, inspector_name, rear_tag, 1, json.dumps(answers),
                   1 if child_found else 0, found_note, status, mins, source))
        c.execute("""UPDATE campus_alerts SET status='RESOLVED', resolved_by=?, resolved_ts=?, resolution='Inspection done'
                     WHERE kind='INSPECTION_MISSED' AND ptype='BUS_RUN' AND pid=? AND status<>'RESOLVED'""", (inspector, ts(when), run_id))
        import campus
        if child_found:
            campus.raise_alert("CHILD_LEFT_ON_BUS", "HIGH", f"Child found on {b['bus_no']} after Trip {r['trip_no']} closed: {found_note or 'see inspector'}"
                               f" — inspector {inspector_name}", r["date"], None, "BUS_RUN", run_id, audience="security", when=when, con=c)
        db.audit(inspector, "security", "BUS_INSPECTION", "trip_run", run_id, f"{b['bus_no']} trip {r['trip_no']}: {status}", c)
        return True, ("⚠ Child found — alert raised to security and transport. Keep the child safe and call the transport office."
                      if child_found else f"{b['bus_no']} Trip {r['trip_no']} inspected — bus empty.")


def check_missed_inspections(date=None, now=None, con=None):
    """Raise one alert per closed trip with no security inspection GRACE_MIN minutes after it closed."""
    now = now or db.now()
    date = date or now.strftime("%Y-%m-%d")
    n = 0
    with db.tx(con) as c:
        cutoff = ts(now - timedelta(minutes=GRACE_MIN))
        rows = q("""SELECT r.id, r.trip_no, r.end_ts, b.bus_no FROM trip_runs r JOIN buses b ON b.id=r.bus_id
                    WHERE r.date=? AND r.status='CLOSED' AND r.end_ts<=? AND r.id NOT IN (SELECT run_id FROM bus_inspections WHERE date=?)
                    AND r.id NOT IN (SELECT pid FROM campus_alerts WHERE kind='INSPECTION_MISSED' AND ptype='BUS_RUN' AND date=?)""",
                 (date, cutoff, date, date), c)
        import campus
        for r in rows:
            when = datetime.strptime(r["end_ts"], "%Y-%m-%d %H:%M:%S") + timedelta(minutes=GRACE_MIN)
            campus.raise_alert("INSPECTION_MISSED", "MEDIUM", f"{r['bus_no']} Trip {r['trip_no']} closed at {r['end_ts'][11:16]} — no security "
                               f"inspection within {GRACE_MIN} min", date, None, "BUS_RUN", r["id"], audience="security", when=min(when, now), con=c)
            n += 1
    return n


def sim_inspection(con, run_id, bus_id, trip_no, date, closed_at, rnd, guards):
    """Demo feed: a guard inspects most buses 3–14 minutes after the trip closes (about 3% are missed)."""
    x_ = rnd.random()
    if x_ < 0.03:
        return
    mins = rnd.randint(3, 14) if x_ > 0.08 else rnd.randint(22, 35)
    when = closed_at + timedelta(minutes=mins)
    b = con.execute("SELECT bus_no FROM buses WHERE id=?", (bus_id,)).fetchone()
    g = rnd.choice(guards) if guards else ("guard1", "Security guard")
    ans = {k: True for k in INSPECT_ITEMS}
    con.execute("""INSERT INTO bus_inspections(date, run_id, bus_id, trip_no, ts, inspector, inspector_name, rear_tag, rear_tag_ok, answers,
                   child_found, found_note, status, minutes_after, source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (date, run_id, bus_id, trip_no, ts(when), g[0], g[1], rear_tag_code(b[0]), 1, json.dumps(ans), 0, None, "DONE", mins,
                 "Security app (demo feed)"))


# ------------------------------------------------------------------ visitor blacklist
def _norm(s):
    return "".join(ch for ch in (s or "").upper() if ch.isalnum())


def blacklist(active_only=True, con=None):
    return q("SELECT * FROM visitor_blacklist" + (" WHERE active=1" if active_only else "") + " ORDER BY ts DESC", (), con)


def add_blacklist(name, id_no, phone, reason, user, con=None):
    with db.tx(con) as c:
        i = c.execute("INSERT INTO visitor_blacklist(name, id_no, phone, reason, added_by, ts, active) VALUES (?,?,?,?,?,?,1)",
                      (name.strip(), (id_no or "").strip(), (phone or "").strip(), reason, user, ts())).lastrowid
        db.audit(user, "security", "BLACKLIST_ADD", "visitor_blacklist", i, f"{name} {id_no}", c)
        return i


def remove_blacklist(bid, user, con=None):
    with db.tx(con) as c:
        c.execute("UPDATE visitor_blacklist SET active=0 WHERE id=?", (bid,))
        db.audit(user, "security", "BLACKLIST_REMOVE", "visitor_blacklist", bid, "", c)


def check_blacklist(name=None, id_no=None, phone=None, con=None):
    """A blacklist entry matching the QID / passport number, the phone, or the exact name."""
    for b in blacklist(True, con):
        if id_no and b["id_no"] and _norm(id_no) == _norm(b["id_no"]):
            return b, "QID / passport number"
        if phone and b["phone"] and _norm(phone)[-8:] == _norm(b["phone"])[-8:]:
            return b, "phone number"
        if name and b["name"] and _norm(name) == _norm(b["name"]):
            return b, "name"
    return None, None


def flag_blacklisted(name, id_no, phone, gate_id, user, con=None):
    """Record a refused entry and alert security."""
    b, why = check_blacklist(name, id_no, phone, con)
    if not b:
        return None
    import campus
    when = db.now()
    with db.tx(con) as c:
        c.execute("""INSERT INTO gate_scans(ts, date, ptype, pid, dir, gate_id, method, operator, result, reason, ref, code)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (ts(when), when.strftime("%Y-%m-%d"), "VISITOR", None, "IN", gate_id, "PASS", user,
                                                          "REJECTED", f"Blacklisted ({why})", None, id_no or name))
        campus.raise_alert("VISITOR_BLACKLISTED", "HIGH", f"Blacklisted visitor at the gate: {name} (matched on {why}; listed for: {b['reason']})",
                           when.strftime("%Y-%m-%d"), None, "VISITOR", None, audience="security", when=when, con=c)
    return b, why


# ------------------------------------------------------------------ standing gate passes (school-transport users collected on set days)
def new_pass_code(con=None):
    n = q1("SELECT COUNT(*) n FROM gate_passes", (), con)["n"] + 1
    return f"GP{n:05d}"


def add_gate_pass(student_id, days, valid_from, valid_to, collector, relation, collector_id, note, user, con=None):
    with db.tx(con) as c:
        code = new_pass_code(c)
        c.execute("""INSERT INTO gate_passes(pass_code, student_id, days, valid_from, valid_to, collector, relation, collector_id, note, created_by,
                     ts, active) VALUES (?,?,?,?,?,?,?,?,?,?,?,1)""",
                  (code, student_id, ",".join(days), valid_from, valid_to, collector, relation, collector_id, note, user, ts()))
        db.audit(user, "manager", "GATE_PASS_ADD", "student", student_id, f"{code} {','.join(days)} {collector}", c)
        return code


def gate_passes(active_only=True, student_id=None, con=None):
    sql = """SELECT p.*, s.name, s.code, s.class_name || '-' || s.section AS class, s.transport FROM gate_passes p JOIN students s ON s.id=p.student_id
             WHERE 1=1"""
    a = []
    if active_only:
        sql += " AND p.active=1"
    if student_id:
        sql += " AND p.student_id=?"
        a.append(student_id)
    return q(sql + " ORDER BY p.id DESC", a, con)


def pass_today(student_id, date, con=None):
    """The student's standing pass valid on this date (weekday in its days, within its dates), if any."""
    wd = DAYS[datetime.strptime(date, "%Y-%m-%d").weekday()]
    for p in gate_passes(True, student_id, con):
        if p["valid_from"] <= date <= (p["valid_to"] or "9999") and wd in (p["days"] or "").split(","):
            return p
    return None


def revoke_gate_pass(pid, user, con=None):
    with db.tx(con) as c:
        c.execute("UPDATE gate_passes SET active=0 WHERE id=?", (pid,))
        db.audit(user, "manager", "GATE_PASS_REVOKE", "gate_pass", pid, "", c)


# ------------------------------------------------------------------ authorised collectors (photo check)
FEMALE = ("MOTHER", "AUNT", "GRANDMOTHER", "SISTER", "NANNY", "MAID", "GUARDIAN (F)")


def collector_gender(relation):
    return "F" if any(w in (relation or "").upper() for w in FEMALE) else "M"


def collectors(student_id, con=None):
    """Parents and the people allowed to collect the child, with a photo seed for each."""
    g = q("""SELECT g.id, g.name, g.relation, g.phone, 'Parent' AS kind FROM guardians g JOIN student_guardians sg ON sg.guardian_id=g.id
             WHERE sg.student_id=?""", (student_id,), con)
    r = q("SELECT id, name, relation, phone, id_number, 'Allowed to collect' AS kind FROM recipients WHERE student_id=? AND active=1", (student_id,), con)
    out = []
    for x_ in g:
        out.append({**x_, "seed": f"G{x_['id']}", "gender": collector_gender(x_["relation"])})
    for x_ in r:
        out.append({**x_, "seed": f"R{x_['id']}", "gender": collector_gender(x_["relation"])})
    return out


def collector_allowed(student_id, collector, collector_id=None, con=None):
    """Is the named collector a parent or on the allowed list? Matches the ID number, else the name (ignoring case and spacing)."""
    cs = collectors(student_id, con)
    if collector_id:
        for c_ in cs:
            if c_.get("id_number") and _norm(c_["id_number"]) == _norm(collector_id):
                return c_
    n = _norm(collector)
    for c_ in cs:
        if n and (_norm(c_["name"]) == n or (len(n) > 5 and (n in _norm(c_["name"]) or _norm(c_["name"]) in n))):
            return c_
    return None


def collectors_html(student_id, size=54, highlight=None, con=None):
    import campus
    cells = []
    for c_ in collectors(student_id, con):
        on = highlight and _norm(highlight) and _norm(highlight) in _norm(c_["name"])
        cells.append(f"<div style='text-align:center;width:{size + 40}px'>{campus.avatar_img(c_['seed'], c_['gender'], 'VISITOR', size, 'border:3px solid ' + ('#2E9E5B' if on else 'transparent'))}"
                     f"<div style='font-size:.75rem;font-weight:700'>{c_['name']}</div><div style='font-size:.7rem;color:#6B7390'>{c_['relation']}</div></div>")
    return "<div style='display:flex;gap:8px;flex-wrap:wrap'>" + "".join(cells) + "</div>"


# ------------------------------------------------------------------ "ready at the gate"
def mark_ready(eid, user, gate_name="Gate 1 · Main gate", when=None, con=None):
    """Teacher sends the child to the gate: parent told the child is ready for collection; security sees it on the early-pickup list."""
    when = when or db.now()
    import campus
    with db.tx(con) as c:
        e = q1("SELECT e.*, s.name FROM early_pickups e JOIN students s ON s.id=e.student_id WHERE e.id=?", (eid,), c)
        if not e or e["status"] != "APPROVED":
            return False, "This early pickup is not approved (or already collected)."
        if e.get("ready_ts"):
            return False, f"Already sent to the gate at {e['ready_ts'][11:16]}."
        c.execute("UPDATE early_pickups SET ready_ts=?, ready_by=? WHERE id=?", (ts(when), user, eid))
        campus._notify(c, e["student_id"], "READY_AT_GATE", f"{e['name']} is ready for collection at {gate_name} ({when:%H:%M}). "
                       f"Collector: {e['collector']} — bring the QID and gate pass {e['pass_code']}.", when)
        db.audit(user, "teacher", "READY_AT_GATE", "student", e["student_id"], f"pass {e['pass_code']}", c)
        return True, f"Parent told {e['name']} is ready at the gate."


# ------------------------------------------------------------------ scan speed (DPS IT: how fast the bus and gate points register a scan)
def timed(point, fn, *a, device=None, **kw):
    """Run a scan and log how long it took (milliseconds) for the scan-speed report."""
    t0 = time.perf_counter()
    res = fn(*a, **kw)
    ms = (time.perf_counter() - t0) * 1000
    try:
        ok = res.get("ok") if isinstance(res, dict) else bool(res)
        now = db.now()
        db.x("INSERT INTO scan_perf(ts, date, point, ms, result, device) VALUES (?,?,?,?,?,?)",
             (ts(now), now.strftime("%Y-%m-%d"), point, round(ms, 1), "OK" if ok else "REJECTED", device))
    except Exception:
        pass
    return res


def scan_speed(date_from, date_to, con=None):
    rows = q("SELECT point, ms FROM scan_perf WHERE date BETWEEN ? AND ?", (date_from, date_to), con)
    out = {}
    for p in sorted({r["point"] for r in rows}):
        v = sorted(r["ms"] for r in rows if r["point"] == p)
        pct = lambda k: v[min(len(v) - 1, int(round(k * (len(v) - 1))))]
        out[p] = {"scans": len(v), "median_ms": pct(.5), "p95_ms": pct(.95), "max_ms": v[-1], "under_1s": sum(1 for x_ in v if x_ < 1000) / len(v)}
    return out


# ------------------------------------------------------------------ demo seed (history days)
def seed(con=None):
    """Demo blacklist entries, standing passes and scan-speed samples (only on an empty database)."""
    import random
    r = random.Random(41)
    with db.tx(con) as c:
        if not c.execute("SELECT COUNT(*) FROM visitor_blacklist").fetchone()[0]:
            for n, i, p, why in [("Rashid Kamal Hussain", "28435612345", "55501234", "Trespassing on campus, Mar 2026"),
                                 ("Vinod Pallath", "27935600871", "33320987", "Court order — not allowed near a student"),
                                 ("Samir El-Masri", "29878811234", "66654321", "Aggressive behaviour at the gate"),
                                 ("Unknown courier (Fast Xpress)", "", "70001122", "Repeated unauthorised entry attempts")]:
                c.execute("INSERT INTO visitor_blacklist(name, id_no, phone, reason, added_by, ts, active) VALUES (?,?,?,?,?,?,1)",
                          (n, i, p, why, "security", "2026-09-01 09:00:00"))
        if not c.execute("SELECT COUNT(*) FROM gate_passes").fetchone()[0]:
            studs = q("SELECT id FROM students WHERE transport='BUS' AND active=1 ORDER BY id LIMIT 400", (), c)
            for k, s in enumerate(r.sample(studs, 12)):
                rec = q1("SELECT name, relation, id_number FROM recipients WHERE student_id=? AND active=1 LIMIT 1", (s["id"],), c) or \
                    q1("SELECT g.name, g.relation, '' id_number FROM guardians g JOIN student_guardians sg ON sg.guardian_id=g.id WHERE sg.student_id=?",
                       (s["id"],), c)
                days = r.choice([["Sun", "Tue"], ["Mon", "Wed"], ["Thu"], ["Sun", "Mon", "Tue", "Wed", "Thu"]])
                c.execute("""INSERT INTO gate_passes(pass_code, student_id, days, valid_from, valid_to, collector, relation, collector_id, note,
                             created_by, ts, active) VALUES (?,?,?,?,?,?,?,?,?,?,?,1)""",
                          (f"GP{k + 1:05d}", s["id"], ",".join(days), "2026-09-01", "2027-06-30", rec["name"], rec["relation"], rec["id_number"],
                           r.choice(["Tuition after school", "Swimming class", "Parent collects on these days", "Therapy session"]), "manager",
                           "2026-09-01 08:00:00"))
        if not c.execute("SELECT COUNT(*) FROM scan_perf").fetchone()[0]:
            import engine as E
            for d in E.school_days_back(4):
                for p, mu in (("BUS", 180), ("GATE", 140)):
                    for i in range(260):
                        ms = max(40, r.gauss(mu, 60)) if r.random() > .02 else r.uniform(700, 1600)
                        c.execute("INSERT INTO scan_perf(ts, date, point, ms, result, device) VALUES (?,?,?,?,?,?)",
                                  (f"{d} {6 + i % 9:02d}:{i % 60:02d}:00", d, p, round(ms, 1), "OK", "demo"))
