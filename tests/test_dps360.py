"""DPS 360 automated function tests (pytest).

Run from the app folder:  python -m pytest -q tests/test_dps360.py
A fresh demo database is generated once for the session (about 20 s) in a temp folder."""
import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
_TMP = tempfile.mkdtemp(prefix="dps360_test_")
os.environ["SBT_DB"] = os.path.join(_TMP, "test.db")

import db  # noqa: E402
import campus  # noqa: E402
import engine as E  # noqa: E402
import dps_data  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def data():
    db.init_db()
    yield


D = lambda: db.today()
AT = lambda hm: datetime.strptime(f"{db.today()} {hm}", "%Y-%m-%d %H:%M")


def private_student(grp="Main", batch=1):
    return db.q1("""SELECT s.*, b.code FROM students s JOIN badges b ON b.student_id=s.id AND b.status='ACTIVE'
                    WHERE s.transport='PRIVATE' AND s.grp=? AND s.batch=? AND s.id NOT IN (SELECT student_id FROM early_pickups)
                    ORDER BY s.id DESC LIMIT 1""", (grp, batch))


# ---------------------------------------------------------------- data / setup
def test_fn01_scale_and_sessions():
    assert db.q1("SELECT COUNT(*) n FROM students")["n"] > 4000
    assert db.q1("SELECT COUNT(*) n FROM buses")["n"] == 100
    buses = {r["trip_no"]: r["b"] for r in db.q("SELECT trip_no, COUNT(DISTINCT bus_id) b FROM trip_assign GROUP BY trip_no")}
    assert buses == {1: 98, 2: 35, 3: 98, 4: 28, 5: 16}


def test_fn02_bus_per_session_can_differ():
    n = db.q1("""SELECT COUNT(*) n FROM trip_assign a JOIN trip_assign b ON a.student_id=b.student_id AND a.trip_no=1 AND b.trip_no=3
                 WHERE a.bus_id<>b.bus_id""")["n"]
    assert n > 0


def test_fn03_every_bus_student_has_assignment_for_each_of_their_trips():
    for s in db.q("SELECT * FROM students WHERE transport='BUS' LIMIT 200"):
        for t in (campus.pickup_trip(s["batch"], s["grp"]), campus.drop_trip(s["batch"], s["grp"])):
            assert db.q1("SELECT 1 x FROM trip_assign WHERE student_id=? AND trip_no=?", (s["id"], t))


def test_fn04_stops_belong_to_the_bus_of_the_session():
    bad = db.q1("""SELECT COUNT(*) n FROM trip_assign a JOIN stops s ON s.id=a.stop_id WHERE s.bus_id<>a.bus_id OR s.trip_no<>a.trip_no""")["n"]
    assert bad == 0


def test_fn05_capacity_respected():
    over = db.q("""SELECT a.trip_no, a.bus_id, COUNT(*) n, b.capacity FROM trip_assign a JOIN buses b ON b.id=a.bus_id
                   GROUP BY a.trip_no, a.bus_id HAVING n > b.capacity""")
    assert over == []


# ---------------------------------------------------------------- manifests
def test_fn06_manifest_uses_session_bus():
    row = db.q1("SELECT student_id, bus_id FROM trip_assign WHERE trip_no=3 LIMIT 1")
    ids = [ln["student_id"] for ln in E.manifest_for(row["bus_id"], D(), 3)]
    assert row["student_id"] in ids


def test_fn07_declared_absence_marks_manifest_absent():
    row = db.q1("SELECT student_id, bus_id FROM trip_assign WHERE trip_no=5 AND student_id NOT IN (SELECT student_id FROM absences) LIMIT 1")
    E.declare_absence(row["student_id"], D(), None, "Sick", "test", "GUARDIAN")
    ln = next(ln for ln in E.manifest_for(row["bus_id"], D(), 5) if ln["student_id"] == row["student_id"])
    assert ln["status"] == "ABSENT_DECLARED"


# ---------------------------------------------------------------- gate scans
def test_fn08_unknown_card_rejected():
    r = campus.gate_scan("00000000", 1, "t")
    assert not r["ok"] and "Unknown" in r["message"]


