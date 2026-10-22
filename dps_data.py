"""DPS 360 demo data at DPS scale (~100 buses, ~4,800 students, 5 daily sessions) and a day simulator.

generate() builds the school (classes, teachers, staff, gates), families, students with homes across Doha, ~420 shared pickup
points, the per-trip bus assignment (98 / 35 / 98 / 28 / 16 buses) and the routes, then replays the last school days and
today up to the current time.

A day is a script of timed events (vehicle checks, trip starts, card scans on the buses, gate scans, visitors, early pickups,
class attendance, patrol rounds). Past days are applied at once; today's script is stored in sim_events and tick()
applies whatever has fallen due, so the dashboard keeps moving while the demo runs."""
import json
import math
import random
import threading
from datetime import datetime, timedelta

import db
from db import TRIPS, SCHOOL_STOP, q, q1, ts

N_BUSES = 100
SIM_TAG = "sim"
_LOCK = threading.Lock()

FIRST_M = ["Aarav", "Arjun", "Vihaan", "Aditya", "Reyansh", "Ishaan", "Sai", "Krishna", "Rohan", "Kabir", "Ayaan", "Atharv", "Dhruv", "Arnav",
           "Rudra", "Yash", "Pranav", "Advik", "Shaurya", "Aryan", "Nikhil", "Rahul", "Siddharth", "Varun", "Karthik", "Abhinav", "Rehan",
           "Mohammed", "Ahmed", "Omar", "Yusuf", "Zayan", "Hamdan", "Faiz", "Imran", "Rayan", "Adam", "Joel", "Aaron", "Ethan", "Nathan",
           "Christo", "Alan", "Tanish", "Harsh", "Manav", "Parth", "Dev", "Om", "Laksh"]
FIRST_F = ["Aadhya", "Ananya", "Diya", "Saanvi", "Myra", "Anika", "Navya", "Ira", "Kiara", "Avni", "Riya", "Prisha", "Sara", "Aarohi",
           "Ishita", "Tara", "Meera", "Pooja", "Sneha", "Kavya", "Nandini", "Shreya", "Aditi", "Zara", "Inaya", "Hiba", "Fatima", "Aisha",
           "Maryam", "Sana", "Noor", "Alina", "Hannah", "Angel", "Maria", "Esther", "Neha", "Divya", "Lakshmi", "Gauri", "Vaidehi", "Mahi"]
LAST = ["Sharma", "Verma", "Gupta", "Nair", "Menon", "Pillai", "Kumar", "Reddy", "Iyer", "Rao", "Patel", "Shah", "Mehta", "Joshi",
        "Kulkarni", "Desai", "Singh", "Chauhan", "Malhotra", "Kapoor", "Bose", "Das", "Banerjee", "Mukherjee", "Thomas", "George",
        "Mathew", "Varghese", "Fernandes", "D'Souza", "Khan", "Sheikh", "Ansari", "Qureshi", "Siddiqui", "Hussain", "Rahman", "Ali",
        "Agarwal", "Bansal", "Saxena", "Tiwari", "Mishra", "Pandey", "Yadav", "Naidu", "Hegde", "Shetty", "Kamath", "Bhat"]
AREAS = [  # (area, lat, lon, weight) — Doha residential areas with many school families
    ("Al Mansoura", 25.2700, 51.5390, 9), ("Najma", 25.2700, 51.5480, 7), ("Old Airport", 25.2530, 51.5560, 9),
    ("Al Hilal", 25.2600, 51.5450, 6), ("Fereej Bin Mahmoud", 25.2830, 51.5190, 6), ("Al Sadd", 25.2860, 51.5040, 6),
    ("Al Muntazah", 25.2690, 51.5270, 6), ("Umm Ghuwailina", 25.2780, 51.5520, 5), ("Abu Hamour", 25.2420, 51.4980, 7),
    ("Ain Khaled", 25.2280, 51.4720, 6), ("Al Aziziya", 25.2500, 51.4610, 5), ("Muaither", 25.2930, 51.3980, 4),
    ("Al Rayyan", 25.2920, 51.4250, 4), ("Al Gharrafa", 25.3360, 51.4560, 4), ("Madinat Khalifa", 25.3170, 51.4800, 5),
    ("Al Thumama", 25.2330, 51.5370, 5), ("Duhail", 25.3480, 51.4760, 3), ("Al Waab", 25.2640, 51.4700, 4),
    ("Al Wakrah", 25.1700, 51.6020, 5), ("Al Wukair", 25.1500, 51.5600, 3), ("Lusail", 25.4200, 51.4900, 2),
    ("The Pearl", 25.3700, 51.5500, 1), ("Al Messila", 25.2940, 51.4700, 3), ("Bu Hamour (Barwa)", 25.2210, 51.5170, 4),
]
LANDMARK = ["Masjid", "Park", "Petrol Station", "Al Meera", "Health Centre", "Roundabout", "Family Food Centre", "Signal", "Lulu Express",
            "Metro Station", "Bus Shelter", "Pharmacy", "Post Office", "Community Hall", "Street", "Villa Compound", "Bakery", "Clinic"]
STAFF_DEPTS = ["Mathematics", "Science", "English", "Hindi", "Arabic", "Social Studies", "Computer Science", "Physical Education", "Art",
               "Music", "Islamic Studies", "French", "Library", "Counselling"]
VIS_PURPOSE = [("Parent meeting", 45), ("Fee payment", 20), ("Admission enquiry", 40), ("Delivery", 15), ("Maintenance (AC)", 120),
               ("IT vendor", 90), ("Canteen supplier", 30), ("Ministry inspection", 120), ("Interview", 60), ("Collect documents", 20)]
VIS_COMPANY = ["—", "—", "—", "Qatar Cool", "Ooredoo Business", "Al Meera", "Snoonu", "Aramex", "MoEHE", "Bluewater Facilities", "Carrefour B2B"]
GUARDS = ["Ram Bahadur Thapa", "Suresh Gurung", "Mohammed Rafiq", "Anil Tamang", "Kamal Hossain", "Dil Bahadur Rai", "Prakash Limbu"]


def _name(r, female):
    return f"{r.choice(FIRST_F if female else FIRST_M)} {r.choice(LAST)}"


def _phone(r):
    return f"+974 {r.choice('3567')}{r.randint(100, 999)} {r.randint(1000, 9999)}"


def _offset(lat, lon, r, max_m):
    d, a = r.uniform(0, max_m), r.uniform(0, 2 * math.pi)
    return lat + d * math.cos(a) / 111320, lon + d * math.sin(a) / (111320 * math.cos(math.radians(lat)))


def _dist(a, b):
    import planner
    return planner.dist_m(a[0], a[1], b[0], b[1])


# ====================================================================== generate
def generate(seed=26, history_days=4, now=None):
    r = random.Random(seed)
    now = now or db.now()
    con = db.connect()
    try:
        _build_school(con, r)
        con.commit()
    finally:
        con.close()
    import planner
    planner.ensure_schema()
    import resets
    resets.snapshot_assignment()
    for d in __import__("engine").school_days_back(history_days):
        apply_day(d, replay=True)
    prepare_today(now)


