"""Fleet maintenance & fuel: dashboard, job orders with the 4-level approval chain, purchase orders, parts & stock, fuel, service plans,
vehicle and crew documents."""
from datetime import datetime, timedelta

import altair as alt
import pandas as pd
import streamlit as st

import db
import fleet as F
import ui360 as U
from ui import msg

U.css()
user = st.session_state.user
role = user["role"]
TODAY = db.today()
NOW = db.now()
GREEN, AMBER, RED, BLUE = "#2E9E5B", "#E0B01F", "#D93025", "#3D4FD6"
EDIT = role in ("admin", "manager", "fleet_supervisor", "dispatcher")
MY_LEVEL = next((i + 1 for i, (_, r) in enumerate(F.LEVELS) if r == role), None)

st.title("🔧 Fleet maintenance & fuel")
if st.session_state.get("fl_msg"):
    msg(*st.session_state.pop("fl_msg"))

buses = db.q("SELECT id, bus_no, plate, route_name FROM buses ORDER BY bus_no")
BUSN = {b["id"]: b["bus_no"] for b in buses}
garages = db.q("SELECT * FROM garages WHERE active=1 ORDER BY id")
GAR = {g["id"]: g["name"] for g in garages}
STATUS_LAB = {"PENDING_APPROVAL": "🟠 Waiting approval", "APPROVED": "🔵 Approved", "IN_WORKSHOP": "🛠 In workshop", "COMPLETED": "🟢 Completed",
              "CLOSED": "⚪ Closed", "REJECTED": "🔴 Rejected", "SENT": "📤 Sent", "RECEIVED": "📦 Received", "DRAFT": "Draft"}
jo_all = F.job_orders()
po_all = F.purchase_orders()
waiting_me = [j for j in jo_all if j["status"] == "PENDING_APPROVAL" and F.can_approve(role, j["level_no"])] + \
             [p for p in po_all if p["status"] == "PENDING_APPROVAL" and F.can_approve(role, p["level_no"])]

tabs = st.tabs(["📊 Dashboard", "🧾 Job orders", f"✅ Approvals ({len(waiting_me)})", "🛒 Purchase orders", "📦 Parts & stock", "⛽ Fuel",
                "🗓 Service plans", "📄 Documents", "🚦 Driver behaviour", "⚙️ Settings"])


def done(kind, text):
    st.session_state.fl_msg = (kind, text)
    st.rerun()


# ------------------------------------------------------------------ dashboard
with tabs[0]:
    S = F.summary()
    w = S["waiting"]
    U.kpis([("OPEN JOB ORDERS", S["open_jo"], f"{S['in_workshop']} bus(es) in the workshop", BLUE, ""),
            ("WAITING APPROVAL", sum(w.values()) + S["po_waiting"], " · ".join(f"{ {'Supervisor': 'Sup', 'Manager': 'Mgr', 'Senior Manager': 'Sr Mgr', 'Vice President': 'VP'}[k]} {v}" for k, v in w.items())
             + f" · POs {S['po_waiting']}", AMBER, ""),
            ("SERVICES OVERDUE", S["overdue"], f"{S['due_soon']} due within 14 days / 1,000 km", RED, "red" if S["overdue"] else ""),
            ("DOCUMENTS", S["docs_expired"], f"expired · {S['docs_expiring']} expire within 30 days", RED, "red" if S["docs_expired"] else "")], n=4)
    U.kpis([("FUEL THIS MONTH", f"{S['fuel_l']:,.0f} L", f"QAR {S['fuel_qar']:,.0f}", "#1B2559", ""),
            ("FLEET EFFICIENCY", f"{S['kmpl']:.2f} km/L" if S["kmpl"] else "—", "last 30 days, all buses", GREEN, "green"),
            ("MAINTENANCE THIS MONTH", f"QAR {S['maint_qar']:,.0f}", "completed job orders", "#1B2559", ""),
            ("LOW STOCK", S["low_stock"], "parts at or below minimum", AMBER, "")], n=4)
    c1, c2 = st.columns([1.4, 1])
    with c1, st.container(border=True):
        st.markdown("<div class='sect'>Fuel cost by week (QAR)</div>", unsafe_allow_html=True)
        fl = pd.DataFrame(F.fuel_logs((NOW - timedelta(days=84)).strftime("%Y-%m-%d"), TODAY))
        if not fl.empty:
            fl["Week"] = pd.to_datetime(fl["date"]).dt.to_period("W-SAT").dt.start_time
            wk = fl.groupby("Week", as_index=False).agg(QAR=("amount", "sum"), Litres=("litres", "sum"))
            wk = wk[wk["Week"] >= pd.Timestamp(NOW.date() - timedelta(days=77))]
            wk["Week of"] = wk["Week"].dt.strftime("%d %b")
            st.altair_chart(alt.Chart(wk).mark_bar(color=BLUE, cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
                x=alt.X("Week of:N", sort=list(wk["Week of"]), title=None, axis=alt.Axis(labelAngle=0)), y=alt.Y("QAR:Q", title=None),
                tooltip=["Week of", alt.Tooltip("QAR:Q", format=",.0f"), alt.Tooltip("Litres:Q", format=",.0f")]
            ).properties(height=240), width="stretch")
            st.caption("The latest week is still running.")
    with c2, st.container(border=True):
        st.markdown("<div class='sect'>Maintenance cost by repair type, last 90 days (QAR)</div>", unsafe_allow_html=True)
        jc = pd.DataFrame([{"Type": j["nature"], "QAR": j["total_cost"]} for j in jo_all if j["completed_ts"]])
        if not jc.empty:
            jc = jc.groupby("Type", as_index=False)["QAR"].sum().sort_values("QAR", ascending=False)
            st.altair_chart(alt.Chart(jc).mark_bar(color="#1B2559", cornerRadiusTopRight=3, cornerRadiusBottomRight=3).encode(
                y=alt.Y("Type:N", sort="-x", title=None, axis=alt.Axis(labelOverlap=False, labelLimit=160)), x=alt.X("QAR:Q", title=None),
                tooltip=["Type", alt.Tooltip("QAR:Q", format=",.0f")]).properties(height=260), width="stretch")
    c3, c4 = st.columns(2)
    with c3, st.container(border=True):
        st.markdown("<div class='sect'>Lowest fuel efficiency, last 30 days</div>", unsafe_allow_html=True)
        ef = pd.DataFrame(F.fuel_efficiency((NOW - timedelta(days=30)).strftime("%Y-%m-%d"), TODAY))
        if not ef.empty:
            st.dataframe(ef.dropna(subset=["Km per litre"]).sort_values("Km per litre").head(8).drop(columns=["bus_id"]), hide_index=True, width="stretch")
    with c4, st.container(border=True):
        st.markdown("<div class='sect'>Highest maintenance cost per bus, last 90 days</div>", unsafe_allow_html=True)
        bc = pd.DataFrame([{"Bus": j["bus_no"], "QAR": j["total_cost"], "n": 1} for j in jo_all if j["completed_ts"]])
        if not bc.empty:
            bc = bc.groupby("Bus", as_index=False).agg(**{"Job orders": ("n", "sum"), "Cost (QAR)": ("QAR", "sum")}).sort_values("Cost (QAR)", ascending=False)
            st.dataframe(bc.head(8), hide_index=True, width="stretch")

