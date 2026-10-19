"""Test resets: put a student, class, bus, trip, visitor, early pickup, alert or the whole day back to a clean state,
so a tester who gets stuck can start again. Every reset is written to the audit log."""
import json

import db
from db import q, q1


def _audit(c, user, action, entity, eid, details):
    db.audit(user, "admin", action, entity, eid, details, c)


# ------------------------------------------------------------------ student
def student_preview(student_id, date, con=None):
    runs = [r["id"] for r in q("SELECT id FROM trip_runs WHERE date=?", (date,), con)] or [-1]
    ph = ",".join("?" * len(runs))
    return {
        "Bus trip lines": q1(f"SELECT COUNT(*) n FROM manifest WHERE student_id=? AND run_id IN ({ph}) AND status<>'EXPECTED'", [student_id] + runs, con)["n"],
        "Bus scans": q1(f"SELECT COUNT(*) n FROM events WHERE student_id=? AND run_id IN ({ph})", [student_id] + runs, con)["n"],
        "Gate scans": q1("SELECT COUNT(*) n FROM gate_scans WHERE ptype='STUDENT' AND pid=? AND date=?", (student_id, date), con)["n"],
        "Class attendance": q1("SELECT COUNT(*) n FROM attendance WHERE student_id=? AND date=?", (student_id, date), con)["n"],
        "Absences": q1("SELECT COUNT(*) n FROM absences WHERE student_id=? AND date=?", (student_id, date), con)["n"],
        "Change requests": q1("SELECT COUNT(*) n FROM change_requests WHERE student_id=? AND date=?", (student_id, date), con)["n"],
        "Early pickups": q1("SELECT COUNT(*) n FROM early_pickups WHERE student_id=? AND date=?", (student_id, date), con)["n"],
        "Alerts": q1("SELECT COUNT(*) n FROM campus_alerts WHERE student_id=? AND date=?", (student_id, date), con)["n"],
        "Parent messages": q1("SELECT COUNT(*) n FROM notifications WHERE student_id=? AND ts LIKE ?", (student_id, date + "%"), con)["n"],
    }


def reset_student(student_id, date, user, parts=None):
    """Clean one student's day: bus lines back to Expected (scans removed), gate scans, attendance, absences, requests,
    early pickups, alerts and parent messages removed. parts = subset of the preview keys (default all)."""
    parts = set(parts or student_preview(student_id, date).keys())
    with db.tx() as c:
        runs = [r["id"] for r in q("SELECT id FROM trip_runs WHERE date=?", (date,), c)] or [-1]
        ph = ",".join("?" * len(runs))
        if "Bus scans" in parts or "Bus trip lines" in parts:
            c.execute(f"DELETE FROM events WHERE student_id=? AND run_id IN ({ph})", [student_id] + runs)
            # lines that the office added (wrong-bus transfers) are removed; planned lines go back to Expected
            c.execute(f"DELETE FROM manifest WHERE student_id=? AND run_id IN ({ph}) AND status='TRANSFER_PENDING'", [student_id] + runs)
            c.execute(f"""UPDATE manifest SET status='EXPECTED', entry_ts=NULL, entry_lat=NULL, entry_lon=NULL, exit_ts=NULL, exit_lat=NULL,
                          exit_lon=NULL, handover=NULL, recipient_name=NULL, recipient_relation=NULL, received_by=NULL, note=NULL
                          WHERE student_id=? AND run_id IN ({ph})""", [student_id] + runs)
        if "Gate scans" in parts:
            c.execute("DELETE FROM gate_scans WHERE ptype='STUDENT' AND pid=? AND date=?", (student_id, date))
        if "Class attendance" in parts:
            c.execute("DELETE FROM attendance WHERE student_id=? AND date=?", (student_id, date))
        if "Absences" in parts:
            c.execute("DELETE FROM absences WHERE student_id=? AND date=?", (student_id, date))
        if "Change requests" in parts:
            c.execute("DELETE FROM change_requests WHERE student_id=? AND date=?", (student_id, date))
        if "Early pickups" in parts:
            c.execute("DELETE FROM early_pickups WHERE student_id=? AND date=?", (student_id, date))
        if "Alerts" in parts:
            c.execute("DELETE FROM campus_alerts WHERE student_id=? AND date=?", (student_id, date))
        if "Parent messages" in parts:
            c.execute("DELETE FROM notifications WHERE student_id=? AND ts LIKE ?", (student_id, date + "%"))
        _audit(c, user, "TEST_RESET_STUDENT", "student", student_id, f"{date}: {', '.join(sorted(parts))}")
    return len(parts)


