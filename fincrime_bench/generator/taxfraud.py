"""Tax-evasion and refund-fraud schemes planted into the tax / registry data."""

from __future__ import annotations

from datetime import date, timedelta

from . import names as N
from .common import (add_task, adult_customer, field, new_personal_account, odd_amount, offshore_shell,
                     us_shell)
from .tax import recompute
from .world import YEAR, World, d2s, money


def _income_lines(r: dict) -> float:
    return r["wages"] + r["interest"] + r["dividends"] + r["business_gross_receipts"] + r["other_income"]


def _drop_return(w: World, r: dict) -> None:
    w.tables["tax_returns"].remove(r)
    del w.returns_by_id[r["return_id"]]
    for tin in (r["filer_tin"], r["spouse_tin"]):
        if tin and w.return_of.get(tin) == r["return_id"]:
            del w.return_of[tin]
    w.tables["return_dependents"] = [d for d in w.tables["return_dependents"] if d["return_id"] != r["return_id"]]


def _info_by_tin(w: World) -> dict[str, float]:
    tot: dict[str, float] = {}
    for i in w.tables["info_returns"]:
        tot[i["payee_tin"]] = tot.get(i["payee_tin"], 0.0) + i["amount"]
    return tot


def _single_returns(w: World) -> list[dict]:
    return [r for r in w.tables["tax_returns"] if r["form_type"] == "1040" and r["spouse_tin"] is None
            and r["_primary"] not in w.reserved and adult_customer(w.P[r["_primary"]], 23, 66)]


