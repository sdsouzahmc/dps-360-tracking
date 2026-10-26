# Flexitrack 360 API and integrations

Flexitrack 360 has a REST API (`api.py`, FastAPI). The school's other systems use it to send data in and read data out. It runs next to the web app on the same database:

```
pip install -r requirements.txt -r requirements-api.txt
SBT_DB=/data/school_bus.db uvicorn api:app --host 0.0.0.0 --port 8600
```

Interactive documentation is at `https://<host>:8600/docs`. The machine-readable spec is `openapi.json` (OpenAPI 3.1).

## Security
- **API keys:** every call sends `X-API-Key: <key>`. Keys are created and revoked in Flexitrack 360 → Setup & data → Integrations & API. One key per system, shown once and stored hashed.
- **Scopes:** each key gets only the scopes it needs:
  - `read`: look-ups
  - `scan`: bus, gate and RFID scans
  - `gps`: positions and telematics
  - `fuel`: fuel transactions
  - `csms`: ERP sync
- **Transport:** HTTPS only in production, behind the school's reverse proxy or firewall. Allow-list the source IPs of CSMS, the tracker vendor and Woqod.
- **Audit:** every write is recorded in the audit log with the key's name.

## Endpoints

| Method | Path | Scope | Used by |
|---|---|---|---|
| GET | /health | — | monitoring |
| GET | /v1/students, /v1/students/{code} | read | CSMS, reports, BI |
| GET | /v1/presence | read | dashboards |
| GET | /v1/buses, /v1/trips, /v1/trips/{id}/manifest | read | CSMS, reports |
| GET | /v1/attendance | read | CSMS (class attendance back to the ERP) |
| GET | /v1/alerts, /v1/inspections | read | security systems |
| GET | /v1/fleet/job-orders, /v1/fleet/documents | read | finance / ERP |
| GET | /v1/eta?bus=&stop_id= | read | parent app, SMS |
| POST | /v1/scans/bus | scan | bus RFID / card readers |
| POST | /v1/scans/gate | scan | gate turnstiles / readers |
| POST | /v1/rfid | scan | any RFID reader registered in Setup |
| POST | /v1/gps/positions | gps | vehicle tracker (FVTS / GPS box) |
| POST | /v1/telematics/events | gps | vehicle tracker (harsh braking, acceleration, idling) |
| POST | /v1/fuel/transactions | fuel | Woqod fuel-card feed |
| POST | /v1/csms/sync | csms | CSMS ERP (students, staff, buses); `dry_run` shows the changes first |

## Integration plan per system
1. **CSMS ERP (students, parents, staff, vehicles, routes, stops).**
   - Flexitrack 360 pulls from the CSMS API on a schedule (nightly plus on demand) once DPS IT shares the base URL and a key. The settings are in Setup → Integrations → CSMS ERP.
   - CSMS can also push changes to `POST /v1/csms/sync`.
   - Until then, a CSMS export (CSV, Excel or JSON) is uploaded in the same screen.
   - Changes are always shown as new, changed and missing before anything is written. CSMS stays the master: no data is typed twice.
2. **Vehicle tracker.**
   - The vendor pushes positions every 15–30 s, plus events, to `/v1/gps/positions` and `/v1/telematics/events`.
   - Positions give the live map, odometer readings for preventive maintenance, and ETA for parents.
   - Speeds above the limit (Setup, default 80 km/h) and harsh-driving events raise alerts and feed the Driver behaviour score.
3. **Woqod fuel.**
   - Statements (CSV or Excel) are imported now in Setup → Integrations → Woqod fuel.
   - With API access, Woqod or a middleware posts the same lines to `/v1/fuel/transactions`.
   - Lines are matched by plate or card number and deduplicated by transaction id. Over-tank fills, odometer rollbacks and low km per litre are flagged.
4. **RFID readers (bus doors, gates).**
   - Each reader is registered to a bus door (in or out) or a gate. A tag read posts to `/v1/rfid` and is processed exactly like a card scan, with the same wrong-bus, unknown-tag and exit-without-pass checks.
   - Scan time is logged for the scan-speed report.
5. **SMS, push and email.**
   - Notifications are created once and queued per channel.
   - On production hosting, an SMS gateway (e.g. Ooredoo), Firebase push and the school's mail server deliver them.

## Hosting (IT requirement: cloud or on-premise, primary and backup)
- **Servers:** the app, API and PostgreSQL database run on a primary server, with a hot standby (streaming replication) and a cold backup (nightly encrypted dumps kept 30 days).
- **Hosting choice:** cloud or DPS servers.
- **Demo vs production:** the demo on Streamlit Community Cloud uses SQLite and resets on restart. It is for demonstration only.
