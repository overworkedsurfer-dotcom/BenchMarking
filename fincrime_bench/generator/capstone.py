"""Cross-domain investigations that require chaining telecom, bank, registry and tax evidence."""

from __future__ import annotations

from datetime import date, timedelta

from . import names as N
from .common import add_task, adult_customer, field, find_officer, fmt_money, light_activity, \
    new_personal_account, odd_amount, offshore_shell, us_shell
from .telecom import _single_registered_phone
from .world import YEAR, World, money


def _has_return(w: World, p: dict) -> bool:
    rid = w.return_of.get(p["tin"]) if p["tin"] else None
    return bool(rid) and w.returns_by_id[rid]["form_type"] == "1040"


# ========================================================= SCAM CAPSTONE
def plant_capstone_scam(w: World, task_id: str) -> None:
    rng = w.rng
    g = w.pick(lambda p: adult_customer(p, 30, 56) and p["_status"] == "employed" and _has_return(w, p)
               and w.returns_by_id[w.return_of[p["tin"]]]["spouse_tin"] is None)[0]
    gid = g["person_id"]
    start = date(YEAR, rng.randint(2, 4), rng.randint(1, 28))
    end = start + timedelta(days=rng.randint(70, 110))
    burner = w.new_phone(None, "prepaid", start - timedelta(days=rng.randint(5, 25)))
    s_off, _ = offshore_shell(w, [(gid, 100.0)], cc=rng.choice(["VG", "SC"]), open_account=False)
    manager = w.pick(lambda p: adult_customer(p, 22, 35))[0]["person_id"]
    s_us, k_acct = us_shell(w, [(s_off, 100.0)], manager, start - timedelta(days=rng.randint(60, 200)),
                            industry="consulting")
    w.add_owner(manager, s_us, "officer", None, start - timedelta(days=30), title="Office Manager")
    mules = w.pick(lambda p: adult_customer(p, 19, 30), 3)
    mule_accts = []
    for m in mules:
        a = new_personal_account(w, m["person_id"], start - timedelta(days=rng.randint(20, 90)))
        light_activity(w, a, m["person_id"], date(YEAR, 1, 1), date(YEAR, 12, 20), rng.randint(2, 5))
        mule_accts.append(a)
    victims = w.pick(lambda p: adult_customer(p, 68, 92) and _single_registered_phone(w, p), 5)
    city = g["_city"]
    losses = 0.0
    k_inflows: list[tuple[float, object]] = []
    first = None
    for v in victims:
        vacct = w.checking_of(v["person_id"])
        d0 = w.r_date(start, end - timedelta(days=30))
        days = [d0] + ([d0 + timedelta(days=rng.randint(7, 20))] if rng.random() < 0.35 else [])
        for day in days:
            tc = w.r_daytime(day, 10, 17)
            vphone = w.active_phone(v["person_id"], day)
            if not vphone:
                continue
            w.add_call(tc, burner, vphone, call_type="voice", duration=rng.randint(420, 1700), tower=w.tower_in(city))
            mi = rng.randrange(3)
            tp = tc + timedelta(minutes=rng.randint(15, 55))
            amt = odd_amount(w, 3_200, 19_500)
            tid = w.add_txn(tp, vacct, mule_accts[mi], amt, rng.choice(["wire", "p2p"]),
                            rng.choice(["tax settlement", "bail", "account protection", "IRS payment"]))
            losses += amt
            if first is None:
                first = (v, tc, mule_accts[mi], tid)
            ft = tp + timedelta(hours=rng.uniform(1, 18))
            famt = money(amt * rng.uniform(0.88, 0.93))
            w.add_txn(ft, mule_accts[mi], k_acct, famt, "ach", "")
            k_inflows.append((famt, ft))
    # failed attempts: called but never paid (not victims)
    non_payers = w.pick(lambda p: adult_customer(p, 68, 92) and _single_registered_phone(w, p), 3)
    for v in non_payers:
        day = w.r_date(start, end)
        ph = w.active_phone(v["person_id"], day)
        if ph:
            w.add_call(w.r_daytime(day, 10, 17), burner, ph, call_type="voice", duration=rng.randint(20, 240),
                       tower=w.tower_in(city))
    # collector pays the organizer and buys a car in the company name
    total_in = sum(a for a, _ in k_inflows)
    g_acct = w.checking_of(gid)
    paid_g = 0.0
    n_pay = rng.randint(3, 5)
    for k in range(n_pay):
        ts = w.r_daytime(start + timedelta(days=30 + 21 * k), 9, 16)
        amt = money(total_in * rng.uniform(0.07, 0.11))
        w.add_txn(ts, k_acct, g_acct, amt, "ach", "MANAGEMENT FEE")
        paid_g += amt
    car = money(total_in * rng.uniform(0.25, 0.33))
    day = end - timedelta(days=rng.randint(0, 10))
    w.add_txn(w.r_daytime(day), k_acct, w.checking_of(rng.choice(w.infra["auto_dealer"])), car, "wire", "FLEET VEHICLE")
    w.add_asset("vehicle", w._vehicle_desc(car), s_us, day, car, "cash")
    v0, tc0, mule0, _tid0 = first
    prompt = f"""
On {tc0.date().isoformat()} at about {tc0.strftime('%H:%M')}, {w.full_name(v0['person_id'])} \
({v0['person_id']}), age {v0['_age']}, received a call from an unknown number, {burner}. Shortly afterwards \
they sent money to a stranger. Police suspect an organised phone-scam operation.

Run the full investigation across call records, bank transactions, the corporate registry and tax data.

Report:
- first_recipient_account: the account that received this victim's payment after the call.
- collection_account: the account where the scam proceeds were consolidated.
- organizer_person_id: the natural person who ultimately owns and profits from the operation.
- victim_person_ids: every person who lost money to this operation (people who were called but did not pay \
are not victims).
- total_victim_losses: total amount (USD) all victims paid into the operation's accounts in 2025.
- organizer_unreported_income: the amount (USD) the organizer personally received from the operation in 2025 \
that does not appear on their 2025 tax return.
"""
    fields = [
        field("first_recipient_account", "id", 0.1, "account_id."),
        field("collection_account", "id", 0.15, "account_id."),
        field("organizer_person_id", "id", 0.3, "person_id."),
        field("victim_person_ids", "id_set", 0.2, "person_ids."),
        field("total_victim_losses", "number", 0.1, "USD.", rel_tol=0.02, zero_at=0.3),
        field("organizer_unreported_income", "number", 0.15, "USD.", rel_tol=0.02, zero_at=0.3),
    ]
    victim_ids = []
    for v in victims:
        vacct = w.checking_of(v["person_id"])
        if any(t["from_account"] == vacct and t["to_account"] in mule_accts for t in w.tables["transactions"]):
            victim_ids.append(v["person_id"])
    answer = {"first_recipient_account": mule0, "collection_account": k_acct, "organizer_person_id": gid,
              "victim_person_ids": victim_ids, "total_victim_losses": money(losses),
              "organizer_unreported_income": money(paid_g)}
    add_task(w, task_id, "investigation", "scam_capstone", "hard", "From a scam call to the organizer's tax return",
             prompt, fields, answer, inputs={"burner": burner, "victim": v0["person_id"], "call_time": tc0.strftime("%Y-%m-%dT%H:%M:%S")},
             decoys={"organizer_person_id": [manager] + [m["person_id"] for m in mules] + w.nominees,
                     "victim_person_ids": [p["person_id"] for p in non_payers]},
             notes="Burner -> victims -> mules -> LLC collector (officer is a decoy) -> offshore parent -> organizer; "
                   "LLC pays organizer 'management fees' absent from their W-2-only return.")