# ======================================================= UNREPORTED INCOME
def plant_unreported(w: World, task_id: str) -> None:
    rng = w.rng
    singles = _single_returns(w)
    used: list[str] = []

    def take(cond) -> dict:
        opts = [r for r in singles if r["return_id"] not in used and cond(r)]
        r = rng.choice(opts)
        used.append(r["return_id"])
        w.reserve(r["_primary"])
        return r

    regular = [b for b in w.businesses if b["_infra"] is None and b.get("_size", 0) > 0]
    # (a) self-employed omits a 1099-NEC (and the cash it was paid alongside)
    r = take(lambda r: any(a >= 12_000 for _b, a in w.P[r["_primary"]]["_nec"]))
    p = w.P[r["_primary"]]
    nec = max(a for _b, a in p["_nec"])
    r["business_gross_receipts"] = money(r["business_gross_receipts"] - nec - p["_extra_se"])
    r["business_expenses"] = money(min(r["business_expenses"], r["business_gross_receipts"] * 0.3))
    recompute(w, r)
    # (b) second W-2 job never reported
    r = take(lambda r: r["wages"] > 20_000)
    p = w.P[r["_primary"]]
    emp2 = rng.choice([b for b in regular if b["business_id"] != p["employer_id"]])
    gross2 = odd_amount(w, 14_000, 38_000)
    w.add_info_return("W-2", emp2["ein"], p["tin"], gross2)
    acct = w.checking_of(p["person_id"])
    for m in range(1, 13):
        w.add_txn(w.r_daytime(date(YEAR, m, 27), 5, 7), w.checking_of(emp2["business_id"]), acct,
                  gross2 * 0.82 / 12, "ach", "PAYROLL")
    # (c) gig-platform payments (1099-K) never reported
    r = take(lambda r: True)
    p = w.P[r["_primary"]]
    gig = odd_amount(w, 18_000, 52_000)
    proc = w.infra["processor"][0]
    w.add_info_return("1099-K", w.B[proc]["ein"], p["tin"], gig)
    for wk in range(0, 52, 2):
        w.add_txn(w.r_daytime(date(YEAR, 1, 6) + timedelta(weeks=wk), 3, 5), w.checking_of(proc),
                  w.checking_of(p["person_id"]), gig / 26, "ach", "PAYFLOW PAYOUT")
    # (d) wages under-reported on the return
    r = take(lambda r: r["wages"] > 45_000)
    r["wages"] = money(r["wages"] - odd_amount(w, 12_500, 29_000))
    recompute(w, r)
    # (e, f) non-filers with substantial third-party income
    info = _info_by_tin(w)
    for _ in range(2):
        r = take(lambda r: r["num_dependents"] == 0 and info.get(w.P[r["_primary"]]["tin"], 0) > 40_000)
        _drop_return(w, r)
    # ---- decoys: line misclassification (total unchanged) and small omissions (< $10k)
    decoys = []
    for _ in range(3):
        r = take(lambda r: r["business_gross_receipts"] > 15_000 or r["wages"] > 30_000)
        if r["business_gross_receipts"] > 15_000:
            moved = money(r["business_gross_receipts"] * rng.uniform(0.3, 0.7))
            r["business_gross_receipts"] = money(r["business_gross_receipts"] - moved)
        else:
            moved = money(r["wages"] * rng.uniform(0.1, 0.3))
            r["wages"] = money(r["wages"] - moved)
        r["other_income"] = money(r["other_income"] + moved)
        recompute(w, r)
        decoys.append(w.P[r["_primary"]]["tin"])
    for _ in range(3):
        r = take(lambda r: True)
        p = w.P[r["_primary"]]
        amt = odd_amount(w, 600, 2_900)
        w.add_info_return(rng.choice(["1099-DIV", "1099-INT", "1099-MISC"]), w.B[rng.choice(w.infra["broker"])]["ein"],
                          p["tin"], amt)
        decoys.append(p["tin"])
    # joint-return spouses with their own W-2s look like non-filers to a naive per-TIN join
    joint = [r for r in w.tables["tax_returns"] if r["filing_status"] == "married_joint"]
    for r in joint:
        sp = w.P[r["_secondary"]]
        if sum(g for _b, g, _wh in sp["_w2"]) > 25_000 and len(decoys) < 12:
            decoys.append(sp["tin"])

    def gold() -> dict:
        return {"taxpayers": unreported_rule(w)}

    prompt = """
Compare the income each individual reported on their 2025 return against the income third parties reported \
about them on information returns (W-2, 1099-NEC, 1099-K, 1099-INT, 1099-DIV, 1099-MISC).

Identify every individual taxpayer (TIN) whose income reported to the IRS — on their return, or not at all if \
they did not file — falls short of the third-party-reported income by at least $10,000, and estimate the \
understated amount (third-party total minus the income they reported). Taxpayers sometimes report income on a \
different line of the return than the information return would suggest; that alone is not under-reporting.

Report taxpayers: a list of {"id": <TIN>, "value": <understated amount in USD>}.
"""
    fields = [field("taxpayers", "id_number_map", 1.0,
                    "List of {id: TIN, value: understated USD}.", value_weight=0.4, rel_tol=0.02, zero_at=0.25)]
    add_task(w, task_id, "tax", "unreported_income", "medium", "Information-return matching", prompt, fields, None,
             decoys={"taxpayers": decoys}, deferred=gold,
             notes="Gold: sum(info returns) - (wages+interest+dividends+gross receipts+other) >= 10000, "
                   "joint returns combine spouses; non-filers use 0.")


def unreported_rule(w: World) -> list[dict]:
    info = _info_by_tin(w)
    out = []
    seen_returns: list[str] = []
    for tin, total in info.items():
        if tin not in w.TIN or not w.TIN[tin].startswith("P"):
            continue
        rid = w.return_of.get(tin)
        if rid is None:
            short = total
            key = tin
        else:
            if rid in seen_returns:
                continue
            r = w.returns_by_id[rid]
            seen_returns.append(rid)
            combined = info.get(r["filer_tin"], 0.0) + (info.get(r["spouse_tin"], 0.0) if r["spouse_tin"] else 0.0)
            short = combined - _income_lines(r)
            key = r["filer_tin"]
        if short >= 10_000:
            out.append({"id": key, "value": money(short)})
    return sorted(out, key=lambda x: x["id"])


