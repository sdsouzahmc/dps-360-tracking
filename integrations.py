"""Flexitrack 360 integrations (DPS IT requirements + 14 Sep email):
- CSMS ERP sync: students, parents, staff, vehicles, routes and stops pulled from the ERP (API or exported file), shown as a diff, then applied.
- Woqod fuel: statement import (CSV / Excel) now; the same mapping takes Woqod's API feed when the school gets access.
- Vehicle tracker (GPS / telematics): positions → odometer, speed alerts, ETA; harsh braking / acceleration events → driver behaviour.
- RFID readers: a tag read on a bus or gate reader becomes the same scan as a card (wrong bus / unknown tag checks unchanged).
- API keys for the REST API (api.py)."""
import hashlib
import json
import math
import random
import secrets
from datetime import datetime, timedelta

import db
from db import q, q1, ts

SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (id INTEGER PRIMARY KEY, name TEXT, key_hash TEXT UNIQUE, prefix TEXT, scopes TEXT, created_by TEXT, ts TEXT,
  active INTEGER DEFAULT 1, last_used TEXT);
CREATE TABLE IF NOT EXISTS gps_positions (id INTEGER PRIMARY KEY, ts TEXT, bus_id INTEGER, lat REAL, lon REAL, speed_kmh REAL, heading REAL,
  odometer INTEGER, ignition INTEGER, source TEXT);
CREATE INDEX IF NOT EXISTS ix_gps ON gps_positions(bus_id, ts);
CREATE TABLE IF NOT EXISTS telematics_events (id INTEGER PRIMARY KEY, ts TEXT, date TEXT, bus_id INTEGER, driver_id INTEGER, kind TEXT, value REAL,
  limit_value REAL, lat REAL, lon REAL, source TEXT);