def _build_school(con, r):
    S = lambda k, v: con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?,?)", (k, str(v)))
    S("school_name", db.SCHOOL_NAME)
    S("school_lat", db.SCHOOL_LAT)
    S("school_lon", db.SCHOOL_LON)
    S("parent_bus_location", "0")           # DPS: parents see campus in / out times, not live bus tracking
    S("require_precheck", "1")
    school = (db.SCHOOL_LAT, db.SCHOOL_LON)

    def staff(name, role, phone, bus_id=None, card=None, dept=None, gender="M"):
        return con.execute("INSERT INTO staff(name, role, phone, bus_id, card_no, dept, gender) VALUES (?,?,?,?,?,?,?)",
                           (name, role, phone, bus_id, card, dept, gender)).lastrowid

    def user(u, pin, role, display, staff_id=None, guardian_id=None):
        con.execute("INSERT INTO users(username, pin_hash, role, staff_id, guardian_id, display_name) VALUES (?,?,?,?,?,?)",
                    (u, db.pin_hash(pin), role, staff_id, guardian_id, display))

    emp = [1000]

    def card():
        emp[0] += r.randint(1, 3)
        return f"EMP{emp[0]}"

    # ---- office users
    aid = staff("System Administrator", "admin", _phone(r), card=card(), dept="IT")
    mid = staff("Rajesh Menon", "manager", _phone(r), card=card(), dept="Transport")
    did = staff("Sanjay Kulkarni", "dispatcher", _phone(r), card=card(), dept="Transport")
    pid = staff("Dr. Anita Sharma", "principal", _phone(r), card=card(), dept="Principal's office", gender="F")
    user("admin", "admin123", "admin", "System Administrator", aid)
    user("manager", "manager123", "manager", "Rajesh Menon (Transport Lead)", mid)
    user("dispatcher", "1234", "dispatcher", "Sanjay Kulkarni (Transport Office)", did)
    user("principal", "1234", "principal", "Dr. Anita Sharma (Principal)", pid)
    sec_head = staff("Major (Retd.) Vikram Singh", "security", _phone(r), card=card(), dept="Security")
    user("security", "1234", "security", "Vikram Singh (Security Manager)", sec_head)
    for k, gname in enumerate(GUARDS, 1):
        g = staff(gname, "security", _phone(r), card=card(), dept="Security")
        if k <= 3:
            user(f"guard{k}", "1234", "security", f"{gname} (Gate {k})", g)
    for k in range(1, 4):
        sid = staff(_name(r, True), "receiving", _phone(r), card=card(), dept="Bus bay", gender="F")
        user(f"recv{k}", "1234", "receiving", con.execute("SELECT name FROM staff WHERE id=?", (sid,)).fetchone()[0], sid)

    # ---- classes and class teachers
    classes = []
    for g in ("KG1", "KG2"):
        for sec in "ABCDEFGH":
            classes.append((g, sec, 1, "KG"))
    for n in range(1, 13):
        for sec in "ABCDEFGHIJ":
            classes.append((f"Grade {n}", sec, 1, "Main"))
    for n in range(1, 9):
        for sec in "PQR":
            classes.append((f"Grade {n}", sec, 2, "Afternoon"))
    class_rows = []
    for i, (g, sec, shift, kind) in enumerate(classes):
        fem = r.random() < 0.8 if kind == "KG" else r.random() < 0.6
        tname = ("Ms. " if fem else "Mr. ") + _name(r, fem)
        dept = "Kindergarten" if kind == "KG" else r.choice(STAFF_DEPTS[:8])
        tid = staff(tname, "teacher", _phone(r), card=card(), dept=f"{dept} · class teacher {g}-{sec}", gender="F" if fem else "M")
        cid = con.execute("INSERT INTO classes(name, section, shift, teacher_id, room) VALUES (?,?,?,?,?)",
                          (g, sec, shift, tid, f"{'K' if kind == 'KG' else 'B'}{100 + i}")).lastrowid
        class_rows.append((cid, g, sec, shift, kind, tid, tname))
    demo_teachers = [("teacher1", "Grade 5", "A"), ("teacher2", "KG2", "C"), ("teacher3", "Grade 9", "D"), ("teacher4", "Grade 6", "Q")]
    for u, g, sec in demo_teachers:
        c = next(c for c in class_rows if c[1] == g and c[2] == sec)
        user(u, "1234", "teacher", f"{c[6]} ({g}-{sec})", c[5])
    for _ in range(60):     # subject teachers and other staff
        fem = r.random() < 0.55
        staff(("Ms. " if fem else "Mr. ") + _name(r, fem), "teacher", _phone(r), card=card(), dept=r.choice(STAFF_DEPTS), gender="F" if fem else "M")
    for dept, n in (("Administration", 14), ("Accounts", 6), ("Nurse", 3), ("Maintenance", 12), ("Housekeeping", 18), ("IT", 4)):
        for _ in range(n):
            fem = r.random() < (0.7 if dept in ("Nurse", "Housekeeping") else 0.4)
            staff(_name(r, fem), "staff", _phone(r), card=card(), dept=dept, gender="F" if fem else "M")

    # ---- pickup points (shared physical stops)
    wsum = sum(a[3] for a in AREAS)
    points = []
    for a, lat, lon, w in AREAS:
        n = max(6, round(420 * w / wsum))
        cnt = {}
        for k in range(n):
            plat, plon = _offset(lat, lon, r, 1500)
            lm = r.choice(LANDMARK)
            cnt[lm] = cnt.get(lm, 0) + 1
            nm = f"{a} · {lm} {r.randint(800, 990)}" if lm == "Street" else f"{a} · {lm}" + (f" {cnt[lm]}" if cnt[lm] > 1 else "")
            points.append({"area": a, "name": nm, "lat": plat, "lon": plon, "w": w})
    for p in points:
        p["ang"] = math.atan2(p["lat"] - school[0], (p["lon"] - school[1]) * math.cos(math.radians(school[0])))
        p["dist"] = _dist((p["lat"], p["lon"]), school)

    # ---- buses and crew
    bus_ids = []
    for b in range(1, N_BUSES + 1):
        plate = str(r.randint(100000, 999999))
        cap = 34 if b % 9 == 0 else 45
        bid = con.execute("INSERT INTO buses(bus_no, plate, capacity, fvts_vehicle, route_name) VALUES (?,?,?,?,?)",
                          (f"BUS-{b:03d}", plate, cap, f"{plate} BUS-{b:03d}", "")).lastrowid
        d = staff(_name(r, False), "driver", _phone(r), bid, card=card(), dept="Transport")
        s = staff(_name(r, r.random() < 0.6), "supervisor", _phone(r), bid, card=card(), dept="Transport")
        c = staff(_name(r, True), "caretaker", _phone(r), bid, card=card(), dept="Transport", gender="F")
        con.execute("UPDATE buses SET driver_id=?, supervisor_id=?, caretaker_id=? WHERE id=?", (d, s, c, bid))
        con.execute("INSERT INTO devices(name, bus_id, status, registered_at) VALUES (?,?,?,?)", (f"BUS-{b:03d} Tablet", bid, "ACTIVE", ts()))
        for role, sid_ in (("driver", d), ("supervisor", s), ("caretaker", c)):
            pre = {"driver": "drv", "supervisor": "sup", "caretaker": "care"}[role]
            user(f"{pre}{b:03d}", "1234", role, con.execute("SELECT name FROM staff WHERE id=?", (sid_,)).fetchone()[0], sid_)
        bus_ids.append(bid)

    # ---- families and students
    weights = [p["w"] for p in points]
    n_stu = 0
    g_count = 0
    stays = 0
    studs = []          # (sid, batch, grp, transport, point index)
    families = []

    def new_family():
        nonlocal g_count
        last = r.choice(LAST)
        pi = r.choices(range(len(points)), weights)[0]
        hlat, hlon = _offset(points[pi]["lat"], points[pi]["lon"], r, 260)
        fa = con.execute("INSERT INTO guardians(name, phone, email, relation, verified) VALUES (?,?,?,?,1)",
                         (f"{r.choice(FIRST_M)} {last}", _phone(r), f"parent{g_count + 1}@example.com", "Father")).lastrowid
        g_count += 1
        mo = None
        if r.random() < 0.6:
            mo = con.execute("INSERT INTO guardians(name, phone, email, relation, verified) VALUES (?,?,?,?,1)",
                             (f"{r.choice(FIRST_F)} {last}", _phone(r), f"parent{g_count + 1}@example.com", "Mother")).lastrowid
            g_count += 1
        fam = {"last": last, "pi": pi, "home": (hlat, hlon), "fa": fa, "mo": mo, "bus": r.random() < 0.7,
               "driver": f"{r.choice(FIRST_M)} {r.choice(['Khan', 'Kumar', 'Ali', 'Thapa', 'Rai'])}" if r.random() < 0.2 else None}
        families.append(fam)
        return fam

    fam = None
    left_in_fam = 0
    for cid, g, sec, shift, kind, tid, tname in class_rows:
        for _ in range(r.randint(28, 32)):
            if left_in_fam <= 0 or fam is None:
                fam = new_family()
                left_in_fam = 1 if r.random() < 0.65 else 2
            left_in_fam -= 1
            n_stu += 1
            female = r.random() < 0.5
            transport = "BUS" if fam["bus"] or (kind == "KG" and r.random() < 0.3) else "PRIVATE"
            grp = kind
            if kind == "Main" and transport == "BUS" and int(g.split()[1]) >= 6 and stays < 300 and r.random() < 0.16:
                grp, stays = "StayBack", stays + 1
            batch = shift
            sid = con.execute("""INSERT INTO students(code, name, class_name, section, batch, grp, bus_id, stop_id, transport, gender)
                                 VALUES (?,?,?,?,?,?,?,?,?,?)""",
                              (f"DPS{24000 + n_stu}", f"{r.choice(FIRST_F if female else FIRST_M)} {fam['last']}", g, sec, batch, grp, None, None,
                               transport, "F" if female else "M")).lastrowid
            con.execute("INSERT INTO student_guardians VALUES (?,?,1)", (sid, fam["fa"]))
            if fam["mo"]:
                con.execute("INSERT INTO student_guardians VALUES (?,?,0)", (sid, fam["mo"]))
            for gid in [fam["fa"]] + ([fam["mo"]] if fam["mo"] else []):
                gname, gph, grel = con.execute("SELECT name, phone, relation FROM guardians WHERE id=?", (gid,)).fetchone()
                con.execute("INSERT INTO recipients(student_id, name, relation, phone, id_number) VALUES (?,?,?,?,?)",
                            (sid, gname, grel, gph, f"QID {r.randint(28000000000, 29999999999)}"))
            if fam["driver"]:
                con.execute("INSERT INTO recipients(student_id, name, relation, phone, id_number) VALUES (?,?,?,?,?)",
                            (sid, fam["driver"], "Family driver", _phone(r), f"QID {r.randint(28000000000, 29999999999)}"))
            con.execute("INSERT INTO emergency_contacts(student_id, name, relation, phone) VALUES (?,?,?,?)",
                        (sid, f"{r.choice(FIRST_M)} {fam['last']}", "Uncle", _phone(r)))
            code = f"26{n_stu:05d}"                  # barcode on the DPS ID card (Code 128 / Code 39)
            if r.random() < 0.01:
                old = f"25{n_stu:05d}"
                con.execute("INSERT INTO badges(code, student_id, status, issued_at, revoked_at, reason, replaced_by) VALUES (?,?,?,?,?,?,?)",
                            (old, sid, "REVOKED", ts(db.now() - timedelta(days=200)), ts(db.now() - timedelta(days=20)), "Lost card", code))
            con.execute("INSERT INTO badges(code, student_id, status, issued_at) VALUES (?,?,?,?)", (code, sid, "ACTIVE", ts(db.now() - timedelta(days=200))))
            studs.append({"id": sid, "batch": batch, "grp": grp, "transport": transport, "pi": fam["pi"], "home": fam["home"],
                          "fa": fam["fa"]})

    # ---- per-trip bus assignment and routes
    by_trip = {}
    for t_no, t in TRIPS.items():
        mv = {(b, g) for b, g, _ in t["movements"]}
        by_trip[t_no] = [s for s in studs if s["transport"] == "BUS" and (s["batch"], s["grp"]) in mv]
    stu_bus = {}
    route_area = {}
    for t_no, members in by_trip.items():
        n = TRIPS[t_no]["buses"]
        members.sort(key=lambda s: (points[s["pi"]]["ang"], points[s["pi"]]["dist"]))
        bounds = [round(j * len(members) / n) for j in range(n + 1)]
        purpose_pick = any(m[2] == "PICKUP" for m in TRIPS[t_no]["movements"])
        for j in range(n):
            chunk = members[bounds[j]:bounds[j + 1]]
            if not chunk:
                continue
            bid = bus_ids[min(97, round(j * 98 / n))]
            # stops: one per pickup point used by the chunk, ordered as a route (far end first for pickups)
            pis = sorted({s["pi"] for s in chunk}, key=lambda i: -points[i]["dist"])
            order, cur = [pis[0]], pis[0]
            rest = set(pis[1:])
            while rest:
                nxt = min(rest, key=lambda i: _dist((points[cur]["lat"], points[cur]["lon"]), (points[i]["lat"], points[i]["lon"])))
                order.append(nxt)
                rest.discard(nxt)
                cur = nxt
            if not purpose_pick:
                order.reverse()
            stop_of = {}
            for k, pi in enumerate(order, 1):
                p = points[pi]
                stop_of[pi] = con.execute("INSERT INTO stops(bus_id, seq, name, area, lat, lon, trip_no) VALUES (?,?,?,?,?,?,?)",
                                          (bid, k, p["name"], p["area"], p["lat"], p["lon"], t_no)).lastrowid
            for s in chunk:
                con.execute("INSERT OR REPLACE INTO trip_assign(student_id, trip_no, bus_id, stop_id) VALUES (?,?,?,?)",
                            (s["id"], t_no, bid, stop_of[s["pi"]]))
                stu_bus.setdefault(s["id"], {})[t_no] = (bid, stop_of[s["pi"]])
            if t_no == 1:
                areas = {}
                for s in chunk:
                    areas[points[s["pi"]]["area"]] = areas.get(points[s["pi"]]["area"], 0) + 1
                top = sorted(areas, key=lambda a: -areas[a])[:2]
                route_area[bid] = " / ".join(top)
    for bid in bus_ids:
        con.execute("UPDATE buses SET route_name=? WHERE id=?", (f"Route {db.bus(bid, con)['bus_no'][4:]} – {route_area.get(bid, 'Spare bus')}", bid))
    for s in studs:
        if s["transport"] != "BUS":
            continue
        pt = 1 if s["batch"] == 1 else 2
        b, st_ = stu_bus[s["id"]][pt]
        con.execute("UPDATE students SET bus_id=?, stop_id=? WHERE id=?", (b, st_, s["id"]))

    # ---- student home locations (approved, transport enrolment)
    import planner
    planner.ensure_schema(con)
    rows = []
    for s in studs:
        if s["transport"] != "BUS":
            continue
        zone = 20 + (s["id"] % 50)
        rows.append((s["id"], "PICKUP", f"Villa {1 + s['id'] * 13 % 90}, Street {800 + s['id'] * 7 % 200}, Zone {zone}, {points[s['pi']]['area']}",
                     round(s["home"][0], 6), round(s["home"][1], 6), "Main gate", s["fa"], 0))
        rows.append((s["id"], "DROPOFF", None, None, None, "Same as pickup", s["fa"], 1))
    con.executemany("""INSERT INTO student_locations(student_id, kind, address, lat, lon, notes, guardian_id, same_as_pickup, status, version,
                       reason, requested_by, requested_ts, decided_by, decided_ts) VALUES (?,?,?,?,?,?,?,?,'APPROVED',1,'Transport enrolment',
                       'system',?, 'system', ?)""", [x_ + (ts(), ts()) for x_ in rows])

    # ---- parent logins: a mix of bus / private / KG / afternoon / stay-back families
    picks, seen = [], set()
    want = [("KG", "BUS"), ("Main", "BUS"), ("Main", "PRIVATE"), ("Afternoon", "BUS"), ("StayBack", "BUS"), ("KG", "PRIVATE"), ("Afternoon", "PRIVATE")]
    for k in range(40):
        g_, tr = want[k % len(want)]
        for s in studs[(k * 97) % len(studs):] + studs:
            if s["grp"] == g_ and s["transport"] == tr and s["fa"] not in seen:
                seen.add(s["fa"])
                picks.append(s["fa"])
                break
    for i, gid in enumerate(picks, 1):
        user(f"g{i:03d}", "1234", "guardian", con.execute("SELECT name FROM guardians WHERE id=?", (gid,)).fetchone()[0], None, gid)
    con.execute("INSERT OR REPLACE INTO settings VALUES ('data_source', ?)",
                (f"DPS-scale demo: {n_stu:,} students ({sum(1 for s in studs if s['transport'] == 'BUS'):,} by bus), {N_BUSES} buses, "
                 f"{len(class_rows)} classes",))