# ================================================================ LIFESTYLE
def plant_lifestyle(w: World, pop_task_id: str, est_task_id: str) -> None:
    rng = w.rng
    owned_2025 = {a["owner_id"] for a in w.tables["assets"]
                  if (a["purchase_date"] or "") >= "2025" or (a["sale_date"] or "") >= "2025"}
    singles = [r for r in _single_returns(w) if 25_000 <= r["total_income"] <= 75_000
               and r["_primary"] not in owned_2025]
    chosen = rng.sample(singles, 4)
    titles = w.infra["title"]
    for i, r in enumerate(chosen):
        pid = r["_primary"]
        w.reserve(pid)
        p = w.P[pid]
        acct = w.checking_of(pid)
        if i == 0:
            price = odd_amount(w, 900_000, 1_300_000)
            day = w.r_date(date(2025, 4, 1), date(2025, 10, 1))
            _sid, off = offshore_shell(w, [], cc=rng.choice(["PA", "AE", "SC"]))
            for k in range(3):
                w.add_txn(w.r_daytime(day - timedelta(days=60 - 20 * k)), off, acct, money(price / 3 * 1.02),
                          "international_wire", "LOAN AGREEMENT")
            w.add_asset("real_estate", "Single-family residence", pid, day, price, "cash",
                        address_id=w.new_address(p["_city"], p["_state"]))
            w.add_txn(w.r_daytime(day), acct, w.checking_of(rng.choice(titles)), price, "wire", "CLOSING FUNDS")
        elif i == 1:
            total = 0.0
            for k in range(2):
                price = odd_amount(w, 110_000, 175_000)
                day = w.r_date(date(2025, 2, 1), date(2025, 11, 1))
                w.add_asset("vehicle", w._vehicle_desc(price), pid, day, price, "cash")
                w.add_txn(w.r_daytime(day), acct, w.checking_of(rng.choice(w.infra["auto_dealer"])), price, "wire",
                          "VEHICLE PURCHASE")
                total += price
            price = odd_amount(w, 150_000, 240_000)
            day = w.r_date(date(2025, 4, 1), date(2025, 8, 1))
            w.add_asset("boat", f"{rng.randint(34, 48)}ft Sea Ray Sundancer", pid, day, price, "cash")
            w.add_txn(w.r_daytime(day), acct, w.checking_of(w.infra["marine_dealer"][0]), price, "wire", "VESSEL PURCHASE")
            total += price
            for _ in range(rng.randint(10, 14)):
                d = w.r_date(date(2025, 1, 10), date(2025, 10, 30))
                w.add_txn(w.r_daytime(d, 9, 17), None, acct, money(rng.uniform(14_000, 36_000)), "cash_deposit", "",
                          branch=w.branch_for(acct, p["_city"]), conducted_by=pid)
        elif i == 2:
            price = odd_amount(w, 650_000, 900_000)
            day = w.r_date(date(2025, 3, 1), date(2025, 11, 1))
            w.add_asset("real_estate", "Condominium", pid, day, price, "cash",
                        address_id=w.new_address(p["_city"], p["_state"]))
        else:
            price = odd_amount(w, 1_200_000, 1_600_000)
            loan = money(price * 0.4)
            day = w.r_date(date(2025, 3, 1), date(2025, 11, 1))
            llc, llc_acct = us_shell(w, [(pid, 100.0)], pid, date(2023, rng.randint(1, 12), rng.randint(1, 28)),
                                     industry="consulting")
            for k in range(4):
                w.add_txn(w.r_daytime(day - timedelta(days=90 - 25 * k)), llc_acct, acct, money((price - loan) / 4),
                          "ach", "DISTRIBUTION")
            w.add_asset("real_estate", "Single-family residence", pid, day, price, "mortgage", loan,
                        address_id=w.new_address(p["_city"], p["_state"]))
            w.add_txn(w.r_daytime(day), acct, w.checking_of(rng.choice(titles)), price - loan, "wire", "CLOSING FUNDS")

    # ---- estimate subject: joint filer with several asset events
    joints = [r for r in w.tables["tax_returns"] if r["filing_status"] == "married_joint"
              and 60_000 <= r["total_income"] <= 140_000 and r["_primary"] not in w.reserved
              and r["_secondary"] not in w.reserved and r["_primary"] not in owned_2025
              and r["_secondary"] not in owned_2025 and adult_customer(w.P[r["_primary"]], 30, 64)]
    r5 = rng.choice(joints)
    s = r5["_primary"] if rng.random() < 0.5 else r5["_secondary"]
    w.reserve(r5["_primary"], r5["_secondary"])
    p5 = w.P[s]
    house = odd_amount(w, 720_000, 940_000)
    car = odd_amount(w, 58_000, 72_000)
    loan = money(car * 0.7)
    old_car = odd_amount(w, 14_000, 21_000)
    a_house = w.add_asset("real_estate", "Single-family residence", s, date(2025, rng.randint(5, 9), rng.randint(1, 28)),
                          house, "cash", address_id=w.new_address(p5["_city"], p5["_state"]))
    a_car = w.add_asset("vehicle", w._vehicle_desc(car), s, date(2025, rng.randint(2, 4), rng.randint(1, 28)), car,
                        "loan", loan)
    w.add_asset("vehicle", w._vehicle_desc(old_car * 1.8), s, date(2019, rng.randint(1, 12), rng.randint(1, 28)),
                old_car * 1.8, "loan", old_car * 1.2, sale_date=date(2025, rng.randint(2, 4), rng.randint(1, 28)),
                sale_price=old_car)
    unexplained = money(house + (car - loan) - r5["total_income"] - money(old_car))

    # ---- decoys
    decoys = []
    rich = sorted([r for r in w.tables["tax_returns"] if r["form_type"] == "1040" and r["_primary"] not in w.reserved
                   and r["_primary"] not in owned_2025], key=lambda r: -r["total_income"])[0]
    w.reserve(rich["_primary"])
    pr = w.P[rich["_primary"]]
    w.add_asset("real_estate", "Single-family residence", pr["person_id"], w.r_date(date(2025, 2, 1), date(2025, 10, 1)),
                money(rich["total_income"] * rng.uniform(0.9, 1.15)), "cash",
                address_id=w.new_address(pr["_city"], pr["_state"]))
    decoys.append(pr["person_id"])
    mod = rng.choice([r for r in singles if r not in chosen and r["_primary"] not in w.reserved])
    w.reserve(mod["_primary"])
    pm = w.P[mod["_primary"]]
    price = money(mod["total_income"] * rng.uniform(8, 10))
    w.add_asset("real_estate", "Single-family residence", pm["person_id"], w.r_date(date(2025, 2, 1), date(2025, 10, 1)),
                price, "mortgage", money(price * 0.9), address_id=w.new_address(pm["_city"], pm["_state"]))
    decoys.append(pm["person_id"])
    decoys.extend(p["person_id"] for p in w.persons if p.get("_home_sale"))

    def gold() -> dict:
        return {"person_ids": lifestyle_rule(w)}

    prompt = """
Use the expenditure approach (source and application of funds) to find individuals whose 2025 asset \
acquisitions cannot be explained by their reported income and documented sources of funds.

For each individual, compare the cash they put into assets acquired in 2025 (purchase price less any financed \
amount) against their documented sources: income reported on their 2025 tax return (a joint return counts \
for both spouses), and proceeds from assets they sold in 2025. Flag every individual whose unexplained \
expenditure exceeds $250,000.

Report person_ids: the person_id of every flagged individual.
"""
    add_task(w, pop_task_id, "tax", "lifestyle", "medium", "Expenditures exceeding reported income", prompt,
             [field("person_ids", "id_set", 1.0, "person_ids of flagged individuals.")], None,
             decoys={"person_ids": decoys}, deferred=gold,
             notes="Gold: sum(price-loan) for 2025 purchases - return total_income - 2025 sale proceeds > 250k.")

    prompt2 = f"""
{w.full_name(s)} ({s}) has been selected for a net-worth audit.

Compute the taxpayer's unexplained expenditure for 2025 as:
  (sum over assets the taxpayer acquired in 2025 of purchase price minus financed amount)
  − (total_income on the 2025 tax return on which the taxpayer is the filer or the spouse)
  − (sale proceeds of assets the taxpayer sold in 2025).

Report:
- assets_acquired: asset_ids of all assets the taxpayer acquired in 2025.
- unexplained_amount: the unexplained expenditure in USD.
"""
    fields2 = [
        field("assets_acquired", "id_set", 0.3, "asset_ids acquired in 2025."),
        field("unexplained_amount", "number", 0.7, "USD.", rel_tol=0.01, zero_at=0.2),
    ]
    add_task(w, est_task_id, "tax", "net_worth", "medium", "Net-worth audit computation", prompt2, fields2,
             {"assets_acquired": [a_house, a_car], "unexplained_amount": unexplained}, inputs={"person_id": s},
             notes="Joint return income counts; the old car sale is a source of funds; the new car is partly financed.")


