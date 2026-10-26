"""Security app (gate phone / tablet): scan student, staff and visitor cards with the photo check, visitor passes,
early-pickup release, alerts to act on (child absent in class but on campus, exit without a pass), gate log and patrol rounds."""
import io

import pandas as pd
import streamlit as st

import campus
import db
import dps_data
import qr_utils
import ui360 as U
from qr_scanner import qr_scanner
from ui import alert_sound, msg

U.css()
user = st.session_state.user
role = user["role"]
D = db.today()
K = "sec"

st.title("🛡 Security app")
gates = campus.gates()
gmap = {g["id"]: g["name"] for g in gates}
c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
gate = c1.pills("Gate", list(gmap), default=st.session_state.get("sec_gate_default", 1), format_func=lambda i: gmap[i].replace("Gate ", "G"),
                key="sec_gate") or 1
_open = campus.alerts(D, "OPEN", "security")
c2.markdown(f"<div style='text-align:right'>{U.pill(f'{len(_open)} open alerts', 'red' if _open else 'green')}</div>", unsafe_allow_html=True)

import security_ext as SX
_board = SX.inspections_board(D)
_due = [b for b in _board if b["state"] in ("DUE", "MISSED")]
tabs = st.tabs(["📷 Scan", f"🚌 Bus inspection ({len(_due)})", f"🪪 Visitors", "🕐 Early pickups", f"🚨 Alerts ({len(_open)})", "📋 Gate log", "🛡 Patrol"])


def show_result(res):
    p = res.get("person")
    lvl = res.get("level", "ok")
    if p:
        kind = "STUDENT" if p["type"] in ("STUDENT", "EARLY_PASS") else p["type"]
        row = p["row"]
        img = campus.avatar_img(row["id"], row.get("gender") or "M", kind, 150)
        if kind == "STUDENT":
            meta = f"{row['class_name']}-{row['section']} · {row['code']} · " + ("🚌 " + (row.get("bus_no") or "") if row["transport"] == "BUS" else "🚗 Private transport")
            meta += f"<br>Class teacher: {row.get('teacher') or '—'}"
        elif kind == "STAFF":
            meta = f"{row['role'].title()} · {row['dept'] or ''}"
        else:
            meta = f"{row['purpose']} → {row['host']} · {row['company'] or ''}"
        dcol = {"IN": "#2E9E5B", "OUT": "#D93025"}.get(res.get("dir"), "#6B7390")
        dbadge = f"<div class='dir' style='background:{dcol}'>{res.get('dir')}</div>" if res.get("dir") else ""
        st.markdown(f"<div class='big-result {lvl}'>{img}<div style='flex:1'>{dbadge}<div style='font-size:1.6rem;font-weight:900;line-height:1.15'>{row['name']}</div>"
                    f"<div class='muted' style='font-size:.95rem'>{meta}</div><div class='msg' style='margin-top:8px'>{res['message']}</div>"
                    f"<div class='mini' style='margin-top:6px'>👁 Check the face against the photo before letting the person through.</div></div></div>",
                    unsafe_allow_html=True)
    else:
        st.markdown(f"<div class='big-result error'><div class='msg'>⛔ {res['message']}</div></div>", unsafe_allow_html=True)
    hold = res.get("early") or res.get("gate_pass")
    if p and hold and p["type"] in ("STUDENT", "EARLY_PASS"):
        st.markdown(f"**Who may collect {p['row']['name']}** — compare the collector's face and QID:")
        st.markdown(SX.collectors_html(p["row"]["id"], 58, hold["collector"]), unsafe_allow_html=True)
        if res.get("collector_ok") is False and not res.get("ok") and role in ("admin", "manager", "security"):
            st.caption("Release only after the office has confirmed this collector by phone with the parent.")
            if st.button("☎️ Office confirmed with the parent — release", key="sec_override", type="primary"):
                do_scan(res["early"]["pass_code"], "OUT", "EARLY_PICKUP", override=True)
                st.rerun()


def do_scan(code, direction, method, override=False):
    res = SX.timed("GATE", campus.gate_scan, code, gate, user["display_name"], direction=direction, method=method, override=override, device=f"gate{gate}")
    st.session_state["sec_last"] = res
    st.session_state["sec_say"] = ("Stop. " if not res["ok"] else "") + res["message"].split(" — ")[0][:80]


