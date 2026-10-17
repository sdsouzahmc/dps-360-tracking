# DPS 360 · powered by AutoTrace

360-degree student tracking for DPS, built on the AutoTrace Student Transport Tracker engine.

**One live view of every child, all day:** bus pickup and drop-off, campus gates, class attendance, early pickup, visitors, staff, vehicle checks and security patrols.

## Apps (sign-in roles)
| App | Who | What |
|---|---|---|
| 360 Command centre | principal, manager, dispatcher | KPIs, where every student is now, headcount through the day, live alerts, the 5 transport sessions, bus map, arrivals by mode, attendance by grade, gate activity, safety compliance, last-days trends |
| Student 360 | office | One child's day: buses per session, gate scans, class attendance, early pickup, alerts, parent messages |
| Security app | security, guards | Gate scan with photo check (camera barcode / card reader), private-transport students, staff, visitor passes, early-pickup release, alerts (absent in class but on campus, exit without pass), gate log, patrol rounds + JWM export import |
| Teacher app | class teachers | Class list pre-filled from bus and gate scans → verify → submit; absent & not on campus → parents notified and child taken off the home bus; absent but on campus → security alert |
| Parent app | guardians | Campus in / out times and class status (no live bus tracking by default), absence, early-pickup request, journey changes, messages |
| Crew app | driver, supervisor, care-taker | DPS per-session routes, in-app pre-/post-trip vehicle checklist (critical defect blocks the start), scanning (Code 128 / Code 39 / QR), sweep |
| Bus assignment board | transport office | Per-session loads, move / swap / merge / switch to spare bus with capacity check |
| Campus & visitors, Vehicle & patrol checks | office | Early-pickup approvals, attendance tracker, visitors, staff, campus alerts, vehicle-check compliance |

## DPS transport sessions
| Trip | Time | Who | Buses |
|---|---|---|---|
| 1 | 05:45–07:25 | Morning pickup (KG, main school, stay-back) | 98 |
| 2 | 11:05–12:40 | KG drop-off + afternoon-shift pickup | 35 |
| 3 | 13:50–15:05 | 2:00 pm drop-off | 98 |
| 4 | 16:30–17:35 | Stay-back drop-off | 28 |
| 5 | 18:35–19:50 | Afternoon shift / late drop-off | 16 |

A student's bus can differ per session (table `trip_assign`).

## Demo data
Generated on first start (~20 s): ~4,800 students in 160 classes (≈3,400 by bus), 100 buses, ~420 pickup points across Doha, 6 gates, staff, visitors, patrols, the last 4 school days and **today up to the current time**. The day keeps running (simulated events are applied as they fall due). Setup → Demo tools has a **demo clock** (replay today at 06:20, 07:40, 10:30, 11:30, 14:10, 16:45, 18:50) and a pause switch.

Demo sign-ins (PIN): `principal` 1234 · `manager` manager123 (+ on-screen code) · `security` / `guard1` 1234 · `teacher1` (Grade 5-A) 1234 · `g001`…`g040` 1234 · `sup001` / `drv001` 1234 · `admin` admin123.

## Run
```
pip install -r requirements.txt
streamlit run streamlit_app.py
```
Data is in SQLite (`school_bus.db`, path override with `SBT_DB`). The demo database resets when the hosting restarts; a trial needs persistent hosting (PostgreSQL) — see the gap analysis.