def test_fn09_revoked_card_rejected_and_alerted():
    code = db.q1("SELECT code, student_id FROM badges WHERE status='REVOKED' LIMIT 1")
    if not code:
        pytest.skip("no revoked card in data")
    r = campus.gate_scan(code["code"], 1, "t")
    assert not r["ok"] and "revoked" in r["message"].lower()
    assert db.q1("SELECT id FROM campus_alerts WHERE kind='CARD_REJECTED' AND student_id=?", (code["student_id"],))


def test_fn10_exit_in_school_hours_without_pass_is_stopped():
    s = private_student()
    r = campus.gate_scan(s["code"], 1, "t", direction="OUT", when=AT("10:00"))
    assert not r["ok"] and r["message"].startswith("STOP")
    assert db.q1("SELECT id FROM campus_alerts WHERE kind='UNAUTHORIZED_EXIT' AND student_id=?", (s["id"],))


def test_fn11_exit_after_dismissal_allowed():
    s = private_student()
    r = campus.gate_scan(s["code"], 1, "t", direction="OUT", when=AT("14:35"))
    assert r["ok"] and r["dir"] == "OUT"


def test_fn12_late_arrival_flagged():
    s = db.q1("""SELECT s.*, b.code FROM students s JOIN badges b ON b.student_id=s.id AND b.status='ACTIVE' WHERE s.transport='PRIVATE'
                 AND s.batch=2 AND s.id NOT IN (SELECT pid FROM campus_moves WHERE ptype='STUDENT' AND date=?) LIMIT 1""", (D(),))
    if not s:
        pytest.skip("all afternoon students already in")
    r = campus.gate_scan(s["code"], 1, "t", direction="IN", when=AT("12:50"))
    assert r["ok"] and r["level"] == "warn" and "LATE" in r["message"]


def test_fn13_on_time_arrival_ok():
    s = db.q1("""SELECT s.*, b.code FROM students s JOIN badges b ON b.student_id=s.id AND b.status='ACTIVE' WHERE s.transport='PRIVATE'
                 AND s.batch=2 AND s.id NOT IN (SELECT pid FROM campus_moves WHERE ptype='STUDENT' AND date=?) ORDER BY s.id DESC LIMIT 1""", (D(),))
    if not s:
        pytest.skip("all afternoon students already in")
    r = campus.gate_scan(s["code"], 1, "t", direction="IN", when=AT("12:10"))
    assert r["ok"] and "LATE" not in r["message"]


def test_fn14_repeat_in_scan_is_warning():
    s = db.q1("""SELECT s.*, b.code FROM students s JOIN badges b ON b.student_id=s.id AND b.status='ACTIVE' WHERE s.transport='PRIVATE'
                 AND s.batch=2 ORDER BY s.id LIMIT 1 OFFSET 3""")
    campus.gate_scan(s["code"], 1, "t", direction="IN", when=AT("12:00"))
    r = campus.gate_scan(s["code"], 1, "t", direction="IN", when=AT("12:01"))
    assert r["level"] == "warn" and "already on campus" in r["message"]


def test_fn15_staff_card_in_out():
    c = db.q1("SELECT card_no FROM staff WHERE role='teacher' LIMIT 1 OFFSET 11")["card_no"]
    assert campus.gate_scan(c, 4, "t", direction="IN")["message"].startswith("STAFF IN")
    assert campus.gate_scan(c, 4, "t", direction="OUT")["message"].startswith("STAFF OUT")


# ---------------------------------------------------------------- visitors
def test_fn16_visitor_register_and_checkout():
    vid, pno = campus.register_visitor("Pytest Visitor", "+974 3999 0001", "1", "X", "Delivery", "Accounts office", "", 5, "t", expected_min=15)
    assert db.q1("SELECT status FROM visitors WHERE id=?", (vid,))["status"] == "ON_CAMPUS"
    r = campus.checkout_visitor(vid, 5, "t", when=db.now() + timedelta(minutes=10))
    assert r["ok"] and r["dir"] == "OUT"
    assert db.q1("SELECT status FROM visitors WHERE id=?", (vid,))["status"] == "LEFT"


def test_fn17_visitor_pass_cannot_be_reused():
    vid, pno = campus.register_visitor("Pytest Visitor 2", "+974 3999 0002", "1", "X", "Delivery", "Accounts office", "", 5, "t")
    campus.gate_scan(pno, 5, "t", direction="OUT")
    r = campus.gate_scan(pno, 5, "t", direction="OUT")
    assert not r["ok"] and "already checked out" in r["message"]