with tabs[0]:
    d1, d2 = st.columns([2, 3])
    direction = d1.segmented_control("Direction", ["Auto", "IN", "OUT"], default="Auto", key="sec_dir") or "Auto"
    d2.caption("Auto = the opposite of the person's last move today. Use IN / OUT at a one-way gate.")
    if st.session_state.get("sec_last"):
        show_result(st.session_state["sec_last"])
        if st.session_state.get("sec_say"):
            alert_sound(st.session_state.pop("sec_say"), key=f"sec_snd{len(st.session_state.get('sec_last', {}).get('message', ''))}")
    t_cam, t_read, t_demo = st.tabs(["📷 Camera (ID-card barcode / QR pass)", "⌨️ Card reader / type number", "🧪 Demo cards"])
    with t_cam:
        val = qr_scanner(key="sec_cam", barcode=True)
        if val and val.get("nonce") and val["nonce"] != st.session_state.get("sec_nonce"):
            st.session_state["sec_nonce"] = val["nonce"]
            do_scan(val["code"], None if direction == "Auto" else direction, "CAMERA")
            st.rerun()
        st.caption("Reads the barcode on school ID cards (Code 128 / Code 39) and the QR code on visitor and early-pickup passes.")
    with t_read:
        with st.form("sec_typed", clear_on_submit=True):
            code = st.text_input("Card / pass number", placeholder="Tap the card on a USB / Bluetooth reader, or type the number")
            if st.form_submit_button("Submit", type="primary") and code.strip():
                do_scan(code.strip(), None if direction == "Auto" else direction, "CARD")
                st.rerun()
    with t_demo:
        st.caption("For the demo: real cards from today's data. Tap one to scan it at this gate.")
        samples = []
        oc = db.q("""SELECT s.name, s.class_name, s.section, b.code FROM students s JOIN badges b ON b.student_id=s.id AND b.status='ACTIVE'
                     WHERE s.transport='PRIVATE' AND s.batch=1 AND s.grp='Main' AND s.id IN (SELECT pid FROM gate_scans WHERE date=? AND ptype='STUDENT'
                     AND dir='IN') AND s.id NOT IN (SELECT pid FROM gate_scans WHERE date=? AND ptype='STUDENT' AND dir='OUT') LIMIT 1""", (D, D))
        if oc:
            samples.append(("Private student on campus (exit test)", oc[0]["code"], f"{oc[0]['name']} {oc[0]['class_name']}-{oc[0]['section']}"))
        aft = db.q("""SELECT s.name, s.class_name, s.section, b.code FROM students s JOIN badges b ON b.student_id=s.id AND b.status='ACTIVE'
                      WHERE s.transport='PRIVATE' AND s.batch=2 AND s.id NOT IN (SELECT pid FROM gate_scans WHERE date=? AND ptype='STUDENT') LIMIT 1""", (D,))
        if aft:
            samples.append(("Afternoon-shift student arriving", aft[0]["code"], f"{aft[0]['name']} {aft[0]['class_name']}-{aft[0]['section']}"))
        ep = db.q("SELECT pass_code, collector FROM early_pickups WHERE date=? AND status='APPROVED' LIMIT 1", (D,))
        if ep:
            samples.append(("Early-pickup pass (approved)", ep[0]["pass_code"], f"collector {ep[0]['collector']}"))
        stf = db.q("SELECT name, card_no FROM staff WHERE role='teacher' AND card_no IS NOT NULL LIMIT 1 OFFSET 7")
        if stf:
            samples.append(("Teacher's staff card", stf[0]["card_no"], stf[0]["name"]))
        vis = db.q("SELECT pass_no, name FROM visitors WHERE date=? AND status='ON_CAMPUS' LIMIT 1", (D,))
        if vis:
            samples.append(("Visitor pass (check-out)", vis[0]["pass_no"], vis[0]["name"]))
        rv = db.q("SELECT code FROM badges WHERE status='REVOKED' LIMIT 1")
        if rv:
            samples.append(("Old lost card (revoked)", rv[0]["code"], ""))
        samples.append(("Unknown card", "99887766", ""))
        cols = st.columns(2)
        for i, (lab, code, who) in enumerate(samples):
            with cols[i % 2]:
                if st.button(f"{lab}\n\n`{code}` {who}", key=f"sec_demo{i}", width="stretch"):
                    do_scan(code, None if direction == "Auto" else direction, "CARD")
                    st.rerun()