def lifestyle_rule(w: World) -> list[str]:
    outlay: dict[str, float] = {}
    sales: dict[str, float] = {}
    for a in w.tables["assets"]:
        o = a["owner_id"]
        if not o.startswith("P"):
            continue
        if a["purchase_date"] and a["purchase_date"].startswith(str(YEAR)):
            outlay[o] = outlay.get(o, 0.0) + a["purchase_price"] - (a["loan_amount"] or 0.0)
        if a["sale_date"] and a["sale_date"].startswith(str(YEAR)):
            sales[o] = sales.get(o, 0.0) + (a["sale_price"] or 0.0)
    out = []
    for pid, spent in outlay.items():
        tin = w.P[pid]["tin"]
        rid = w.return_of.get(tin) if tin else None
        income = w.returns_by_id[rid]["total_income"] if rid else 0.0
        if spent - income - sales.get(pid, 0.0) > 250_000:
            out.append(pid)
    return sorted(out)


# ================================================================ PREPARER
def plant_preparer(w: World, task_id: str) -> None:
    rng = w.rng
    preps = [x for x in w.tables["preparers"] if x["person_id"] not in w.reserved and w.checking_of(x["person_id"])]
    fp, dp = rng.sample(preps, 2)
    fpp = w.P[fp["person_id"]]
    w.reserve(fp["person_id"], dp["person_id"])
    rel = [r for r in fpp["_children"] + fpp["_parents"] + [fpp["_spouse"]] if r and r not in w.reserved
           and w.P[r]["_age"] >= 18]
    relative = rel[0] if rel else w.pick(lambda p: adult_customer(p, 21, 80))[0]["person_id"]
    w.reserve(relative)
    rel_acct = new_personal_account(w, relative, date(2024, rng.randint(6, 11), rng.randint(1, 28)),
                                    signer=fp["person_id"])
    firm_name = f"{fpp['last_name']} Tax Pros LLC"
    firm = w.new_business(firm_name, "LLC", "accounting", fpp["_state"],
                          w.new_address(fpp["_city"], fpp["_state"], "commercial"), date(2023, 9, 1),
                          _files_return=False)
    w.reserve(firm["business_id"])
    w.add_owner(fp["person_id"], firm["business_id"], "member", 100.0, date(2023, 9, 1))
    firm_acct = w.new_account(firm["business_id"], w._pick_bank(), "US", "business_checking", date(2023, 9, 20),
                              signer=fp["person_id"])
    controlled = [w.checking_of(fp["person_id"]), rel_acct, firm_acct]
    ind = [r for r in w.tables["tax_returns"] if r["form_type"] == "1040" and r["_primary"] not in w.reserved
           and (r["_secondary"] is None or r["_secondary"] not in w.reserved)]
    clients = rng.sample(ind, 19)
    diverted, diverted_accts = [], []
    for i, r in enumerate(clients):
        w.reserve(r["_primary"])
        if r["_secondary"]:
            w.reserve(r["_secondary"])
        r["preparer_id"] = fp["preparer_id"]
        r["credits"] = money((r["credits"] or 0) + rng.uniform(2_500, 6_500))
        r["deductions"] = money((r["deductions"] or 0) + rng.uniform(6_000, 18_000))
        recompute(w, r)
        if i < 12:
            if r["refund_amount"] <= 0:
                r["withholding"] = money(r["withholding"] + rng.uniform(1_500, 4_000))
                recompute(w, r)
            acct = controlled[i % 3]
            r["refund_account_id"] = acct
            diverted.append(r["return_id"])
            if acct not in diverted_accts:
                diverted_accts.append(acct)
        elif r["refund_amount"] > 0 and not r["refund_account_id"]:
            r["refund_account_id"] = w.checking_of(r["_primary"])
    # decoy preparer: legitimately serves low-income families with large refundable credits
    low = [r for r in ind if r not in clients and r["num_dependents"] >= 2 and r["total_income"] < 55_000
           and r["refund_amount"] > 0]
    for r in rng.sample(low, min(15, len(low))):
        r["preparer_id"] = dp["preparer_id"]

    prompt = """
The IRS suspects that a paid tax return preparer is running a refund-fraud scheme in the 2025 filing season: \
inflating clients' deductions and credits and diverting refunds into accounts the preparer controls.

Identify the preparer, the returns whose refunds were diverted away from the taxpayers, and the bank accounts \
that received the diverted refunds.

Report:
- preparer_id: the preparer_id of the fraudulent preparer.
- diverted_return_ids: return_ids whose refunds were directed to accounts not belonging to the taxpayer(s) on \
the return.
- refund_accounts: account_ids that received the diverted refunds.
"""
    fields = [
        field("preparer_id", "id", 0.35, "preparer_id."),
        field("diverted_return_ids", "id_set", 0.4, "return_ids with diverted refunds."),
        field("refund_accounts", "id_set", 0.25, "account_ids receiving diverted refunds."),
    ]
    add_task(w, task_id, "tax", "preparer_fraud", "hard", "Refund-mill preparer", prompt, fields,
             {"preparer_id": fp["preparer_id"], "diverted_return_ids": diverted, "refund_accounts": diverted_accts},
             decoys={"preparer_id": [dp["preparer_id"]]},
             notes="Diverted to: preparer's own account, a relative's account (preparer is signer), preparer's LLC.")