# ------------------------------------------------------------------ job orders
with tabs[1]:
    if EDIT:
        with st.expander("➕ New job order"):
            a, b = st.columns(2)
            jb = a.selectbox("Bus", list(BUSN), format_func=BUSN.get, key="jo_bus")
            jn = b.selectbox("Nature of repair / maintenance", F.NATURES, key="jo_nat")
            jd = st.text_area("Description", key="jo_desc", placeholder="What is wrong, as reported by the driver / found in the check")
            c_, d_, e_ = st.columns(3)
            jk = c_.number_input("Odometer (km)", min_value=0, value=int(F.odometer(jb)), step=1, key=f"jo_km{jb}")
            jg = d_.selectbox("Garage / workshop", list(GAR), format_func=GAR.get, key="jo_gar")
            jp = e_.selectbox("Priority", ["Normal", "High", "Urgent"], key="jo_pri")
            je = st.number_input("Estimated cost (QAR)", min_value=0.0, value=500.0, step=50.0, key="jo_est")
            need = F.levels_for(je)
            st.caption("Approval needed: " + " → ".join(n for n, _ in F.LEVELS[:need]))
            dfx = db.q("""SELECT id, date, defects FROM vehicle_checks WHERE bus_id=? AND defects IS NOT NULL AND defects<>'' AND defects<>'[]'
                          AND cleared_by IS NULL ORDER BY date DESC LIMIT 5""", (jb,))
            jdef = st.selectbox("Link to a driver defect report (optional)", [None] + [x_["id"] for x_ in dfx], key="jo_def",
                                format_func=lambda i: "—" if i is None else next(f"{x_['date']}: {x_['defects']}" for x_ in dfx if x_["id"] == i))
            if st.button("Create job order", type="primary", key="jo_go", disabled=not jd.strip()):
                jid, no = F.create_job_order(jb, jn, jd.strip(), je, user["username"], jg, jp, "Driver defect report" if jdef else "Manual", jk,
                                             defect_check_id=jdef)
                done("ok", f"{no} created — waiting for the {F.LEVELS[0][0]}.")
    f1, f2 = st.columns([2, 1])
    stf = f1.pills("Status", ["Open", "Waiting approval", "In workshop", "Completed", "All"], default="Open", key="jo_f") or "Open"
    bf = f2.selectbox("Bus", [None] + list(BUSN), format_func=lambda i: "All buses" if i is None else BUSN[i], key="jo_bf")
    sel = {"Open": ("PENDING_APPROVAL", "APPROVED", "IN_WORKSHOP"), "Waiting approval": ("PENDING_APPROVAL",), "In workshop": ("IN_WORKSHOP",),
           "Completed": ("COMPLETED", "CLOSED"), "All": None}[stf]
    rows = [j for j in jo_all if (sel is None or j["status"] in sel) and (bf is None or j["bus_id"] == bf)]
    st.dataframe(pd.DataFrame([{"JO": j["jo_no"], "Opened": j["opened_ts"][:10], "Bus": j["bus_no"], "Nature": j["nature"], "Description": j["description"],
                                "Km": j["odometer"], "Garage": j["garage"], "Priority": j["priority"], "Estimate (QAR)": j["est_cost"],
                                "Status": STATUS_LAB.get(j["status"], j["status"]) + (f" · {j['waiting_for']}" if j["waiting_for"] else ""),
                                "Cost (QAR)": j["total_cost"] or None} for j in rows]), hide_index=True, width="stretch", height=330)
    pick = st.selectbox("Open a job order", [None] + [j["id"] for j in rows], key="jo_pick",
                        format_func=lambda i: "—" if i is None else next(f"{j['jo_no']} · {j['bus_no']} · {j['nature']}" for j in rows if j["id"] == i))
    if pick:
        j = next(x_ for x_ in rows if x_["id"] == pick)
        with st.container(border=True):
            st.markdown(f"### {j['jo_no']} · {j['bus_no']} · {j['nature']}  \n{STATUS_LAB.get(j['status'])}"
                        + (f" — waiting for the **{j['waiting_for']}**" if j["waiting_for"] else ""))
            st.markdown(f"{j['description']}  \nOdometer {j['odometer']:,} km · garage **{j['garage'] or '—'}** · priority {j['priority']} · "
                        f"estimate QAR {j['est_cost']:,.0f} · opened {j['opened_ts'][:16]} by {j['opened_by']}")
            trail = F.approval_trail("JO", j["id"])
            chain = []
            for i, (lv, _) in enumerate(F.LEVELS[:j["levels_needed"]], start=1):
                a_ = next((t_ for t_ in trail if t_["level_no"] == i), None)
                chain.append(f"{'✅' if a_ and a_['decision'] == 'APPROVED' else '❌' if a_ else '⏳' if i == j['level_no'] and j['status'] == 'PENDING_APPROVAL' else '·'} "
                             f"**{lv}**" + (f" {a_['approver']} {a_['ts'][5:16]}" + (f" — {a_['note']}" if a_['note'] else "") if a_ else ""))
            st.markdown(" → ".join(chain))
            if j["status"] == "APPROVED" and EDIT:
                g_ = st.selectbox("Garage", list(GAR), index=list(GAR).index(j["garage_id"]) if j["garage_id"] in GAR else 0, format_func=GAR.get,
                                  key=f"jo_g{pick}")
                if st.button("🛠 Send bus to the workshop", key=f"jo_out{pick}", type="primary"):
                    ok, m = F.send_to_garage(pick, user["username"], g_)
                    done("ok" if ok else "error", m)
            if j["status"] in ("APPROVED", "IN_WORKSHOP") and EDIT:
                st.markdown("**Parts used**")
                pl = F.parts()
                a_, b_, c_ = st.columns([3, 1, 1], vertical_alignment="bottom")
                pp = a_.selectbox("Part", [p["id"] for p in pl], key=f"jo_pp{pick}",
                                  format_func=lambda i: next(f"{p['name']} ({p['stock']:g} {p['unit']} in stock, QAR {p['unit_cost']:,.0f})" for p in pl if p["id"] == i))
                pq = b_.number_input("Qty", min_value=1.0, value=1.0, step=1.0, key=f"jo_pq{pick}")
                if c_.button("Issue", key=f"jo_pi{pick}"):
                    ok, m = F.issue_part(pp, pq, user["username"], j["bus_id"], pick)
                    done("ok" if ok else "error", m)
                st.markdown("**Complete**")
                a_, b_, c_ = st.columns(3)
                lc = a_.number_input("Labour / garage invoice (QAR)", min_value=0.0, value=float(round(j["est_cost"] * .5)), step=50.0, key=f"jo_lc{pick}")
                inv = b_.text_input("Invoice no.", key=f"jo_inv{pick}")
                km2 = c_.number_input("Odometer at completion", min_value=0, value=int(j["odometer"] or 0), step=1, key=f"jo_km2{pick}")
                if st.button("✅ Mark completed", key=f"jo_done{pick}", type="primary"):
                    ok, m = F.complete_job_order(pick, user["username"], lc, inv, km2)
                    done("ok" if ok else "error", m)
            if j["status"] == "COMPLETED":
                st.markdown(f"Completed {j['completed_ts'][:16]} · labour QAR {j['labour_cost']:,.0f} + parts QAR {j['parts_cost']:,.0f} = "
                            f"**QAR {j['total_cost']:,.0f}** · invoice {j['invoice_no'] or '—'}")
                if EDIT and st.button("Close job order", key=f"jo_close{pick}"):
                    F.close_job_order(pick, user["username"])
                    done("ok", f"{j['jo_no']} closed.")
            used = db.q("""SELECT m.ts, p.name, -m.qty qty, p.unit, m.unit_cost FROM stock_moves m JOIN parts p ON p.id=m.part_id WHERE m.jo_id=? AND m.kind='OUT'""",
                        (pick,))
            if used:
                st.dataframe(pd.DataFrame(used), hide_index=True, width="stretch")