with tabs[1]:
    done_ = [b for b in _board if b["state"] in ("DONE", "LATE", "CHILD_FOUND")]
    U.kpis([("TRIPS CLOSED TODAY", len(_board), "need a security check", "#1B2559", ""),
            ("INSPECTED", len(done_), f"{sum(1 for b in _board if b['state'] == 'LATE')} late", "#2E9E5B", "green"),
            ("DUE NOW", sum(1 for b in _board if b["state"] == "DUE"), f"within {SX.GRACE_MIN} min of closing", "#E07A1F", "orange"),
            ("MISSED", sum(1 for b in _board if b["state"] == "MISSED"), "alert raised", "#D93025", "red")], n=4)
    st.caption("After every trip, a guard walks the bus to the rear door, scans the tag there and confirms that no child is left on board. "
               "The crew's own seat sweep is not enough — this is the independent check.")
    if not _due:
        st.success("No bus waiting for inspection.")
    pick_ = st.selectbox("Bus to inspect", [b["run_id"] for b in _due], key="insp_run",
                         format_func=lambda rid: next(f"{b['bus_no']} · Trip {b['trip_no']} · closed {b['end_ts'][11:16]} ({b['mins_since_close']} min ago)"
                                                      + (" · MISSED" if b["state"] == "MISSED" else "") for b in _due if b["run_id"] == rid)) if _due else None
    if pick_:
        b_ = next(b for b in _due if b["run_id"] == pick_)
        with st.container(border=True):
            st.markdown(f"**{b_['bus_no']}** · Trip {b_['trip_no']} · crew sweep by {b_['sweep_by'] or '—'}")
            st.markdown("1️⃣ Walk to the **rear door** and scan the tag")
            tv = qr_scanner(key=f"insp_cam{pick_}")
            if tv and tv.get("code"):
                st.session_state[f"insp_tag{pick_}"] = tv["code"]
            tag_ = st.text_input("Rear-door tag", value=st.session_state.get(f"insp_tag{pick_}", ""), key=f"insp_tagt{pick_}",
                                 placeholder=f"Scan the QR inside the rear door (demo: {SX.rear_tag_code(b_['bus_no'])})")
            if tag_:
                st.markdown(U.pill("Tag OK — rear of the bus reached", "green") if SX.rear_tag_ok(b_["bus_id"], tag_) else U.pill("Wrong tag", "red"),
                            unsafe_allow_html=True)
            st.markdown("2️⃣ Check and tick")
            ans_ = {k: st.checkbox(k, key=f"insp_{pick_}_{i}") for i, k in enumerate(SX.INSPECT_ITEMS)}
            found_ = st.toggle("⚠ A child was found on the bus", key=f"insp_found{pick_}")
            note_ = st.text_input("Child's name / where found", key=f"insp_note{pick_}") if found_ else ""
            if st.button("✅ Confirm inspection" if not found_ else "🚨 Report child found", type="primary", key=f"insp_go{pick_}"):
                ok_, m_ = SX.save_inspection(pick_, user["username"], user["display_name"], tag_, ans_, found_, note_)
                st.session_state["insp_msg"] = ("ok" if ok_ and not found_ else "error", m_)
                st.rerun()
    if st.session_state.get("insp_msg"):
        msg(*st.session_state.pop("insp_msg"))
    with st.expander(f"Inspection log today ({len(done_)})"):
        st.dataframe(pd.DataFrame([{"Bus": b["bus_no"], "Trip": b["trip_no"], "Closed": (b["end_ts"] or "")[11:16],
                                    "Inspected": (b["inspection"]["ts"] if b["inspection"] else "")[11:16],
                                    "Minutes after": b["inspection"]["minutes_after"] if b["inspection"] else None,
                                    "Inspector": b["inspection"]["inspector_name"] if b["inspection"] else "",
                                    "Result": {"DONE": "Bus empty", "LATE": "Bus empty (late)", "CHILD_FOUND": "CHILD FOUND"}.get(b["state"], b["state"])}
                                   for b in done_]), hide_index=True, width="stretch")