# ====================================================================== day script
def _t(date, hm, plus_s=0):
    return datetime.strptime(f"{date} {hm}", "%Y-%m-%d %H:%M") + timedelta(seconds=plus_s)


def build_day(date, seed=None):
    """All timed events of one school day (list of (datetime, kind, data))."""
    import planner
    import campus as camp
    r = random.Random(seed if seed is not None else int(date.replace("-", "")))
    ev = []
    E = lambda _w, _k, **d: ev.append((_w, _k, d))
    studs = q("SELECT id, batch, grp, transport, class_name, section, gender FROM students WHERE active=1")
    by_id = {s["id"]: s for s in studs}
    school = db.school_pos()
    BUSNO = {b_["id"]: b_["bus_no"] for b_ in q("SELECT id, bus_no FROM buses")}
    STOPS = {}
    for s_ in q("SELECT * FROM stops WHERE trip_no IS NOT NULL ORDER BY seq"):
        STOPS.setdefault((s_["bus_id"], s_["trip_no"]), []).append(s_)

    # ---- whole-day absences (≈4.5%): most declared by parents early in the morning
    absent = set(s["id"] for s in r.sample(studs, int(len(studs) * r.uniform(0.038, 0.052))))
    declared = set()
    for sid in absent:
        if r.random() < 0.72:
            declared.add(sid)
            E(_t(date, "05:00", r.randint(0, 5400)), "ABSENCE", sid=sid,
              reason=r.choice(["Sick", "Fever", "Family travel", "Doctor appointment", "Family function", "Not feeling well"]))

    # ---- early pickups (decided before the drop trip; collected at the gate)
    pres = [s for s in studs if s["id"] not in absent]
    early = r.sample(pres, r.randint(8, 14))
    early_ids = {s["id"] for s in early}
    for s in early:
        a_by, (d0, _) = camp.student_group_times(s["batch"], s["grp"])
        start = _t(date, a_by, 3600)
        end = _t(date, d0, -1800)
        if end <= start:
            continue
        pick = start + timedelta(seconds=r.randint(0, int((end - start).total_seconds())))
        req = pick - timedelta(minutes=r.randint(60, 180))
        rec = q("SELECT name, relation, phone, id_number FROM recipients WHERE student_id=? AND active=1", (s["id"],))
        rc = r.choice(rec) if rec else {"name": f"{r.choice(FIRST_M)} {r.choice(LAST)}", "relation": "Uncle", "phone": _phone(r), "id_number": ""}
        E(req, "EP_REQ", sid=s["id"], time=pick.strftime("%H:%M"), reason=r.choice(["Doctor appointment", "Family emergency", "Travel",
          "Dental check-up", "Visa medical", "Not feeling well"]), collector=rc["name"], relation=rc["relation"], phone=rc["phone"],
          qid=(rc["id_number"] or "").replace("QID ", "") or f"{r.randint(28000000000, 29999999999)}")
        E(req + timedelta(minutes=r.randint(5, 35)), "EP_DECIDE", sid=s["id"])
        E(pick + timedelta(minutes=r.randint(-5, 15)), "EP_OUT", sid=s["id"])

    # ---- buses: vehicle checks, runs, scans
    blocked_bus = r.choice(range(3, 98)) if r.random() < 0.7 else None
    first_trip, last_close = {}, {}
    for t_no, t in TRIPS.items():
        w0 = t["window"][0]
        rows = q("""SELECT ta.student_id, ta.bus_id, ta.stop_id FROM trip_assign ta JOIN students s ON s.id=ta.student_id
                    WHERE ta.trip_no=? AND s.active=1""", (t_no,))
        mv = {(b, g): p for b, g, p in t["movements"]}
        per_bus = {}
        for row in rows:
            per_bus.setdefault(row["bus_id"], []).append(row)
        for bid, lines in sorted(per_bus.items()):
            order = STOPS.get((bid, t_no), [])
            start = _t(date, w0, r.randint(-120, 540))
            late = r.random() < 0.035
            if late:
                start += timedelta(minutes=r.randint(14, 26))
            first_of_bus = bid not in first_trip
            if first_of_bus:
                first_trip[bid] = start
                chk = start - timedelta(minutes=r.randint(10, 28))
                defects = []
                if bid == blocked_bus:
                    defects = ["Dashboard: no warning lights"]
                    E(chk, "VCHECK", bus_id=bid, kind="PRE", trip_no=t_no, defects=defects)
                    E(chk + timedelta(minutes=18), "VCLEAR", bus_id=bid, note="Mechanic checked: loose sensor connector, refitted. Safe to run.")
                    start = chk + timedelta(minutes=r.randint(22, 30))
                    late = True
                else:
                    if r.random() < 0.06:
                        defects = [r.choice(["Mirrors clean and adjusted", "Air-conditioning cooling", "First-aid kit complete",
                                             "Horn, wipers and washer", "Bus clean, floor and seats"])]
                    E(chk, "VCHECK", bus_id=bid, kind="PRE", trip_no=t_no, defects=defects)
            if late:
                E(start, "INCIDENT", kind="LATE_START", severity="MEDIUM", bus_id=bid, trip_no=t_no,
                  details=f"{BUSNO[bid]} Trip {t_no} started {start:%H:%M}, scheduled {w0}"
                          + (" — critical pre-trip defect had to be cleared first" if bid == blocked_bus and first_of_bus else ""))
            E(start, "RUN_START", bus_id=bid, trip_no=t_no)
            tcur = start
            pos = school
            drop = [ln for ln in lines if mv.get((by_id[ln["student_id"]]["batch"], by_id[ln["student_id"]]["grp"])) == "DROPOFF"]
            pick = [ln for ln in lines if mv.get((by_id[ln["student_id"]]["batch"], by_id[ln["student_id"]]["grp"])) == "PICKUP"]
            if drop:
                for ln in drop:
                    sid = ln["student_id"]
                    if sid in absent or sid in early_ids:
                        continue
                    tcur += timedelta(seconds=r.randint(4, 10))
                    E(tcur, "SCAN", bus_id=bid, trip_no=t_no, sid=sid, kind="CHECK_IN", stop_id=SCHOOL_STOP)
                tcur += timedelta(minutes=2)
            else:
                pos = None
            for s_ in order:
                exits = [ln for ln in drop if ln["stop_id"] == s_["id"] and ln["student_id"] not in absent and ln["student_id"] not in early_ids]
                boards = [ln for ln in pick if ln["stop_id"] == s_["id"]]
                if not exits and not boards:
                    continue
                if pos is not None:
                    tcur += timedelta(minutes=planner.travel_min(pos, (s_["lat"], s_["lon"])) * r.uniform(0.9, 1.25))
                pos = (s_["lat"], s_["lon"])
                for ln in exits:
                    tcur += timedelta(seconds=r.randint(12, 35))
                    E(tcur, "SCAN", bus_id=bid, trip_no=t_no, sid=ln["student_id"], kind="CHECK_OUT", stop_id=s_["id"])
                for ln in boards:
                    sid = ln["student_id"]
                    tcur += timedelta(seconds=r.randint(10, 28))
                    if sid in absent:
                        if sid not in declared:
                            E(tcur, "SCAN", bus_id=bid, trip_no=t_no, sid=sid, kind="NO_SHOW", stop_id=s_["id"])
                        continue
                    if r.random() < 0.012:          # came privately today instead
                        E(tcur, "SCAN", bus_id=bid, trip_no=t_no, sid=sid, kind="NO_SHOW", stop_id=s_["id"])
                        E(_t(date, "07:00", r.randint(0, 1500)) if t_no == 1 else tcur + timedelta(minutes=30), "GATE", ptype="STUDENT", pid=sid,
                          dir="IN", gate=1, method="CARD", note="Bus student came privately today")
                        continue
                    E(tcur, "SCAN", bus_id=bid, trip_no=t_no, sid=sid, kind="CHECK_IN", stop_id=s_["id"],
                      manual=r.random() < 0.015)
                tcur += timedelta(seconds=45)
            if pick:
                tcur += timedelta(minutes=planner.travel_min(pos or school, school) * r.uniform(0.9, 1.2))
                for ln in pick:
                    tcur += timedelta(seconds=r.randint(3, 7))
                    E(tcur, "SCAN", bus_id=bid, trip_no=t_no, sid=ln["student_id"], kind="CHECK_OUT", stop_id=SCHOOL_STOP)
            else:
                tcur += timedelta(minutes=planner.travel_min(pos or school, school) * 0.9)
            tcur += timedelta(minutes=r.randint(2, 4))
            E(tcur, "RUN_CLOSE", bus_id=bid, trip_no=t_no)
            last_close[bid] = tcur
    for bid, tc in last_close.items():
        if r.random() < 0.95:          # ~5% of buses skip the post-trip check (shows on the compliance panel)
            d = ["New damage or warning lights reported"] if r.random() < 0.03 else []
            E(tc + timedelta(minutes=r.randint(3, 12)), "VCHECK", bus_id=bid, kind="POST", trip_no=None, defects=d)

    # ---- private students and staff at the gates
    for s in studs:
        sid = s["id"]
        if s["transport"] != "PRIVATE" or sid in absent:
            continue
        a_by, (d0, d1) = camp.student_group_times(s["batch"], s["grp"])
        gate = 2 if s["grp"] == "KG" else 1
        if r.random() < 0.008:          # forgot the card: no entry scan (teacher marks present → data-quality alert)
            E(_t(date, a_by, -r.randint(300, 2400)), "NOSCAN", sid=sid)
        else:
            arr = _t(date, a_by, -r.randint(120, 4200)) if r.random() > 0.05 else _t(date, a_by, r.randint(60, 2700))
            E(arr, "GATE", ptype="STUDENT", pid=sid, dir="IN", gate=gate, method="CARD", note="Late arrival" if arr > _t(date, a_by) else "",
              ref="Late" if arr > _t(date, a_by) else None)
        if sid not in early_ids:
            E(_t(date, d0, r.randint(0, int((_t(date, d1) - _t(date, d0)).total_seconds()))), "GATE", ptype="STUDENT", pid=sid, dir="OUT",
              gate=gate, method="CARD")
    for s in q("SELECT id, role, dept FROM staff WHERE role IN ('teacher','staff','principal','security','receiving','manager','dispatcher')"):
        if r.random() < 0.04:
            continue
        aft = s["dept"] and any(f"-{x_}" in s["dept"] for x_ in "PQR")
        tin = _t(date, "11:20" if aft else "06:00", r.randint(0, 3600))
        tout = _t(date, "18:50" if aft else "14:30", r.randint(0, 6000))
        E(tin, "GATE", ptype="STAFF", pid=s["id"], dir="IN", gate=4, method="CARD", ref=s["dept"])
        E(tout, "GATE", ptype="STAFF", pid=s["id"], dir="OUT", gate=4, method="CARD", ref=s["dept"])

    # ---- unauthorized exit attempts (security stops the child)
    for s in r.sample([s for s in pres if s["transport"] == "PRIVATE" and s["grp"] == "Main"], r.randint(1, 2)):
        E(_t(date, "10:00", r.randint(0, 9000)), "GATE_TRY_OUT", sid=s["id"])

    # ---- visitors
    teachers = [x_["name"] for x_ in q("SELECT name FROM staff WHERE role='teacher' LIMIT 200")]
    for k in range(r.randint(28, 42)):
        purpose, dur = r.choice(VIS_PURPOSE)
        female = r.random() < 0.35
        name = _name(r, female)
        tin = _t(date, "07:40", r.randint(0, 22000))
        phone = _phone(r)
        host = r.choice(teachers) if purpose in ("Parent meeting", "Interview") else r.choice(["Accounts office", "Admissions office", "Transport office",
                                                                                                "Facilities", "Principal's office", "IT office"])
        E(tin, "VISITOR_IN", name=name, phone=phone, qid=f"{r.randint(28000000000, 29999999999)}",
          company=r.choice(VIS_COMPANY) if purpose not in ("Parent meeting", "Fee payment", "Admission enquiry", "Collect documents") else "Parent",
          purpose=purpose, host=host, vehicle=f"{r.randint(100000, 999999)}" if r.random() < 0.5 else "", expected=dur,
          gender="F" if female else "M")
        stay = dur * r.uniform(0.5, 1.3) if r.random() > 0.06 else dur + r.randint(60, 140)
        E(tin + timedelta(minutes=stay), "VISITOR_OUT", phone=phone)
    for hm in ("09:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00"):
        E(_t(date, hm), "OVERSTAY_CHECK")

    # ---- class attendance (pre-filled list → teacher verifies → submit)
    demo_cls = {r_["id"] for r_ in q("""SELECT c.id FROM classes c JOIN users u ON u.staff_id=c.teacher_id
                                        WHERE u.role='teacher' AND u.username LIKE 'teacher%'""")}   # teacher1–4 only, not every class login
    for c in q("SELECT * FROM classes"):
        a_by = camp.SHIFT_TIMES[c["shift"]][1]
        if r.random() < 0.03 or (c["id"] in demo_cls and date == db.today()):
            continue          # some classes submit late / the demo teachers' classes stay open for the live demo
        when = _t(date, a_by, r.randint(300, 2400))
        mem = [s["id"] for s in studs if s["class_name"] == c["name"] and s["section"] == c["section"]]
        force_abs = [sid for sid in mem if sid not in absent and r.random() < 0.0008]
        E(when, "ATT", class_id=c["id"], force_absent=force_abs)

    # ---- security patrol rounds (guard tour) — night plan of the evening before + day rounds
    cps = [g[0] for g in camp.GATES]
    prev = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    for k, hm in enumerate(["18:00", "20:00", "22:00"]):
        for cp_i, cp in enumerate(cps):
            planned = _t(prev, hm, cp_i * 300)
            _patrol(E, r, planned, "Night patrol", k + 1, cp, GUARDS[k % 2])
    for k, hm in enumerate(["00:00", "02:00", "04:00"]):
        for cp_i, cp in enumerate(cps):
            _patrol(E, r, _t(date, hm, cp_i * 300), "Night patrol", k + 4, cp, GUARDS[k % 2], pdate=prev)   # part of last night's plan
    for k, hm in enumerate(["06:00", "08:00", "10:00", "12:00", "14:00", "16:00"]):
        for cp_i, cp in enumerate(cps):
            _patrol(E, r, _t(date, hm, cp_i * 300), "Day patrol", k + 1, cp, GUARDS[2 + k % 3])
    ev.sort(key=lambda e: e[0])
    return ev


