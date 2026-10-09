"""Corporate-ownership schemes: hidden beneficial owners and insider kickbacks."""

from __future__ import annotations

from datetime import date, timedelta

from . import names as N
from .common import add_task, adult_customer, field, find_officer, foreign_person, nominee_director, odd_amount, \
    offshore_shell, remove_insider_conflicts, shell_name
from .tax import add_wages
from .world import YEAR, World, d2s, money


def _adult(p: dict) -> bool:
    return adult_customer(p, 30, 75)


def _us_llc(w: World, name: str, state: str, inc: date, industry: str, agent: bool = True) -> str:
    addr = w.rng.choice(w.reg_agent_addrs) if agent else w.new_address(*w.random_city(), "commercial")
    b = w.new_business(name, "LLC", industry, state, addr, inc, _shell=True, _files_return=False)
    w.reserve(b["business_id"])
    return b["business_id"]


# ====================================================================== UBO
def plant_ubo(w: World, task_id: str, difficulty: str) -> None:
    rng = w.rng
    gold: dict[str, float] = {}
    decoys: list[str] = []
    if difficulty == "easy":
        p1, p2, p3, p4 = [p["person_id"] for p in w.pick(_adult, 4)]
        inc = w.r_date(date(2016, 1, 1), date(2021, 1, 1))
        h1 = _us_llc(w, f"{rng.choice(N.BUSINESS_WORDS)} Family Holdings LLC", "DE", inc, "holding company")
        w.add_owner(p1, h1, "member", 100.0, inc)
        t = _us_llc(w, f"{rng.choice(N.BUSINESS_WORDS)} Civil Works LLC", rng.choice(["TX", "AZ", "OH"]),
                    inc + timedelta(days=200), "construction", agent=False)
        tinc = inc + timedelta(days=200)
        w.add_owner(h1, t, "member", 60.0, tinc)
        w.add_owner(p2, t, "member", 30.0, tinc)
        w.add_owner(p3, t, "member", 10.0, tinc)
        w.add_owner(p4, t, "officer", None, tinc, title="Managing Director")
        gold = {p1: 60.0, p2: 30.0}
        decoys = [p3, p4]
        context = "was awarded a municipal road-maintenance contract"
    elif difficulty == "medium":
        p1, p2, p3, p4 = [p["person_id"] for p in w.pick(_adult, 4)]
        inc = w.r_date(date(2014, 1, 1), date(2019, 1, 1))
        h3, _ = offshore_shell(w, [(p2, 100.0)], cc="CY", inc=inc, open_account=False)
        h1 = _us_llc(w, shell_name(w, "LLC"), "WY", inc + timedelta(days=60), "holding company")
        w.add_owner(p1, h1, "member", 56.0, inc + timedelta(days=60))
        w.add_owner(h3, h1, "member", 44.0, inc + timedelta(days=60))
        h2 = _us_llc(w, shell_name(w, "LLC"), "DE", inc + timedelta(days=90), "holding company")
        w.add_owner(p2, h2, "member", 40.0, inc + timedelta(days=90))
        w.add_owner(p3, h2, "member", 60.0, inc + timedelta(days=90))
        tinc = inc + timedelta(days=400)
        t = _us_llc(w, f"{rng.choice(N.BUSINESS_WORDS)} Medical Supply LLC", rng.choice(["FL", "NV", "NJ"]), tinc,
                    "retail", agent=False)
        w.add_owner(h1, t, "member", 50.0, tinc)
        w.add_owner(h2, t, "member", 50.0, tinc)
        w.add_owner(p4, t, "officer", None, tinc, title="CEO")
        gold = {p1: 28.0, p2: 42.0, p3: 30.0}
        decoys = [p4] + w.nominees
        context = "is bidding on a state Medicaid supply contract"
    else:
        p5, p7, p8 = [p["person_id"] for p in w.pick(_adult, 3)]
        p6 = w.pick(_adult)[0]["person_id"]
        p9 = foreign_person(w, "KY")
        inc = w.r_date(date(2012, 1, 1), date(2016, 1, 1))
        tr = w.new_business(f"{rng.choice(N.SHELL_WORDS)} Family Trust", "Trust", "trust", "KY",
                            rng.choice(w.offshore_addrs["KY"]), inc, _shell=True, _files_return=False)["business_id"]
        w.reserve(tr)
        w.add_owner(p7, tr, "beneficiary", 75.0, inc)
        w.add_owner(p8, tr, "beneficiary", 25.0, inc)
        w.add_owner(p9, tr, "trustee", None, inc)
        s2, _ = offshore_shell(w, [(tr, 100.0)], cc="VG", inc=inc + timedelta(days=30), open_account=False)
        # a shareholder of S2 must be recorded as shareholder, not beneficiary
        for o in w.tables["ownership"]:
            if o["owned_id"] == s2 and o["owner_id"] == tr:
                o["role"] = "shareholder"
        s1, _ = offshore_shell(w, [], cc="CY", inc=date(2019, 1, 1), open_account=False)
        w.add_owner(p6, s1, "shareholder", 100.0, date(2019, 1, 1), end=date(2023, 5, 1))
        w.add_owner(s2, s1, "shareholder", 100.0, date(2023, 5, 1))
        tinc = date(2021, rng.randint(1, 12), rng.randint(1, 28))
        t = _us_llc(w, f"{rng.choice(N.BUSINESS_WORDS)} Port Logistics LLC", rng.choice(["NJ", "FL", "TX"]), tinc,
                    "logistics", agent=False)
        w.add_owner(s1, t, "member", 80.0, tinc)
        w.add_owner(p5, t, "member", 20.0, tinc)
        gold = {p7: 60.0}
        decoys = [p5, p6, p8, p9] + w.nominees
        context = "applied for a federal port-security grant"
    tb = w.B[t]
    acct = w.new_account(t, w._pick_bank(), "US", "business_checking",
                         date.fromisoformat(tb["incorporation_date"]) + timedelta(days=20))
    for _ in range(rng.randint(4, 9)):
        payer = rng.choice([b for b in w.businesses if b["_infra"] is None and b.get("_size", 0) > 0])
        w.add_txn(w.r_daytime(w.r_date(date(YEAR, 1, 5), date(YEAR, 12, 20))), w.checking_of(payer["business_id"]),
                  acct, odd_amount(w, 8_000, 60_000), "ach", f"INV {rng.randint(1000, 9999)}")
    prompt = f"""
{tb['name']} ({t}) {context}. Under beneficial-ownership rules you must identify every natural person who \
owns at least 25% of it, directly or indirectly, as of 2025-12-31.

Effective ownership through a chain of entities is the product of the ownership percentages along the chain, \
summed over all chains. Interests of trust beneficiaries count as ownership. Control-only roles (directors, \
officers, trustees) do not, and roles that ended before 2025-12-31 do not count.

Report owners: a list of {{"id": <person_id>, "value": <effective ownership percent, 0-100>}} for every natural \
person with at least 25%.
"""
    fields = [field("owners", "id_number_map", 1.0, "List of {id: person_id, value: percent}.", value_weight=0.4,
                    abs_tol=1.0, abs_zero_at=10.0)]
    add_task(w, task_id, "ownership", "beneficial_ownership", difficulty, "Ultimate beneficial owners", prompt,
             fields, {"owners": [{"id": k, "value": v} for k, v in gold.items()]}, inputs={"business_id": t},
             decoys={"owners": decoys})