# ------------------------------------------------------------------ class attendance
def reset_class_attendance(class_id, date, user):
    with db.tx() as c:
        cl = q1("SELECT * FROM classes WHERE id=?", (class_id,), c)
        ids = [r["id"] for r in q("SELECT id FROM students WHERE class_name=? AND section=?", (cl["name"], cl["section"]), c)] or [-1]
        ph = ",".join("?" * len(ids))
        n = c.execute("DELETE FROM attendance WHERE date=? AND class_id=?", (date, class_id)).rowcount
        c.execute("DELETE FROM att_submissions WHERE date=? AND class_id=?", (date, class_id))
        c.execute(f"""DELETE FROM campus_alerts WHERE date=? AND kind IN ('ABSENT_ON_CAMPUS','ABSENT_NOT_ON_CAMPUS','PRESENT_NO_SCAN')
                      AND student_id IN ({ph})""", [date] + ids)
        c.execute(f"DELETE FROM notifications WHERE kind='ABSENT_ALERT' AND ts LIKE ? AND student_id IN ({ph})", [date + "%"] + ids)
        c.execute(f"DELETE FROM absences WHERE date=? AND source='TEACHER' AND student_id IN ({ph})", [date] + ids)
        _audit(c, user, "TEST_RESET_CLASS", "class", class_id, f"{date}: {cl['name']}-{cl['section']} attendance ({n} marks)")
    return n


# ------------------------------------------------------------------ bus
def reset_bus_day(bus_id, date, user, checks=True, messages=True):
    """All trips of the bus (incl. closed), its vehicle checks, office messages and SOS for the day; bus kept for manual testing."""
    import engine as E
    n, _, _ = E.reset_trips(date, user, "admin", bus_id=bus_id, include_closed=True)
    with db.tx() as c:
        nc = 0
        if checks:
            nc = c.execute("DELETE FROM vehicle_checks WHERE bus_id=? AND date=?", (bus_id, date)).rowcount
            c.execute("DELETE FROM campus_alerts WHERE kind='VEHICLE_DEFECT' AND ptype='BUS' AND pid=? AND date=?", (bus_id, date))
        if messages:
            try:
                c.execute("DELETE FROM bus_messages WHERE bus_id=? AND ts LIKE ?", (bus_id, date + "%"))
            except Exception:
                pass
            c.execute("DELETE FROM incidents WHERE bus_id=? AND ts LIKE ?", (bus_id, date + "%"))
        _audit(c, user, "TEST_RESET_BUS", "bus", bus_id, f"{date}: {n} trip(s), {nc} vehicle check(s)")
    return n, nc


def reset_vehicle_checks(bus_id, date, user):
    with db.tx() as c:
        n = c.execute("DELETE FROM vehicle_checks WHERE bus_id=? AND date=?", (bus_id, date)).rowcount
        c.execute("DELETE FROM campus_alerts WHERE kind='VEHICLE_DEFECT' AND ptype='BUS' AND pid=? AND date=?", (bus_id, date))
        _audit(c, user, "TEST_RESET_VEHICLE_CHECK", "bus", bus_id, f"{date}: {n} check(s)")
    return n


# ------------------------------------------------------------------ staff, visitors, early pickups, alerts, assignment
def reset_staff_day(staff_id, date, user):
    with db.tx() as c:
        n = c.execute("DELETE FROM gate_scans WHERE ptype='STAFF' AND pid=? AND date=?", (staff_id, date)).rowcount
        _audit(c, user, "TEST_RESET_STAFF", "staff", staff_id, f"{date}: {n} gate scan(s)")
    return n


def reset_visitors(date, user, only_open=False):
    with db.tx() as c:
        cond = " AND status<>'LEFT'" if only_open else ""
        ids = [r["id"] for r in q("SELECT id FROM visitors WHERE date=?" + cond, (date,), c)] or [-1]
        ph = ",".join("?" * len(ids))
        c.execute(f"DELETE FROM gate_scans WHERE ptype='VISITOR' AND pid IN ({ph})", ids)
        c.execute(f"DELETE FROM campus_alerts WHERE ptype='VISITOR' AND pid IN ({ph})", ids)
        n = c.execute(f"DELETE FROM visitors WHERE id IN ({ph})", ids).rowcount
        _audit(c, user, "TEST_RESET_VISITORS", "visitors", date, f"{n} visitor(s){' still on campus' if only_open else ''}")
    return n