# ------------------------------------------------------------------ approvals
with tabs[2]:
    st.caption("Approval chain: " + " → ".join(n for n, _ in F.LEVELS) + ". "
               + ("Every job order and PO goes through all four levels." if F.approval_mode() == "all" else
                  "Levels needed depend on the amount: " + ", ".join(f"{k} above QAR {v:,.0f}" for k, v in F.approval_limits().items()) + "."))
    if MY_LEVEL:
        st.markdown(f"You approve at **level {MY_LEVEL} · {F.LEVELS[MY_LEVEL - 1][0]}**.")
    elif role == "admin":
        st.markdown("Administrator: you can act at any level (for testing).")
    else:
        st.info("Your role does not approve job orders or POs. Sign in as fleetsup, manager, srmanager or vp (PIN 1234 for the demo approvers).")
    if not waiting_me:
        st.success("Nothing waiting for you.")
    for it in waiting_me:
        is_po = "po_no" in it
        with st.container(border=True):
            a_, b_ = st.columns([4, 1.6], vertical_alignment="center")
            if is_po:
                lines = db.q("SELECT p.name, l.qty, l.unit_cost FROM po_lines l JOIN parts p ON p.id=l.part_id WHERE l.po_id=?", (it["id"],))
                a_.markdown(f"**{it['po_no']}** · purchase order · {it['supplier']} · **QAR {it['total']:,.0f}**  \n"
                            + " · ".join(f"{l_['qty']:g} × {l_['name']}" for l_ in lines) + f"  \nLevel {it['level_no']} of {it['levels_needed']}")
            else:
                a_.markdown(f"**{it['jo_no']}** · {it['bus_no']} · {it['nature']} · {it['priority']} · **QAR {it['est_cost']:,.0f}**  \n{it['description']}  \n"
                            f"Garage {it['garage'] or '—'} · level {it['level_no']} of {it['levels_needed']}")
            note = b_.text_input("Note", key=f"apn{'po' if is_po else 'jo'}{it['id']}", label_visibility="collapsed", placeholder="Note (optional)")
            x1, x2 = b_.columns(2)
            if x1.button("Approve", key=f"apy{'po' if is_po else 'jo'}{it['id']}", type="primary", width="stretch"):
                ok, m = (F.decide_po if is_po else F.decide_job_order)(it["id"], True, user["username"], role, note)
                done("ok" if ok else "error", f"{it.get('po_no') or it.get('jo_no')}: {m}")
            if x2.button("Reject", key=f"apx{'po' if is_po else 'jo'}{it['id']}", width="stretch"):
                ok, m = (F.decide_po if is_po else F.decide_job_order)(it["id"], False, user["username"], role, note or "Rejected")
                done("warn" if ok else "error", f"{it.get('po_no') or it.get('jo_no')}: {m}")
    with st.expander("Everything waiting, all levels"):
        allw = [j for j in jo_all if j["status"] == "PENDING_APPROVAL"] + [p for p in po_all if p["status"] == "PENDING_APPROVAL"]
        st.dataframe(pd.DataFrame([{"No.": x_.get("jo_no") or x_.get("po_no"), "Type": "PO" if "po_no" in x_ else "Job order",
                                    "QAR": x_.get("est_cost") or x_.get("total"), "Waiting for": x_["waiting_for"],
                                    "Since": (x_.get("opened_ts") or x_.get("created_ts"))[:16]} for x_ in allw]), hide_index=True, width="stretch")