def test_fn18_overstay_alert():
    vid, pno = campus.register_visitor("Pytest Overstay", "+974 3999 0003", "1", "X", "Delivery", "Accounts office", "", 5, "t", expected_min=15,
                                       when=db.now() - timedelta(minutes=60))
    campus.check_overstays(db.now())
    assert db.q1("SELECT id FROM campus_alerts WHERE kind='VISITOR_OVERSTAY' AND pid=?", (vid,))


# ---------------------------------------------------------------- early pickup
def test_fn19_early_pickup_full_flow():
    s = db.q1("SELECT * FROM students WHERE transport='BUS' AND grp='Main' AND id NOT IN (SELECT student_id FROM early_pickups) LIMIT 1")
    eid = campus.request_early(s["id"], D(), "11:00", "Doctor", "Pytest Father", "Father", "+974 5", "284", "g")
    ok, m = campus.decide_early(eid, True, "t")
    assert ok and "pass" in m
    assert E.is_absent(s["id"], D(), 3, None)                       # off the 2:00 pm home bus
    pc = db.q1("SELECT pass_code FROM early_pickups WHERE id=?", (eid,))["pass_code"]
    r = campus.gate_scan(pc, 1, "t", direction="OUT", when=AT("11:05"))
    assert r["ok"] and r.get("early")
    assert db.q1("SELECT status FROM early_pickups WHERE id=?", (eid,))["status"] == "COMPLETED"


def test_fn20_early_pickup_reject():
    s = db.q1("SELECT * FROM students WHERE transport='PRIVATE' AND id NOT IN (SELECT student_id FROM early_pickups) LIMIT 1")
    eid = campus.request_early(s["id"], D(), "10:00", "x", "Y", "Uncle", "1", "2", "g")
    ok, _ = campus.decide_early(eid, False, "t", note="No")
    assert ok and db.q1("SELECT status FROM early_pickups WHERE id=?", (eid,))["status"] == "REJECTED"
    assert campus.decide_early(eid, True, "t")[0] is False          # cannot decide twice


# ---------------------------------------------------------------- attendance
def _open_class():
    c = db.q1("""SELECT c.* FROM classes c WHERE c.id NOT IN (SELECT class_id FROM att_submissions WHERE date=?) AND c.shift=1 LIMIT 1""", (D(),))
    if not c:
        c = db.q1("SELECT * FROM classes WHERE shift=1 LIMIT 1")
    return c


def test_fn21_roster_prefill_sources():
    c = _open_class()
    _, roster = campus.class_roster(c["id"], D())
    assert roster and all(r["suggested"] in ("PRESENT", "LATE", "ABSENT") for r in roster)
    assert all(r["source"] for r in roster)


def test_fn22_absent_after_scan_raises_alert_and_present_no_alert():
    c = _open_class()
    _, roster = campus.class_roster(c["id"], D())
    arrived = [r for r in roster if r["arrived"]]
    if not arrived:
        pytest.skip("nobody arrived yet at this time of day")
    marks = {r["student_id"]: r["suggested"] for r in roster}
    marks[arrived[0]["student_id"]] = "ABSENT"
    counts, notified, alerted = campus.submit_attendance(c["id"], D(), marks, "Pytest Teacher")
    assert alerted >= 1
    assert db.q1("SELECT id FROM att_submissions WHERE date=? AND class_id=?", (D(), c["id"]))


def test_fn23_absent_no_scan_notifies_parents_and_removes_from_bus():
    s = db.q1("""SELECT s.* FROM students s WHERE s.transport='BUS' AND s.grp='Main' AND s.id NOT IN (SELECT pid FROM campus_moves WHERE ptype='STUDENT'
                 AND date=?) AND s.id NOT IN (SELECT student_id FROM absences WHERE date=?) LIMIT 1""", (D(), D()))
    if not s:
        pytest.skip("every bus student already scanned today")
    c = db.q1("SELECT * FROM classes WHERE name=? AND section=?", (s["class_name"], s["section"]))
    _, roster = campus.class_roster(c["id"], D())
    marks = {r["student_id"]: r["suggested"] for r in roster}
    marks[s["id"]] = "ABSENT"
    before = db.q1("SELECT COUNT(*) n FROM notifications WHERE student_id=? AND kind='ABSENT_ALERT'", (s["id"],))["n"]
    campus.submit_attendance(c["id"], D(), marks, "Pytest Teacher")
    assert db.q1("SELECT COUNT(*) n FROM notifications WHERE student_id=? AND kind='ABSENT_ALERT'", (s["id"],))["n"] > before
    assert E.is_absent(s["id"], D(), 3, None)