def _patrol(E, r, planned, plan, rnd_no, cp, guard, pdate=None):
    x_ = r.random()
    pdate = pdate or planned.strftime("%Y-%m-%d")
    if x_ < 0.035:
        E(planned + timedelta(minutes=20), "PATROL", plan=plan, round_no=rnd_no, cp=cp, guard=guard, planned=ts(planned), actual=None, status="OMITTED",
          pdate=pdate)
    else:
        act = planned + timedelta(minutes=r.uniform(-3, 6) if x_ > 0.09 else r.uniform(12, 25))
        E(act, "PATROL", plan=plan, round_no=rnd_no, cp=cp, guard=guard, planned=ts(planned), actual=ts(act),
          status="ARRIVED" if x_ > 0.09 else "LATE", pdate=pdate)


# ====================================================================== apply
RECV = None


def _apply(con, events, notify=True, rnd=None):
    """Apply events in order on one connection."""
    import campus
    import engine as E
    rnd = rnd or random.Random(7)
    global RECV
    RECV = RECV or [x_["name"] for x_ in q("SELECT name FROM staff WHERE role='receiving'", (), con)] or ["Bus bay staff"]
    runs = {}
    lines = {}
    BUS = {b_["id"]: b_ for b_ in q("SELECT * FROM buses", (), con)}
    NAME = {r_[0]: r_[1] for r_ in con.execute("SELECT id, name FROM staff").fetchall()}
    STOP, RECIP, GUARD, SNAME = {}, {}, {}, {}

    def stop_of(i):
        if i not in STOP:
            STOP[i] = db.stop(i, con)
        return STOP[i]

    def line_of(rid, sid):
        if rid not in lines:
            lines[rid] = {r_[0]: [r_[1], r_[2], r_[3]] for r_ in
                          con.execute("SELECT student_id, id, status, purpose FROM manifest WHERE run_id=?", (rid,)).fetchall()}
        return lines[rid].get(sid)

    def run_of(date, bid, t_no):
        k = (date, bid, t_no)
        if k not in runs:
            rr = q1("""SELECT r.id, r.status FROM trip_runs r JOIN sim_runs s ON s.run_id=r.id WHERE r.bus_id=? AND r.date=? AND r.trip_no=?""",
                    (bid, date, t_no), con)
            runs[k] = rr["id"] if rr and rr["status"] in ("IN_PROGRESS", "PENDING_SYNC") else None
        return runs[k]

    try:
        manual = set(json.loads(db.setting("manual_buses", "{}", con) or "{}").get(db.today(), [])) | reserved_buses(con)
    except ValueError:
        manual = reserved_buses(con)
    for when, kind, d in events:
        date = when.strftime("%Y-%m-%d")
        if manual and d.get("bus_id") in manual and kind in ("RUN_START", "VCHECK", "VCLEAR", "INCIDENT") and date == db.today():
            continue                     # bus handed to manual testing today
        try:
            if kind in ("ABSENCE", "ATT", "EP_DECIDE"):
                lines.clear()
            if kind == "ABSENCE":
                if not q1("SELECT id FROM absences WHERE student_id=? AND date=? AND trip_no IS NULL", (d["sid"], date), con):
                    con.execute("""INSERT INTO absences(student_id, date, trip_no, reason, declared_by, source, status, created_at)
                                   VALUES (?,?,?,?,?,?,?,?)""", (d["sid"], date, None, d["reason"], "guardian", "GUARDIAN", "APPROVED", ts(when)))
                    con.execute("""UPDATE manifest SET status='ABSENT_DECLARED' WHERE student_id=? AND status='EXPECTED'
                                   AND run_id IN (SELECT id FROM trip_runs WHERE date=?)""", (d["sid"], date))
            elif kind == "VCHECK":
                b = BUS[d["bus_id"]]
                items = campus.PRE_CHECKS if d["kind"] == "PRE" else campus.POST_CHECKS
                ans = {i: ("DEFECT" if i in d["defects"] else "OK") for _, i, _c in items}
                campus.save_check(d["bus_id"], date, d.get("trip_no"), d["kind"], ans, f"drv{d['bus_id']:03d}", NAME.get(b["driver_id"], ""),
                                  odometer=(rnd.randint(40000, 260000) and __import__('fleet').odo_model(d['bus_id'], when.date())), fuel=rnd.choice(["Full", "3/4", "1/2"]), when=when, con=con)
            elif kind == "VCLEAR":
                v = q1("SELECT id FROM vehicle_checks WHERE bus_id=? AND date=? AND status='BLOCKED' ORDER BY id DESC LIMIT 1", (d["bus_id"], date), con)
                if v:
                    con.execute("UPDATE vehicle_checks SET status='CLEARED', cleared_by=?, cleared_ts=?, cleared_note=? WHERE id=?",
                                ("Rajesh Menon (Transport Lead)", ts(when), d["note"], v["id"]))
                    con.execute("""UPDATE campus_alerts SET status='RESOLVED', resolved_by='manager', resolved_ts=?, resolution=? WHERE kind='VEHICLE_DEFECT'
                                   AND ptype='BUS' AND pid=? AND date=? AND status<>'RESOLVED'""", (ts(when), d["note"], d["bus_id"], date))
            elif kind == "INCIDENT":
                E.open_incident(d["kind"], d["severity"], d["details"], None, d["bus_id"], None, con, when)
            elif kind == "RUN_START":
                if q1("SELECT id FROM trip_runs WHERE bus_id=? AND date=? AND trip_no=?", (d["bus_id"], date, d["trip_no"]), con):
                    continue          # someone ran / is running this trip by hand (live demo) — leave it alone
                b = db.bus(d["bus_id"], con)
                first = db.stop(SCHOOL_STOP, con) if any(m[2] == "DROPOFF" for m in TRIPS[d["trip_no"]]["movements"]) else \
                    (db.stops_of_bus(d["bus_id"], con, d["trip_no"]) or [db.stop(SCHOOL_STOP, con)])[0]
                dev = q1("SELECT id FROM devices WHERE bus_id=? AND status='ACTIVE'", (d["bus_id"],), con)
                rid, _m = E.start_run(d["bus_id"], date, d["trip_no"], b["driver_id"], b["supervisor_id"], b["caretaker_id"], first["lat"],
                                      first["lon"], f"sup{d['bus_id']:03d}", "supervisor", dev["id"] if dev else None, when=when, con=con)
                if rid:
                    lines.pop(rid, None)
                    con.execute("INSERT OR IGNORE INTO sim_runs(run_id) VALUES (?)", (rid,))
                    runs[(date, d["bus_id"], d["trip_no"])] = rid
            elif kind == "SCAN":
                rid = run_of(date, d["bus_id"], d["trip_no"])
                if not rid:
                    continue
                ln = line_of(rid, d["sid"])
                if not ln:
                    continue
                mid, status, purpose = ln
                stop = stop_of(d["stop_id"])
                gps_ok = rnd.random() > 0.02
                lat = stop["lat"] + rnd.uniform(-.0004, .0004) if gps_ok else None
                lon = stop["lon"] + rnd.uniform(-.0004, .0004) if gps_ok else None
                b = BUS[d["bus_id"]]
                sup = NAME.get(b["supervisor_id"], "")
                k = d["kind"]
                method = "MANUAL" if d.get("manual") else "CARD"
                if k == "CHECK_IN" and status == "EXPECTED":
                    con.execute("UPDATE manifest SET status='ONBOARD', entry_ts=?, entry_lat=?, entry_lon=? WHERE id=?", (ts(when), lat, lon, mid))
                    ln[1] = "ONBOARD"
                    msg = (f"boarded {b['bus_no']} at school at {when:%H:%M} (going home)" if purpose == "DROPOFF"
                           else f"boarded {b['bus_no']} at {stop['name']} at {when:%H:%M}")
                    nk = "BOARDED"
                elif k == "CHECK_OUT" and status == "ONBOARD":
                    ln[1] = "COMPLETED"
                    if d["stop_id"] == SCHOOL_STOP:
                        con.execute("""UPDATE manifest SET status='COMPLETED', exit_ts=?, exit_lat=?, exit_lon=?, handover='SCHOOL_RECEIPT', received_by=?
                                       WHERE id=?""", (ts(when), lat, lon, rnd.choice(RECV), mid))
                        msg, nk = f"arrived at school on {b['bus_no']} at {when:%H:%M} and was received at the bus bay", "AT_SCHOOL"
                    else:
                        if d["sid"] not in RECIP:
                            RECIP[d["sid"]] = q("SELECT name, relation FROM recipients WHERE student_id=? AND active=1", (d["sid"],), con)
                        rec = RECIP[d["sid"]]
                        rc = rnd.choice(rec) if rec else {"name": "Guardian", "relation": "Parent"}
                        con.execute("""UPDATE manifest SET status='COMPLETED', exit_ts=?, exit_lat=?, exit_lon=?, handover='HOME_RELEASE', recipient_name=?,
                                       recipient_relation=? WHERE id=?""", (ts(when), lat, lon, rc["name"], rc["relation"], mid))
                        msg, nk = f"got off {b['bus_no']} at {stop['name']} at {when:%H:%M} — handed to {rc['name']} ({rc['relation']})", "DROPPED"
                elif k == "NO_SHOW" and status == "EXPECTED":
                    con.execute("UPDATE manifest SET status='NO_SHOW' WHERE id=?", (mid,))
                    ln[1] = "NO_SHOW"
                    msg, nk = f"was not at the stop for {b['bus_no']} at {when:%H:%M}", "NO_SHOW"
                else:
                    continue
                tsw = ts(when)
                con.execute("""INSERT INTO events(client_uuid, run_id, manifest_id, student_id, badge_code, kind, result, reason, captured_ts, received_ts,
                               lat, lon, accuracy, gps_ok, stop_id, operator, device_id, method, manual_reason, verified_by, sync_state, connectivity)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (f"sim-{rid}-{d['sid']}-{k}", rid, mid, d["sid"], None, k, "ACCEPTED", "No show" if k == "NO_SHOW" else None,
                             tsw, tsw, lat, lon, round(rnd.uniform(4, 20), 1) if gps_ok else None, 1 if gps_ok else 0, d["stop_id"], sup, None,
                             method, "Card forgotten" if method == "MANUAL" else None, NAME.get(b["caretaker_id"]) if method == "MANUAL" else None,
                             "SYNCED", "Online"))
                if notify:
                    if d["sid"] not in SNAME:
                        SNAME[d["sid"]] = q1("SELECT name FROM students WHERE id=?", (d["sid"],), con)["name"]
                        GUARD[d["sid"]] = [r_[0] for r_ in con.execute("SELECT guardian_id FROM student_guardians WHERE student_id=?", (d["sid"],))]
                    con.executemany("""INSERT INTO notifications(ts, guardian_id, student_id, run_id, channel, kind, message, status, attempts, sent_ts)
                                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                                    [(tsw, g, d["sid"], rid, "IN_APP", nk, f"{SNAME[d['sid']]} {msg}.", "SENT", 1, tsw) for g in GUARD[d["sid"]]])
            elif kind == "RUN_CLOSE":
                rid = run_of(date, d["bus_id"], d["trip_no"])
                if not rid:
                    continue
                for ln in q("SELECT id, purpose FROM manifest WHERE run_id=? AND status='ONBOARD'", (rid,), con):
                    con.execute("""UPDATE manifest SET status=?, exit_ts=?, handover=?, received_by=? WHERE id=?""",
                                ("COMPLETED" if ln["purpose"] == "PICKUP" else "RETURNED", ts(when),
                                 "SCHOOL_RECEIPT" if ln["purpose"] == "PICKUP" else "RETURNED_TO_SCHOOL", rnd.choice(RECV), ln["id"]))
                con.execute("UPDATE manifest SET status='NO_SHOW' WHERE run_id=? AND status='EXPECTED'", (rid,))
                b = BUS[d["bus_id"]]
                sweeper = NAME.get(rnd.choice([b["supervisor_id"], b["caretaker_id"]]), "")
                lat, lon = db.school_pos(con)
                con.execute("""UPDATE trip_runs SET status='CLOSED', end_ts=?, end_lat=?, end_lon=?, final_driver_id=driver_id, final_supervisor_id=supervisor_id,
                               final_caretaker_id=caretaker_id, sweep_by=?, sweep_ts=?, closed_by=? WHERE id=?""",
                            (ts(when), lat, lon, sweeper, ts(when), f"sup{d['bus_id']:03d}", rid))
                runs[(date, d["bus_id"], d["trip_no"])] = None
                import security_ext
                security_ext.sim_inspection(con, rid, d["bus_id"], d["trip_no"], date, when, random.Random(rid * 1009 + int(date.replace("-", ""))),
                                            [("guard1", "Ram Bahadur Thapa"), ("guard2", "Anil Tamang"), ("guard3", "Mohammed Rafiq")])
            elif kind == "GATE":
                con.execute("""INSERT INTO gate_scans(ts, date, ptype, pid, dir, gate_id, method, operator, result, reason, ref, code)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (ts(when), date, d["ptype"], d["pid"], d["dir"], d["gate"], d["method"], f"guard{(d['gate'] % 3) + 1}", "OK",
                             d.get("note") or "", d.get("ref"), None))
            elif kind == "GATE_TRY_OUT":
                bd = q1("SELECT code FROM badges WHERE student_id=? AND status='ACTIVE'", (d["sid"],), con)
                last = q1("SELECT dir FROM campus_moves WHERE ptype='STUDENT' AND pid=? AND date=? ORDER BY ts DESC LIMIT 1", (d["sid"], date), con)
                if bd and last and last["dir"] == "IN":
                    campus.gate_scan(bd["code"], 1, "guard1", direction="OUT", when=when, con=con)
            elif kind == "EP_REQ":
                if not q1("SELECT id FROM early_pickups WHERE student_id=? AND date=?", (d["sid"], date), con):
                    campus.request_early(d["sid"], date, d["time"], d["reason"], d["collector"], d["relation"], d["phone"], d["qid"],
                                         "guardian", when=when, con=con)
            elif kind == "EP_DECIDE":
                e = q1("SELECT id FROM early_pickups WHERE student_id=? AND date=? AND status='PENDING'", (d["sid"], date), con)
                if e:
                    campus.decide_early(e["id"], True, "Rajesh Menon (Transport Lead)", when=when, con=con)
            elif kind == "EP_OUT":
                e = q1("SELECT pass_code FROM early_pickups WHERE student_id=? AND date=? AND status='APPROVED'", (d["sid"], date), con)
                if e:
                    campus.gate_scan(e["pass_code"], 1, "guard1", direction="OUT", when=when, con=con, notify_parent=notify)
            elif kind == "VISITOR_IN":
                campus.register_visitor(d["name"], d["phone"], d["qid"], d["company"], d["purpose"], d["host"], d["vehicle"], 5, "guard3",
                                        expected_min=d["expected"], when=when, gender=d.get("gender", "M"), con=con)
            elif kind == "VISITOR_OUT":
                v = q1("SELECT pass_no FROM visitors WHERE date=? AND phone=? AND status='ON_CAMPUS'", (date, d["phone"]), con)
                if v:
                    campus.gate_scan(v["pass_no"], 5, "guard3", direction="OUT", method="PASS", when=when, con=con)
            elif kind == "OVERSTAY_CHECK":
                campus.check_overstays(when, con)
            elif kind == "ATT":
                if q1("SELECT id FROM att_submissions WHERE date=? AND class_id=?", (date, d["class_id"]), con):
                    continue
                c_, roster = campus.class_roster(d["class_id"], date, con)
                marks = {}
                for rr in roster:
                    stt = rr["suggested"]
                    if rr["student_id"] in d["force_absent"] and rr["on_campus"]:
                        stt = "ABSENT"
                    if rr["student_id"] in d.get("force_present", []) or (rr["source"].startswith("No bus") and _noscan(con, rr["student_id"], date)):
                        stt = "PRESENT"
                    marks[rr["student_id"]] = stt
                t_ = q1("SELECT name FROM staff WHERE id=?", (c_["teacher_id"],), con)["name"]
                campus.submit_attendance(d["class_id"], date, marks, t_, when=when, con=con, notify_parents=notify, roster=(c_, roster))
            elif kind == "NOSCAN":
                con.execute("INSERT OR IGNORE INTO sim_noscan(date, student_id) VALUES (?,?)", (date, d["sid"]))
            elif kind == "PATROL":
                con.execute("""INSERT INTO patrol_rounds(date, plan, round_no, checkpoint, guard, planned_ts, actual_ts, status, note, source, site)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                            (d.get("pdate") or d["planned"][:10], d["plan"], d["round_no"], d["cp"], d["guard"], d["planned"], d["actual"], d["status"],
                             "Checkpoint not scanned" if d["status"] == "OMITTED" else "", "Patrol app (demo feed)", "DPS Main campus"))
                if d["status"] == "OMITTED" and (d.get("pdate") or d["planned"][:10]) == date:
                    campus.raise_alert("PATROL_MISSED", "LOW", f"{d['plan']} round {d['round_no']}: {d['cp']} not scanned by {d['guard']}",
                                       date, None, "PATROL", None, audience="security", when=when, con=con)
        except Exception as ex:      # one bad event must never stop the day
            con.execute("INSERT INTO settings(key, value) VALUES ('sim_last_error', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (f"{kind} {when}: {ex}",))


def reserved_buses(con=None):
    """Bus ids kept for manual testing every day (maintenance.json → reserved_buses)."""
    import os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "maintenance.json")
    try:
        names = json.load(open(path)).get("reserved_buses", [])
    except (OSError, ValueError):
        return set()
    if not names:
        return set()
    return {r["id"] for r in q(f"SELECT id FROM buses WHERE bus_no IN ({','.join('?' * len(names))})", names, con)}