# ------------------------------------------------------------------ purchase orders
with tabs[3]:
    sups = db.q("SELECT * FROM suppliers WHERE active=1")
    SUP = {s_["id"]: s_["name"] for s_ in sups}
    if EDIT:
        with st.expander("➕ New purchase order"):
            sp = st.selectbox("Supplier", list(SUP), format_func=SUP.get, key="po_sup")
            pl = F.parts()
            jl = [j for j in jo_all if j["status"] in ("APPROVED", "IN_WORKSHOP")]
            pj = st.selectbox("For job order (optional)", [None] + [j["id"] for j in jl], key="po_jo",
                              format_func=lambda i: "— stock replenishment" if i is None else next(f"{j['jo_no']} · {j['bus_no']}" for j in jl if j["id"] == i))
            ed = st.data_editor(pd.DataFrame([{"Part": p["name"], "Qty": 0, "Unit cost (QAR)": p["unit_cost"]} for p in pl if p["supplier_id"] == sp] or
                                             [{"Part": pl[0]["name"], "Qty": 0, "Unit cost (QAR)": pl[0]["unit_cost"]}]),
                                hide_index=True, width="stretch", key=f"po_ed{sp}",
                                column_config={"Part": st.column_config.SelectboxColumn(options=[p["name"] for p in pl])}, num_rows="dynamic")
            lines = [(next(p["id"] for p in pl if p["name"] == r_["Part"]), float(r_["Qty"]), float(r_["Unit cost (QAR)"]))
                     for r_ in ed.to_dict("records") if r_.get("Part") and (r_.get("Qty") or 0) > 0]
            tot = sum(a * b for _, a, b in lines)
            st.caption(f"Total QAR {tot:,.2f} · approval: " + " → ".join(n for n, _ in F.LEVELS[:F.levels_for(tot)]))
            if st.button("Submit PO for approval", type="primary", disabled=not lines, key="po_go"):
                pid, no = F.create_po(sp, lines, user["username"], pj)
                done("ok", f"{no} submitted — waiting for the {F.LEVELS[0][0]}.")
    st.dataframe(pd.DataFrame([{"PO": p["po_no"], "Date": p["created_ts"][:10], "Supplier": p["supplier"], "Job order": p["jo_no"] or "",
                                "Total (QAR)": p["total"], "Status": STATUS_LAB.get(p["status"], p["status"]) + (f" · {p['waiting_for']}" if p["waiting_for"] else "")}
                               for p in po_all]), hide_index=True, width="stretch", height=300)
    act = [p for p in po_all if p["status"] in ("APPROVED", "SENT")]
    if act and EDIT:
        pp_ = st.selectbox("Approved / sent PO", [p["id"] for p in act], format_func=lambda i: next(f"{p['po_no']} · {p['supplier']} · {p['status'].title()}" for p in act if p["id"] == i),
                           key="po_act")
        p_ = next(p for p in act if p["id"] == pp_)
        x1, x2, x3 = st.columns(3)
        if p_["status"] == "APPROVED" and x1.button("📤 Mark sent to supplier", key="po_send"):
            ok, m = F.send_po(pp_, user["username"])
            done("ok" if ok else "error", m)
        if x2.button("📦 Goods received (add to stock)", key="po_recv", type="primary"):
            ok, m = F.receive_po(pp_, user["username"])
            done("ok" if ok else "error", m)
        po_lines = db.q("SELECT p.name, l.qty, l.unit_cost FROM po_lines l JOIN parts p ON p.id=l.part_id WHERE l.po_id=?", (pp_,))
        rows_ = "".join(f"<tr><td>{l_['name']}</td><td>{l_['qty']:g}</td><td>{l_['unit_cost']:,.2f}</td><td>{l_['qty'] * l_['unit_cost']:,.2f}</td></tr>" for l_ in po_lines)
        html = (f"<html><body style='font-family:Arial'><h2>Purchase order {p_['po_no']}</h2><p>{db.setting('school_name', db.SCHOOL_NAME)}<br>Date {p_['created_ts'][:10]}"
                f"<br>Supplier: {p_['supplier']}</p><table border='1' cellpadding='6' cellspacing='0'><tr><th>Item</th><th>Qty</th><th>Unit QAR</th><th>Total QAR</th></tr>"
                f"{rows_}<tr><td colspan='3'><b>Total</b></td><td><b>{p_['total']:,.2f}</b></td></tr></table><p>Approved: "
                + ", ".join(f"{a['level_name']} ({a['approver']}, {a['ts'][:10]})" for a in F.approval_trail('PO', pp_)) + "</p></body></html>")
        x3.download_button("🖨 Print PO", html.encode(), f"{p_['po_no']}.html", "text/html", key="po_print")

