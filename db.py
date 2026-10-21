"""DPS 360 — SQLite data layer: schema, connection helpers, seed entry point and audit log."""
import hashlib
import os
import random
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("SBT_DB", os.path.join(BASE, "school_bus.db"))
TZ = ZoneInfo("Asia/Qatar")

SCHOOL_NAME = "DPS School, Doha"
SCHOOL_LAT, SCHOOL_LON = 25.2585, 51.4930
SCHOOL_STOP = 0           # pseudo stop id used for "at school"
TRANSPORT_PHONE = "+974 4400 0000"

# DPS daily transport sessions (17 Oct meeting): a different set of buses runs each session and a student's bus
# can change by departure time (per-trip assignment in table trip_assign). Movement = (shift, group, purpose)
TRIPS = {
    1: {"window": ("05:45", "07:25"), "movements": [(1, "KG", "PICKUP"), (1, "Main", "PICKUP"), (1, "StayBack", "PICKUP")],
        "label": "Morning pickup (all morning-shift students)", "short": "Morning pickup", "buses": 98},
    2: {"window": ("11:05", "12:40"), "movements": [(1, "KG", "DROPOFF"), (2, "Afternoon", "PICKUP")],
        "label": "KG drop-off + afternoon-shift pickup", "short": "KG drop + PM pickup", "buses": 35},
    3: {"window": ("13:50", "15:05"), "movements": [(1, "Main", "DROPOFF")], "label": "2:00 pm drop-off (main school)",
        "short": "2:00 pm drop-off", "buses": 98},
    4: {"window": ("16:30", "17:35"), "movements": [(1, "StayBack", "DROPOFF")], "label": "4:30 pm stay-back drop-off",
        "short": "4:30 pm stay-back", "buses": 28},
    5: {"window": ("18:35", "19:50"), "movements": [(2, "Afternoon", "DROPOFF")], "label": "After 6:30 pm drop-off (afternoon shift + late)",
        "short": "6:30 pm drop-off", "buses": 16},
}
GROUP_LABEL = {(1, "KG"): "KG", (1, "Main"): "Main school", (1, "StayBack"): "Stay-back", (2, "Afternoon"): "Afternoon shift"}


def mlabel(batch, grp):
    """Readable name of a student group, e.g. 'Afternoon shift'."""
    return GROUP_LABEL.get((batch, grp), f"Shift {batch} {grp}")


ROLES = ["admin", "manager", "dispatcher", "driver", "supervisor", "caretaker", "receiving", "guardian", "security", "teacher", "principal",
         "fleet_supervisor", "senior_manager", "vp"]
