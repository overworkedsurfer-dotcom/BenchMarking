"""Background (honest) tax data: preparers, information returns, returns."""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from .world import YEAR, World, d2s, money

STD_DEDUCTION = {"single": 15_000, "married_joint": 30_000, "married_separate": 15_000, "head_of_household": 22_500}
BRACKETS_SINGLE = [(11_925, 0.10), (48_475, 0.12), (103_350, 0.22), (197_300, 0.24), (250_525, 0.32),
                   (626_350, 0.35), (float("inf"), 0.37)]


def tax_on(taxable: float, status: str) -> float:
    mult = 2.0 if status == "married_joint" else (1.5 if status == "head_of_household" else 1.0)
    tax, prev = 0.0, 0.0
    for top, rate in BRACKETS_SINGLE:
        top *= mult
        if taxable <= prev:
            break
        tax += (min(taxable, top) - prev) * rate
        prev = top
    return tax


def compute_return(row: dict, n_kids_under17: int, earned: float) -> dict:
    """Fill derived fields of an individual return in place (simplified 2025 rules)."""
    total = (row["wages"] + row["interest"] + row["dividends"] + row["business_gross_receipts"]
             - row["business_expenses"] + row["other_income"])
    row["total_income"] = money(total)
    status = row["filing_status"]
    std = STD_DEDUCTION.get(status, 15_000)
    if row.get("deductions") is None:
        row["deductions"] = money(max(std, total * 0.19) if total > 250_000 else std)
    taxable = max(0.0, total - row["deductions"])
    tax = tax_on(taxable, status)
    if row.get("credits") is None:
        ctc = 2_000 * n_kids_under17
        eitc = 0.0
        if n_kids_under17 and earned < 50_000:
            eitc = min(n_kids_under17, 3) * 1_350 * (1 - max(0.0, earned - 25_000) / 25_000)
        elif not n_kids_under17 and 4_000 < earned < 18_000:
            eitc = 600
        row["credits"] = money(ctc + max(0.0, eitc))
    row["tax_liability"] = money(tax - row["credits"])
    diff = row["withholding"] - row["tax_liability"]
    row["refund_amount"] = money(max(0.0, diff))
    row["balance_due"] = money(max(0.0, -diff))
    return row