# ------------------------------------------------------------------ parts & stock
with tabs[4]:
    pl = F.parts()
    cat = st.pills("Category", ["All"] + F.PART_CATS, default="All", key="pt_cat") or "All"
    st.dataframe(pd.DataFrame([{"Code": p["code"], "Item": p["name"], "Category": p["category"], "In stock": p["stock"], "Unit": p["unit"],
                                "Minimum": p["min_stock"], "Unit cost (QAR)": p["unit_cost"], "Supplier": p["supplier"],
                                "": "⚠ reorder" if p["stock"] <= p["min_stock"] else ""} for p in pl if cat == "All" or p["category"] == cat]),
                 hide_index=True, width="stretch", height=360)
    mv = db.q("""SELECT m.ts, p.name, m.qty, p.unit, m.kind, b.bus_no, j.jo_no, po.po_no, m.by_user FROM stock_moves m JOIN parts p ON p.id=m.part_id
                 LEFT JOIN buses b ON b.id=m.bus_id LEFT JOIN job_orders j ON j.id=m.jo_id LEFT JOIN purchase_orders po ON po.id=m.po_id
                 ORDER BY m.ts DESC LIMIT 100""")
    with st.expander(f"Stock movements ({len(mv)} latest)"):
        st.dataframe(pd.DataFrame(mv), hide_index=True, width="stretch")
    if EDIT:
        with st.expander("➕ Issue a part to a bus (without a job order)"):
            a_, b_, c_ = st.columns([3, 1, 2])
            ip = a_.selectbox("Part", [p["id"] for p in pl], format_func=lambda i: next(f"{p['name']} ({p['stock']:g} {p['unit']})" for p in pl if p["id"] == i), key="is_p")
            iq = b_.number_input("Qty", min_value=1.0, value=1.0, key="is_q")
            ib = c_.selectbox("Bus", list(BUSN), format_func=BUSN.get, key="is_b")
            if st.button("Issue", key="is_go"):
                ok, m = F.issue_part(ip, iq, user["username"], ib, None, "Direct issue")
                done("ok" if ok else "error", m)