# ================================================================ KICKBACK
def plant_kickback(w: World, task_id: str, difficulty: str, variant: str) -> None:
    rng = w.rng
    def biz_ok(b: dict) -> bool:
        return b["_ind"] not in N.CASH_INTENSIVE and b["_rev"] >= 1_000_000 and len(b["_employees"]) >= 3

    def person_ok(p: dict) -> bool:
        if variant == "spouse":
            return bool(p["_spouse"]) and p["_spouse"] not in w.reserved and p["_age"] >= 25
        return p["_age"] >= 25

    u, insider = find_officer(w, biz_ok, person_ok)
    w.reserve(u, insider)
    bo = w.P[insider]["_spouse"] if variant == "spouse" else insider
    w.reserve(bo)
    uacct = w.checking_of(u)
    inc_h = w.r_date(date(2022, 6, 1), date(2024, 1, 1))
    h, hacct = offshore_shell(w, [(bo, 100.0)], cc=rng.choice(["BZ", "SC", "AE"]), inc=inc_h)
    inc_v = w.r_date(date(2024, 1, 1), date(2024, 10, 1))
    vname = f"{rng.choice(N.SHELL_WORDS)} Strategic Consulting LLC"
    v = _us_llc(w, vname, rng.choice(["WY", "DE"]), inc_v, "consulting")
    w.add_owner(h, v, "member", 100.0, inc_v)
    if variant == "direct":
        signer = insider
        w.add_owner(insider, v, "officer", None, inc_v, title="Manager")
    else:
        signer = nominee_director(w)
        w.add_owner(signer, v, "director", None, inc_v)
    vacct = w.new_account(v, w._pick_bank(), "US", "business_checking", inc_v + timedelta(days=15), signer=signer)
    total = 0.0
    months = sorted(rng.sample(range(1, 13), rng.randint(9, 12)))
    for m in months:
        amt = odd_amount(w, 17_000, 46_000)
        ts = w.r_daytime(date(YEAR, m, rng.randint(3, 25)), 9, 17)
        w.add_txn(ts, uacct, vacct, amt, "ach", f"CONSULTING SERVICES INV {rng.randint(100, 999)}")
        total += amt
        w.add_txn(ts + timedelta(days=rng.uniform(1, 4)), vacct, hacct, money(amt * rng.uniform(0.9, 0.95)),
                  "international_wire", "ADVISORY RETAINER")
    # decoy vendors: also new LLCs paid by the company, but real operating businesses
    regular = [b for b in w.businesses if b["_infra"] is None and b.get("_size", 0) > 0 and b["business_id"] != u]
    decoy_vendors = []
    for _ in range(2):
        owner = w.pick(_adult)[0]["person_id"]
        city, st = w.random_city()
        dv = w.new_business(f"{rng.choice(N.BUSINESS_WORDS)} {rng.choice(['IT Services', 'Facilities', 'Staffing'])} LLC",
                            "LLC", "consulting", st, w.new_address(city, st, "commercial"),
                            w.r_date(date(2023, 6, 1), date(2024, 8, 1)), city=city, _size=3)
        w.reserve(dv["business_id"])
        w.add_owner(owner, dv["business_id"], "member", 100.0, date.fromisoformat(dv["incorporation_date"]))
        dacct = w.new_account(dv["business_id"], w._pick_bank(), "US", "business_checking",
                              date.fromisoformat(dv["incorporation_date"]) + timedelta(days=10), signer=owner)
        inflow = 0.0
        for k in range(rng.randint(5, 9)):
            payer = uacct if k % 2 == 0 else w.checking_of(rng.choice(regular)["business_id"])
            amt = odd_amount(w, 6_000, 28_000)
            w.add_txn(w.r_daytime(w.r_date(date(YEAR, 1, 10), date(YEAR, 12, 15))), payer, dacct, amt, "ach",
                      f"INV {rng.randint(1000, 9999)}")
            inflow += amt
        staff = w.pick(lambda p: adult_customer(p, 22, 60) and bool(w.return_of.get(p["tin"])), 2)
        for s in staff:
            for m in range(1, 13):
                w.add_txn(w.r_daytime(date(YEAR, m, 28), 5, 7), dacct, w.checking_of(s["person_id"]),
                          inflow * 0.25 / 12, "ach", "PAYROLL")
            add_wages(w, s["person_id"], dv["business_id"], money(inflow * 0.25 / 0.8))
        rid = w.ids.new("RT", 8)
        row = {"return_id": rid, "tax_year": YEAR, "form_type": "1065", "filer_tin": dv["ein"], "spouse_tin": None,
               "filing_status": "entity", "preparer_id": None, "wages": 0.0, "interest": 0.0, "dividends": 0.0,
               "business_gross_receipts": money(inflow * 1.01), "business_expenses": money(inflow * 0.88),
               "other_income": 0.0, "total_income": money(inflow * 0.13), "deductions": 0.0, "credits": 0.0,
               "tax_liability": 0.0, "withholding": 0.0, "refund_amount": 0.0, "balance_due": 0.0,
               "refund_account_id": None, "num_dependents": 0, "filed_date": d2s(date(YEAR + 1, 3, 12)),
               "_entity": dv["business_id"]}
        w.tables["tax_returns"].append(row)
        w.returns_by_id[rid] = row
        w.return_of[dv["ein"]] = rid
        decoy_vendors.append(dv["business_id"])
    remove_insider_conflicts(w, u, keep=[v])
    ub = w.B[u]
    prompt = f"""
Internal audit at {ub['name']} ({u}) suspects that one of the company's vendors is a vehicle for diverting \
company funds to a company insider.

Review the company's 2025 outgoing payments together with the corporate registry and related records.

Report:
- vendor_id: business_id of the vendor used to divert funds.
- insider_person_id: person_id of the {ub['name']} officer or employee involved.
- beneficial_owner_person_id: person_id of the natural person who ultimately owns the vendor.
- total_paid: total amount {ub['name']} paid that vendor in 2025 (USD).
"""
    fields = [
        field("vendor_id", "id", 0.25, "business_id of the vendor."),
        field("insider_person_id", "id", 0.35, "person_id of the insider."),
        field("beneficial_owner_person_id", "id", 0.2, "person_id of the vendor's ultimate owner."),
        field("total_paid", "number", 0.2, "USD.", rel_tol=0.01, zero_at=0.25),
    ]
    add_task(w, task_id, "ownership", "kickback", difficulty, "Insider vendor kickback", prompt, fields,
             {"vendor_id": v, "insider_person_id": insider, "beneficial_owner_person_id": bo,
              "total_paid": money(total)},
             inputs={"business_id": u},
             decoys={"vendor_id": decoy_vendors, "beneficial_owner_person_id": w.nominees},
             notes=f"variant={variant}: vendor <- offshore holding <- {'insider' if variant == 'direct' else 'insider spouse'}")