def _noscan(con, sid, date):
    return q1("SELECT 1 x FROM sim_noscan WHERE date=? AND student_id=?", (date, sid), con) is not None


def _ensure_sim_tables(con):
    con.execute("CREATE TABLE IF NOT EXISTS sim_runs (run_id INTEGER PRIMARY KEY)")
    con.execute("CREATE TABLE IF NOT EXISTS sim_noscan (date TEXT, student_id INTEGER, PRIMARY KEY(date, student_id))")


def apply_day(date, replay=True):
    """A past school day, start to finish (no parent messages)."""
    events = build_day(date)
    con = db.connect()
    try:
        _ensure_sim_tables(con)
        _apply(con, events, notify=False)
        con.commit()
    finally:
        con.close()
    import security_ext
    security_ext.check_missed_inspections(date, datetime.strptime(date + " 23:00", "%Y-%m-%d %H:%M"))
    return len(events)


def today_start_mode():
    """'zero' (default): each new day starts empty — all totals 0 except students enrolled; testers' scans are the only activity.
    'simulate': the demo school day plays automatically. Set "today_start" in maintenance.json."""
    import os
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "maintenance.json")
    try:
        return json.load(open(path)).get("today_start", "zero")
    except Exception:
        return "zero"


def prepare_today(now=None):
    """Store today's script and apply everything that is already due — or, in 'zero' mode, start the day empty."""
    now = now or db.now()
    date = now.strftime("%Y-%m-%d")
    if today_start_mode() == "zero" and db.setting("sim_live_day") != date and db.setting("sim_day") != date:
        db.set_setting("sim_off_day", date)
        db.set_setting("sim_day", date)
        return 0
    with _LOCK:
        con = db.connect()
        try:
            _ensure_sim_tables(con)
            if db.setting("sim_day", None, con) == date:
                return 0
            con.execute("DELETE FROM sim_events WHERE done=1")
            events = build_day(date)
            con.executemany("INSERT INTO sim_events(date, ts, kind, data, done) VALUES (?,?,?,?,0)",
                            [(date, ts(w), k, json.dumps(d)) for w, k, d in events])
            con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('sim_day', ?)", (date,))
            con.commit()
        finally:
            con.close()
    return tick(now)