# ------------------------------------------------------------------ fuel
with tabs[5]:
    a_, b_ = st.columns(2)
    fr = a_.date_input("From", value=NOW.date() - timedelta(days=30), key="fu_from")
    to = b_.date_input("To", value=NOW.date(), key="fu_to")
    logs = F.fuel_logs(fr.isoformat(), to.isoformat())
    U.kpis([("FUEL ISSUED", f"{sum(x_['litres'] for x_ in logs):,.0f} L", f"{len(logs)} fills", "#1B2559", ""),
            ("COST", f"QAR {sum(x_['amount'] for x_ in logs):,.0f}", f"at QAR {F.diesel_price():.2f}/L (setting)", BLUE, ""),
            ("FLAGGED FILLS", sum(1 for x_ in logs if x_["flag"]), "check these", RED, "red" if any(x_["flag"] for x_ in logs) else "")], n=3)
    if EDIT:
        with st.expander("➕ Record a fuel issuance"):
            a_, b_, c_ = st.columns(3)
            fb = a_.selectbox("Bus", list(BUSN), format_func=BUSN.get, key="fu_b")
            fl_ = b_.number_input("Litres", min_value=1.0, value=120.0, step=1.0, key="fu_l")
            fk = c_.number_input("Odometer (km)", min_value=0, value=int(F.odometer(fb)), step=1, key=f"fu_k{fb}")
            a_, b_, c_ = st.columns(3)
            fs = a_.text_input("Station", value="Woqod Al Wakrah", key="fu_s")
            fc = b_.text_input("Fuel card no.", key="fu_c")
            frc = c_.text_input("Receipt no.", key="fu_r")
            st.caption(f"Amount = litres × QAR {F.diesel_price():.2f}. Woqod statements can be imported in Setup → Integrations.")
            if st.button("Save fuel record", type="primary", key="fu_go"):
                fid, flags = F.add_fuel(fb, fl_, fk, user["username"], fs, fc, frc)
                done("warn" if flags else "ok", "Fuel recorded." + (" ⚠ " + "; ".join(flags) if flags else ""))
    ef = pd.DataFrame(F.fuel_efficiency(fr.isoformat(), to.isoformat()))
    c1, c2 = st.columns([1.2, 1])
    with c1, st.container(border=True):
        st.markdown("<div class='sect'>Efficiency by bus (km per litre)</div>", unsafe_allow_html=True)
        if not ef.empty:
            e2 = ef.dropna(subset=["Km per litre"])
            avg = e2["Km per litre"].mean()
            st.altair_chart((alt.Chart(e2).mark_circle(size=60, color=BLUE).encode(
                x=alt.X("Km:Q", title="Km driven"), y=alt.Y("Km per litre:Q", scale=alt.Scale(zero=False)), tooltip=["Bus", "Km", "Litres", "Km per litre"])
                + alt.Chart(pd.DataFrame({"y": [avg]})).mark_rule(color=GREEN, strokeDash=[4, 4]).encode(y="y:Q")).properties(height=260), width="stretch")
            st.caption(f"Dashed line: fleet average {avg:.2f} km/L. Buses well below it are worth a check (driving style, leaks, card misuse).")
    with c2, st.container(border=True):
        st.markdown("<div class='sect'>Flagged fills</div>", unsafe_allow_html=True)
        fg = [x_ for x_ in logs if x_["flag"]]
        st.dataframe(pd.DataFrame([{"Date": x_["date"], "Bus": x_["bus_no"], "Litres": x_["litres"], "Flag": x_["flag"]} for x_ in fg]), hide_index=True,
                     width="stretch", height=260)
    with st.expander(f"All fuel records ({len(logs)})"):
        st.dataframe(pd.DataFrame([{"Date": x_["date"], "Bus": x_["bus_no"], "Litres": x_["litres"], "QAR": x_["amount"], "Odometer": x_["odometer"],
                                    "Station": x_["station"], "Card": x_["card_no"], "Receipt": x_["receipt_no"], "Source": x_["source"], "Flag": x_["flag"] or ""}
                                   for x_ in logs]), hide_index=True, width="stretch")
        st.download_button("Download fuel report (CSV)", pd.DataFrame(logs).to_csv(index=False).encode(), f"fuel_{fr}_{to}.csv", "text/csv")