CREATE INDEX IF NOT EXISTS ix_tele ON telematics_events(date, bus_id);
CREATE TABLE IF NOT EXISTS integration_log (id INTEGER PRIMARY KEY, ts TEXT, system TEXT, direction TEXT, status TEXT, records INTEGER, message TEXT);
CREATE TABLE IF NOT EXISTS rfid_readers (id INTEGER PRIMARY KEY, reader_id TEXT UNIQUE, point TEXT, bus_id INTEGER, gate_id INTEGER, note TEXT);
CREATE TABLE IF NOT EXISTS rfid_tags (uid TEXT PRIMARY KEY, code TEXT, note TEXT);
"""

SCOPES = ["read", "scan", "gps", "fuel", "csms"]
TELE_KINDS = {"SPEEDING": "Over the speed limit", "HARSH_BRAKE": "Harsh braking", "HARSH_ACCEL": "Rash acceleration", "HARSH_TURN": "Sharp cornering",
              "IDLE": "Long idling"}


def ensure_schema(con=None):
    with db.tx(con) as c:
        c.executescript(SCHEMA)


def log(system, direction, status, records, message, con=None):
    db.x("INSERT INTO integration_log(ts, system, direction, status, records, message) VALUES (?,?,?,?,?,?)",
         (ts(), system, direction, status, records, message), con)


def last_log(system, con=None):
    return q1("SELECT * FROM integration_log WHERE system=? ORDER BY id DESC LIMIT 1", (system,), con)


# ------------------------------------------------------------------ API keys
def _h(key):
    return hashlib.sha256(("dps360-api:" + key).encode()).hexdigest()


def create_api_key(name, scopes, user, con=None):
    """Returns the key ONCE (only its hash is stored)."""
    key = "dps360_" + secrets.token_urlsafe(24)
    db.x("INSERT INTO api_keys(name, key_hash, prefix, scopes, created_by, ts, active) VALUES (?,?,?,?,?,?,1)",
         (name, _h(key), key[:14], ",".join(scopes), user, ts()), con)
    db.audit(user, "admin", "API_KEY_CREATE", "api_key", name, ",".join(scopes), con)
    return key


def revoke_api_key(kid, user, con=None):
    db.x("UPDATE api_keys SET active=0 WHERE id=?", (kid,), con)
    db.audit(user, "admin", "API_KEY_REVOKE", "api_key", kid, "", con)


def check_api_key(key, scope, con=None):
    r = q1("SELECT * FROM api_keys WHERE key_hash=? AND active=1", (_h(key or ""),), con)
    if not r or (scope and scope not in (r["scopes"] or "").split(",")):
        return None
    db.x("UPDATE api_keys SET last_used=? WHERE id=?", (ts(), r["id"]), con)
    return r


def api_keys(con=None):
    return q("SELECT id, name, prefix, scopes, created_by, ts, active, last_used FROM api_keys ORDER BY id DESC", (), con)


# ------------------------------------------------------------------ CSMS ERP sync
CSMS_FIELDS = {
    "students": {"code": ["student_id", "admission_no", "code", "studentcode"], "name": ["name", "student_name", "full_name"],
                 "class_name": ["class", "grade", "class_name"], "section": ["section", "division"], "gender": ["gender", "sex"],
                 "transport": ["transport", "transport_mode", "mode"], "batch": ["shift", "batch"], "grp": ["group", "grp", "session"]},
    "staff": {"card_no": ["employee_id", "emp_id", "card_no", "staff_id"], "name": ["name", "employee_name"], "role": ["role", "designation"],
              "phone": ["phone", "mobile"], "dept": ["department", "dept"]},
    "buses": {"bus_no": ["bus_no", "vehicle_no", "bus"], "plate": ["plate", "plate_no", "registration"], "capacity": ["capacity", "seats"],
              "route_name": ["route", "route_name"]},
}


def _pick(row, names):
    low = {str(k).lower().strip().replace(" ", "_"): v for k, v in row.items()}
    for n in names:
        if n in low and low[n] not in (None, "") and not (isinstance(low[n], float) and low[n] != low[n]):
            return str(low[n]).strip()
    return None


def csms_diff(entity, rows, con=None):
    """Compare ERP rows with Flexitrack 360: new, changed (field by field) and missing (in Flexitrack 360 but not in the ERP)."""
    f = CSMS_FIELDS[entity]
    key = {"students": "code", "staff": "card_no", "buses": "bus_no"}[entity]
    table = {"students": "students", "staff": "staff", "buses": "buses"}[entity]
    cur = {str(r[key]).upper(): r for r in q(f"SELECT * FROM {table}", (), con) if r.get(key)}
    new, changed, seen = [], [], set()
    for r in rows:
        rec = {k: _pick(r, names) for k, names in f.items()}
        if entity == "students" and rec.get("class_name") and not str(rec["class_name"]).startswith("Grade") and str(rec["class_name"])[:1].isdigit():
            rec["class_name"] = f"Grade {rec['class_name']}"
        if entity == "students" and rec.get("transport"):
            rec["transport"] = "BUS" if "BUS" in rec["transport"].upper() else "PRIVATE"
        k = (rec.get(key) or "").upper()
        if not k:
            continue
        seen.add(k)
        if k not in cur:
            new.append(rec)
        else:
            d = {c: (cur[k].get(c), v) for c, v in rec.items() if v is not None and c != key and str(cur[k].get(c) or "") != str(v)}
            if d:
                changed.append({"key": k, "id": cur[k]["id"], "changes": d})
    missing = [{"key": k, "id": v["id"], "name": v.get("name")} for k, v in cur.items() if k not in seen
               and (entity != "students" or v.get("active", 1))]
    return {"entity": entity, "new": new, "changed": changed, "missing": missing}


def csms_apply(diff, user, deactivate_missing=False, con=None):
    """Apply a diff: insert new, update changed, optionally deactivate students missing from the ERP. Returns counts."""
    e = diff["entity"]
    table = {"students": "students", "staff": "staff", "buses": "buses"}[e]
    n_new = n_upd = n_off = 0
    with db.tx(con) as c:
        for rec in diff["new"]:
            cols = [k for k, v in rec.items() if v is not None]
            if e == "students":
                rec.setdefault("transport", "BUS")
                cols = [k for k, v in rec.items() if v is not None]
            c.execute(f"INSERT INTO {table}({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [rec[k] for k in cols])
            n_new += 1
        for ch in diff["changed"]:
            sets = ", ".join(f"{k}=?" for k in ch["changes"])
            c.execute(f"UPDATE {table} SET {sets} WHERE id=?", [v[1] for v in ch["changes"].values()] + [ch["id"]])
            n_upd += 1
        if deactivate_missing and e == "students":
            for m in diff["missing"]:
                c.execute("UPDATE students SET active=0 WHERE id=?", (m["id"],))
                n_off += 1
        db.audit(user, "admin", "CSMS_SYNC", e, "", f"new {n_new}, updated {n_upd}, deactivated {n_off}", c)
    log("CSMS", "IN", "OK", n_new + n_upd + n_off, f"{e}: new {n_new}, updated {n_upd}, deactivated {n_off}", con)
    return {"new": n_new, "updated": n_upd, "deactivated": n_off}


def csms_config(con=None):
    return {"base_url": db.setting("csms_base_url", "", con) or "", "has_key": bool(db.setting("csms_api_key", "", con)),
            "schedule": db.setting("csms_schedule", "Nightly 02:00 + on demand", con)}


def csms_pull(entity, con=None):
    """Pull one entity from the CSMS API (GET {base_url}/{entity}) — used once school IT shares the endpoint and key."""
    cfg = csms_config(con)
    if not cfg["base_url"] or not cfg["has_key"]:
        raise RuntimeError("CSMS API not configured yet — set the base URL and key in Setup → Integrations (from school IT).")
    import urllib.request
    req = urllib.request.Request(f"{cfg['base_url'].rstrip('/')}/{entity}", headers={"Authorization": f"Bearer {db.setting('csms_api_key', '', con)}",
                                                                                   "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode())
    return data.get("data", data) if isinstance(data, dict) else data


# ------------------------------------------------------------------ Woqod fuel statement
WOQOD_FIELDS = {"ts": ["transaction_date", "date", "date_time", "transaction date", "datetime", "time"],
                "plate": ["plate", "plate_no", "vehicle", "vehicle_no", "plate number", "vehicle number"],
                "card": ["card", "card_no", "card number", "fahes card", "card_number"],
                "litres": ["litres", "liters", "quantity", "qty", "volume", "quantity (l)"],
                "amount": ["amount", "amount (qar)", "value", "total", "net amount"],
                "station": ["station", "station name", "site", "location"],
                "odometer": ["odometer", "km", "mileage", "odometer reading"],
                "receipt": ["transaction_id", "receipt", "receipt_no", "transaction id", "reference", "txn id"]}


def woqod_import(df, user, con=None):
    """Import a Woqod fuel statement: matches each line to a bus by plate or fuel-card number; skips lines already imported."""
    import campus
    cols = {str(c).lower().strip(): c for c in df.columns}
    m = {}
    for k, names in WOQOD_FIELDS.items():
        for n in names:
            if n in cols:
                m[k] = cols[n]
                break
    if "litres" not in m or ("plate" not in m and "card" not in m):
        return 0, [], f"Need a litres column and a plate or card column. Found: {', '.join(map(str, df.columns))}"
    buses = q("SELECT id, bus_no, plate FROM buses", (), con)
    by_plate = {"".join(ch for ch in (b["plate"] or "") if ch.isalnum()).upper(): b["id"] for b in buses}
    by_no = {b["bus_no"].upper(): b["id"] for b in buses}
    by_card = {r["card_no"]: r["bus_id"] for r in q("SELECT DISTINCT card_no, bus_id FROM fuel_logs WHERE card_no IS NOT NULL", (), con)}
    n, flagged, problems = 0, [], []
    import fleet
    for i, r in df.iterrows():
        g = lambda k: (None if k not in m or r[m[k]] is None or (isinstance(r[m[k]], float) and r[m[k]] != r[m[k]]) else r[m[k]])
        rec = str(g("receipt") or "")
        if rec and q1("SELECT id FROM fuel_logs WHERE receipt_no=?", (rec,), con):
            continue
        plate = "".join(ch for ch in str(g("plate") or "") if ch.isalnum()).upper()
        bid = by_plate.get(plate) or by_no.get(str(g("plate") or "").upper()) or by_card.get(str(g("card") or ""))
        if not bid:
            problems.append(f"row {int(i) + 2}: vehicle {g('plate') or g('card')} not matched to a bus")
            continue
        when = campus._parse_ts(g("ts")) or db.now()
        litres = float(g("litres"))
        amount = float(g("amount")) if g("amount") is not None else None
        odo = int(float(g("odometer"))) if g("odometer") is not None else None
        fid, flags = fleet.add_fuel(bid, litres, odo, user, str(g("station") or ""), str(g("card") or ""), rec,
                                    price=(amount / litres) if amount and litres else None, amount=amount, when=when, source="Woqod statement", con=con)
        n += 1
        if flags:
            flagged.append((bid, flags))
    log("WOQOD", "IN", "OK" if not problems else "PARTIAL", n, f"{n} fuel line(s) imported; {len(problems)} not matched", con)
    return n, flagged, ("; ".join(problems[:8]) + (" …" if len(problems) > 8 else "")) if problems else ""


def woqod_template(con=None):
    b = q("SELECT bus_no, plate FROM buses ORDER BY id LIMIT 3", (), con)
    t = db.today()
    return [{"Transaction ID": f"WQ{t.replace('-', '')}{i:03d}", "Transaction Date": f"{t} 0{6 + i}:1{i}", "Plate Number": x_["plate"] or x_["bus_no"],
             "Card Number": f"WQ-{10001 + i}", "Station Name": "Woqod Al Wakrah", "Quantity (L)": 110 + 10 * i, "Amount (QAR)": round((110 + 10 * i) * 2.05, 2),
             "Odometer": None} for i, x_ in enumerate(b)]


# ------------------------------------------------------------------ vehicle tracker: positions + telematics
def speed_limit(con=None):
    return float(db.setting("speed_limit_kmh", "80", con) or 80)


def _bus_id(ref, con=None):
    if ref is None:
        return None
    if isinstance(ref, int) or str(ref).isdigit():
        b = q1("SELECT id FROM buses WHERE id=?", (int(ref),), con)
        if b:
            return b["id"]
    b = q1("SELECT id FROM buses WHERE UPPER(bus_no)=? OR UPPER(plate)=? OR UPPER(fvts_vehicle)=?", (str(ref).upper(),) * 3, con)
    return b["id"] if b else None


def ingest_positions(points, source="tracker", con=None):
    """points: [{bus | plate | vehicle_id, ts, lat, lon, speed_kmh, heading, odometer, ignition}] → stored; speeding raises an alert (max one per bus per 10 min)."""
    import campus
    n, alerts = 0, 0
    lim = speed_limit(con)
    with db.tx(con) as c:
        for p in points:
            bid = _bus_id(p.get("bus") or p.get("plate") or p.get("vehicle_id"), c)
            if not bid:
                continue
            when = campus._parse_ts(p.get("ts")) or db.now()
            sp = float(p.get("speed_kmh") or 0)
            c.execute("""INSERT INTO gps_positions(ts, bus_id, lat, lon, speed_kmh, heading, odometer, ignition, source) VALUES (?,?,?,?,?,?,?,?,?)""",
                      (ts(when), bid, p.get("lat"), p.get("lon"), sp, p.get("heading"), p.get("odometer"), p.get("ignition"), source))
            n += 1
            if sp > lim:
                recent = q1("SELECT id FROM telematics_events WHERE bus_id=? AND kind='SPEEDING' AND ts>=?", (bid, ts(when - timedelta(minutes=10))), c)
                if not recent:
                    b = db.bus(bid, c)
                    c.execute("""INSERT INTO telematics_events(ts, date, bus_id, driver_id, kind, value, limit_value, lat, lon, source) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                              (ts(when), when.strftime("%Y-%m-%d"), bid, b["driver_id"], "SPEEDING", sp, lim, p.get("lat"), p.get("lon"), source))
                    campus.raise_alert("SPEEDING", "MEDIUM", f"{b['bus_no']} at {sp:.0f} km/h (limit {lim:.0f}) — driver {db.staff_name(b['driver_id'])}",
                                       when.strftime("%Y-%m-%d"), None, "BUS", bid, audience="transport", when=when, con=c)
                    alerts += 1
    if n:
        log("GPS", "IN", "OK", n, f"{n} position(s), {alerts} speeding alert(s)", con)
    return n, alerts


