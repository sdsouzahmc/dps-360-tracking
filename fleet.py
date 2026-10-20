"""DPS 360 fleet module (DPS requirements: Maintenance & Fuel; vehicle and staff documents):
job orders with a Supervisor → Manager → Senior Manager → Vice President approval chain, purchase orders, spare parts / tyres / batteries
stock, fuel issuance and efficiency, preventive maintenance by km and days, cost history, and a document wallet for buses and crew."""
import json
import random
from datetime import date as Date, datetime, timedelta

import db
from db import q, q1, ts

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (id INTEGER PRIMARY KEY, owner_type TEXT, owner_id INTEGER, doc_type TEXT, doc_no TEXT,
  issue_date TEXT, expiry_date TEXT, file_name TEXT, file_mime TEXT, file_data BLOB, uploaded_by TEXT, ts TEXT, note TEXT, active INTEGER DEFAULT 1);
CREATE INDEX IF NOT EXISTS ix_docs ON documents(owner_type, owner_id);
CREATE TABLE IF NOT EXISTS garages (id INTEGER PRIMARY KEY, name TEXT, area TEXT, phone TEXT, kind TEXT, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS suppliers (id INTEGER PRIMARY KEY, name TEXT, category TEXT, phone TEXT, email TEXT, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS parts (id INTEGER PRIMARY KEY, code TEXT UNIQUE, name TEXT, category TEXT, unit TEXT, stock REAL DEFAULT 0,
  min_stock REAL DEFAULT 0, unit_cost REAL, supplier_id INTEGER);
CREATE TABLE IF NOT EXISTS job_orders (id INTEGER PRIMARY KEY, jo_no TEXT UNIQUE, bus_id INTEGER, opened_ts TEXT, opened_by TEXT, source TEXT,
  nature TEXT, description TEXT, odometer INTEGER, garage_id INTEGER, priority TEXT, est_cost REAL, status TEXT, level_no INTEGER DEFAULT 0,
  levels_needed INTEGER DEFAULT 1, out_ts TEXT, completed_ts TEXT, labour_cost REAL DEFAULT 0, parts_cost REAL DEFAULT 0, invoice_no TEXT,
  plan_id INTEGER, defect_check_id INTEGER, note TEXT);
CREATE INDEX IF NOT EXISTS ix_jo ON job_orders(status, bus_id);
CREATE TABLE IF NOT EXISTS approvals (id INTEGER PRIMARY KEY, entity TEXT, entity_id INTEGER, level_no INTEGER, level_name TEXT,
  approver TEXT, role TEXT, decision TEXT, ts TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS purchase_orders (id INTEGER PRIMARY KEY, po_no TEXT UNIQUE, supplier_id INTEGER, jo_id INTEGER, created_ts TEXT,
  created_by TEXT, status TEXT, total REAL DEFAULT 0, level_no INTEGER DEFAULT 0, levels_needed INTEGER DEFAULT 1, sent_ts TEXT,
  received_ts TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS po_lines (id INTEGER PRIMARY KEY, po_id INTEGER, part_id INTEGER, qty REAL, unit_cost REAL);
CREATE TABLE IF NOT EXISTS stock_moves (id INTEGER PRIMARY KEY, ts TEXT, part_id INTEGER, qty REAL, kind TEXT, bus_id INTEGER, jo_id INTEGER,
  po_id INTEGER, unit_cost REAL, by_user TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS fuel_logs (id INTEGER PRIMARY KEY, date TEXT, ts TEXT, bus_id INTEGER, litres REAL, price REAL, amount REAL,
  odometer INTEGER, station TEXT, card_no TEXT, receipt_no TEXT, source TEXT, entered_by TEXT, flag TEXT);
CREATE INDEX IF NOT EXISTS ix_fuel ON fuel_logs(bus_id, date);
CREATE TABLE IF NOT EXISTS service_plans (id INTEGER PRIMARY KEY, name TEXT, interval_km INTEGER, interval_days INTEGER, est_cost REAL,
  active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS service_status (bus_id INTEGER, plan_id INTEGER, last_km INTEGER, last_date TEXT, PRIMARY KEY(bus_id, plan_id));
"""

LEVELS = [("Supervisor", "fleet_supervisor"), ("Manager", "manager"), ("Senior Manager", "senior_manager"), ("Vice President", "vp")]
DEFAULT_LIMITS = {"Manager": 2000, "Senior Manager": 10000, "Vice President": 25000}    # QAR: a level is needed above this amount
BUS_DOCS = ["Vehicle registration (Istimara)", "Insurance", "MOI school-bus permit", "Fire extinguisher service"]
STAFF_DOCS = {"driver": ["Driving licence (heavy vehicle)", "QID", "Medical fitness"],
              "supervisor": ["QID", "Medical fitness", "Child-safeguarding training"],
              "caretaker": ["QID", "Medical fitness", "Child-safeguarding training"]}
NATURES = ["Engine", "Brakes", "Tyres", "Electrical", "AC", "Body / glass", "Suspension", "Transmission", "Doors / emergency exit",
           "Preventive service", "Other"]
PART_CATS = ["Spare part", "Tyre", "Battery", "Oil & lubricant", "Filter", "Consumable"]
TANK_L = 200
JO_STATUS = ["PENDING_APPROVAL", "APPROVED", "IN_WORKSHOP", "COMPLETED", "CLOSED", "REJECTED"]


def ensure_schema(con=None):
    with db.tx(con) as c:
        c.executescript(SCHEMA)


# ------------------------------------------------------------------ settings
def diesel_price(con=None):
    return float(db.setting("diesel_price_qar", "2.05", con) or 2.05)


def approval_mode(con=None):
    return db.setting("approval_mode", "amount", con) or "amount"


def approval_limits(con=None):
    try:
        return {**DEFAULT_LIMITS, **json.loads(db.setting("approval_limits", "{}", con) or "{}")}
    except ValueError:
        return dict(DEFAULT_LIMITS)


def levels_for(amount, con=None):
    """How many approval levels an amount needs: always the Supervisor; Manager / Senior Manager / VP above their limits (or all four)."""
    if approval_mode(con) == "all":
        return 4
    lim = approval_limits(con)
    n = 1
    for i, (name, _) in enumerate(LEVELS[1:], start=2):
        if (amount or 0) > lim.get(name, 1e12):
            n = i
    return n


def can_approve(role, level_no):
    return role == "admin" or (1 <= level_no <= 4 and LEVELS[level_no - 1][1] == role)


# ------------------------------------------------------------------ odometer
_ANCHOR = Date(2026, 7, 1)


def _school_days_between(d0, d1):
    n, d = 0, d0
    while d < d1:
        if d.weekday() not in (4, 5):        # Friday, Saturday off in Qatar
            n += 1
        d += timedelta(days=1)
    return n


def odo_model(bus_id, on):
    """Demo odometer of a bus on a date (km): a per-bus base plus its daily route distance on school days."""
    on = on if isinstance(on, Date) else datetime.strptime(str(on)[:10], "%Y-%m-%d").date()
    base = 40000 + (bus_id * 7919) % 210000
    daily = 48 + (bus_id * 37) % 64
    return int(base + daily * _school_days_between(_ANCHOR, on))


def odometer(bus_id, con=None):
    """Latest known odometer: fuel fill, vehicle check, job order or GPS reading."""
    vals = [r["v"] for r in q("""SELECT MAX(odometer) v FROM fuel_logs WHERE bus_id=? UNION ALL SELECT MAX(odometer) FROM vehicle_checks WHERE bus_id=?
                                 UNION ALL SELECT MAX(odometer) FROM job_orders WHERE bus_id=?""", (bus_id, bus_id, bus_id), con) if r["v"]]
    try:
        g = q1("SELECT MAX(odometer) v FROM gps_positions WHERE bus_id=?", (bus_id,), con)
        if g and g["v"]:
            vals.append(g["v"])
    except Exception:
        pass
    return max(vals) if vals else odo_model(bus_id, db.today())


# ------------------------------------------------------------------ documents
def doc_state(expiry, today=None):
    today = today or db.today()
    if not expiry:
        return "NO_EXPIRY", None
    days = (datetime.strptime(expiry, "%Y-%m-%d").date() - datetime.strptime(today, "%Y-%m-%d").date()).days
    return ("EXPIRED" if days < 0 else "EXPIRING" if days <= 30 else "VALID"), days


def documents(owner_type=None, owner_id=None, con=None):
    sql = """SELECT d.id, d.owner_type, d.owner_id, d.doc_type, d.doc_no, d.issue_date, d.expiry_date, d.file_name, d.file_mime, d.uploaded_by, d.ts,
                    d.note, CASE WHEN d.file_data IS NULL THEN 0 ELSE 1 END AS has_file,
                    CASE d.owner_type WHEN 'BUS' THEN (SELECT bus_no FROM buses WHERE id=d.owner_id) ELSE (SELECT name FROM staff WHERE id=d.owner_id) END owner,
                    CASE d.owner_type WHEN 'STAFF' THEN (SELECT role FROM staff WHERE id=d.owner_id) ELSE 'bus' END owner_role
             FROM documents d WHERE d.active=1"""
    a = []
    if owner_type:
        sql += " AND d.owner_type=?"
        a.append(owner_type)
    if owner_id:
        sql += " AND d.owner_id=?"
        a.append(owner_id)
    rows = q(sql + " ORDER BY d.expiry_date", a, con)
    t = db.today()
    for r in rows:
        r["state"], r["days_left"] = doc_state(r["expiry_date"], t)
    return rows


def doc_file(doc_id, con=None):
    return q1("SELECT file_name, file_mime, file_data FROM documents WHERE id=?", (doc_id,), con)


def save_document(owner_type, owner_id, doc_type, doc_no, issue_date, expiry_date, user, file_name=None, file_mime=None, file_data=None, note="",
                  con=None):
    """Add or renew a document (the previous one of the same type is kept as history)."""
    with db.tx(con) as c:
        c.execute("UPDATE documents SET active=0 WHERE owner_type=? AND owner_id=? AND doc_type=? AND active=1", (owner_type, owner_id, doc_type))
        i = c.execute("""INSERT INTO documents(owner_type, owner_id, doc_type, doc_no, issue_date, expiry_date, file_name, file_mime, file_data,
                         uploaded_by, ts, note, active) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1)""",
                      (owner_type, owner_id, doc_type, doc_no, issue_date, expiry_date, file_name, file_mime, file_data, user, ts(), note)).lastrowid
        db.audit(user, "manager", "DOCUMENT_SAVE", owner_type.lower(), owner_id, f"{doc_type} {doc_no} exp {expiry_date}", c)
        return i


# ------------------------------------------------------------------ job orders + approvals
def _next_no(prefix, table, col, con):
    n = q1(f"SELECT COUNT(*) n FROM {table}", (), con)["n"] + 1
    return f"{prefix}-{db.today()[2:4]}{n:04d}"


def create_job_order(bus_id, nature, description, est_cost, user, garage_id=None, priority="Normal", source="Manual", odometer_km=None,
                     plan_id=None, defect_check_id=None, when=None, con=None):
    when = when or db.now()
    with db.tx(con) as c:
        no = _next_no("JO", "job_orders", "jo_no", c)
        need = levels_for(est_cost, c)
        jid = c.execute("""INSERT INTO job_orders(jo_no, bus_id, opened_ts, opened_by, source, nature, description, odometer, garage_id, priority, est_cost,
                           status, level_no, levels_needed, plan_id, defect_check_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (no, bus_id, ts(when), user, source, nature, description, odometer_km or odometer(bus_id, c), garage_id, priority, est_cost,
                         "PENDING_APPROVAL", 1, need, plan_id, defect_check_id)).lastrowid
        db.audit(user, "manager", "JOB_ORDER_CREATE", "job_order", jid, f"{no} bus {bus_id} {nature} QAR {est_cost}", c)
        return jid, no


def _decide(entity, table, eid, approve, user, role, note, when, c):
    r = q1(f"SELECT * FROM {table} WHERE id=?", (eid,), c)
    pending = "PENDING_APPROVAL"
    if not r or r["status"] != pending:
        return False, "Not waiting for approval."
    lv = r["level_no"]
    if not can_approve(role, lv):
        return False, f"Waiting for the {LEVELS[lv - 1][0]} — your role cannot approve this level."
    c.execute("INSERT INTO approvals(entity, entity_id, level_no, level_name, approver, role, decision, ts, note) VALUES (?,?,?,?,?,?,?,?,?)",
              (entity, eid, lv, LEVELS[lv - 1][0], user, role, "APPROVED" if approve else "REJECTED", ts(when), note))
    if not approve:
        c.execute(f"UPDATE {table} SET status='REJECTED', note=? WHERE id=?", (note, eid))
        return True, f"Rejected at {LEVELS[lv - 1][0]} level."
    if lv >= r["levels_needed"]:
        c.execute(f"UPDATE {table} SET status='APPROVED', level_no=? WHERE id=?", (lv, eid))
        return True, "Fully approved."
    c.execute(f"UPDATE {table} SET level_no=? WHERE id=?", (lv + 1, eid))
    return True, f"Approved at {LEVELS[lv - 1][0]} level — now with the {LEVELS[lv][0]}."


def decide_job_order(jid, approve, user, role, note="", when=None, con=None):
    with db.tx(con) as c:
        ok, m = _decide("JO", "job_orders", jid, approve, user, role, note, when or db.now(), c)
        if ok:
            db.audit(user, role, "JOB_ORDER_" + ("APPROVE" if approve else "REJECT"), "job_order", jid, m, c)
        return ok, m


def send_to_garage(jid, user, garage_id=None, when=None, con=None):
    with db.tx(con) as c:
        r = q1("SELECT status FROM job_orders WHERE id=?", (jid,), c)
        if not r or r["status"] != "APPROVED":
            return False, "Only an approved job order can go to the workshop."
        c.execute("UPDATE job_orders SET status='IN_WORKSHOP', out_ts=?, garage_id=COALESCE(?, garage_id) WHERE id=?", (ts(when), garage_id, jid))
        db.audit(user, "manager", "JOB_ORDER_WORKSHOP", "job_order", jid, "", c)
        return True, "Bus sent to the workshop."


def issue_part(part_id, qty, user, bus_id=None, jo_id=None, note="", when=None, con=None):
    with db.tx(con) as c:
        p = q1("SELECT * FROM parts WHERE id=?", (part_id,), c)
        if not p or p["stock"] < qty:
            return False, f"Not enough stock ({p['stock'] if p else 0:g} {p['unit'] if p else ''})."
        c.execute("UPDATE parts SET stock=stock-? WHERE id=?", (qty, part_id))
        c.execute("INSERT INTO stock_moves(ts, part_id, qty, kind, bus_id, jo_id, po_id, unit_cost, by_user, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (ts(when), part_id, -qty, "OUT", bus_id, jo_id, None, p["unit_cost"], user, note))
        if jo_id:
            c.execute("UPDATE job_orders SET parts_cost=parts_cost+? WHERE id=?", (qty * p["unit_cost"], jo_id))
        return True, f"Issued {qty:g} × {p['name']}."


def complete_job_order(jid, user, labour_cost, invoice_no, odometer_km=None, note="", when=None, con=None):
    when = when or db.now()
    with db.tx(con) as c:
        r = q1("SELECT * FROM job_orders WHERE id=?", (jid,), c)
        if not r or r["status"] not in ("APPROVED", "IN_WORKSHOP"):
            return False, "Only an approved or in-workshop job order can be completed."
        km = odometer_km or r["odometer"]
        c.execute("UPDATE job_orders SET status='COMPLETED', completed_ts=?, labour_cost=?, invoice_no=?, odometer=?, note=? WHERE id=?",
                  (ts(when), labour_cost, invoice_no, km, note, jid))
        if r["plan_id"]:
            c.execute("INSERT OR REPLACE INTO service_status(bus_id, plan_id, last_km, last_date) VALUES (?,?,?,?)",
                      (r["bus_id"], r["plan_id"], km, when.strftime("%Y-%m-%d")))
        if r["defect_check_id"]:
            c.execute("UPDATE vehicle_checks SET cleared_by=?, cleared_ts=?, cleared_note=? WHERE id=? AND cleared_by IS NULL",
                      (user, ts(when), f"Repaired under {r['jo_no']}", r["defect_check_id"]))
        db.audit(user, "manager", "JOB_ORDER_COMPLETE", "job_order", jid, f"labour {labour_cost} invoice {invoice_no}", c)
        return True, "Job order completed."


def close_job_order(jid, user, con=None):
    with db.tx(con) as c:
        c.execute("UPDATE job_orders SET status='CLOSED' WHERE id=? AND status='COMPLETED'", (jid,))
        db.audit(user, "manager", "JOB_ORDER_CLOSE", "job_order", jid, "", c)


def job_orders(status=None, bus_id=None, con=None):
    sql = """SELECT j.*, b.bus_no, g.name garage FROM job_orders j JOIN buses b ON b.id=j.bus_id LEFT JOIN garages g ON g.id=j.garage_id WHERE 1=1"""
    a = []
    if status:
        sts = [status] if isinstance(status, str) else list(status)
        sql += f" AND j.status IN ({','.join('?' * len(sts))})"
        a += sts
    if bus_id:
        sql += " AND j.bus_id=?"
        a.append(bus_id)
    rows = q(sql + " ORDER BY j.opened_ts DESC", a, con)
    for r in rows:
        r["total_cost"] = (r["labour_cost"] or 0) + (r["parts_cost"] or 0)
        r["waiting_for"] = LEVELS[r["level_no"] - 1][0] if r["status"] == "PENDING_APPROVAL" and r["level_no"] else ""
    return rows


def approval_trail(entity, eid, con=None):
    return q("SELECT * FROM approvals WHERE entity=? AND entity_id=? ORDER BY level_no, ts", (entity, eid), con)


# ------------------------------------------------------------------ purchase orders + stock
def create_po(supplier_id, lines, user, jo_id=None, note="", when=None, con=None):
    """lines: [(part_id, qty, unit_cost)]. Goes to the same approval chain as job orders, by total amount."""
    when = when or db.now()
    with db.tx(con) as c:
        total = sum(qn * uc for _, qn, uc in lines)
        no = _next_no("PO", "purchase_orders", "po_no", c)
        pid = c.execute("""INSERT INTO purchase_orders(po_no, supplier_id, jo_id, created_ts, created_by, status, total, level_no, levels_needed, note)
                           VALUES (?,?,?,?,?,?,?,?,?,?)""", (no, supplier_id, jo_id, ts(when), user, "PENDING_APPROVAL", total, 1, levels_for(total, c), note)).lastrowid
        c.executemany("INSERT INTO po_lines(po_id, part_id, qty, unit_cost) VALUES (?,?,?,?)", [(pid, p, qn, uc) for p, qn, uc in lines])
        db.audit(user, "manager", "PO_CREATE", "purchase_order", pid, f"{no} QAR {total:.2f}", c)
        return pid, no


def decide_po(pid, approve, user, role, note="", when=None, con=None):
    with db.tx(con) as c:
        ok, m = _decide("PO", "purchase_orders", pid, approve, user, role, note, when or db.now(), c)
        if ok:
            db.audit(user, role, "PO_" + ("APPROVE" if approve else "REJECT"), "purchase_order", pid, m, c)
        return ok, m


def send_po(pid, user, con=None):
    with db.tx(con) as c:
        if not q1("SELECT id FROM purchase_orders WHERE id=? AND status='APPROVED'", (pid,), c):
            return False, "Only an approved PO can be sent."
        c.execute("UPDATE purchase_orders SET status='SENT', sent_ts=? WHERE id=?", (ts(), pid))
        db.audit(user, "manager", "PO_SENT", "purchase_order", pid, "", c)
        return True, "PO sent to the supplier."


def receive_po(pid, user, when=None, con=None):
    with db.tx(con) as c:
        po = q1("SELECT * FROM purchase_orders WHERE id=?", (pid,), c)
        if not po or po["status"] not in ("APPROVED", "SENT"):
            return False, "Only an approved / sent PO can be received."
        for ln in q("SELECT * FROM po_lines WHERE po_id=?", (pid,), c):
            c.execute("UPDATE parts SET stock=stock+?, unit_cost=? WHERE id=?", (ln["qty"], ln["unit_cost"], ln["part_id"]))
            c.execute("INSERT INTO stock_moves(ts, part_id, qty, kind, bus_id, jo_id, po_id, unit_cost, by_user, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (ts(when), ln["part_id"], ln["qty"], "IN", None, po["jo_id"], pid, ln["unit_cost"], user, f"Received on {po['po_no']}"))
        c.execute("UPDATE purchase_orders SET status='RECEIVED', received_ts=? WHERE id=?", (ts(when), pid))
        db.audit(user, "manager", "PO_RECEIVED", "purchase_order", pid, "", c)
        return True, "Goods received — stock updated."


def purchase_orders(status=None, con=None):
    sql = """SELECT p.*, s.name supplier, j.jo_no FROM purchase_orders p LEFT JOIN suppliers s ON s.id=p.supplier_id
             LEFT JOIN job_orders j ON j.id=p.jo_id WHERE 1=1"""
    a = []
    if status:
        sql += " AND p.status=?"
        a.append(status)
    rows = q(sql + " ORDER BY p.created_ts DESC", a, con)
    for r in rows:
        r["waiting_for"] = LEVELS[r["level_no"] - 1][0] if r["status"] == "PENDING_APPROVAL" and r["level_no"] else ""
    return rows


def parts(con=None):
    return q("SELECT p.*, s.name supplier FROM parts p LEFT JOIN suppliers s ON s.id=p.supplier_id ORDER BY p.category, p.name", (), con)


# ------------------------------------------------------------------ fuel
def add_fuel(bus_id, litres, odometer_km, user, station="", card_no="", receipt_no="", price=None, amount=None, when=None, source="Manual",
             con=None):
    """One fuel issuance. Flags a fill above the tank size, an odometer lower than the last fill, or poor km per litre."""
    when = when or db.now()
    with db.tx(con) as c:
        price = price or diesel_price(c)
        amount = amount if amount is not None else round(litres * price, 2)
        last = q1("SELECT odometer, litres FROM fuel_logs WHERE bus_id=? AND ts<? ORDER BY ts DESC LIMIT 1", (bus_id, ts(when)), c)
        flags = []
        if litres > TANK_L:
            flags.append(f"More than the {TANK_L} L tank")
        if last and odometer_km and last["odometer"] and odometer_km < last["odometer"]:
            flags.append("Odometer lower than the last fill")
        if last and odometer_km and last["odometer"] and litres > 0:
            kmpl = (odometer_km - last["odometer"]) / litres
            avg = bus_kmpl(bus_id, c)
            if avg and 0 < kmpl < 0.65 * avg:
                flags.append(f"Low efficiency {kmpl:.1f} km/L (bus average {avg:.1f})")
        fid = c.execute("""INSERT INTO fuel_logs(date, ts, bus_id, litres, price, amount, odometer, station, card_no, receipt_no, source, entered_by, flag)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (when.strftime("%Y-%m-%d"), ts(when), bus_id, litres, price, amount, odometer_km, station, card_no, receipt_no, source, user,
                         "; ".join(flags) or None)).lastrowid
        return fid, flags


def bus_kmpl(bus_id, con=None):
    rows = q("SELECT odometer, litres FROM fuel_logs WHERE bus_id=? AND odometer IS NOT NULL ORDER BY ts", (bus_id,), con)
    if len(rows) < 3:
        return None
    km = rows[-1]["odometer"] - rows[0]["odometer"]
    litres = sum(r["litres"] for r in rows[1:])
    return km / litres if litres and km > 0 else None


def fuel_efficiency(date_from, date_to, con=None):
    """Per bus: litres, cost, km driven and km per litre between the first and last fill in the period."""
    out = []
    for b in q("SELECT id, bus_no FROM buses ORDER BY bus_no", (), con):
        rows = q("SELECT odometer, litres, amount FROM fuel_logs WHERE bus_id=? AND date BETWEEN ? AND ? ORDER BY ts", (b["id"], date_from, date_to), con)
        if not rows:
            continue
        km = (rows[-1]["odometer"] or 0) - (rows[0]["odometer"] or 0)
        lit_after = sum(r["litres"] for r in rows[1:])
        out.append({"bus_id": b["id"], "Bus": b["bus_no"], "Fills": len(rows), "Litres": round(sum(r["litres"] for r in rows), 1),
                    "Cost (QAR)": round(sum(r["amount"] for r in rows), 2), "Km": km,
                    "Km per litre": round(km / lit_after, 2) if lit_after and km > 0 else None})
    return out


def fuel_logs(date_from, date_to, bus_id=None, con=None):
    sql = "SELECT f.*, b.bus_no FROM fuel_logs f JOIN buses b ON b.id=f.bus_id WHERE f.date BETWEEN ? AND ?"
    a = [date_from, date_to]
    if bus_id:
        sql += " AND f.bus_id=?"
        a.append(bus_id)
    return q(sql + " ORDER BY f.ts DESC", a, con)


# ------------------------------------------------------------------ preventive maintenance
def service_board(con=None, today=None):
    """Every bus × active plan: last service, km and days to go, state OVERDUE / DUE_SOON / OK."""
    today = today or db.today()
    td = datetime.strptime(today, "%Y-%m-%d").date()
    plans = q("SELECT * FROM service_plans WHERE active=1", (), con)
    st_ = {(r["bus_id"], r["plan_id"]): r for r in q("SELECT * FROM service_status", (), con)}
    open_jo = {(r["bus_id"], r["plan_id"]) for r in q("SELECT bus_id, plan_id FROM job_orders WHERE plan_id IS NOT NULL AND status NOT IN ('COMPLETED','CLOSED','REJECTED')", (), con)}
    out = []
    for b in q("SELECT id, bus_no FROM buses ORDER BY bus_no", (), con):
        km_now = odometer(b["id"], con)
        for p in plans:
            s = st_.get((b["id"], p["id"]))
            last_km = s["last_km"] if s else km_now - (p["interval_km"] or 0) // 2
            last_d = datetime.strptime(s["last_date"], "%Y-%m-%d").date() if s else td - timedelta(days=(p["interval_days"] or 0) // 2)
            km_left = (last_km + p["interval_km"] - km_now) if p["interval_km"] else None
            days_left = (last_d + timedelta(days=p["interval_days"]) - td).days if p["interval_days"] else None
            over = (km_left is not None and km_left < 0) or (days_left is not None and days_left < 0)
            soon = (km_left is not None and km_left <= 1000) or (days_left is not None and days_left <= 14)
            out.append({"bus_id": b["id"], "Bus": b["bus_no"], "plan_id": p["id"], "Service": p["name"], "Last km": last_km,
                        "Last date": last_d.isoformat(), "Odometer": km_now, "Km left": km_left, "Days left": days_left,
                        "State": "OVERDUE" if over else "DUE_SOON" if soon else "OK", "Job order open": (b["id"], p["id"]) in open_jo,
                        "est_cost": p["est_cost"]})
    return out


# ------------------------------------------------------------------ dashboard numbers
def summary(con=None, today=None):
    today = today or db.today()
    m0 = today[:8] + "01"
    jo = job_orders(con=con)
    docs = documents(con=con)
    sb = service_board(con, today)
    fuel_m = q1("SELECT COALESCE(SUM(litres),0) l, COALESCE(SUM(amount),0) a FROM fuel_logs WHERE date BETWEEN ? AND ?", (m0, today), con)
    maint_m = sum(j["total_cost"] for j in jo if (j["completed_ts"] or "")[:10] >= m0)
    eff = [r["Km per litre"] for r in fuel_efficiency((datetime.strptime(today, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d"), today, con)
           if r["Km per litre"]]
    return {"open_jo": sum(1 for j in jo if j["status"] in ("PENDING_APPROVAL", "APPROVED", "IN_WORKSHOP")),
            "waiting": {lv: sum(1 for j in jo if j["status"] == "PENDING_APPROVAL" and j["waiting_for"] == lv) for lv, _ in LEVELS},
            "po_waiting": sum(1 for p in purchase_orders(con=con) if p["status"] == "PENDING_APPROVAL"),
            "in_workshop": len({j["bus_id"] for j in jo if j["status"] == "IN_WORKSHOP"}),
            "overdue": sum(1 for s in sb if s["State"] == "OVERDUE"), "due_soon": sum(1 for s in sb if s["State"] == "DUE_SOON"),
            "fuel_l": fuel_m["l"], "fuel_qar": fuel_m["a"], "maint_qar": maint_m,
            "kmpl": sum(eff) / len(eff) if eff else None,
            "docs_expired": sum(1 for d in docs if d["state"] == "EXPIRED"), "docs_expiring": sum(1 for d in docs if d["state"] == "EXPIRING"),
            "low_stock": sum(1 for p in parts(con) if p["stock"] <= p["min_stock"])}


# ------------------------------------------------------------------ demo seed
def seed(con=None):
    """Demo fleet data on an empty database: documents, garages, suppliers, parts, 90 days of fuel, service plans, job orders, POs."""
    if q1("SELECT COUNT(*) n FROM garages", (), con)["n"]:
        return
    r = random.Random(77)
    today = datetime.strptime(db.today(), "%Y-%m-%d").date()
    with db.tx(con) as c:
        buses = q("SELECT id, bus_no FROM buses ORDER BY id", (), c)
        # documents
        rows = []
        for b in buses:
            for dt in BUS_DOCS:
                exp = today + timedelta(days=r.randint(-12, 380))
                rows.append(("BUS", b["id"], dt, f"{r.randint(100000, 999999)}", (exp - timedelta(days=365)).isoformat(), exp.isoformat()))
        for s in q("SELECT id, role FROM staff WHERE role IN ('driver','supervisor','caretaker')", (), c):
            for dt in STAFF_DOCS.get(s["role"], []):
                exp = today + timedelta(days=r.randint(-10, 700))
                rows.append(("STAFF", s["id"], dt, f"{r.randint(10000000, 99999999)}", (exp - timedelta(days=730)).isoformat(), exp.isoformat()))
        c.executemany("""INSERT INTO documents(owner_type, owner_id, doc_type, doc_no, issue_date, expiry_date, uploaded_by, ts, active)
                         VALUES (?,?,?,?,?,?,'transport','2026-09-01 08:00:00',1)""", rows)
        # garages, suppliers, parts
        for g in [("DPS in-house workshop", "DPS campus, Al Wakrah", "44499109", "In-house"), ("Al Wakrah Auto Workshop", "Al Wakrah", "44112233", "External"),
                  ("Industrial Area Heavy Vehicles", "Industrial Area, St. 24", "44600987", "External"),
                  ("Mesaieed Diesel Services", "Mesaieed", "44770011", "External")]:
            c.execute("INSERT INTO garages(name, area, phone, kind) VALUES (?,?,?,?)", g)
        for sp in [("Gulf Spare Parts Trading", "Spare part", "44001100", "sales@gulfspares.example"),
                   ("Doha Tyre Centre", "Tyre", "44002200", "orders@dohatyre.example"),
                   ("Qatar Battery House", "Battery", "44003300", "info@batteryhouse.example"),
                   ("Lubricants & Filters Co.", "Oil & lubricant", "44004400", "sales@lubefilters.example")]:
            c.execute("INSERT INTO suppliers(name, category, phone, email) VALUES (?,?,?,?)", sp)
        P = [("BRK-PAD-F", "Brake pads, front axle (set)", "Spare part", "set", 380, 1), ("BRK-PAD-R", "Brake pads, rear axle (set)", "Spare part", "set", 360, 1),
             ("BRK-DRUM", "Brake drum", "Spare part", "pc", 950, 1), ("ALT-24V", "Alternator 24 V", "Spare part", "pc", 1850, 1),
             ("STR-MOT", "Starter motor", "Spare part", "pc", 1650, 1), ("AC-COMP", "AC compressor", "Spare part", "pc", 3200, 1),
             ("AC-GAS", "AC refrigerant R134a", "Consumable", "kg", 65, 4), ("DOOR-CYL", "Door air cylinder", "Spare part", "pc", 540, 1),
             ("WIPER", "Wiper blade", "Consumable", "pc", 45, 4), ("BULB-H7", "Headlamp bulb 24 V", "Consumable", "pc", 28, 4),
             ("TYR-275", "Tyre 275/70 R22.5", "Tyre", "pc", 1450, 2), ("TYR-215", "Tyre 215/75 R17.5", "Tyre", "pc", 820, 2),
             ("BAT-150", "Battery 12 V 150 Ah", "Battery", "pc", 780, 3), ("BAT-200", "Battery 12 V 200 Ah", "Battery", "pc", 980, 3),
             ("OIL-15W40", "Engine oil 15W-40", "Oil & lubricant", "L", 14, 4), ("ATF", "Transmission fluid", "Oil & lubricant", "L", 22, 4),
             ("COOL", "Coolant", "Oil & lubricant", "L", 11, 4), ("GREASE", "Chassis grease", "Oil & lubricant", "kg", 18, 4),
             ("FLT-OIL", "Oil filter", "Filter", "pc", 55, 1), ("FLT-FUEL", "Fuel filter", "Filter", "pc", 85, 1),
             ("FLT-AIR", "Air filter", "Filter", "pc", 140, 1), ("FLT-AC", "AC cabin filter", "Filter", "pc", 60, 1),
             ("FIRE-EXT", "Fire extinguisher 6 kg", "Consumable", "pc", 210, 1), ("FAK", "First-aid kit refill", "Consumable", "set", 95, 1)]
        for code, name, cat, unit, cost, sup in P:
            mn = 4 if unit in ("pc", "set") else 40
            c.execute("INSERT INTO parts(code, name, category, unit, stock, min_stock, unit_cost, supplier_id) VALUES (?,?,?,?,?,?,?,?)",
                      (code, name, cat, unit, r.randint(int(mn * .5), mn * 4), mn, cost, sup))
        # service plans + last services
        plans = [("Engine oil & filters", 10000, 180, 650), ("Brake inspection", 20000, 180, 400), ("Tyre rotation & alignment", 15000, None, 300),
                 ("AC service", None, 365, 900), ("Annual inspection (Fahes)", None, 365, 350)]
        pid = {}
        for n, km, dd, est in plans:
            pid[n] = c.execute("INSERT INTO service_plans(name, interval_km, interval_days, est_cost) VALUES (?,?,?,?)", (n, km, dd, est)).lastrowid
        for b in buses:
            km_now = odo_model(b["id"], today)
            for n, km, dd, est in plans:
                frac = r.uniform(0.05, 1.08)
                lk = int(km_now - (km or 0) * frac) if km else km_now - r.randint(500, 12000)
                ld = today - timedelta(days=int((dd or 300) * (frac if dd else r.uniform(.1, .9))))
                c.execute("INSERT INTO service_status(bus_id, plan_id, last_km, last_date) VALUES (?,?,?,?)", (b["id"], pid[n], lk, ld.isoformat()))
        # fuel, last 90 days (fills when the tank drops below ~40%)
        price = diesel_price(c)
        stations = ["Woqod Al Wakrah", "Woqod Al Wukair", "Woqod Mesaieed Road", "Woqod Industrial Area", "Woqod Barwa City"]
        frows = []
        for b in buses:
            kmpl = r.uniform(3.0, 3.8)
            tank = r.uniform(.5, .95) * TANK_L
            d = today - timedelta(days=90)
            while d < today:
                if d.weekday() not in (4, 5):
                    daily = 48 + (b["id"] * 37) % 64
                    tank -= daily / kmpl
                    if tank < 0.4 * TANK_L:
                        fill = round(min(TANK_L - tank, TANK_L) * r.uniform(.95, 1.0), 1)
                        if r.random() < 0.01:
                            fill = round(fill * 1.5, 1)
                        tank = min(TANK_L, tank + fill)
                        t_ = f"{d.isoformat()} {r.randint(15, 19):02d}:{r.randint(0, 59):02d}:00"
                        frows.append((d.isoformat(), t_, b["id"], fill, price, round(fill * price, 2), odo_model(b["id"], d) + r.randint(-3, 3),
                                      r.choice(stations), f"WQ-{10000 + b['id']}", f"R{r.randint(1000000, 9999999)}", "Woqod statement (demo)",
                                      "transport", "More than the 200 L tank" if fill > TANK_L else None))
                d += timedelta(days=1)
        c.executemany("""INSERT INTO fuel_logs(date, ts, bus_id, litres, price, amount, odometer, station, card_no, receipt_no, source, entered_by, flag)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", frows)
        # job orders over the last 90 days
        users = {1: "fleetsup", 2: "manager", 3: "srmanager", 4: "vp"}
        roles = {1: "fleet_supervisor", 2: "manager", 3: "senior_manager", 4: "vp"}
        descs = {"Engine": "Engine warning light on, loss of power", "Brakes": "Brakes squealing / pads worn", "Tyres": "Front tyre worn below limit",
                 "Electrical": "Alternator not charging", "AC": "AC not cooling at the rear", "Body / glass": "Cracked side window",
                 "Suspension": "Rear suspension noise", "Transmission": "Gear change rough", "Doors / emergency exit": "Front door slow to close",
                 "Other": "Seat belt latch broken (row 6)"}
        garages = [g["id"] for g in q("SELECT id FROM garages", (), c)]
        for k in range(70):
            b = r.choice(buses)
            age = r.randint(0, 88)
            opened = datetime.combine(today - timedelta(days=age), datetime.min.time()) + timedelta(hours=r.randint(7, 14), minutes=r.randint(0, 59))
            nature = r.choice(list(descs))
            est = round(r.choice([r.uniform(150, 1900), r.uniform(150, 1900), r.uniform(2000, 9500), r.uniform(10000, 24000), r.uniform(25500, 42000)]), 0)
            need = levels_for(est, c)
            no = f"JO-{opened:%y}{k + 1:04d}"
            jid = c.execute("""INSERT INTO job_orders(jo_no, bus_id, opened_ts, opened_by, source, nature, description, odometer, garage_id, priority,
                               est_cost, status, level_no, levels_needed) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (no, b["id"], ts(opened), r.choice(["transport", "fleetsup"]), r.choice(["Driver defect report", "Manual", "Driver defect report"]),
                             nature, descs[nature], odo_model(b["id"], opened.date()), r.choice(garages), r.choice(["Normal", "Normal", "High", "Urgent"]),
                             est, "PENDING_APPROVAL", 1, need)).lastrowid
            # how far it got
            if age <= 3:
                stop_at = r.randint(1, need)          # still in approval
            else:
                stop_at = need + 1
            t_ = opened
            rejected = False
            for lv in range(1, min(stop_at, need + 1)):
                t_ += timedelta(hours=r.uniform(1, 20))
                rej = r.random() < 0.04
                c.execute("INSERT INTO approvals(entity, entity_id, level_no, level_name, approver, role, decision, ts, note) VALUES (?,?,?,?,?,?,?,?,?)",
                          ("JO", jid, lv, LEVELS[lv - 1][0], users[lv], roles[lv], "REJECTED" if rej else "APPROVED", ts(t_),
                           "Get a second quotation" if rej else ""))
                if rej:
                    c.execute("UPDATE job_orders SET status='REJECTED', level_no=?, note='Get a second quotation' WHERE id=?", (lv, jid))
                    rejected = True
                    break
            if rejected:
                continue
            if stop_at <= need:
                c.execute("UPDATE job_orders SET level_no=? WHERE id=?", (stop_at, jid))
                continue
            out = t_ + timedelta(hours=r.uniform(2, 30))
            done = out + timedelta(days=r.uniform(0.3, 4))
            if done.date() >= today:
                c.execute("UPDATE job_orders SET status=?, level_no=?, out_ts=? WHERE id=?",
                          ("IN_WORKSHOP" if out.date() < today else "APPROVED", need, ts(out) if out.date() < today else None, jid))
                continue
            labour = round(est * r.uniform(.35, .6), 0)
            partsc = round(est * r.uniform(.3, .6), 0)
            c.execute("""UPDATE job_orders SET status=?, level_no=?, out_ts=?, completed_ts=?, labour_cost=?, parts_cost=?, invoice_no=? WHERE id=?""",
                      ("CLOSED" if age > 10 else "COMPLETED", need, ts(out), ts(done), labour, partsc, f"INV-{r.randint(10000, 99999)}", jid))
        # purchase orders
        sups = q("SELECT id FROM suppliers", (), c)
        plist = q("SELECT id, unit_cost, supplier_id FROM parts", (), c)
        for k in range(16):
            sp = r.choice(sups)["id"]
            items = [p for p in plist if p["supplier_id"] == sp] or plist
            lines = [(p["id"], r.randint(2, 12), p["unit_cost"]) for p in r.sample(items, min(len(items), r.randint(1, 3)))]
            total = sum(a * b_ for _, a, b_ in lines)
            age = r.randint(0, 80)
            created = datetime.combine(today - timedelta(days=age), datetime.min.time()) + timedelta(hours=9)
            need = levels_for(total, c)
            stt = "PENDING_APPROVAL" if age < 3 else r.choice(["RECEIVED", "RECEIVED", "RECEIVED", "SENT"])
            pid_ = c.execute("""INSERT INTO purchase_orders(po_no, supplier_id, created_ts, created_by, status, total, level_no, levels_needed, sent_ts,
                                received_ts) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                             (f"PO-{created:%y}{k + 1:04d}", sp, ts(created), "transport", stt, total, 1 if stt == "PENDING_APPROVAL" else need, need,
                              ts(created + timedelta(days=1)) if stt != "PENDING_APPROVAL" else None,
                              ts(created + timedelta(days=4)) if stt == "RECEIVED" else None)).lastrowid
            c.executemany("INSERT INTO po_lines(po_id, part_id, qty, unit_cost) VALUES (?,?,?,?)", [(pid_, p, a, b_) for p, a, b_ in lines])
            if stt != "PENDING_APPROVAL":
                for lv in range(1, need + 1):
                    c.execute("INSERT INTO approvals(entity, entity_id, level_no, level_name, approver, role, decision, ts, note) VALUES (?,?,?,?,?,?,?,?,?)",
                              ("PO", pid_, lv, LEVELS[lv - 1][0], users[lv], roles[lv], "APPROVED", ts(created + timedelta(hours=3 * lv)), ""))