# ------------------------------------------------------------------ service plans
with tabs[6]:
    sb = F.service_board()
    stt = st.pills("Show", ["Overdue", "Due soon", "All"], default="Overdue", key="sv_f") or "Overdue"
    rows = [s_ for s_ in sb if stt == "All" or (stt == "Overdue" and s_["State"] == "OVERDUE") or (stt == "Due soon" and s_["State"] == "DUE_SOON")]
    st.caption("Preventive maintenance by mileage and service interval. The odometer comes from fuel fills, the daily vehicle check and (when connected) "
               "the GPS tracker.")
    st.dataframe(pd.DataFrame([{"Bus": s_["Bus"], "Service": s_["Service"], "Last done": s_["Last date"], "Last km": s_["Last km"], "Odometer": s_["Odometer"],
                                "Km left": s_["Km left"], "Days left": s_["Days left"],
                                "State": {"OVERDUE": "🔴 Overdue", "DUE_SOON": "🟠 Due soon", "OK": "🟢 OK"}[s_["State"]],
                                "Job order": "open" if s_["Job order open"] else ""} for s_ in rows]), hide_index=True, width="stretch", height=380)
    todo = [s_ for s_ in rows if s_["State"] != "OK" and not s_["Job order open"]]
    if todo and EDIT:
        pk = st.multiselect("Raise job orders for", [f"{s_['Bus']} · {s_['Service']}" for s_ in todo], key="sv_pick")
        if st.button("Raise job orders", type="primary", disabled=not pk, key="sv_go"):
            n_ = 0
            for s_ in todo:
                if f"{s_['Bus']} · {s_['Service']}" in pk:
                    F.create_job_order(s_["bus_id"], "Preventive service", f"{s_['Service']} due (last {s_['Last date']}, {s_['Last km']:,} km)",
                                       s_["est_cost"] or 500, user["username"], garages[0]["id"] if garages else None, "Normal", "Service plan",
                                       s_["Odometer"], plan_id=s_["plan_id"])
                    n_ += 1
            done("ok", f"{n_} job order(s) raised for approval.")
    with st.expander("Service plans"):
        st.dataframe(pd.DataFrame(db.q("SELECT name AS Service, interval_km AS 'Every km', interval_days AS 'Every days', est_cost AS 'Estimate QAR' FROM service_plans WHERE active=1")),
                     hide_index=True, width="stretch")

# ------------------------------------------------------------------ documents
with tabs[7]:
    docs = F.documents()
    t1, t2 = st.columns([2, 1])
    df_ = t1.pills("Show", ["Expired", "Expiring ≤ 30 days", "All"], default="Expiring ≤ 30 days", key="dc_f") or "All"
    ow = t2.pills("For", ["Buses", "Staff", "Both"], default="Both", key="dc_o") or "Both"
    rows = [d_ for d_ in docs if (df_ == "All" or (df_ == "Expired" and d_["state"] == "EXPIRED") or (df_.startswith("Expiring") and d_["state"] == "EXPIRING"))
            and (ow == "Both" or (ow == "Buses") == (d_["owner_type"] == "BUS"))]
    st.dataframe(pd.DataFrame([{"Owner": d_["owner"], "Role": d_["owner_role"].title(), "Document": d_["doc_type"], "No.": d_["doc_no"], "Expiry": d_["expiry_date"],
                                "State": {"EXPIRED": f"🔴 Expired {-d_['days_left']} d", "EXPIRING": f"🟠 {d_['days_left']} d left", "VALID": "🟢 Valid"}.get(d_["state"], ""),
                                "File": "📎" if d_["has_file"] else ""} for d_ in rows]), hide_index=True, width="stretch", height=340)
    if EDIT:
        with st.expander("➕ Add or renew a document (with scan / photo)"):
            kind = st.radio("For", ["Bus", "Staff"], horizontal=True, key="dc_k")
            if kind == "Bus":
                oid = st.selectbox("Bus", list(BUSN), format_func=BUSN.get, key="dc_b")
                types = F.BUS_DOCS
            else:
                stf = db.q("SELECT id, name, role FROM staff WHERE role IN ('driver','supervisor','caretaker') ORDER BY name")
                oid = st.selectbox("Staff", [s_["id"] for s_ in stf], format_func=lambda i: next(f"{s_['name']} · {s_['role']}" for s_ in stf if s_["id"] == i), key="dc_s")
                rl = next(s_["role"] for s_ in stf if s_["id"] == oid)
                types = F.STAFF_DOCS.get(rl, ["QID"])
            a_, b_ = st.columns(2)
            dt = a_.selectbox("Document", types + ["Other"], key="dc_t")
            dn = b_.text_input("Number", key="dc_n")
            di = a_.date_input("Issued", key="dc_i")
            de = b_.date_input("Expires", value=NOW.date() + timedelta(days=365), key="dc_e")
            up = st.file_uploader("Scan / photo (PDF or image)", type=["pdf", "png", "jpg", "jpeg"], key="dc_u")
            if st.button("Save document", type="primary", key="dc_go", disabled=not dn.strip()):
                F.save_document("BUS" if kind == "Bus" else "STAFF", oid, dt, dn.strip(), di.isoformat(), de.isoformat(), user["username"],
                                up.name if up else None, up.type if up else None, up.getvalue() if up else None)
                done("ok", f"{dt} saved — it shows in the crew app for this bus.")

