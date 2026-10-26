"""Flexitrack 360 extensions: security (bus inspection, blacklist, gate passes, collectors), fleet (job orders, approvals, POs, fuel, services,
documents), integrations (CSMS, Woqod, GPS / telematics, RFID) and the REST API.

Run:  python -m pytest -q tests/test_extensions.py"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
if "SBT_DB" not in os.environ or "dps360_test_" not in os.environ.get("SBT_DB", ""):
    os.environ["SBT_DB"] = os.path.join(tempfile.mkdtemp(prefix="dps360_test_"), "test.db")

import db  # noqa: E402
import campus  # noqa: E402
import engine as E  # noqa: E402
import fleet as F  # noqa: E402
import integrations as I  # noqa: E402
import security_ext as SX  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def data():
    db.init_db()
    yield


def _closed_run(date=None):
    d = date or E.school_days_back(1)[0]
    return db.q1("SELECT r.*, b.bus_no FROM trip_runs r JOIN buses b ON b.id=r.bus_id WHERE r.date=? AND r.status='CLOSED' ORDER BY r.id LIMIT 1", (d,))


# ---------------------------------------------------------------- security
def test_ext01_history_has_inspections_and_missed_alerts():
    d = E.school_days_back(1)[0]
    b = SX.inspections_board(d, datetime.strptime(d + " 23:00", "%Y-%m-%d %H:%M"))
    assert b and sum(1 for x in b if x["state"] in ("DONE", "LATE")) / len(b) > 0.9
    missed = [x for x in b if x["state"] == "MISSED"]
    for m in missed:
        assert db.q1("SELECT id FROM campus_alerts WHERE kind='INSPECTION_MISSED' AND pid=?", (m["run_id"],))


def test_ext02_inspection_needs_rear_tag_and_all_items():
    with db.tx() as c:
        c.execute("DELETE FROM bus_inspections WHERE run_id=(SELECT id FROM trip_runs WHERE status='CLOSED' ORDER BY id LIMIT 1)")
    r = db.q1("SELECT r.*, b.bus_no FROM trip_runs r JOIN buses b ON b.id=r.bus_id WHERE r.status='CLOSED' ORDER BY r.id LIMIT 1")
    ok, m = SX.save_inspection(r["id"], "guard1", "Guard", "WRONG", {k: True for k in SX.INSPECT_ITEMS})
    assert not ok and "Rear-door tag" in m
    ok, m = SX.save_inspection(r["id"], "guard1", "Guard", SX.rear_tag_code(r["bus_no"]), {SX.INSPECT_ITEMS[0]: True})
    assert not ok and "Tick every item" in m
    ok, m = SX.save_inspection(r["id"], "guard1", "Guard", SX.rear_tag_code(r["bus_no"]), {k: True for k in SX.INSPECT_ITEMS})
    assert ok
    ok, m = SX.save_inspection(r["id"], "guard1", "Guard", SX.rear_tag_code(r["bus_no"]), {k: True for k in SX.INSPECT_ITEMS})
    assert not ok and "already" in m


def test_ext03_child_found_raises_high_alert():
    r = db.q1("""SELECT r.*, b.bus_no FROM trip_runs r JOIN buses b ON b.id=r.bus_id WHERE r.status='CLOSED'
                 AND r.id NOT IN (SELECT run_id FROM bus_inspections) ORDER BY r.id LIMIT 1""")
    if not r:
        with db.tx() as c:
            rid = c.execute("SELECT id FROM trip_runs WHERE status='CLOSED' ORDER BY id DESC LIMIT 1").fetchone()[0]
            c.execute("DELETE FROM bus_inspections WHERE run_id=?", (rid,))
        r = db.q1("SELECT r.*, b.bus_no FROM trip_runs r JOIN buses b ON b.id=r.bus_id WHERE r.id=?", (rid,))
    ok, m = SX.save_inspection(r["id"], "guard1", "Guard", SX.rear_tag_code(r["bus_no"]), {}, True, "Grade 1 child asleep, row 9")
    assert ok and "Child found" in m
    assert db.q1("SELECT severity FROM campus_alerts WHERE kind='CHILD_LEFT_ON_BUS' AND pid=?", (r["id"],))["severity"] == "HIGH"


def test_ext04_blacklisted_visitor_refused_with_alert():
    with pytest.raises(campus.BlacklistedVisitor):
        campus.register_visitor("Someone", "55501234", "", "", "Meeting", "Office", "", 5, "guard1")
    assert db.q1("SELECT id FROM campus_alerts WHERE kind='VISITOR_BLACKLISTED' AND date=?", (db.today(),))
    vid, pno = campus.register_visitor("Clean Visitor", "50000001", "29900000001", "", "Meeting", "Office", "", 5, "guard1")
    assert vid and pno


def test_ext05_collector_not_on_list_is_held_until_office_confirms():
    s = db.q1("""SELECT s.* FROM students s WHERE s.transport='PRIVATE' AND s.active=1 AND s.id NOT IN (SELECT student_id FROM early_pickups)
                 ORDER BY s.id LIMIT 1""")
    d = db.today()
    eid = campus.request_early(s["id"], d, "10:30", "Doctor", "Total Stranger", "Friend", "55555555", "28800000000", "g001")
    campus.decide_early(eid, True, "manager")
    code = db.q1("SELECT pass_code FROM early_pickups WHERE id=?", (eid,))["pass_code"]
    when = datetime.strptime(f"{d} 10:35", "%Y-%m-%d %H:%M")
    res = campus.gate_scan(code, 1, "guard1", direction="OUT", when=when)
    assert not res["ok"] and "NOT a parent" in res["message"]
    assert db.q1("SELECT status FROM early_pickups WHERE id=?", (eid,))["status"] == "APPROVED"
    res = campus.gate_scan(code, 1, "guard1", direction="OUT", when=when, override=True)
    assert res["ok"] and db.q1("SELECT status FROM early_pickups WHERE id=?", (eid,))["status"] == "COMPLETED"


def test_ext06_allowed_collector_released_and_ready_message():
    s = db.q1("""SELECT s.* FROM students s WHERE s.transport='PRIVATE' AND s.active=1 AND s.id NOT IN (SELECT student_id FROM early_pickups)
                 AND s.id IN (SELECT student_id FROM recipients WHERE active=1) ORDER BY s.id DESC LIMIT 1""")
    rc = db.q1("SELECT * FROM recipients WHERE student_id=? AND active=1", (s["id"],))
    d = db.today()
    eid = campus.request_early(s["id"], d, "11:00", "Doctor", rc["name"], rc["relation"], rc["phone"], rc["id_number"], "g001")
    campus.decide_early(eid, True, "manager")
    ok, m = SX.mark_ready(eid, "teacher1")
    assert ok and db.q1("SELECT id FROM notifications WHERE student_id=? AND kind='READY_AT_GATE'", (s["id"],))
    code = db.q1("SELECT pass_code FROM early_pickups WHERE id=?", (eid,))["pass_code"]
    res = campus.gate_scan(code, 1, "guard1", direction="OUT", when=datetime.strptime(f"{d} 11:05", "%Y-%m-%d %H:%M"))
    assert res["ok"] and res.get("collector_ok")


def test_ext07_standing_gate_pass_releases_bus_student_and_removes_from_bus():
    s = db.q1("""SELECT s.* FROM students s WHERE s.transport='BUS' AND s.batch=1 AND s.grp='Main' AND s.active=1
                 AND s.id NOT IN (SELECT student_id FROM gate_passes) ORDER BY s.id DESC LIMIT 1""")
    d = db.today()
    wd = SX.DAYS[datetime.strptime(d, "%Y-%m-%d").weekday()]
    code = SX.add_gate_pass(s["id"], [wd], d, d, "Uncle Test", "Uncle", "", "Tuition", "manager")
    _, (d0, _) = campus.student_group_times(s["batch"], s["grp"])
    res = campus.gate_scan(code, 1, "guard1", direction="OUT", when=datetime.strptime(f"{d} {d0}", "%Y-%m-%d %H:%M") + timedelta(minutes=5))
    assert res["ok"] and "GATE PASS" in res["message"]
    t_ = campus.drop_trip(s["batch"], s["grp"])
    assert E.is_absent(s["id"], d, t_, None)


def test_ext08_scan_speed_logged():
    SX.timed("GATE", lambda: {"ok": True})
    sp = SX.scan_speed(db.today(), db.today())
    assert sp["GATE"]["scans"] >= 1 and sp["GATE"]["median_ms"] >= 0


def test_ext09_auto_delay_notice_once():
    bus = db.q1("SELECT id FROM buses WHERE id NOT IN (SELECT bus_id FROM trip_runs WHERE date=? AND trip_no=3) ORDER BY id DESC LIMIT 1", (db.today(),))
    st_ = db.bus(bus["id"])
    w = datetime.strptime(f"{db.today()} {db.TRIPS[3]['window'][0]}", "%Y-%m-%d %H:%M") + timedelta(minutes=35)
    rid, m = E.start_run(bus["id"], db.today(), 3, st_["driver_id"], st_["supervisor_id"], st_["caretaker_id"], 25.2, 51.5, "sup", "supervisor", None,
                         when=w)
    assert rid, m
    late = E.auto_delay_notice(rid)
    assert late >= 35
    n1 = db.q1("SELECT COUNT(*) n FROM notifications WHERE run_id=? AND kind='DELAY'", (rid,))["n"]
    E.auto_delay_notice(rid)
    assert db.q1("SELECT COUNT(*) n FROM notifications WHERE run_id=? AND kind='DELAY'", (rid,))["n"] == n1


# ---------------------------------------------------------------- fleet
def test_ext10_approval_levels_by_amount():
    assert F.levels_for(500) == 1 and F.levels_for(5000) == 2 and F.levels_for(15000) == 3 and F.levels_for(30000) == 4
    db.set_setting("approval_mode", "all")
    assert F.levels_for(10) == 4
    db.set_setting("approval_mode", "amount")


def test_ext11_job_order_full_chain_and_completion():
    bus = db.q1("SELECT id FROM buses ORDER BY id LIMIT 1")
    jid, no = F.create_job_order(bus["id"], "Brakes", "Pads worn", 30000, "fleetsup")
    ok, m = F.decide_job_order(jid, True, "manager", "manager")
    assert not ok and "Supervisor" in m          # wrong level
    for u, r in (("fleetsup", "fleet_supervisor"), ("manager", "manager"), ("srmanager", "senior_manager")):
        ok, m = F.decide_job_order(jid, True, u, r)
        assert ok
    assert db.q1("SELECT status FROM job_orders WHERE id=?", (jid,))["status"] == "PENDING_APPROVAL"
    ok, m = F.decide_job_order(jid, True, "vp", "vp")
    assert ok and m == "Fully approved."
    assert F.send_to_garage(jid, "manager")[0]
    p = db.q1("SELECT id, stock, unit_cost FROM parts WHERE stock>=2 LIMIT 1")
    assert F.issue_part(p["id"], 2, "manager", bus["id"], jid)[0]
    assert db.q1("SELECT stock FROM parts WHERE id=?", (p["id"],))["stock"] == p["stock"] - 2
    assert F.complete_job_order(jid, "manager", 1200, "INV-1")[0]
    j = next(x for x in F.job_orders() if x["id"] == jid)
    assert j["status"] == "COMPLETED" and j["total_cost"] == 1200 + 2 * p["unit_cost"]
    assert len(F.approval_trail("JO", jid)) == 4


def test_ext12_reject_stops_the_chain():
    bus = db.q1("SELECT id FROM buses ORDER BY id DESC LIMIT 1")
    jid, _ = F.create_job_order(bus["id"], "AC", "Not cooling", 800, "fleetsup")
    ok, m = F.decide_job_order(jid, False, "fleetsup", "fleet_supervisor", "Use warranty")
    assert ok and db.q1("SELECT status FROM job_orders WHERE id=?", (jid,))["status"] == "REJECTED"


def test_ext13_po_approval_and_receipt_adds_stock():
    p = db.q1("SELECT id, stock, supplier_id FROM parts LIMIT 1")
    pid, no = F.create_po(p["supplier_id"], [(p["id"], 5, 10.0)], "manager")
    assert F.decide_po(pid, True, "fleetsup", "fleet_supervisor")[0]
    assert F.send_po(pid, "manager")[0]
    assert F.receive_po(pid, "manager")[0]
    assert db.q1("SELECT stock FROM parts WHERE id=?", (p["id"],))["stock"] == p["stock"] + 5


def test_ext14_fuel_flags():
    bus = db.q1("SELECT id FROM buses ORDER BY id LIMIT 1")
    km = F.odometer(bus["id"])
    _, flags = F.add_fuel(bus["id"], 260, km + 50, "manager")
    assert any("tank" in f for f in flags)
    _, flags = F.add_fuel(bus["id"], 100, km - 500, "manager")
    assert any("Odometer lower" in f for f in flags)
    assert F.fuel_efficiency((datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"), db.today())


def test_ext15_service_board_and_service_job_updates_plan():
    sb = F.service_board()
    assert any(s["State"] == "OVERDUE" for s in sb)
    s = next(s for s in sb if s["State"] == "OVERDUE")
    jid, _ = F.create_job_order(s["bus_id"], "Preventive service", s["Service"], 100, "fleetsup", plan_id=s["plan_id"])
    F.decide_job_order(jid, True, "fleetsup", "fleet_supervisor")
    F.complete_job_order(jid, "manager", 100, "INV-S", F.odometer(s["bus_id"]))
    s2 = next(x for x in F.service_board() if x["bus_id"] == s["bus_id"] and x["plan_id"] == s["plan_id"])
    assert s2["State"] == "OK"


def test_ext16_documents_and_renewal():
    docs = F.documents("BUS")
    assert any(d["state"] == "EXPIRED" for d in docs) and any(d["state"] == "EXPIRING" for d in docs)
    d = next(d for d in docs if d["state"] == "EXPIRED")
    F.save_document("BUS", d["owner_id"], d["doc_type"], "NEW-1", db.today(), "2030-01-01", "manager", "scan.pdf", "application/pdf", b"%PDF-1.4")
    now = [x for x in F.documents("BUS", d["owner_id"]) if x["doc_type"] == d["doc_type"]]
    assert len(now) == 1 and now[0]["state"] == "VALID" and now[0]["has_file"]


def test_ext17_odometer_consistent_with_checks():
    v = db.q1("SELECT bus_id, odometer, date FROM vehicle_checks WHERE odometer IS NOT NULL ORDER BY id DESC LIMIT 1")
    assert v and v["odometer"] == F.odo_model(v["bus_id"], v["date"])


# ---------------------------------------------------------------- integrations + API
def test_ext18_csms_diff_and_apply():
    s = db.q1("SELECT code, name FROM students ORDER BY id LIMIT 1")
    d = I.csms_diff("students", [{"Admission No": s["code"], "Student Name": s["name"] + " X"}, {"admission_no": "CSMS0001", "name": "New Child",
                                                                                                "grade": "3", "section": "B", "transport": "Bus"}])
    assert len(d["new"]) == 1 and len(d["changed"]) == 1 and d["new"][0]["class_name"] == "Grade 3"
    r = I.csms_apply(d, "admin")
    assert r == {"new": 1, "updated": 1, "deactivated": 0}
    assert db.q1("SELECT name FROM students WHERE code=?", (s["code"],))["name"].endswith(" X")


def test_ext19_woqod_import_matches_and_dedupes():
    import pandas as pd
    df = pd.DataFrame(I.woqod_template())
    n, flagged, prob = I.woqod_import(df, "admin")
    assert n == len(df)
    n2, _, _ = I.woqod_import(df, "admin")
    assert n2 == 0


def test_ext20_gps_speeding_and_harsh_events():
    n, a = I.ingest_positions([{"bus": "BUS-010", "lat": 25.2, "lon": 51.5, "speed_kmh": 99}, {"bus": "BUS-010", "lat": 25.2, "lon": 51.5, "speed_kmh": 101}])
    assert n == 2 and a == 1          # one alert per 10 minutes
    assert I.ingest_events([{"bus": "BUS-010", "kind": "HARSH_BRAKE", "value": 0.6}, {"bus": "BUS-010", "kind": "NONSENSE"}]) == 1
    assert I.driver_behaviour(db.today(), db.today())


def test_ext21_rfid_reader_gate():
    I.register_reader("GATE1-R", "GATE", gate_id=1)
    res = I.rfid_scan("GATE1-R", "99887766")
    assert res["ok"] is False and "Unknown card" in res["message"]
    assert "Unknown RFID reader" in I.rfid_scan("NOPE", "1")["message"]


def test_ext22_rest_api_auth_and_endpoints():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    import api
    c = TestClient(api.app)
    k = I.create_api_key("pytest", I.SCOPES, "admin")
    ro = I.create_api_key("pytest-ro", ["read"], "admin")
    assert c.get("/v1/buses", headers={"X-API-Key": "nope"}).status_code == 401
    assert c.post("/v1/gps/positions", headers={"X-API-Key": ro}, json=[]).status_code == 403
    assert len(c.get("/v1/buses", headers={"X-API-Key": ro}).json()) == 100
    r = c.post("/v1/csms/sync", headers={"X-API-Key": k}, json={"students": [{"student_id": "ZZ1", "name": "Dry Run"}]})
    assert r.json()["dry_run"] and not db.q1("SELECT id FROM students WHERE code='ZZ1'")