MFA_ROLES = {"admin", "manager"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS staff (id INTEGER PRIMARY KEY, name TEXT, role TEXT, phone TEXT, bus_id INTEGER, card_no TEXT, dept TEXT, gender TEXT);
CREATE TABLE IF NOT EXISTS buses (
  id INTEGER PRIMARY KEY, bus_no TEXT UNIQUE, plate TEXT, capacity INTEGER, fvts_vehicle TEXT, route_name TEXT,
  driver_id INTEGER, supervisor_id INTEGER, caretaker_id INTEGER);
CREATE TABLE IF NOT EXISTS stops (id INTEGER PRIMARY KEY, bus_id INTEGER, seq INTEGER, name TEXT, area TEXT, lat REAL, lon REAL, trip_no INTEGER);
CREATE TABLE IF NOT EXISTS trip_assign (student_id INTEGER, trip_no INTEGER, bus_id INTEGER, stop_id INTEGER, PRIMARY KEY(student_id, trip_no));
CREATE TABLE IF NOT EXISTS guardians (id INTEGER PRIMARY KEY, name TEXT, phone TEXT, email TEXT, relation TEXT, verified INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS students (
  id INTEGER PRIMARY KEY, code TEXT UNIQUE, name TEXT, class_name TEXT, section TEXT, batch INTEGER, grp TEXT,
  bus_id INTEGER, stop_id INTEGER, active INTEGER DEFAULT 1, transport TEXT DEFAULT 'BUS', gender TEXT, drop_stop_id INTEGER);
CREATE TABLE IF NOT EXISTS student_guardians (student_id INTEGER, guardian_id INTEGER, is_primary INTEGER, PRIMARY KEY(student_id, guardian_id));
CREATE TABLE IF NOT EXISTS recipients (id INTEGER PRIMARY KEY, student_id INTEGER, name TEXT, relation TEXT, phone TEXT, id_number TEXT, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS emergency_contacts (id INTEGER PRIMARY KEY, student_id INTEGER, name TEXT, relation TEXT, phone TEXT);
CREATE TABLE IF NOT EXISTS badges (id INTEGER PRIMARY KEY, code TEXT UNIQUE, student_id INTEGER, status TEXT, issued_at TEXT,
  revoked_at TEXT, reason TEXT, replaced_by TEXT);
CREATE TABLE IF NOT EXISTS devices (id INTEGER PRIMARY KEY, name TEXT, bus_id INTEGER, status TEXT, registered_at TEXT, revoked_at TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, pin_hash TEXT, role TEXT, staff_id INTEGER, guardian_id INTEGER,
  display_name TEXT, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS absences (id INTEGER PRIMARY KEY, student_id INTEGER, date TEXT, trip_no INTEGER, reason TEXT,
  declared_by TEXT, source TEXT, status TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS change_requests (id INTEGER PRIMARY KEY, student_id INTEGER, date TEXT, trip_no INTEGER, kind TEXT,
  new_stop_id INTEGER, new_bus_id INTEGER, recipient_name TEXT, recipient_phone TEXT, note TEXT, requested_by TEXT,
  status TEXT, decided_by TEXT, decided_at TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS trip_runs (
  id INTEGER PRIMARY KEY, bus_id INTEGER, date TEXT, trip_no INTEGER, status TEXT,
  start_ts TEXT, start_lat REAL, start_lon REAL, end_ts TEXT, end_lat REAL, end_lon REAL,
  driver_id INTEGER, supervisor_id INTEGER, caretaker_id INTEGER,
  final_driver_id INTEGER, final_supervisor_id INTEGER, final_caretaker_id INTEGER,
  sweep_by TEXT, sweep_ts TEXT, started_by TEXT, closed_by TEXT, device_id INTEGER, cancelled INTEGER DEFAULT 0,
  UNIQUE(bus_id, date, trip_no));
CREATE TABLE IF NOT EXISTS manifest (
  id INTEGER PRIMARY KEY, run_id INTEGER, student_id INTEGER, purpose TEXT, batch INTEGER, grp TEXT, stop_id INTEGER, status TEXT,
  entry_ts TEXT, entry_lat REAL, entry_lon REAL, exit_ts TEXT, exit_lat REAL, exit_lon REAL,
  handover TEXT, recipient_name TEXT, recipient_relation TEXT, received_by TEXT, note TEXT,
  UNIQUE(run_id, student_id));
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, client_uuid TEXT UNIQUE, run_id INTEGER, manifest_id INTEGER, student_id INTEGER, badge_code TEXT,
  kind TEXT, result TEXT, reason TEXT, captured_ts TEXT, received_ts TEXT, lat REAL, lon REAL, accuracy REAL, gps_ok INTEGER,
  stop_id INTEGER, operator TEXT, device_id INTEGER, method TEXT, manual_reason TEXT, verified_by TEXT,
  sync_state TEXT, clock_flag TEXT, connectivity TEXT DEFAULT 'Online', device_seq INTEGER);
CREATE TABLE IF NOT EXISTS corrections (id INTEGER PRIMARY KEY, event_id INTEGER, manifest_id INTEGER, field TEXT, old_value TEXT,
  new_value TEXT, reason TEXT, requested_by TEXT, status TEXT, decided_by TEXT, decided_at TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS incidents (id INTEGER PRIMARY KEY, ts TEXT, run_id INTEGER, bus_id INTEGER, student_id INTEGER, kind TEXT,
  severity TEXT, details TEXT, status TEXT, assigned_to TEXT, resolution TEXT, resolved_ts TEXT, receiving_custody TEXT, ack_ts TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS incident_contacts (id INTEGER PRIMARY KEY, incident_id INTEGER, ts TEXT, contact_name TEXT, phone TEXT,
  outcome TEXT, by_user TEXT);
CREATE TABLE IF NOT EXISTS notifications (id INTEGER PRIMARY KEY, ts TEXT, guardian_id INTEGER, student_id INTEGER, run_id INTEGER,
  channel TEXT, kind TEXT, message TEXT, status TEXT, attempts INTEGER DEFAULT 0, last_error TEXT, delayed_offline INTEGER DEFAULT 0,
  is_read INTEGER DEFAULT 0, sent_ts TEXT);
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, ts TEXT, username TEXT, role TEXT, action TEXT, entity TEXT, entity_id TEXT, details TEXT);
CREATE INDEX IF NOT EXISTS ix_man_run ON manifest(run_id);
CREATE INDEX IF NOT EXISTS ix_man_stu ON manifest(student_id);
CREATE INDEX IF NOT EXISTS ix_ev_run ON events(run_id);
CREATE INDEX IF NOT EXISTS ix_ev_stu ON events(student_id);
CREATE INDEX IF NOT EXISTS ix_run_date ON trip_runs(date, trip_no);
CREATE INDEX IF NOT EXISTS ix_not_g ON notifications(guardian_id);
CREATE INDEX IF NOT EXISTS ix_abs ON absences(student_id, date);
CREATE INDEX IF NOT EXISTS ix_ta_bus ON trip_assign(trip_no, bus_id);
CREATE INDEX IF NOT EXISTS ix_stops_bus ON stops(bus_id, trip_no);
CREATE INDEX IF NOT EXISTS ix_badge_stu ON badges(student_id);
CREATE INDEX IF NOT EXISTS ix_sg_s ON student_guardians(student_id);
CREATE INDEX IF NOT EXISTS ix_sg_g ON student_guardians(guardian_id);
CREATE INDEX IF NOT EXISTS ix_inc_status ON incidents(status);
CREATE INDEX IF NOT EXISTS ix_runs_bus ON trip_runs(bus_id, date);
CREATE INDEX IF NOT EXISTS ix_cr ON change_requests(date, status);
CREATE INDEX IF NOT EXISTS ix_rec_s ON recipients(student_id);
CREATE INDEX IF NOT EXISTS ix_emc_s ON emergency_contacts(student_id);
CREATE INDEX IF NOT EXISTS ix_stu_bus ON students(bus_id);
CREATE INDEX IF NOT EXISTS ix_not_s ON notifications(student_id);
CREATE INDEX IF NOT EXISTS ix_ta_stu ON trip_assign(student_id);
"""


_CLOCK = {"off": 0.0, "read": None}


def clock_offset():
    """Demo clock: seconds added to real time (0 = real time). Re-read from settings every 20 s."""
    t = datetime.now()
    if _CLOCK["read"] is None or (t - _CLOCK["read"]).total_seconds() > 20:
        _CLOCK["read"] = t
        try:
            con = sqlite3.connect(DB_PATH, timeout=5)
            r = con.execute("SELECT value FROM settings WHERE key='clock_offset_s'").fetchone()
            con.close()
            _CLOCK["off"] = float(r[0]) if r else 0.0
        except sqlite3.Error:
            _CLOCK["off"] = 0.0
    return _CLOCK["off"]


def set_clock_offset(seconds):
    x("INSERT OR REPLACE INTO settings(key, value) VALUES ('clock_offset_s', ?)", (str(seconds),))
    _CLOCK["off"], _CLOCK["read"] = float(seconds), datetime.now()


def now():
    return datetime.now(TZ).replace(tzinfo=None) + timedelta(seconds=clock_offset())


def ts(dt=None):
    if isinstance(dt, str):
        return dt
    return (dt or now()).strftime("%Y-%m-%d %H:%M:%S")


def today():
    return now().strftime("%Y-%m-%d")


def connect():
    con = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


@contextmanager
def tx(con=None):
    """Use an existing connection (no commit) or open one and commit at the end."""
    if con is not None:
        yield con
        return
    c = connect()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def q(sql, params=(), con=None):
    with tx(con) as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def q1(sql, params=(), con=None):
    r = q(sql, params, con)
    return r[0] if r else None


def x(sql, params=(), con=None):
    with tx(con) as c:
        return c.execute(sql, params).lastrowid


def pin_hash(pin):
    return hashlib.sha256(("sbt-demo-salt:" + str(pin)).encode()).hexdigest()


def audit(username, role, action, entity="", entity_id="", details="", con=None):
    x("INSERT INTO audit(ts, username, role, action, entity, entity_id, details) VALUES (?,?,?,?,?,?,?)",
      (ts(), username, role, action, entity, str(entity_id), details), con)


# ------------------------------------------------------------------ seed
FIRST_M = ["Ahmed", "Mohammed", "Omar", "Yusuf", "Ali", "Hamad", "Khalid", "Abdullah", "Faisal", "Saad", "Ibrahim", "Hassan", "Aarav",
           "Arjun", "Rohan", "Vihaan", "Aditya", "Rayan", "Zayd", "Adam", "Liam", "Noah", "Ethan", "Lucas", "Daniel", "Joseph", "Ryan",
           "Karim", "Tariq", "Bilal", "Hamza", "Imran", "Kevin", "Mark", "John", "Paul"]
FIRST_F = ["Fatima", "Mariam", "Aisha", "Noor", "Sara", "Layla", "Hessa", "Reem", "Dana", "Lulwa", "Shaikha", "Ananya", "Diya",
           "Priya", "Sana", "Zara", "Hiba", "Emma", "Olivia", "Sophia", "Mia", "Ava", "Grace", "Hannah", "Leah", "Amira", "Yasmin",
           "Salma", "Rania", "Huda", "Angel", "Joy", "Maria", "Anna"]
LAST = ["Al-Thani", "Al-Kuwari", "Al-Mannai", "Al-Sulaiti", "Al-Marri", "Al-Hajri", "Al-Kaabi", "Al-Naimi", "Al-Mohannadi", "Khan",
        "Sheikh", "Hussain", "Rahman", "Qureshi", "Malik", "Nair", "Menon", "Pillai", "Kumar", "Sharma", "Patel", "Iyer", "Reddy",
        "Fernandes", "D'Souza", "Santos", "Reyes", "Cruz", "Garcia", "Smith", "Brown", "Wilson", "Haddad", "Nasser", "Mansour",
        "Saleh", "Aziz", "Farouk", "Youssef", "Abdallah"]
STAFF_NAMES = ["Ramesh Kumar", "Suresh Babu", "Anil Thomas", "Joseph Mathew", "Imran Ali", "Shahid Khan", "Rajan Pillai", "Biju Varghese",
               "Faisal Ahmed", "Naveed Iqbal", "Santosh Rai", "Dinesh Thapa", "Ravi Shankar", "Manoj Nair", "Sajid Hussain",
               "Bashir Ahmed", "Prakash Gurung", "Jose Fernandes", "Arjun Das", "Kamal Hossain", "Rafiq Uddin", "Salim Malik",
               "Vinod Kumar", "Tariq Mehmood", "George Mathew", "Hari Prasad", "Abdul Kareem", "Noel D'Souza", "Mary Joseph",
               "Fatima Begum", "Sheeba Thomas", "Aisha Khan", "Rosemary Dsouza", "Nisha Nair", "Lina Haddad", "Asma Qureshi",
               "Bindu Menon", "Grace Fernandes", "Hanan Saleh", "Jessy George", "Shabana Ali", "Reena Pillai", "Samira Nasser",
               "Teresa Cruz", "Anita Sharma", "Mona Aziz", "Divya Iyer", "Huda Mansour", "Rekha Patel", "Salma Rahman",
               "Angela Reyes", "Noura Farouk", "Priya Kumar", "Joyce Santos", "Ruby Mathew", "Leena Das"]
AREAS = [("Al Sadd", 25.2850, 51.5050), ("West Bay", 25.3230, 51.5280), ("Al Waab", 25.2620, 51.4700), ("Al Rayyan", 25.2920, 51.4240),
         ("Old Airport", 25.2520, 51.5530), ("Al Wakrah", 25.1710, 51.6030), ("Lusail", 25.4200, 51.4900), ("The Pearl", 25.3710, 51.5510),
         ("Madinat Khalifa", 25.3180, 51.4780), ("Al Gharrafa", 25.3350, 51.4560), ("Abu Hamour", 25.2400, 51.5000),
         ("Al Hilal", 25.2580, 51.5480), ("Najma", 25.2710, 51.5460), ("Muaither", 25.2900, 51.3950), ("Al Thumama", 25.2300, 51.5400),
         ("Duhail", 25.3480, 51.4760), ("Mansoura", 25.2700, 51.5400), ("Fereej Bin Mahmoud", 25.2830, 51.5200),
         ("Ain Khaled", 25.2300, 51.4700), ("Al Aziziya", 25.2500, 51.4600), ("Umm Salal", 25.4100, 51.4050), ("Al Luqta", 25.3200, 51.4500)]
CLASSES = ["KG1", "KG2", "Grade 1", "Grade 2", "Grade 3", "Grade 4", "Grade 5", "Grade 6", "Grade 7", "Grade 8", "Grade 9", "Grade 10",
           "Grade 11", "Grade 12"]
JUNIOR = set(CLASSES[:7])


def init_db(reset=False, seed_data=True, **kw):
    """Create the schema; on an empty database generate the DPS-scale demo (dps_data.generate)."""
    if reset:
        store = os.path.join(os.path.dirname(DB_PATH) or ".", "device_store")
        if os.path.isdir(store):
            for f in os.listdir(store):
                os.remove(os.path.join(store, f))
        for ext in ("", "-wal", "-shm"):
            try:
                os.remove(DB_PATH + ext)
            except FileNotFoundError:
                pass
    con = connect()
    try:
        con.executescript(SCHEMA)
        _POS.clear()
        empty = not con.execute("SELECT COUNT(*) FROM buses").fetchone()[0]
    finally:
        con.close()
    import campus
    campus.ensure_schema()
    if empty and seed_data:
        import dps_data
        dps_data.generate(**kw)
    if seed_data:
        seed_extras()


def teacher_login(class_name, section):
    """Class-teacher sign-in: 't' + grade + section, e.g. Grade 5-A → t5a, KG1-A → tkg1a (demo PIN 1234)."""
    g = str(class_name).replace("Grade", "").replace(" ", "").lower()
    return f"t{g}{str(section).lower()}"


TEST_PARENT_SETS = {   # tester → (login prefix, how to pick children): every child of the chosen parents fits the tester's data range
    "A1": ("pa1-", "bus", "BUS-001"), "A2": ("pa2-", "bus", "BUS-026"),
    "B1": ("pb1-", "class", ("Grade 5-A", "Grade 9-D")), "B2": ("pb2-", "class", ("Grade 6-Q", "KG2-C")),
}


B_CLASSES = ("Grade 5-A", "Grade 9-D", "Grade 6-Q", "KG2-C")


def test_parents(tester, limit=10, con=None, _cache={}):
    """One parent per family whose children ALL fit the tester's range and touch no other team's data:
    A1 / A2 → every child rides only the reserved bus (BUS-001 / BUS-026) and is not in a Team B class;
    B1 / B2 → every child is in the tester's classes and rides no Team A bus (BUS-001…049, BUS-099).
    All data is read in 3 queries (fast on a large school)."""
    prefix, kind, val = TEST_PARENT_SETS[tester]
    cls = {r["id"]: f"{r['class_name']}-{r['section']}" for r in q("SELECT id, class_name, section FROM students", (), con)}
    buses = {}
    for r in q("SELECT ta.student_id, b.bus_no FROM trip_assign ta JOIN buses b ON b.id=ta.bus_id", (), con):
        buses.setdefault(r["student_id"], set()).add(r["bus_no"])
    fam = {}
    for r in q("SELECT guardian_id, student_id FROM student_guardians ORDER BY guardian_id", (), con):
        fam.setdefault(r["guardian_id"], set()).add(r["student_id"])
    a_bus = lambda sid: any(b <= "BUS-049" or b == "BUS-099" for b in buses.get(sid, ()))
    if kind == "bus":
        fits = [lambda sid: buses.get(sid) == {val} and cls.get(sid) not in B_CLASSES]
    else:
        fits = [(lambda c: (lambda sid: cls.get(sid) == c and not a_bus(sid)))(c) for c in val]
    per = -(-limit // len(fits))
    out, seen = [], set()
    for fit in fits:
        n = 0
        for gid in sorted(fam):
            kids = frozenset(fam[gid])
            if kids not in seen and all(fit(k) for k in kids):
                seen.add(kids)
                out.append(gid)
                n += 1
                if n >= per:
                    break
    return [(f"{prefix}{i + 1:02d}", gid) for i, gid in enumerate(out[:limit])]


_LOGIN_LOCK = __import__("threading").Lock()


def parent_login(guardian_id):
    """Every parent's sign-in: 'p' + guardian number (5 digits), e.g. p00123 (demo PIN 1234). g001…g040 and the pa/pb test logins also work."""
    return f"p{int(guardian_id):05d}"


def parent_userids(con=None):
    """student_id → [{name, relation, phone, logins}] for every parent of the student (all their sign-in user IDs)."""
    logins = {}
    for r in q("SELECT username, guardian_id FROM users WHERE role='guardian' AND guardian_id IS NOT NULL AND active=1 ORDER BY username", (), con):
        logins.setdefault(r["guardian_id"], []).append(r["username"])
    out = {}
    for r in q("""SELECT sg.student_id, g.id, g.name, g.relation, g.phone FROM student_guardians sg JOIN guardians g ON g.id=sg.guardian_id
                  ORDER BY sg.student_id, sg.is_primary DESC, g.id""", (), con):
        ids = sorted(logins.get(r["id"], []), key=lambda u: (not u.startswith("p0"), u))
        out.setdefault(r["student_id"], []).append({"name": r["name"], "relation": r["relation"], "phone": r["phone"], "logins": ids})
    return out


def ensure_demo_logins():
    """Class-teacher and test-parent sign-ins. Runs on every app start (cheap after the first time) so a redeploy picks them up
    without rebuilding the database."""
    if setting("demo_logins_v", "") == "3" or not _LOGIN_LOCK.acquire(blocking=False):
        return                       # done already, or another session is doing it right now
    try:
        if setting("demo_logins_v", "") == "3" or not q1("SELECT COUNT(*) n FROM buses")["n"]:
            return
        _ensure_demo_logins()
    finally:
        _LOGIN_LOCK.release()


def _ensure_demo_logins():
    rows = []
    for c in q("""SELECT c.name, c.section, c.teacher_id, s.name tname FROM classes c JOIN staff s ON s.id=c.teacher_id WHERE c.teacher_id IS NOT NULL"""):
        rows.append((teacher_login(c["name"], c["section"]), pin_hash("1234"), "teacher", c["teacher_id"], None,
                     f"{c['tname']} ({c['name']}-{c['section']})"))
    gnames = {r["id"]: r["name"] for r in q("SELECT id, name FROM guardians", ())}
    for t in TEST_PARENT_SETS:
        for login, gid in test_parents(t):
            rows.append((login, pin_hash("1234"), "guardian", None, gid, f"{gnames.get(gid, '')} (test parent {t})"))
    ph = pin_hash("1234")
    for gid, gname in gnames.items():          # a sign-in for every parent
        rows.append((parent_login(gid), ph, "guardian", None, gid, gname))
    with tx() as c:
        c.executemany("INSERT OR IGNORE INTO users(username, pin_hash, role, staff_id, guardian_id, display_name) VALUES (?,?,?,?,?,?)", rows)
        c.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('demo_logins_v', '3')")


def seed_extras():
    """Fleet, security-extension and integration demo data + their sign-ins (idempotent; skipped on an empty school)."""
    if not q1("SELECT COUNT(*) n FROM buses")["n"]:
        return
    import fleet
    import integrations
    import security_ext
    fleet.seed()
    security_ext.seed()
    integrations.seed_telematics()
    ensure_demo_logins()
    for u, role, name in (("fleetsup", "fleet_supervisor", "Fleet supervisor"), ("srmanager", "senior_manager", "Senior Manager (Facilities)"),
                          ("vp", "vp", "Vice President")):
        x("INSERT OR IGNORE INTO users(username, pin_hash, role, display_name) VALUES (?,?,?,?)", (u, pin_hash("1234"), role, name))


# ------------------------------------------------------------------ common lookups
def bus(bus_id, con=None):
    return q1("SELECT * FROM buses WHERE id=?", (bus_id,), con)


def buses(con=None):
    return q("SELECT * FROM buses ORDER BY bus_no", (), con)


def staff_name(staff_id, con=None):
    r = q1("SELECT name FROM staff WHERE id=?", (staff_id,), con) if staff_id else None
    return r["name"] if r else ""


def stop(stop_id, con=None):
    if stop_id == SCHOOL_STOP:
        lat, lon = school_pos(con)
        return {"id": SCHOOL_STOP, "name": "School", "lat": lat, "lon": lon, "seq": 0}
    return q1("SELECT * FROM stops WHERE id=?", (stop_id,), con)


def setting(key, default=None, con=None):
    try:
        r = q1("SELECT value FROM settings WHERE key=?", (key,), con)
    except sqlite3.OperationalError:
        return default
    return r["value"] if r else default


def set_setting(key, value, con=None):
    x("INSERT OR REPLACE INTO settings(key, value) VALUES (?,?)", (key, str(value)), con)


_POS = {}


def school_pos(con=None):
    if DB_PATH not in _POS:
        _POS[DB_PATH] = (float(setting("school_lat", SCHOOL_LAT, con)), float(setting("school_lon", SCHOOL_LON, con)))
    return _POS[DB_PATH]


def stops_of_bus(bus_id, con=None, trip_no=None):
    """Route stops of a bus; DPS buses have a different route per trip, so pass trip_no where known."""
    if trip_no is not None:
        return q("SELECT * FROM stops WHERE bus_id=? AND (trip_no IS NULL OR trip_no=?) ORDER BY seq", (bus_id, trip_no), con)
    return q("SELECT * FROM stops WHERE bus_id=? ORDER BY trip_no, seq", (bus_id,), con)


def get_user(username, pin):
    u = q1("SELECT * FROM users WHERE username=? AND active=1", (username.strip().lower(),))
    return u if u and u["pin_hash"] == pin_hash(pin.strip()) else None