def build_tax_background(w: World) -> None:
    rng = w.rng
    T = w.tables

    # ---------------------------------------------------------------- preparers
    cands = [p for p in w.persons if p["occupation"] in ("Tax Preparer", "Accountant", "Bookkeeper")
             and p["_age"] >= 25]
    n_prep = max(12, int(w.n_persons / 120))
    chosen = cands[:]
    rng.shuffle(chosen)
    chosen = chosen[:n_prep]
    extra = [p for p in w.persons if p["_status"] == "self" and p["_age"] >= 30 and p not in chosen]
    while len(chosen) < n_prep and extra:
        p = extra.pop(rng.randrange(len(extra)))
        p["occupation"] = "Tax Preparer"
        chosen.append(p)
    w.preparer_weight = {}
    for p in chosen:
        pr_id = w.ids.new("PTN", 6)
        firm = p["employer_id"] if p["employer_id"] and w.B[p["employer_id"]]["_ind"] == "accounting" else None
        T["preparers"].append({"preparer_id": pr_id, "person_id": p["person_id"], "firm_business_id": firm})
        w.preparer_ids.append(pr_id)
        w.preparer_weight[pr_id] = rng.uniform(0.4, 3.0)
        p["_preparer_id"] = pr_id

    # ----------------------------------------------------------- info returns
    def info(form: str, payer_tin: str, payee_tin: str, amount: float) -> str:
        iid = w.ids.new("IR", 8)
        T["info_returns"].append({"info_return_id": iid, "tax_year": YEAR, "form_type": form, "payer_tin": payer_tin,
                                  "payee_tin": payee_tin, "amount": money(amount)})
        return iid

    w.add_info_return = info
    for p in w.persons:
        for bid, gross, _wh in p["_w2"]:
            info("W-2", w.B[bid]["ein"], p["tin"], gross)
        for bid, amt in p["_nec"]:
            info("1099-NEC", w.B[bid]["ein"], p["tin"], amt)
        for bid, amt in p["_int"]:
            info("1099-INT", w.B[bid]["ein"], p["tin"], amt)
        for bid, amt in p["_div"]:
            info("1099-DIV", w.B[bid]["ein"], p["tin"], amt)
    proc_ein = w.B[w.infra["processor"][0]]["ein"]
    for p in w.persons:
        if p.get("_k1099"):
            info("1099-K", proc_ein, p["tin"], p["_k1099"])
    for b in w.businesses:
        if b.get("_card_total"):
            info("1099-K", proc_ein, b["ein"], b["_card_total"])

    # -------------------------------------------------------- individual 1040s
    w.return_of: dict[str, str] = {}  # tin -> return_id (primary or spouse)
    w.returns_by_id: dict[str, dict] = {}
    handled_set: set[str] = set()
    couple_joint: dict[tuple[str, str], bool] = {}

    def facts(p: dict) -> dict:
        return {
            "wages": sum(g for _b, g, _wh in p["_w2"]),
            "wh": sum(wh for _b, _g, wh in p["_w2"]),
            "interest": sum(a for _b, a in p["_int"]),
            "dividends": sum(a for _b, a in p["_div"]),
            "se_gross": sum(a for _b, a in p["_nec"]) + p["_extra_se"] + p.get("_k1099", 0.0),
            "other": p["_rent_income"],
        }

    for hh, members in w.hh_members.items():
        adults = [w.P[m] for m in members if w.P[m]["_age"] >= 16]
        kids = [w.P[m] for m in members if w.P[m]["_age"] < 19 or (w.P[m]["_age"] < 24 and w.P[m]["_status"] == "student")]
        kids = [k for k in kids if k["_parents"] and k["_parents"][0] in members]
        kid_ids = [k["person_id"] for k in kids]
        for a in adults:
            if a["person_id"] in handled_set:
                continue
            f = facts(a)
            is_kid = a["person_id"] in kid_ids
            spouse = None if is_kid else (w.P[a["_spouse"]] if a["_spouse"] and a["_spouse"] in members else None)
            joint = False
            if spouse is not None:
                key = tuple(sorted((a["person_id"], spouse["person_id"])))
                if key not in couple_joint:
                    couple_joint[key] = rng.random() < 0.86
                joint = couple_joint[key]
            if joint:
                fs = facts(spouse)
                for k in f:
                    f[k] += fs[k]
                primary = a if a["_salary"] >= spouse["_salary"] else spouse
                secondary = spouse if primary is a else a
                status = "married_joint"
            else:
                primary, secondary = a, None
                status = "married_separate" if spouse else "single"
            gross = f["wages"] + f["interest"] + f["dividends"] + f["se_gross"] + f["other"]
            my_kids = [] if is_kid else [k for k in kids if primary["person_id"] in k["_parents"] or
                                         (secondary and secondary["person_id"] in k["_parents"])]
            if status == "married_separate" and my_kids and primary["person_id"] != my_kids[0]["_parents"][0]:
                my_kids = []
            if status == "single" and my_kids:
                status = "head_of_household"
            # no filing requirement: small income (always below the $10k matching threshold)
            if gross < 1_000 or (gross < 9_500 and not my_kids and (f["wh"] == 0 or rng.random() < 0.35)):
                handled_set.add(a["person_id"])
                continue
            se_exp = f["se_gross"] * rng.uniform(0.18, 0.42) if f["se_gross"] else 0.0
            se_net = f["se_gross"] - se_exp
            rid = w.ids.new("RT", 8)
            prep = None
            if rng.random() < 0.56:
                prep = rng.choices(w.preparer_ids, [w.preparer_weight[x] for x in w.preparer_ids])[0]
            refund_acct = w.checking_of(primary["person_id"]) or (secondary and w.checking_of(secondary["person_id"]))
            row = {
                "return_id": rid, "tax_year": YEAR, "form_type": "1040", "filer_tin": primary["tin"],
                "spouse_tin": secondary["tin"] if secondary else None, "filing_status": status,
                "preparer_id": prep, "wages": money(f["wages"]), "interest": money(f["interest"]),
                "dividends": money(f["dividends"]), "business_gross_receipts": money(f["se_gross"]),
                "business_expenses": money(se_exp), "other_income": money(f["other"]),
                "total_income": 0.0, "deductions": None, "credits": None, "tax_liability": 0.0,
                "withholding": money(f["wh"] + max(0.0, se_net) * rng.uniform(0.12, 0.2)),
                "refund_amount": 0.0, "balance_due": 0.0, "refund_account_id": refund_acct or None,
                "num_dependents": len(my_kids),
                "filed_date": d2s(w.r_date(date(YEAR + 1, 1, 25), date(YEAR + 1, 4, 15))),
                "_primary": primary["person_id"], "_secondary": secondary["person_id"] if secondary else None,
            }
            n17 = sum(1 for k in my_kids if k["_age"] < 17)
            compute_return(row, n17, f["wages"] + se_net)
            if row["refund_amount"] <= 0:
                row["refund_account_id"] = None
            T["tax_returns"].append(row)
            w.returns_by_id[rid] = row
            for k in my_kids:
                T["return_dependents"].append({"return_id": rid, "dependent_person_id": k["person_id"]})
            for person in (primary, secondary):
                if person:
                    handled_set.add(person["person_id"])
                    w.return_of[person["tin"]] = rid

    # ---------------------------------------------------------- entity returns
    acct_holder = {a["account_id"]: a["holder_id"] for a in T["accounts"]}
    inflow: dict[str, float] = defaultdict(float)
    owners_of: dict[str, list[str]] = defaultdict(list)
    for o in T["ownership"]:
        owners_of[o["owned_id"]].append(o["owner_id"])
    for t in T["transactions"]:
        if t["to_account"]:
            h = acct_holder[t["to_account"]]
            memo = (t["memo"] or "").upper()
            src = acct_holder.get(t["from_account"]) if t["from_account"] else None
            if "LOAN" in memo or "CAPITAL" in memo or src == h or src in owners_of.get(h, []):
                continue  # not revenue
            inflow[h] += t["amount"]
    w.biz_inflow_at_filing = dict(inflow)
    for b in w.businesses:
        if b["jurisdiction"] not in w.city_state.values() and b["jurisdiction"] not in ("DE", "WY"):
            continue  # offshore entities file no US return
        if not b["_files_return"]:
            continue
        dep = inflow.get(b["business_id"], 0.0)
        if b["_infra"]:
            gross = max(dep * rng.uniform(1.6, 3.0), 2_000_000)
        elif b.get("_holding"):
            gross = rng.uniform(20_000, 150_000)
        else:
            gross = dep * rng.uniform(1.0, 1.04)
        exp = gross * rng.uniform(0.78, 0.97)
        form = {"Corporation": "1120", "S-Corporation": "1120S", "LLC": rng.choice(["1065", "1120S"]),
                "Partnership": "1065"}.get(b["entity_type"], "1120")
        rid = w.ids.new("RT", 8)
        prep = rng.choices(w.preparer_ids, [w.preparer_weight[x] for x in w.preparer_ids])[0] if rng.random() < 0.7 else None
        row = {
            "return_id": rid, "tax_year": YEAR, "form_type": form, "filer_tin": b["ein"], "spouse_tin": None,
            "filing_status": "entity", "preparer_id": prep, "wages": 0.0, "interest": 0.0, "dividends": 0.0,
            "business_gross_receipts": money(gross), "business_expenses": money(exp), "other_income": 0.0,
            "total_income": money(gross - exp), "deductions": 0.0, "credits": 0.0,
            "tax_liability": money(max(0.0, (gross - exp) * 0.21) if form == "1120" else 0.0),
            "withholding": 0.0, "refund_amount": 0.0, "balance_due": 0.0, "refund_account_id": None,
            "num_dependents": 0, "filed_date": d2s(w.r_date(date(YEAR + 1, 2, 1), date(YEAR + 1, 4, 15))),
            "_entity": b["business_id"],
        }
        row["withholding"] = row["tax_liability"]
        T["tax_returns"].append(row)
        w.returns_by_id[rid] = row
        w.return_of[b["ein"]] = rid