def test_fn24_resubmit_overwrites():
    c = _open_class()
    _, roster = campus.class_roster(c["id"], D())
    marks = {r["student_id"]: "PRESENT" for r in roster}
    campus.submit_attendance(c["id"], D(), marks, "T1", notify_parents=False)
    marks = {r["student_id"]: "LATE" for r in roster}
    campus.submit_attendance(c["id"], D(), marks, "T2", notify_parents=False)
    sub = db.q1("SELECT * FROM att_submissions WHERE date=? AND class_id=?", (D(), c["id"]))
    assert sub["late"] == len(roster) and sub["teacher"] == "T2"


# ---------------------------------------------------------------- vehicle checks
def _ans(defect=None):
    a = {i: "OK" for _, i, _c in campus.PRE_CHECKS}
    if defect:
        a[defect] = "DEFECT"
    return a


def test_fn25_precheck_required():
    bus = db.q1("SELECT id FROM buses WHERE bus_no='BUS-100'")["id"]
    ok, msg = campus.precheck_status(bus, D())
    assert not ok and "not done" in msg


def test_fn26_minor_defect_does_not_block():
    bus = db.q1("SELECT id FROM buses WHERE bus_no='BUS-100'")["id"]
    _, st = campus.save_check(bus, D(), 1, "PRE", _ans("Mirrors clean and adjusted"), "u", "n")
    assert st == "DEFECT" and campus.precheck_status(bus, D())[0]


def test_fn27_critical_defect_blocks_start_until_cleared():
    bus = db.q1("SELECT id FROM buses WHERE bus_no='BUS-099'")["id"]
    cid, st = campus.save_check(bus, D(), 1, "PRE", _ans("Brakes and parking brake work"), "u", "n")
    assert st == "BLOCKED"
    b = db.bus(bus)
    rid, msg = E.start_run(bus, D(), 4, b["driver_id"], b["supervisor_id"], b["caretaker_id"], 0, 0, "u", "supervisor", None, require_check=True)
    assert rid is None and "blocked" in msg.lower()
    campus.clear_check(cid, "mgr", "fixed")
    assert campus.precheck_status(bus, D())[0]


# ---------------------------------------------------------------- assignment board
def test_fn28_move_today_and_permanent():
    loads = campus.bus_loads(5, D())
    a, b = loads[0], loads[1]
    sid = E.manifest_for(a["bus_id"], D(), 5)[0]["student_id"]
    assert campus.move_students([sid], 5, D(), b["bus_id"], "m", "manager") == 1
    assert sid in [x["student_id"] for x in E.manifest_for(b["bus_id"], D(), 5)]
    sid2 = E.manifest_for(a["bus_id"], D(), 5)[0]["student_id"]
    campus.move_students([sid2], 5, D(), b["bus_id"], "m", "manager", permanent=True)
    assert db.q1("SELECT bus_id FROM trip_assign WHERE student_id=? AND trip_no=5", (sid2,))["bus_id"] == b["bus_id"]


def test_fn29_merge_routes_empties_source():
    loads = campus.bus_loads(4, D())
    a, b = loads[2], loads[3]
    campus.merge_routes(a["bus_id"], b["bus_id"], 4, D(), "m", "manager")
    assert E.manifest_for(a["bus_id"], D(), 4) == []


def test_fn30_swap_students():
    loads = campus.bus_loads(2, D())
    a, b = loads[0], loads[1]
    sa = E.manifest_for(a["bus_id"], D(), 2)[0]["student_id"]
    sb = E.manifest_for(b["bus_id"], D(), 2)[0]["student_id"]
    campus.swap_students(sa, sb, 2, D(), "m", "manager")
    assert sa in [x["student_id"] for x in E.manifest_for(b["bus_id"], D(), 2)]
    assert sb in [x["student_id"] for x in E.manifest_for(a["bus_id"], D(), 2)]


