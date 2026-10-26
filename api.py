"""Flexitrack 360 REST API — for the CSMS ERP, the vehicle tracker, RFID readers, Woqod and other school systems.

Run next to the web app, on the same database:
    pip install fastapi uvicorn
    SBT_DB=/path/to/school_bus.db uvicorn api:app --host 0.0.0.0 --port 8600
Interactive documentation: http://<host>:8600/docs  (OpenAPI JSON at /openapi.json)

Every call needs an API key in the `X-API-Key` header. Keys are created in Flexitrack 360 → Setup & data → Integrations, each with scopes:
read (look-ups), scan (bus / gate / RFID scans), gps (positions and telematics), fuel (fuel transactions), csms (ERP sync).
"""
from datetime import datetime
from typing import List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

import campus
import db
import engine as E
import fleet
import integrations as I
import security_ext as SX

app = FastAPI(title="Flexitrack 360 API", version="1.0",
              description="Student, transport, gate, fleet and security data for the school. All times are Qatar time "
                          "(YYYY-MM-DD HH:MM:SS). Authenticate with the X-API-Key header.")


def key(scope):
    def dep(x_api_key: str = Header(..., description="API key from Setup → Integrations")):
        k = I.check_api_key(x_api_key, scope)
        if not k:
            raise HTTPException(401 if not I.check_api_key(x_api_key, None) else 403, f"API key missing, revoked or without the '{scope}' scope")
        return k
    return dep


# ------------------------------------------------------------------ models
class BusScan(BaseModel):
    bus: str = Field(..., examples=["BUS-001"], description="Bus number or plate")
    code: str = Field(..., description="Card / tag number read from the student's ID card")
    direction: str = Field("CHECK_IN", pattern="^(CHECK_IN|CHECK_OUT)$")
    lat: Optional[float] = None
    lon: Optional[float] = None
    ts: Optional[str] = Field(None, description="Capture time; defaults to now")


class GateScan(BaseModel):
    gate_id: int = Field(..., ge=1, le=6)
    code: str
    direction: Optional[str] = Field(None, pattern="^(IN|OUT)$", description="Leave empty for automatic IN/OUT")
    operator: str = "API"


class RfidRead(BaseModel):
    reader_id: str
    uid: str
    ts: Optional[str] = None


class Position(BaseModel):
    bus: str = Field(..., description="Bus number, plate or tracker vehicle id")
    ts: Optional[str] = None
    lat: float
    lon: float
    speed_kmh: float = 0
    heading: Optional[float] = None
    odometer: Optional[int] = None
    ignition: Optional[int] = None


class TeleEvent(BaseModel):
    bus: str
    ts: Optional[str] = None
    kind: str = Field(..., description="HARSH_BRAKE, HARSH_ACCEL, HARSH_TURN, IDLE or SPEEDING")
    value: Optional[float] = None
    limit: Optional[float] = None
    lat: Optional[float] = None
    lon: Optional[float] = None


class FuelTxn(BaseModel):
    transaction_id: str
    ts: str
    plate: Optional[str] = None
    card: Optional[str] = None
    litres: float
    amount: Optional[float] = None
    station: Optional[str] = None
    odometer: Optional[int] = None


class CsmsBatch(BaseModel):
    students: List[dict] = []
    staff: List[dict] = []
    buses: List[dict] = []
    dry_run: bool = True
    deactivate_missing: bool = False


# ------------------------------------------------------------------ health
@app.get("/health", tags=["system"])
def health():
    return {"ok": True, "time": db.ts(), "school": db.setting("school_name", db.SCHOOL_NAME)}


# ------------------------------------------------------------------ look-ups (read)
@app.get("/v1/students", tags=["students"])
def students(active: bool = True, transport: Optional[str] = Query(None, pattern="^(BUS|PRIVATE)$"), limit: int = Query(500, le=5000), offset: int = 0,
             _=Depends(key("read"))):
    sql = "SELECT id, code, name, class_name, section, batch, grp, transport, gender, active FROM students WHERE 1=1"
    a = []
    if active:
        sql += " AND active=1"
    if transport:
        sql += " AND transport=?"
        a.append(transport)
    return db.q(sql + " ORDER BY id LIMIT ? OFFSET ?", a + [limit, offset])


@app.get("/v1/students/{code}", tags=["students"])
def student(code: str, date: Optional[str] = None, _=Depends(key("read"))):
    s = db.q1("SELECT id FROM students WHERE UPPER(code)=?", (code.upper(),))
    if not s:
        raise HTTPException(404, "Student not found")
    d = date or db.today()
    card = campus.student_card(s["id"])
    return {"student": card, "state_today": campus.presence(d).get(s["id"]) if d == db.today() else None,
            "buses": [{"trip": n, "bus": (E.manifest_for_student(card, d, n, None) or {}).get("bus_no")} for n in db.TRIPS],
            "timeline": campus.timeline(s["id"], d)}


@app.get("/v1/presence", tags=["students"])
def presence(_=Depends(key("read"))):
    from collections import Counter
    return dict(Counter(campus.presence(db.today()).values()))


@app.get("/v1/buses", tags=["transport"])
def buses(_=Depends(key("read"))):
    return db.q("SELECT id, bus_no, plate, capacity, route_name, fvts_vehicle FROM buses ORDER BY bus_no")


@app.get("/v1/trips", tags=["transport"])
def trips(date: Optional[str] = None, _=Depends(key("read"))):
    return db.q("""SELECT r.id, b.bus_no, r.trip_no, r.status, r.start_ts, r.end_ts FROM trip_runs r JOIN buses b ON b.id=r.bus_id WHERE r.date=?
                   ORDER BY r.trip_no, b.bus_no""", (date or db.today(),))