# ======================================================== AUDIT CAPSTONE
def plant_capstone_audit(w: World, task_id: str) -> None:
    rng = w.rng
    owned_2025 = {a["owner_id"] for a in w.tables["assets"] if (a["purchase_date"] or "") >= "2025"}

    def biz_ok(b: dict) -> bool:
        return b["_rev"] > 900_000 and b["_ind"] not in N.CASH_INTENSIVE

    def person_ok(p: dict) -> bool:
        return (adult_customer(p, 30, 64) and _has_return(w, p) and p["person_id"] not in owned_2025
                and w.returns_by_id[w.return_of[p["tin"]]]["total_income"] < 150_000)

    u2, q = find_officer(w, biz_ok, person_ok, titles=("Procurement Director", "Controller", "CFO",
                                                         "Procurement Manager"))
    w.reserve(u2, q)
    qp = w.P[q]
    rel = [r for r in qp["_children"] + qp["_parents"] if r not in w.reserved and w.P[r]["_age"] >= 21]
    nominee = rel[0] if rel else w.pick(lambda p: adult_customer(p, 25, 75))[0]["person_id"]
    w.reserve(nominee)
    v2, v_acct = us_shell(w, [(nominee, 100.0)], q, date(2024, rng.randint(1, 9), rng.randint(1, 28)),
                          industry="consulting")
    s_off, o_acct = offshore_shell(w, [(nominee, 100.0)], cc=rng.choice(["PA", "CY", "AE"]))
    u_acct = w.checking_of(u2)
    r = w.returns_by_id[w.return_of[qp["tin"]]]
    price = money(r["total_income"] + rng.uniform(380_000, 520_000))
    target_div = price / rng.uniform(0.72, 0.8)
    months = sorted(rng.sample(range(1, 11), rng.randint(9, 10)))
    diverted = 0.0
    for m in months:
        amt = money(target_div / len(months) * rng.uniform(0.85, 1.15)) + rng.choice([0.0, 0.27, 0.64])
        ts = w.r_daytime(date(YEAR, m, rng.randint(2, 26)), 9, 17)
        w.add_txn(ts, u_acct, v_acct, amt, "ach", f"MARKETING SERVICES INV {rng.randint(100, 999)}")
        diverted += amt
        w.add_txn(ts + timedelta(days=rng.uniform(1, 3)), v_acct, o_acct, money(amt * rng.uniform(0.88, 0.93)),
                  "international_wire", "MEDIA BUYING")
    other = rng.choice([b for b in w.businesses if b["_infra"] is None and b.get("_size", 0) > 0
                        and b["business_id"] not in (u2,)])
    for _ in range(2):
        w.add_txn(w.r_daytime(w.r_date(date(YEAR, 2, 1), date(YEAR, 9, 1))), w.checking_of(other["business_id"]),
                  v_acct, odd_amount(w, 3_000, 9_000), "ach", "MARKETING")
    # offshore account funds the officer's house purchase
    q_acct = w.checking_of(q)
    buy = date(YEAR, 11, rng.randint(3, 25))
    received = 0.0
    n = rng.randint(2, 3)
    for k in range(n):
        amt = money(price / n * rng.uniform(1.0, 1.04))
        w.add_txn(w.r_daytime(buy - timedelta(days=50 - 18 * k)), o_acct, q_acct, amt, "international_wire",
                  "LOAN DISBURSEMENT")
        received += amt
    w.expect("tax_lifestyle_01", "person_ids", [q])
    w.add_asset("real_estate", "Single-family residence", q, buy, price, "cash",
                address_id=w.new_address(qp["_city"], qp["_state"]))
    w.add_txn(w.r_daytime(buy), q_acct, w.checking_of(rng.choice(w.infra["title"])), price, "wire", "CLOSING FUNDS")
    prompt = f"""
{w.full_name(q)} ({q}) reported total income of {fmt_money(r['total_income'])} on their 2025 tax return \
(return {r['return_id']}), yet bought a home for {fmt_money(price)} in cash on {buy.isoformat()}.

Trace the source of the money used for the purchase back to where it came from, and identify the people and \
entities involved.

Report:
- funding_account: the account that sent the taxpayer the money used for the purchase.
- conduit_vendor_id: business_id of the company used to channel the money.
- source_company_id: business_id of the company whose funds were diverted.
- nominee_person_id: person_id of the person named as owner of the conduit company in the registry.
- total_diverted: total amount (USD) the source company paid the conduit company in 2025.
- unreported_income: total amount (USD) the taxpayer received from the funding account in 2025.
"""
    fields = [
        field("funding_account", "id", 0.15, "account_id."),
        field("conduit_vendor_id", "id", 0.2, "business_id."),
        field("source_company_id", "id", 0.2, "business_id."),
        field("nominee_person_id", "id", 0.15, "person_id."),
        field("total_diverted", "number", 0.15, "USD.", rel_tol=0.01, zero_at=0.25),
        field("unreported_income", "number", 0.15, "USD.", rel_tol=0.01, zero_at=0.25),
    ]
    add_task(w, task_id, "investigation", "audit_capstone", "hard", "Source of funds for a cash home purchase",
             prompt, fields,
             {"funding_account": o_acct, "conduit_vendor_id": v2, "source_company_id": u2,
              "nominee_person_id": nominee, "total_diverted": money(diverted), "unreported_income": money(received)},
             inputs={"person_id": q},
             decoys={"source_company_id": [other["business_id"]], "nominee_person_id": [q] + w.nominees},
             notes="Employer -> conduit LLC (relative is member, taxpayer is signer) -> offshore (relative owns) -> "
                   "taxpayer -> title company.")