# =============================================================== DEPENDENTS
def plant_dependents(w: World, task_id: str) -> None:
    rng = w.rng
    deps = w.tables["return_dependents"]
    by_return: dict[str, list[str]] = {}
    for d in deps:
        by_return.setdefault(d["return_id"], []).append(d["dependent_person_id"])
    ind = [r for r in w.tables["tax_returns"] if r["form_type"] == "1040" and r["_primary"] not in w.reserved]

    def bump(r: dict, dep_pid: str) -> None:
        deps.append({"return_id": r["return_id"], "dependent_person_id": dep_pid})
        r["num_dependents"] += 1
        r["credits"] = None
        recompute(w, r)

    # duplicate claims: a child already claimed by a parent is also claimed by a grandparent or unrelated filer
    with_kids = [r for r in ind if any(w.P[k]["_age"] < 17 for k in by_return.get(r["return_id"], []))]
    for r1 in rng.sample(with_kids, 3):
        kid = [k for k in by_return[r1["return_id"]] if w.P[k]["_age"] < 17][0]
        parent = w.P[r1["_primary"]]
        gps = [g for g in parent["_parents"] if w.return_of.get(w.P[g]["tin"])]
        r2 = None
        if gps:
            r2 = w.returns_by_id[w.return_of[w.P[gps[0]]["tin"]]]
            if r2["_primary"] in w.reserved or r2 is r1:
                r2 = None
        if r2 is None:
            r2 = rng.choice([x for x in ind if x is not r1 and x["_primary"] not in w.reserved])
        w.reserve(r1["_primary"], r2["_primary"])
        bump(r2, kid)
    # deceased dependents (died before 2025) and one valid claim for a parent who died during 2025
    for k, (died, age) in enumerate([(date(2023, rng.randint(1, 12), rng.randint(1, 28)), rng.randint(70, 90)),
                                      (date(2024, rng.randint(1, 12), rng.randint(1, 28)), rng.randint(6, 15)),
                                      (date(2025, rng.randint(3, 8), rng.randint(1, 28)), rng.randint(75, 92))]):
        r = rng.choice([x for x in ind if x["_primary"] not in w.reserved and x["filing_status"] != "married_separate"])
        filer = w.P[r["_primary"]]
        w.reserve(filer["person_id"])
        last = filer["last_name"]
        d = w.new_person(rng.choice(N.FIRST_NAMES), last, age + (YEAR - died.year), filer["address_id"],
                         filer["_city"], filer["_state"], occupation="Retired" if age > 60 else "Student")
        d["date_of_death"] = d2s(died)
        w.reserve(d["person_id"])
        if age > 60:
            w.add_rel(d["person_id"], filer["person_id"], "parent")
        else:
            w.add_rel(filer["person_id"], d["person_id"], "parent")
        bump(r, d["person_id"])
        if k == 2:
            valid_decoy = r["return_id"]

    def gold() -> dict:
        return {"return_ids": dependents_rule(w)}

    prompt = """
Review dependents claimed on 2025 individual returns. A dependent may be claimed on only one return per year, \
and a person who died before the tax year cannot be claimed (a person who died during 2025 can still be claimed \
for 2025).

Identify every 2025 return that claims a dependent who is also claimed on another 2025 return, or who died \
before 2025.

Report return_ids: all such return_ids (include every return involved in a duplicate claim).
"""
    add_task(w, task_id, "tax", "dependents", "easy", "Duplicate and deceased dependents", prompt,
             [field("return_ids", "id_set", 1.0, "return_ids with invalid dependent claims.")], None,
             decoys={"return_ids": [valid_decoy]}, deferred=gold)