# ------------------------------------------------------------------ driver behaviour (vehicle tracker telematics)
with tabs[8]:
    import integrations as I
    a_, b_ = st.columns(2)
    df_from = a_.date_input("From", value=NOW.date() - timedelta(days=7), key="db_f")
    df_to = b_.date_input("To", value=NOW.date(), key="db_t")
    beh = I.driver_behaviour(df_from.isoformat(), df_to.isoformat())
    ev = db.q("SELECT kind, COUNT(*) n FROM telematics_events WHERE date BETWEEN ? AND ? GROUP BY kind", (df_from.isoformat(), df_to.isoformat()))
    evm = {e["kind"]: e["n"] for e in ev}
    U.kpis([("SPEEDING", evm.get("SPEEDING", 0), f"above {I.speed_limit():.0f} km/h", RED, "red" if evm.get("SPEEDING") else ""),
            ("HARSH BRAKING", evm.get("HARSH_BRAKE", 0), "", AMBER, ""), ("RASH ACCELERATION", evm.get("HARSH_ACCEL", 0), "", AMBER, ""),
            ("LONG IDLING", evm.get("IDLE", 0), "engine on, bus stopped > 10 min", "#1B2559", "")], n=4)
    st.caption("From the vehicle tracker's telematics feed (POST /v1/telematics/events and /v1/gps/positions). Score = 100 − 5 per speeding event − "
               "3 per harsh braking / acceleration / cornering − 1 per long idle. Demo data until the tracker is connected.")
    if beh:
        st.dataframe(pd.DataFrame([{"Driver": b["Driver"], "Bus": b["Bus"], "Score": b["Score"], "Speeding": b["SPEEDING"], "Harsh braking": b["HARSH_BRAKE"],
                                    "Rash acceleration": b["HARSH_ACCEL"], "Sharp cornering": b["HARSH_TURN"], "Long idling": b["IDLE"]} for b in beh]),
                     hide_index=True, width="stretch", height=380,
                     column_config={"Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%d")})
    else:
        st.info("No telematics events in this period.")

# ------------------------------------------------------------------ settings
with tabs[9]:
    if role not in ("admin", "manager"):
        st.info("Only the administrator or transport manager can change these settings.")
    else:
        mode = st.radio("Approval chain", ["amount", "all"], index=0 if F.approval_mode() == "amount" else 1, horizontal=True, key="se_mode",
                        format_func={"amount": "By amount (higher levels only above their limits)", "all": "All four levels for every job order and PO"}.get)
        lim = F.approval_limits()
        a_, b_, c_ = st.columns(3)
        l2 = a_.number_input("Manager above (QAR)", value=float(lim["Manager"]), step=500.0, key="se_l2")
        l3 = b_.number_input("Senior Manager above (QAR)", value=float(lim["Senior Manager"]), step=500.0, key="se_l3")
        l4 = c_.number_input("Vice President above (QAR)", value=float(lim["Vice President"]), step=1000.0, key="se_l4")
        pr = st.number_input("Diesel price (QAR per litre)", value=F.diesel_price(), step=0.05, format="%.2f", key="se_pr",
                             help="Used for manual fuel entries; imported Woqod statements carry their own amounts.")
        if st.button("Save settings", type="primary", key="se_go"):
            db.set_setting("approval_mode", mode)
            db.set_setting("approval_limits", __import__("json").dumps({"Manager": l2, "Senior Manager": l3, "Vice President": l4}))
            db.set_setting("diesel_price_qar", pr)
            db.audit(user["username"], role, "FLEET_SETTINGS", "settings", "fleet", f"{mode} {l2}/{l3}/{l4} price {pr}")
            done("ok", "Settings saved.")
        st.caption("Demo approvers (PIN 1234): fleetsup = Supervisor, manager = Manager (manager123), srmanager = Senior Manager, vp = Vice President.")