def tick(now=None, limit=20000):
    """Apply the simulated events that have fallen due (called on every page load; cheap when nothing is due)."""
    now = now or db.now()
    if db.setting("sim_paused") == "1" or db.setting("sim_off_day") == now.strftime("%Y-%m-%d"):
        return 0
    if db.setting("sim_day") != now.strftime("%Y-%m-%d"):
        return prepare_today(now)
    if not _LOCK.acquire(blocking=False):
        return 0
    try:
        con = db.connect()
        try:
            _ensure_sim_tables(con)
            rows = con.execute("SELECT id, ts, kind, data FROM sim_events WHERE done=0 AND ts<=? ORDER BY ts, id LIMIT ?", (ts(now), limit)).fetchall()
            if not rows:
                return 0
            events = [(datetime.strptime(r_[1], "%Y-%m-%d %H:%M:%S"), r_[2], json.loads(r_[3])) for r_ in rows]
            _apply(con, events, notify=True)
            con.executemany("UPDATE sim_events SET done=1 WHERE id=?", [(r_[0],) for r_ in rows])
            con.commit()
            return len(rows)
        finally:
            con.close()
    finally:
        _LOCK.release()


def _wipe_today(date):
    """Remove every record of today's activity (previous days are never touched)."""
    with _LOCK:
        con = db.connect()
        try:
            _ensure_sim_tables(con)
            run_ids = [r_[0] for r_ in con.execute("SELECT id FROM trip_runs WHERE date=?", (date,)).fetchall()] or [-1]
            ph = ",".join("?" * len(run_ids))
            con.execute(f"DELETE FROM events WHERE run_id IN ({ph})", run_ids)
            con.execute(f"DELETE FROM manifest WHERE run_id IN ({ph})", run_ids)
            con.execute(f"DELETE FROM sim_runs WHERE run_id IN ({ph})", run_ids)
            for tbl, col in (("trip_runs", "date"), ("gate_scans", "date"), ("visitors", "date"), ("attendance", "date"), ("att_submissions", "date"),
                             ("campus_alerts", "date"), ("early_pickups", "date"), ("vehicle_checks", "date"), ("patrol_rounds", "date"),
                             ("absences", "date"), ("sim_noscan", "date"), ("change_requests", "date")):
                con.execute(f"DELETE FROM {tbl} WHERE {col}=?", (date,))
            for tbl in ("incidents", "notifications"):
                con.execute(f"DELETE FROM {tbl} WHERE ts LIKE ?", (date + "%",))
            for tbl, col in (("bus_messages", "ts"), ("route_plans", "date")):
                try:
                    con.execute(f"DELETE FROM {tbl} WHERE {col} LIKE ?", (date + "%",))
                except Exception:
                    pass
            con.execute("DELETE FROM sim_events")
            con.execute("DELETE FROM settings WHERE key IN ('sim_day', 'sim_off_day')")
            con.commit()
        finally:
            con.close()