@app.get("/v1/trips/{run_id}/manifest", tags=["transport"])
def manifest(run_id: int, _=Depends(key("read"))):
    return [{k: ln[k] for k in ("student_id", "code", "name", "class", "purpose", "status", "stop_name", "entry_ts", "exit_ts")} for ln in E.run_lines(run_id)]


@app.get("/v1/attendance", tags=["students"])
def attendance(date: Optional[str] = None, _=Depends(key("read"))):
    return db.q("""SELECT s.code, s.name, s.class_name, s.section, a.status, a.source, a.ts FROM attendance a JOIN students s ON s.id=a.student_id
                   WHERE a.date=? ORDER BY s.class_name, s.section, s.name""", (date or db.today(),))


@app.get("/v1/alerts", tags=["security"])
def alerts(date: Optional[str] = None, status: Optional[str] = Query(None, pattern="^(OPEN|ACKNOWLEDGED|RESOLVED)$"), _=Depends(key("read"))):
    return campus.alerts(date or db.today(), status)


@app.get("/v1/inspections", tags=["security"])
def inspections(date: Optional[str] = None, _=Depends(key("read"))):
    return [{k: v for k, v in b.items() if k != "inspection"} | {"inspected_ts": (b["inspection"] or {}).get("ts")} for b in SX.inspections_board(date or db.today())]


@app.get("/v1/fleet/job-orders", tags=["fleet"])
def job_orders(status: Optional[str] = None, _=Depends(key("read"))):
    return fleet.job_orders(status)


@app.get("/v1/fleet/documents", tags=["fleet"])
def documents(state: Optional[str] = Query(None, pattern="^(EXPIRED|EXPIRING|VALID)$"), _=Depends(key("read"))):
    return [d for d in fleet.documents() if not state or d["state"] == state]


# ------------------------------------------------------------------ scans (scan)
@app.post("/v1/scans/bus", tags=["scans"])
def bus_scan(s: BusScan, _=Depends(key("scan"))):
    bid = I._bus_id(s.bus)
    if not bid:
        raise HTTPException(404, "Bus not found")
    run = E.open_run(bid)
    if not run:
        raise HTTPException(409, "No trip in progress on this bus")
    import uuid
    ctx = {"operator": "API", "role": "device", "method": "API", "captured_ts": s.ts or db.ts(), "client_uuid": str(uuid.uuid4()), "lat": s.lat, "lon": s.lon}
    return SX.timed("BUS", E.process, run["id"], s.direction, ctx, badge_code=s.code, device="api")


@app.post("/v1/scans/gate", tags=["scans"])
def gate_scan(s: GateScan, _=Depends(key("scan"))):
    res = SX.timed("GATE", campus.gate_scan, s.code, s.gate_id, s.operator, direction=s.direction, method="API", device="api")
    res.pop("person", None)
    return res


@app.post("/v1/rfid", tags=["scans"])
def rfid(r: RfidRead, _=Depends(key("scan"))):
    res = I.rfid_scan(r.reader_id, r.uid, datetime.strptime(r.ts, "%Y-%m-%d %H:%M:%S") if r.ts else None)
    res.pop("person", None)
    return res


# ------------------------------------------------------------------ vehicle tracker (gps)
@app.post("/v1/gps/positions", tags=["vehicle tracker"])
def positions(points: List[Position], _=Depends(key("gps"))):
    n, a = I.ingest_positions([p.model_dump() for p in points], "tracker API")
    return {"stored": n, "speed_alerts": a}


@app.post("/v1/telematics/events", tags=["vehicle tracker"])
def tele_events(events: List[TeleEvent], _=Depends(key("gps"))):
    return {"stored": I.ingest_events([e.model_dump() for e in events], "tracker API")}


@app.get("/v1/eta", tags=["vehicle tracker"])
def eta(bus: str, stop_id: int, _=Depends(key("read"))):
    bid = I._bus_id(bus)
    return {"bus": bus, "stop_id": stop_id, "eta_min": I.eta_minutes(bid, stop_id) if bid else None}


# ------------------------------------------------------------------ fuel (fuel)
@app.post("/v1/fuel/transactions", tags=["fleet"])
def fuel(txns: List[FuelTxn], _=Depends(key("fuel"))):
    import pandas as pd
    df = pd.DataFrame([{"Transaction ID": t.transaction_id, "Transaction Date": t.ts, "Plate Number": t.plate, "Card Number": t.card,
                        "Quantity (L)": t.litres, "Amount (QAR)": t.amount, "Station Name": t.station, "Odometer": t.odometer} for t in txns])
    n, flagged, problems = I.woqod_import(df, "Woqod API")
    return {"imported": n, "flagged": len(flagged), "problems": problems}


# ------------------------------------------------------------------ CSMS ERP (csms)
@app.post("/v1/csms/sync", tags=["CSMS ERP"])
def csms_sync(b: CsmsBatch, k=Depends(key("csms"))):
    out = {}
    for entity in ("students", "staff", "buses"):
        rows = getattr(b, entity)
        if not rows:
            continue
        d = I.csms_diff(entity, rows)
        out[entity] = {"new": len(d["new"]), "changed": len(d["changed"]), "missing": len(d["missing"])}
        if not b.dry_run:
            out[entity]["applied"] = I.csms_apply(d, f"api:{k['name']}", b.deactivate_missing)
    return {"dry_run": b.dry_run, "result": out}