def dependents_rule(w: World) -> list[str]:
    claims: dict[str, list[str]] = {}
    for d in w.tables["return_dependents"]:
        claims.setdefault(d["dependent_person_id"], []).append(d["return_id"])
    out: list[str] = []
    for pid, rids in claims.items():
        dod = w.P[pid]["date_of_death"]
        if len(rids) > 1 or (dod and dod < f"{YEAR}-01-01"):
            out.extend(r for r in rids if r not in out)
    return sorted(out)


# ================================================================ SKIMMING
def plant_skimming(w: World, task_id: str) -> None:
    rng = w.rng
    cands = [b for b in w.businesses if b["_ind"] in N.CASH_INTENSIVE and b["business_id"] not in w.reserved
             and w.return_of.get(b["ein"]) and w.biz_inflow_at_filing.get(b["business_id"], 0) > 150_000
             and any(o.startswith("P") for o, _ in b["_owners"])]
    rng.shuffle(cands)
    high_cash = [b for b in cands if b["_ind"] != "restaurant"]
    positives = high_cash[:2] + [b for b in cands if b["_ind"] == "restaurant"][:1]
    if len(positives) < 3:
        positives = cands[:3]
    for b in positives:
        w.reserve(b["business_id"])
        r = w.returns_by_id[w.return_of[b["ein"]]]
        dep = w.biz_inflow_at_filing[b["business_id"]]
        card = b.get("_card_total", 0.0)
        reported = max(card * rng.uniform(1.0, 1.03), dep * rng.uniform(0.42, 0.62))
        r["business_gross_receipts"] = money(reported)
        r["business_expenses"] = money(reported * rng.uniform(0.85, 0.97))
        r["total_income"] = money(r["business_gross_receipts"] - r["business_expenses"])
        owner = [o for o, _ in b["_owners"] if o.startswith("P")][0]
        oacct = w.checking_of(owner)
        if oacct:
            for _ in range(rng.randint(3, 6)):
                d = w.r_date(date(2025, 1, 10), date(2025, 12, 15))
                w.add_txn(w.r_daytime(d, 9, 17), None, oacct, money(rng.uniform(2_000, 4_800)), "cash_deposit", "",
                          branch=w.branch_for(oacct, w.P[owner]["_city"]), conducted_by=owner)
    others = [b for b in cands if b not in positives]
    loan_b, cap_b = others[0], others[1]
    w.reserve(loan_b["business_id"], cap_b["business_id"])
    w.add_txn(w.r_daytime(w.r_date(date(2025, 3, 1), date(2025, 9, 1))),
              w.checking_of(w.infra["sba_lender"][0]), w.checking_of(loan_b["business_id"]),
              odd_amount(w, 150_000, 300_000), "ach", "SBA 7A LOAN DISBURSEMENT")
    owner = [o for o, _ in cap_b["_owners"] if o.startswith("P")][0]
    src = w.checking_of(owner) or new_personal_account(w, owner, date(2015, 1, 1))
    for _ in range(2):
        w.add_txn(w.r_daytime(w.r_date(date(2025, 2, 1), date(2025, 10, 1))), src, w.checking_of(cap_b["business_id"]),
                  odd_amount(w, 40_000, 90_000), "wire", "CAPITAL CONTRIBUTION")

    def gold() -> dict:
        return {"businesses": skimming_rule(w)}

    prompt = """
Cash-intensive businesses (restaurants, salons, laundromats and car washes) are at high risk of skimming — \
keeping cash receipts off the books.

Using the bank deposits method, identify every cash-intensive business whose gross receipts on its 2025 return \
are understated by more than 20% relative to the business revenue deposited into its bank accounts, and \
estimate the understated receipts (deposited revenue minus reported gross receipts). Deposits that are not \
revenue — loan proceeds, owner capital contributions, transfers between the business's own accounts — must be \
excluded.

Report businesses: a list of {"id": <business_id>, "value": <understated receipts in USD>}.
"""
    fields = [field("businesses", "id_number_map", 1.0, "List of {id: business_id, value: understated USD}.",
                    value_weight=0.4, rel_tol=0.05, zero_at=0.5)]
    add_task(w, task_id, "tax", "skimming", "hard", "Bank-deposits analysis of cash businesses", prompt, fields, None,
             decoys={"businesses": [loan_b["business_id"], cap_b["business_id"]]}, deferred=gold,
             notes="Gold: revenue deposits (excluding loans and owner contributions) vs return gross receipts.")


def skimming_rule(w: World) -> list[dict]:
    acct_holder = {a["account_id"]: a["holder_id"] for a in w.tables["accounts"]}
    owners: dict[str, list[str]] = {}
    for o in w.tables["ownership"]:
        owners.setdefault(o["owned_id"], []).append(o["owner_id"])
    out = []
    for b in w.businesses:
        if b["industry"] not in N.CASH_INTENSIVE:
            continue
        rid = w.return_of.get(b["ein"])
        if not rid:
            continue
        bid = b["business_id"]
        rev = 0.0
        for t in w.tables["transactions"]:
            if t["to_account"] and acct_holder[t["to_account"]] == bid:
                memo = (t["memo"] or "").upper()
                src_holder = acct_holder.get(t["from_account"]) if t["from_account"] else None
                if "LOAN" in memo or "CAPITAL" in memo or src_holder == bid or src_holder in owners.get(bid, []):
                    continue
                rev += t["amount"]
        reported = w.returns_by_id[rid]["business_gross_receipts"]
        if rev > 0 and reported < 0.8 * rev:
            out.append({"id": bid, "value": money(rev - reported)})
    return sorted(out, key=lambda x: x["id"])