with tabs[2]:
    vis_today = campus.visitors(D)
    on = [v for v in vis_today if v["status"] == "ON_CAMPUS"]
    U.kpis([("ON CAMPUS NOW", len(on), "visitors with an open pass", "#0F9BB5", ""),
            ("TODAY", len(vis_today), "registered", "#1B2559", ""),
            ("OVERSTAY", sum(1 for a in campus.alerts(D, "OPEN") if a["kind"] == "VISITOR_OVERSTAY"), "longer than expected", "#E07A1F", "orange")], n=3)
    with st.expander("➕ Register a visitor and print the pass", expanded=not on):
        with st.form("vis_new", clear_on_submit=True):
            a, b = st.columns(2)
            vn = a.text_input("Full name *")
            vp = b.text_input("Mobile *", placeholder="+974 …")
            vq = a.text_input("QID / passport no.")
            vc = b.text_input("Company / relation", placeholder="e.g. Parent of Grade 5-A, Qatar Cool")
            purpose = a.selectbox("Purpose", [p_ for p_, _ in dps_data.VIS_PURPOSE])
            host = b.text_input("Host / meeting with *", placeholder="Teacher or office")
            veh = a.text_input("Vehicle plate (optional)")
            exp = b.select_slider("Expected stay (min)", [15, 30, 45, 60, 90, 120, 180], value=60)
            ok = st.form_submit_button("Check in & issue pass", type="primary")
        if ok:
            if not (vn.strip() and vp.strip() and host.strip()):
                msg("error", "Name, mobile and host are required.")
            else:
                try:
                    vid, pno = campus.register_visitor(vn.strip(), vp.strip(), vq.strip(), vc.strip(), purpose, host.strip(), veh.strip(), gate,
                                                       user["display_name"], exp)
                    st.session_state["vis_pass"] = vid
                    st.rerun()
                except campus.BlacklistedVisitor as ex:
                    msg("error", f"<b>⛔ ENTRY REFUSED</b> — {ex}")
                    alert_sound("Stop. This visitor is on the blacklist.", key="vis_bl_snd")
    if st.session_state.get("vis_pass"):
        v = db.q1("SELECT * FROM visitors WHERE id=?", (st.session_state["vis_pass"],))
        pc1, pc2 = st.columns([1, 2])
        pc1.image(qr_utils.qr_png(v["pass_no"], box=6), width=170)
        pc2.markdown(f"<div class='card'><div class='muted'>VISITOR PASS · {db.setting('school_name', db.SCHOOL_NAME)}</div>"
                     f"<div style='font-size:1.5rem;font-weight:800'>{v['pass_no']}</div><b>{v['name']}</b> · {v['purpose']}<br>Host: {v['host']} · "
                     f"in {v['in_ts'][11:16]} · expected {v['expected_min']} min<br><span class='muted'>Scan the pass at the gate when leaving.</span></div>",
                     unsafe_allow_html=True)
        if pc2.button("Done", key="vis_done"):
            st.session_state.pop("vis_pass")
            st.rerun()
    st.markdown("**On campus now**")
    for v in on:
        mins = int((db.now() - pd.to_datetime(v["in_ts"])).total_seconds() // 60)
        over = mins > (v["expected_min"] or 60)
        a, b = st.columns([5, 1], vertical_alignment="center")
        with a:
            U.person_card(v, "VISITOR", f"<div class='mini'>In {v['in_ts'][11:16]} · {mins} min "
                          + (U.pill(f"over {v['expected_min']} min", "orange") if over else U.pill(f"expected {v['expected_min']} min", "grey")) + "</div>",
                          size=54)
        if b.button("Check out", key=f"vout{v['id']}", width="stretch"):
            st.session_state["sec_last"] = campus.checkout_visitor(v["id"], gate, user["display_name"])
            st.rerun()
    bl = SX.blacklist()
    with st.expander(f"⛔ Visitor blacklist ({len(bl)})"):
        st.caption("Checked automatically at registration (QID / passport number, phone, or exact name). A match refuses entry and alerts security.")
        st.dataframe(pd.DataFrame([{"Name": b_["name"], "QID / passport": b_["id_no"], "Phone": b_["phone"], "Reason": b_["reason"],
                                    "Added by": b_["added_by"], "Since": (b_["ts"] or "")[:10]} for b_ in bl]), hide_index=True, width="stretch")
        if role in ("admin", "manager", "security"):
            with st.form("bl_add", clear_on_submit=True):
                a, b, c_ = st.columns(3)
                bn, bq, bp = a.text_input("Name"), b.text_input("QID / passport"), c_.text_input("Phone")
                br = st.text_input("Reason *")
                if st.form_submit_button("Add to blacklist") and bn.strip() and br.strip():
                    SX.add_blacklist(bn, bq, bp, br, user["display_name"])
                    st.rerun()
            rm = st.selectbox("Remove an entry", [None] + [b_["id"] for b_ in bl], format_func=lambda i: "—" if i is None else next(
                f"{b_['name']} ({b_['id_no'] or b_['phone']})" for b_ in bl if b_["id"] == i), key="bl_rm")
            if rm and st.button("Remove from blacklist", key="bl_rm_go"):
                SX.remove_blacklist(rm, user["display_name"])
                st.rerun()
    with st.expander(f"All visitors today ({len(vis_today)})"):
        st.dataframe(pd.DataFrame([{"Pass": v["pass_no"], "Name": v["name"], "Purpose": v["purpose"], "Host": v["host"], "Company": v["company"],
                                    "In": (v["in_ts"] or "")[11:16], "Out": (v["out_ts"] or "")[11:16], "Status": v["status"].replace("_", " ").title()}
                                   for v in vis_today]), hide_index=True, width="stretch")

with tabs[3]:
    eps = campus.early_pickups(D)
    st.caption("Requests come from the parent app; the school approves them. Security checks the collector's ID against the pass and releases the child.")
    if not eps:
        st.info("No early pickups today.")
    for e in eps:
        a, b = st.columns([5, 1.3], vertical_alignment="center")
        stt = {"PENDING": ("Waiting for school approval", "orange"), "APPROVED": (f"Approved · pass {e['pass_code']}", "blue"),
               "COMPLETED": (f"Released {(e['out_ts'] or '')[11:16]}", "green"), "REJECTED": ("Rejected", "red")}[e["status"]]
        with a:
            U.person_card({"id": e["student_id"], "name": e["name"], "gender": e["gender"], "class_name": e["class"].split("-")[0],
                           "section": e["class"].split("-")[-1], "code": e["code"], "transport": e["transport"]}, "STUDENT",
                          f"<div class='mini'>🕐 {e['pickup_time']} · collector <b>{e['collector']}</b> ({e['relation']}, QID {e['collector_id'] or '—'}) · "
                          f"{e['reason']}</div>{U.pill(stt[0], stt[1])} "
                          + (U.pill(f"Ready at the gate since {e['ready_ts'][11:16]}", "green") if e.get("ready_ts") and e["status"] == "APPROVED" else "")
                          + ("" if SX.collector_allowed(e["student_id"], e["collector"], e["collector_id"]) else " " + U.pill("Collector NOT on the allowed list", "red")),
                          size=56)
            if e["status"] == "APPROVED":
                with st.expander("Who may collect (photos)"):
                    st.markdown(SX.collectors_html(e["student_id"], 52, e["collector"]), unsafe_allow_html=True)
        if e["status"] == "APPROVED" and b.button("✅ ID checked — release", key=f"eprel{e['id']}", type="primary", width="stretch"):
            do_scan(e["pass_code"], "OUT", "EARLY_PICKUP")
            st.rerun()
    gps_ = SX.gate_passes()
    wd_ = SX.DAYS[db.now().weekday()]
    todays = [g_ for g_ in gps_ if wd_ in (g_["days"] or "").split(",") and g_["valid_from"] <= D <= (g_["valid_to"] or "9999")]
    with st.expander(f"🎫 Standing gate passes valid today ({len(todays)} of {len(gps_)})"):
        st.caption("Bus students collected by a named person on set days (set up by the office). Scan the student's card or the pass code at "
                   "dismissal — the child is taken off the home bus list automatically.")
        st.dataframe(pd.DataFrame([{"Pass": g_["pass_code"], "Student": g_["name"], "Class": g_["class"], "Days": g_["days"],
                                    "Collector": f"{g_['collector']} ({g_['relation']})", "Note": g_["note"]} for g_ in todays]),
                     hide_index=True, width="stretch")

with tabs[4]:
    al = campus.alerts(D, None, "security")
    if not al:
        st.success("No security alerts today.")
    for a_ in al:
        done = a_["status"] == "RESOLVED"
        with st.container(border=True):
            l, r = st.columns([4, 1.4], vertical_alignment="center")
            with l:
                if a_["student_id"]:
                    stu = campus.student_card(a_["student_id"])
                    last = db.q1("SELECT * FROM campus_moves WHERE ptype='STUDENT' AND pid=? AND date=? ORDER BY ts DESC LIMIT 1", (a_["student_id"], D))
                    U.person_card(stu, "STUDENT", f"<div class='mini'>{U.pill(a_['severity'].title(), U.SEV_PILL.get(a_['severity'], 'grey'))} "
                                  f"{a_['ts'][11:16]} · {a_['details']}</div>"
                                  + (f"<div class='mini'>Last seen: {last['gate']} {last['dir']} at {last['ts'][11:16]}</div>" if last else ""), size=64)
                else:
                    st.markdown(f"{U.pill(a_['severity'].title(), U.SEV_PILL.get(a_['severity'], 'grey'))} **{a_['ts'][11:16]}** · "
                                f"{campus.ALERT_TEXT.get(a_['kind'], a_['kind'])} — {a_['details']}", unsafe_allow_html=True)
                if done:
                    st.caption(f"✔ Resolved by {a_['resolved_by']} at {(a_['resolved_ts'] or '')[11:16]}: {a_['resolution']}")
                elif a_["status"] == "ACKNOWLEDGED":
                    st.caption(f"👀 Acknowledged by {a_['ack_by']} at {(a_['ack_ts'] or '')[11:16]}")
            with r:
                if not done:
                    if a_["status"] == "OPEN" and st.button("👀 On it", key=f"ack{a_['id']}", width="stretch"):
                        campus.ack_alert(a_["id"], user["display_name"])
                        st.rerun()
                    note = st.text_input("Outcome", key=f"res_n{a_['id']}", placeholder="e.g. found in library", label_visibility="collapsed")
                    if st.button("✔ Resolve", key=f"res{a_['id']}", width="stretch", type="primary"):
                        campus.resolve_alert(a_["id"], user["display_name"], note.strip() or "Resolved")
                        st.rerun()

with tabs[5]:
    gl = campus.gate_log(D, None if st.toggle("All gates", value=True, key="gl_all") else gate)
    st.dataframe(pd.DataFrame([{"Time": r["ts"][11:19], "Gate": (r["gate"] or "").replace("Gate ", "G"), "Who": r["ptype"].title(),
                                "Name": r["person"] or r["code"], "Dir": r["dir"], "Method": (r["method"] or "").replace("_", " ").title(),
                                "Result": r["result"], "Note": r["reason"] or "", "By": r["operator"]} for r in gl]),
                 hide_index=True, width="stretch", height=480)

with tabs[6]:
    pr = campus.patrol(D)
    ok = sum(1 for p in pr if p["status"] == "ARRIVED")
    U.kpis([("CHECKPOINTS TODAY", len(pr), "planned so far", "#1B2559", ""), ("ON TIME", ok, f"{ok / max(1, len(pr)):.0%}", "#2E9E5B", "green"),
            ("LATE", sum(1 for p in pr if p["status"] == "LATE"), "", "#E0B01F", ""),
            ("MISSED", sum(1 for p in pr if p["status"] == "OMITTED"), "checkpoint not scanned", "#D93025", "red")], n=4)
    if pr:
        df = pd.DataFrame([{"Plan": p["plan"], "Round": p["round_no"], "Checkpoint": p["checkpoint"], "Guard": p["guard"],
                            "Planned": (p["planned_ts"] or "")[11:16], "Scanned": (p["actual_ts"] or "")[11:16], "Status": p["status"].title(),
                            "Source": p["source"]} for p in pr])
        st.dataframe(df, hide_index=True, width="stretch", height=360)
    st.caption("Patrol rounds come from the guard-tour app (JWM patrol). Upload its Excel / CSV export to show the results here.")
    up = st.file_uploader("Patrol export (Excel or CSV)", type=["xlsx", "xls", "csv"], key="pat_up")
    if st.session_state.get("pat_msg"):
        k_, m_ = st.session_state.pop("pat_msg")
        msg(k_, m_)
    if up is not None and st.button("Import patrol records", key="pat_imp"):
        try:
            df = pd.read_csv(up, dtype=str, encoding="utf-8-sig", skipinitialspace=True) if up.name.lower().endswith(".csv") else pd.read_excel(up)
            n, m = campus.import_patrol(df)
            st.session_state.pat_msg = ("ok" if n else "warn" if "already" in m else "error", m)
        except Exception as e:
            st.session_state.pat_msg = ("error", f"Could not read the file: {e}")
        st.rerun()
    try:
        st.page_link("page_patrol.py", label="Open the Security patrols dashboard", icon="🛡️")
    except Exception:
        pass
    tmpl = pd.DataFrame([{"Plan": "Night patrol", "Checkpoint": "Gate 1", "Patrolman": "Ram Bahadur Thapa", "Planned time": f"{D} 22:00:00",
                          "Arrival time": f"{D} 22:03:12", "Status": "Normal", "Site": "DPS Main campus"}])
    st.download_button("Template (CSV)", tmpl.to_csv(index=False).encode(), "patrol_import_template.csv", "text/csv")