def ingest_events(events, source="tracker", con=None):
    """Tracker events: [{bus, ts, kind: HARSH_BRAKE | HARSH_ACCEL | HARSH_TURN | IDLE | SPEEDING, value, lat, lon}]."""
    import campus
    n = 0
    with db.tx(con) as c:
        for e in events:
            bid = _bus_id(e.get("bus") or e.get("plate") or e.get("vehicle_id"), c)
            kind = str(e.get("kind", "")).upper()
            if not bid or kind not in TELE_KINDS:
                continue
            when = campus._parse_ts(e.get("ts")) or db.now()
            b = db.bus(bid, c)
            c.execute("""INSERT INTO telematics_events(ts, date, bus_id, driver_id, kind, value, limit_value, lat, lon, source) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                      (ts(when), when.strftime("%Y-%m-%d"), bid, b["driver_id"], kind, e.get("value"), e.get("limit"), e.get("lat"), e.get("lon"), source))
            if kind in ("HARSH_BRAKE", "HARSH_ACCEL", "HARSH_TURN"):
                campus.raise_alert("HARSH_DRIVING", "LOW", f"{b['bus_no']}: {TELE_KINDS[kind].lower()} ({e.get('value') or ''}) — driver "
                                   f"{db.staff_name(b['driver_id'])}", when.strftime("%Y-%m-%d"), None, "BUS", bid, audience="transport", when=when, con=c)
            n += 1
    if n:
        log("TELEMATICS", "IN", "OK", n, f"{n} event(s)", con)
    return n


def last_position(bus_id, con=None):
    return q1("SELECT * FROM gps_positions WHERE bus_id=? ORDER BY ts DESC LIMIT 1", (bus_id,), con)


def _km(a_lat, a_lon, b_lat, b_lon):
    r = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lon - a_lon)
    return 2 * r * math.asin(math.sqrt(math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2))


def eta_minutes(bus_id, stop_id, max_age_min=5, con=None):
    """ETA from the latest live GPS position to a stop (road factor 1.35, 24 km/h urban average). None when the feed is not live."""
    p = last_position(bus_id, con)
    s = db.stop(stop_id, con)
    if not p or not s or not s.get("lat"):
        return None
    age = (db.now() - datetime.strptime(p["ts"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
    if age > max_age_min:
        return None
    return max(1, round(_km(p["lat"], p["lon"], s["lat"], s["lon"]) * 1.35 / 24 * 60))


def driver_behaviour(date_from, date_to, con=None):
    """Events per driver and a simple score (100 minus 5 per speeding, 3 per harsh event, 1 per long idle)."""
    rows = q("""SELECT t.kind, t.bus_id, t.driver_id, b.bus_no, s.name driver FROM telematics_events t JOIN buses b ON b.id=t.bus_id
                LEFT JOIN staff s ON s.id=t.driver_id WHERE t.date BETWEEN ? AND ?""", (date_from, date_to), con)
    agg = {}
    for r in rows:
        k = (r["driver_id"], r["bus_no"], r["driver"])
        a = agg.setdefault(k, {"Driver": r["driver"] or "—", "Bus": r["bus_no"], "SPEEDING": 0, "HARSH_BRAKE": 0, "HARSH_ACCEL": 0, "HARSH_TURN": 0, "IDLE": 0})
        a[r["kind"]] = a.get(r["kind"], 0) + 1
    out = []
    for a in agg.values():
        a["Score"] = max(0, 100 - 5 * a["SPEEDING"] - 3 * (a["HARSH_BRAKE"] + a["HARSH_ACCEL"] + a["HARSH_TURN"]) - a["IDLE"])
        out.append(a)
    return sorted(out, key=lambda a: a["Score"])


def seed_telematics(con=None):
    """Demo telematics for the last school days (events only; no alerts on past days)."""
    if q1("SELECT COUNT(*) n FROM telematics_events", (), con)["n"]:
        return
    import engine as E
    r = random.Random(91)
    buses = q("SELECT id, driver_id FROM buses", (), con)
    rows = []
    for d in E.school_days_back(4):
        for b in buses:
            risk = 0.25 if b["id"] % 9 == 0 else 0.05            # a few drivers drive harder than the rest
            for kind, base in (("SPEEDING", 0.6), ("HARSH_BRAKE", 1.0), ("HARSH_ACCEL", 0.7), ("HARSH_TURN", 0.3), ("IDLE", 0.8)):
                for _ in range(sum(1 for _ in range(4) if r.random() < base * risk * 2)):
                    t_ = f"{d} {r.choice([6, 7, 12, 14, 15, 17, 19]):02d}:{r.randint(0, 59):02d}:00"
                    val = {"SPEEDING": r.randint(84, 104), "HARSH_BRAKE": round(r.uniform(0.45, 0.8), 2), "HARSH_ACCEL": round(r.uniform(0.4, 0.7), 2),
                           "HARSH_TURN": round(r.uniform(0.4, 0.6), 2), "IDLE": r.randint(11, 35)}[kind]
                    rows.append((t_, d, b["id"], b["driver_id"], kind, val, 80 if kind == "SPEEDING" else None, None, None, "Tracker (demo feed)"))
    with db.tx(con) as c:
        c.executemany("""INSERT INTO telematics_events(ts, date, bus_id, driver_id, kind, value, limit_value, lat, lon, source) VALUES (?,?,?,?,?,?,?,?,?,?)""", rows)


def demo_drive(bus_id, minutes=6, overspeed=True, con=None):
    """Send a short demo GPS track for one bus (one point every 30 s along its stops) — to see positions, ETA and a speed alert."""
    stops = db.stops_of_bus(bus_id, con) or []
    if len(stops) < 2:
        return 0, 0
    now = db.now()
    pts = []
    for i in range(minutes * 2):
        a, b = stops[i % (len(stops) - 1)], stops[i % (len(stops) - 1) + 1]
        f = (i % 4) / 4
        sp = 92 if (overspeed and i == minutes) else 35 + (i * 7) % 25
        pts.append({"bus": bus_id, "ts": ts(now - timedelta(seconds=30 * (minutes * 2 - i))), "lat": a["lat"] + (b["lat"] - a["lat"]) * f,
                    "lon": a["lon"] + (b["lon"] - a["lon"]) * f, "speed_kmh": sp, "heading": 90, "ignition": 1})
    return ingest_positions(pts, "Demo drive", con)


# ------------------------------------------------------------------ RFID readers
def rfid_scan(reader_id, uid, when=None, con=None):
    """A tag read on a reader: maps the reader to its bus or gate and the tag to a card number, then scans as usual."""
    import campus
    import engine as E
    rd = q1("SELECT * FROM rfid_readers WHERE reader_id=?", (reader_id,), con)
    if not rd:
        return {"ok": False, "level": "error", "message": f"Unknown RFID reader {reader_id} — register it in Setup → Integrations."}
    tag = q1("SELECT code FROM rfid_tags WHERE uid=?", (str(uid).upper(),), con)
    code = tag["code"] if tag else str(uid)
    if rd["point"] == "GATE":
        return campus.gate_scan(code, rd["gate_id"], f"RFID {reader_id}", method="RFID", when=when, con=con)
    run = E.open_run(rd["bus_id"])
    if not run:
        return {"ok": False, "level": "error", "message": "No trip in progress on this bus — start the trip on the crew tablet first."}
    import uuid
    p = last_position(rd["bus_id"], con) or {}
    ctx = {"operator": f"RFID {reader_id}", "role": "device", "method": "RFID", "captured_ts": ts(when), "client_uuid": str(uuid.uuid4()),
           "lat": p.get("lat"), "lon": p.get("lon")}
    return E.process(run["id"], "CHECK_IN" if rd["point"] == "BUS_IN" else "CHECK_OUT", ctx, badge_code=code)


def register_reader(reader_id, point, bus_id=None, gate_id=None, note="", con=None):
    db.x("INSERT OR REPLACE INTO rfid_readers(reader_id, point, bus_id, gate_id, note) VALUES (?,?,?,?,?)", (reader_id, point, bus_id, gate_id, note), con)


def status(con=None):
    """One line per integration for the Setup screen."""
    lp = q1("SELECT MAX(ts) t, COUNT(DISTINCT bus_id) n FROM gps_positions WHERE ts>=?", (ts(db.now() - timedelta(minutes=10)),), con)
    return [
        {"System": "CSMS ERP (students, parents, staff, vehicles, routes)", "Mode": "API" if csms_config(con)["base_url"] else "File import (CSV / Excel / JSON)",
         "Last": (last_log("CSMS", con) or {}).get("ts", "—"), "State": "Waiting for CSMS API details from school IT" if not csms_config(con)["base_url"] else "Configured"},
        {"System": "Woqod fuel", "Mode": "Statement import (CSV / Excel); API when the school has access", "Last": (last_log("WOQOD", con) or {}).get("ts", "—"),
         "State": "Ready"},
        {"System": "Vehicle tracker (GPS, speed, harsh driving)", "Mode": "REST push to /v1/gps/positions and /v1/telematics/events",
         "Last": lp["t"] or (last_log("GPS", con) or {}).get("ts", "—"), "State": f"Live for {lp['n']} bus(es)" if lp["n"] else "No live feed yet"},
        {"System": "RFID readers (bus and gate)", "Mode": "REST push to /v1/rfid", "Last": "—",
         "State": f"{q1('SELECT COUNT(*) n FROM rfid_readers', (), con)['n']} reader(s) registered"},
        {"System": "SMS / push / email", "Mode": "In-app messages today; gateway on production hosting", "Last": "—", "State": "Gateway to choose (e.g. Ooredoo SMS, Firebase push)"},
    ]
