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

tabs = st.tabs(["📷 Scan", f"🪪 Visitors", "🕐 Early pickups", f"🚨 Alerts ({len(_open)})", "📋 Gate log", "🛡 Patrol"])


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


def do_scan(code, direction, method):
    res = campus.gate_scan(code, gate, user["display_name"], direction=direction, method=method)
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
        st.caption("Reads the barcode on DPS ID cards (Code 128 / Code 39) and the QR code on visitor and early-pickup passes.")
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
                vid, pno = campus.register_visitor(vn.strip(), vp.strip(), vq.strip(), vc.strip(), purpose, host.strip(), veh.strip(), gate,
                                                   user["display_name"], exp)
                st.session_state["vis_pass"] = vid
                st.rerun()
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
    with st.expander(f"All visitors today ({len(vis_today)})"):
        st.dataframe(pd.DataFrame([{"Pass": v["pass_no"], "Name": v["name"], "Purpose": v["purpose"], "Host": v["host"], "Company": v["company"],
                                    "In": (v["in_ts"] or "")[11:16], "Out": (v["out_ts"] or "")[11:16], "Status": v["status"].replace("_", " ").title()}
                                   for v in vis_today]), hide_index=True, width="stretch")

with tabs[2]:
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
                          f"{e['reason']}</div>{U.pill(stt[0], stt[1])}", size=56)
        if e["status"] == "APPROVED" and b.button("✅ ID checked — release", key=f"eprel{e['id']}", type="primary", width="stretch"):
            st.session_state["sec_last"] = campus.gate_scan(e["pass_code"], gate, user["display_name"], direction="OUT", method="EARLY_PICKUP")
            st.rerun()

with tabs[3]:
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

with tabs[4]:
    gl = campus.gate_log(D, None if st.toggle("All gates", value=True, key="gl_all") else gate)
    st.dataframe(pd.DataFrame([{"Time": r["ts"][11:19], "Gate": (r["gate"] or "").replace("Gate ", "G"), "Who": r["ptype"].title(),
                                "Name": r["person"] or r["code"], "Dir": r["dir"], "Method": (r["method"] or "").replace("_", " ").title(),
                                "Result": r["result"], "Note": r["reason"] or "", "By": r["operator"]} for r in gl]),
                 hide_index=True, width="stretch", height=480)

with tabs[5]:
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
    if up is not None and st.button("Import patrol records", key="pat_imp"):
        df = pd.read_csv(up) if up.name.lower().endswith(".csv") else pd.read_excel(up)
        n, m = campus.import_patrol(df)
        msg("ok" if n else "error", m)
    tmpl = pd.DataFrame([{"Plan": "Night patrol", "Checkpoint": "Gate 1", "Patrolman": "Ram Bahadur Thapa", "Planned time": f"{D} 22:00:00",
                          "Arrival time": f"{D} 22:03:12", "Status": "Normal", "Site": "DPS Main campus"}])
    st.download_button("Template (CSV)", tmpl.to_csv(index=False).encode(), "patrol_import_template.csv", "text/csv")