def reset_early_pickups(date, user, student_id=None):
    with db.tx() as c:
        rows = q("SELECT * FROM early_pickups WHERE date=?" + (" AND student_id=?" if student_id else ""), (date, student_id) if student_id else (date,), c)
        for e in rows:
            c.execute("DELETE FROM absences WHERE student_id=? AND date=? AND source='EARLY_PICKUP'", (e["student_id"], date))
            c.execute("UPDATE manifest SET status='EXPECTED' WHERE student_id=? AND status='ABSENT_DECLARED' AND run_id IN "
                      "(SELECT id FROM trip_runs WHERE date=? AND trip_no=?)", (e["student_id"], date, e["trip_no"]))
            c.execute("DELETE FROM gate_scans WHERE ptype='STUDENT' AND pid=? AND date=? AND method='EARLY_PICKUP'", (e["student_id"], date))
            c.execute("DELETE FROM campus_alerts WHERE kind='EARLY_PICKUP' AND student_id=? AND date=?", (e["student_id"], date))
            c.execute("DELETE FROM notifications WHERE kind='EARLY_PICKUP' AND student_id=? AND ts LIKE ?", (e["student_id"], date + "%"))
            c.execute("DELETE FROM early_pickups WHERE id=?", (e["id"],))
        _audit(c, user, "TEST_RESET_EARLY_PICKUP", "early_pickups", date, f"{len(rows)} request(s)")
    return len(rows)


def reset_alerts(date, user, mode="reopen", kind=None):
    """mode 'reopen' = resolved / acknowledged alerts back to OPEN; 'delete' = remove the alerts."""
    with db.tx() as c:
        cond, p = " WHERE date=?", [date]
        if kind:
            cond += " AND kind=?"; p.append(kind)
        if mode == "delete":
            n = c.execute("DELETE FROM campus_alerts" + cond, p).rowcount
        else:
            n = c.execute("UPDATE campus_alerts SET status='OPEN', ack_by=NULL, ack_ts=NULL, resolved_by=NULL, resolved_ts=NULL, resolution=NULL"
                          + cond + " AND status<>'OPEN'", p).rowcount
        _audit(c, user, "TEST_RESET_ALERTS", "campus_alerts", date, f"{mode} {kind or 'all types'}: {n}")
    return n


def reset_parent_requests(date, user):
    with db.tx() as c:
        a = c.execute("DELETE FROM absences WHERE date=? AND source='GUARDIAN' AND declared_by<>'guardian'", (date,)).rowcount
        r = c.execute("DELETE FROM change_requests WHERE date=? AND kind<>'BUS'", (date,)).rowcount
        loc = c.execute("DELETE FROM student_locations WHERE status='PENDING'").rowcount
        _audit(c, user, "TEST_RESET_PARENT_REQUESTS", "parent", date, f"{a} absence(s) by parent logins, {r} change request(s), {loc} pending location(s)")
    return a, r, loc


def snapshot_assignment(con=None):
    """Keep a copy of the original per-session bus assignment (once)."""
    with db.tx(con) as c:
        if not c.execute("SELECT name FROM sqlite_master WHERE name='trip_assign_orig'").fetchone():
            c.execute("CREATE TABLE trip_assign_orig AS SELECT * FROM trip_assign")
            c.execute("CREATE TABLE students_bus_orig AS SELECT id, bus_id, stop_id FROM students")


def reset_assignment(date, user, permanent=True):
    """Undo assignment-board changes: today's one-day bus moves and (optionally) permanent moves back to the original plan."""
    with db.tx() as c:
        n1 = c.execute("DELETE FROM change_requests WHERE date=? AND kind='BUS'", (date,)).rowcount
        n2 = 0
        if permanent and c.execute("SELECT name FROM sqlite_master WHERE name='trip_assign_orig'").fetchone():
            n2 = c.execute("""SELECT COUNT(*) FROM trip_assign a LEFT JOIN trip_assign_orig o ON o.student_id=a.student_id AND o.trip_no=a.trip_no
                              WHERE o.bus_id IS NULL OR o.bus_id<>a.bus_id""").fetchone()[0]
            c.execute("DELETE FROM trip_assign")
            c.execute("INSERT INTO trip_assign SELECT * FROM trip_assign_orig")
            c.execute("UPDATE students SET bus_id=(SELECT bus_id FROM students_bus_orig o WHERE o.id=students.id), "
                      "stop_id=(SELECT stop_id FROM students_bus_orig o WHERE o.id=students.id)")
        _audit(c, user, "TEST_RESET_ASSIGNMENT", "trip_assign", date, f"{n1} one-day move(s) removed, {n2} permanent move(s) restored")
    return n1, n2


def release_manual_buses(user):
    db.set_setting("manual_buses", json.dumps({}))
    db.audit(user, "admin", "TEST_RELEASE_MANUAL_BUSES", "settings", "manual_buses", "")