# ---------------------------------------------------------------- presence / timeline / patrol / sim
def test_fn31_presence_states_valid():
    p = campus.presence()
    assert set(p.values()) <= {"ON_CAMPUS", "ON_BUS", "LEFT", "ABSENT", "NOT_ARRIVED", "UNACCOUNTED", "NOT_SEEN"}
    assert len(p) == db.q1("SELECT COUNT(*) n FROM students WHERE active=1")["n"]


def test_fn32_timeline_sorted():
    sid = db.q1("SELECT student_id FROM manifest LIMIT 1")["student_id"]
    day = db.q1("SELECT r.date FROM manifest m JOIN trip_runs r ON r.id=m.run_id WHERE m.student_id=? LIMIT 1", (sid,))["date"]
    tl = campus.timeline(sid, day)
    assert tl and [e["ts"] for e in tl] == sorted(e["ts"] for e in tl)


def test_fn33_patrol_import():
    import pandas as pd
    df = pd.DataFrame([{"Plan": "Night", "Checkpoint": "Gate 1", "Patrolman": "G", "Planned time": f"{D()} 22:00:00", "Arrival time": f"{D()} 22:02:00",
                        "Status": "Normal"}, {"Plan": "Night", "Checkpoint": "Gate 2", "Patrolman": "G", "Planned time": f"{D()} 22:05:00",
                                              "Arrival time": None, "Status": "Omitted"}])
    n, _ = campus.import_patrol(df, source="pytest")
    assert n == 2
    rows = db.q("SELECT status FROM patrol_rounds WHERE source='pytest' ORDER BY id")
    assert [r["status"] for r in rows] == ["ARRIVED", "OMITTED"]


def test_fn34_patrol_import_rejects_bad_file():
    import pandas as pd
    n, msg = campus.import_patrol(pd.DataFrame([{"foo": 1}]))
    assert n == 0 and "checkpoint" in msg.lower()


def test_fn35_tick_is_idempotent():
    dps_data.tick()
    assert dps_data.tick() == 0


def test_fn36_alert_ack_and_resolve():
    a = db.q1("SELECT id FROM campus_alerts WHERE status='OPEN' LIMIT 1")
    campus.ack_alert(a["id"], "guard")
    assert db.q1("SELECT status FROM campus_alerts WHERE id=?", (a["id"],))["status"] == "ACKNOWLEDGED"
    campus.resolve_alert(a["id"], "guard", "found")
    r = db.q1("SELECT status, resolution FROM campus_alerts WHERE id=?", (a["id"],))
    assert r["status"] == "RESOLVED" and r["resolution"] == "found"


def test_fn37_login_and_wrong_pin():
    assert db.get_user("security", "1234")["role"] == "security"
    assert db.get_user("teacher1", "1234")["role"] == "teacher"
    assert db.get_user("security", "9999") is None


def test_fn38_avatar_deterministic():
    assert campus.avatar_svg(5, "F") == campus.avatar_svg(5, "F")
    assert campus.avatar_svg(5, "F") != campus.avatar_svg(6, "F")


def test_fn39_patrol_import_us_dates_night_shift():
    import pandas as pd
    """JWM export with US dates (10/20/2026 18:00, 10/21/2026 0:00): all rows import, night rows count for the evening, re-upload is not doubled."""
    rows = []
    for d, h in [("10/20/2026", 18), ("10/20/2026", 22), ("10/21/2026", 0), ("10/21/2026", 6)]:
        for g in (1, 2):
            rows.append({"Plan": "Night patrol", "Checkpoint": f"Gate {g}", "Patrolman": "Ram Bahadur Thapa", "Planned time": f"{d} {h}:00",
                         "Arrival time": f"{d} {h}:0{g}", "Status": "Normal", "Site": "DPS Main campus"})
    df = pd.DataFrame(rows).astype(str)
    n, msg = campus.import_patrol(df, source="pytest-us")
    assert n == 8, msg
    got = db.q("SELECT date, planned_ts, actual_ts, status FROM patrol_rounds WHERE source='pytest-us' ORDER BY planned_ts")
    assert {r["date"] for r in got} == {"2026-10-20"}
    assert got[0]["planned_ts"] == "2026-10-20 18:00:00" and got[-1]["actual_ts"] == "2026-10-21 06:02:00"
    assert all(r["status"] == "ARRIVED" for r in got)
    n2, msg2 = campus.import_patrol(df, source="pytest-us")
    assert n2 == 0 and "already" in msg2