def recompute(w: World, row: dict) -> None:
    """Recompute derived fields after a scheme mutates an individual return."""
    deps = [d for d in w.tables["return_dependents"] if d["return_id"] == row["return_id"]]
    n17 = sum(1 for d in deps if w.P[d["dependent_person_id"]]["_age"] < 17)
    earned = row["wages"] + row["business_gross_receipts"] - row["business_expenses"]
    row["total_income"] = 0.0
    compute_return(row, n17, earned)


def tin_income_reported(w: World, tin: str) -> float:
    rid = w.return_of.get(tin)
    if not rid:
        return 0.0
    r = w.returns_by_id[rid]
    return r["wages"] + r["interest"] + r["dividends"] + r["business_gross_receipts"] + r["other_income"]



def add_wages(w: World, pid: str, employer_bid: str, gross: float) -> bool:
    """Record extra W-2 wages for a person and report them on their existing return."""
    p = w.P[pid]
    rid = w.return_of.get(p["tin"]) if p["tin"] else None
    if not rid:
        return False
    w.add_info_return("W-2", w.B[employer_bid]["ein"], p["tin"], gross)
    r = w.returns_by_id[rid]
    r["wages"] = money(r["wages"] + gross)
    r["withholding"] = money(r["withholding"] + gross * 0.12)
    recompute(w, r)
    return True