def clear_today(user="admin"):
    """Start today from zero: all of today's activity removed and the simulated day switched off for today,
    so every total is zero except students enrolled. Tomorrow the simulation runs again."""
    date = db.today()
    _wipe_today(date)
    db.set_setting("sim_off_day", date)
    db.set_setting("sim_day", date)
    db.set_setting("sim_live_day", "")
    db.audit(user, "admin", "TEST_RESET_DAY_ZERO", "day", date, "today cleared, simulation off for today")
    return True


def replay_today_at(hm):
    """Demo clock: rebuild today's data as it stood at hh:mm (removes today's live data, then re-applies the script up to that time)."""
    date = db.today()
    _wipe_today(date)
    real = db.now() - timedelta(seconds=db.clock_offset())
    if hm == "now":
        db.set_clock_offset(0)
    else:
        db.set_clock_offset((datetime.strptime(f"{date} {hm}", "%Y-%m-%d %H:%M") - real).total_seconds())
    db.set_setting("sim_live_day", db.today())       # chosen on purpose: play the demo day even in 'zero' mode
    prepare_today(db.now())
    return True


def real_time():
    """Demo clock back to real time: the day simply continues up to now."""
    db.set_clock_offset(0)
    return tick(db.now())


def apply_maintenance(path=None):
    """Apply one-off actions from maintenance.json (once per database)."""
    import os
    import engine as E
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "maintenance.json")
    if not os.path.exists(path):
        return 0
    try:
        acts = json.load(open(path)).get("actions", [])
    except ValueError:
        return 0
    done = set(json.loads(db.setting("maintenance_done", "[]") or "[]"))
    n = 0
    for a in acts:
        if a.get("id") in done or a.get("date") != db.today():
            continue
        if a.get("type") == "reset":
            b = q1("SELECT id FROM buses WHERE bus_no=?", (a["bus"],))
            if b:
                E.reset_trips(a["date"], "maintenance", "admin", bus_id=b["id"], include_closed=True)
                n += 1
        elif a.get("type") == "zero_today" and db.setting("sim_off_day") != a["date"]:
            clear_today("maintenance")
            n += 1
        done.add(a["id"])
    db.set_setting("maintenance_done", json.dumps(sorted(done)))
    return n
