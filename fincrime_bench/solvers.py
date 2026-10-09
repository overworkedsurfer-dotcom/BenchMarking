"""Reference solvers: solve each task using ONLY the warehouse (never the answer key).

They prove every task is solvable from the data and that the planted signal is unambiguous. ``validate``
runs them and expects a perfect score; they also serve as worked examples of the intended analysis.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from itertools import combinations

from .db import Warehouse

ASOF = "2025-12-31"


def _rows(wh: Warehouse, sql: str, params: tuple = ()) -> list[dict]:
    cols, rows, _ = wh.query(sql, params, max_rows=1_000_000)
    return [dict(zip(cols, r)) for r in rows]


def _one(wh: Warehouse, sql: str, params: tuple = ()):
    r = _rows(wh, sql, params)
    return r[0] if r else None


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%S")


def ubo(wh: Warehouse, entity: str, asof: str = ASOF, _seen: tuple = ()) -> dict[str, float]:
    """Effective ownership (percent) of natural persons in ``entity``."""
    out: dict[str, float] = defaultdict(float)
    for r in _rows(wh, "SELECT owner_id, ownership_pct FROM ownership WHERE owned_id = ? AND ownership_pct IS NOT "
                       "NULL AND start_date <= ? AND (end_date IS NULL OR end_date > ?)", (entity, asof, asof)):
        frac = r["ownership_pct"] / 100.0
        if r["owner_id"].startswith("P"):
            out[r["owner_id"]] += frac * 100
        elif r["owner_id"] not in _seen:
            for p, pct in ubo(wh, r["owner_id"], asof, _seen + (entity,)).items():
                out[p] += frac * pct
    return dict(out)


def top_ubo(wh: Warehouse, entity: str) -> str | None:
    if entity.startswith("P"):
        return entity
    owners = ubo(wh, entity)
    return max(owners, key=owners.get) if owners else None


def holder(wh: Warehouse, account: str) -> dict:
    return _one(wh, "SELECT * FROM accounts WHERE account_id = ?", (account,))


# ------------------------------------------------------------------- AML
def solve_layering(task: dict, wh: Warehouse) -> dict:
    t = _one(wh, "SELECT * FROM transactions WHERE txn_id = ?", (task["inputs"]["txn_id"],))
    frontier = [(t["to_account"], t["amount"], _ts(t["timestamp"]))]
    accounts: list[str] = []
    exit_acct = beneficiary = None
    while frontier:
        nxt: dict[str, list] = {}
        for acct, m, ts in frontier:
            if acct not in accounts:
                accounts.append(acct)
            outs = _rows(wh, "SELECT * FROM transactions WHERE from_account = ? AND timestamp > ? AND timestamp <= ? "
                             "ORDER BY timestamp", (acct, _iso(ts), _iso(ts + timedelta(hours=120))))
            cash = [o for o in outs if o["channel"] == "cash_withdrawal"]
            if cash and 0.9 * m <= sum(o["amount"] for o in cash) <= m:
                exit_acct = acct
                beneficiary = Counter(o["conducted_by"] for o in cash).most_common(1)[0][0]
                continue
            intl = [o for o in outs if o["channel"] == "international_wire" and 0.9 * m <= o["amount"] <= m]
            if intl:
                dest = intl[0]["to_account"]
                accounts.append(dest)
                exit_acct = dest
                beneficiary = top_ubo(wh, holder(wh, dest)["holder_id"])
                continue
            dom = [o for o in outs if o["channel"] not in ("cash_withdrawal", "international_wire")
                   and _ts(o["timestamp"]) <= ts + timedelta(hours=72)]
            single = [o for o in dom if 0.95 * m <= o["amount"] <= m]
            chosen = single[:1]
            if not chosen:
                for a, b in combinations(dom, 2):
                    if 0.95 * m <= a["amount"] + b["amount"] <= m:
                        chosen = [a, b]
                        break
            for o in chosen:
                nxt.setdefault(o["to_account"], []).append((o["amount"], _ts(o["timestamp"])))
        frontier = [(a, sum(x[0] for x in v), max(x[1] for x in v)) for a, v in nxt.items()]
    return {"accounts": accounts, "exit_account": exit_acct, "beneficiary_person_id": beneficiary}


def structured_depositors(wh: Warehouse) -> list[str]:
    rows = _rows(wh, "SELECT conducted_by, timestamp FROM transactions WHERE channel = 'cash_deposit' AND "
                     "conducted_by IS NOT NULL AND amount >= 7000 AND amount < 10000 ORDER BY timestamp")
    by: dict[str, list[datetime]] = defaultdict(list)
    for r in rows:
        by[r["conducted_by"]].append(_ts(r["timestamp"]))
    out = []
    for pid, times in by.items():
        if any(sum(1 for t2 in times if timedelta(0) <= t2 - t1 <= timedelta(days=30)) >= 3 for t1 in times):
            out.append(pid)
    return sorted(out)


def solve_structuring(task: dict, wh: Warehouse) -> dict:
    return {"person_ids": structured_depositors(wh)}


def solve_smurfing(task: dict, wh: Warehouse) -> dict:
    acct = task["inputs"]["account_id"]
    smurfs = [r["conducted_by"] for r in _rows(
        wh, "SELECT DISTINCT conducted_by FROM transactions WHERE to_account = ? AND channel = 'cash_deposit' "
            "AND amount < 10000", (acct,))]
    a = holder(wh, acct)
    ctrl = a["authorized_signer_id"]
    if not ctrl:
        top = _one(wh, "SELECT to_account, SUM(amount) s FROM transactions WHERE from_account = ? GROUP BY to_account "
                       "ORDER BY s DESC", (acct,))
        ctrl = holder(wh, top["to_account"])["holder_id"]
    return {"smurf_person_ids": smurfs, "controller_person_id": ctrl}


def solve_mule_ring(task: dict, wh: Warehouse) -> dict:
    t = _one(wh, "SELECT * FROM transactions WHERE txn_id = ?", (task["inputs"]["txn_id"],))
    first_mule = t["to_account"]
    collector = _one(wh, "SELECT to_account, SUM(amount) s FROM transactions WHERE from_account = ? AND to_account "
                         "IS NOT NULL GROUP BY to_account ORDER BY s DESC", (first_mule,))["to_account"]
    mules = [r["from_account"] for r in _rows(
        wh, "SELECT from_account, SUM(amount) s FROM transactions WHERE to_account = ? AND from_account IS NOT NULL "
            "GROUP BY from_account HAVING s >= 1000", (collector,))]
    victims = sorted({holder(wh, r["from_account"])["holder_id"] for m in mules for r in _rows(
        wh, "SELECT from_account FROM transactions WHERE to_account = ? AND amount >= 1000 AND from_account IS NOT NULL",
        (m,))})
    fwd = [r["txn_id"] for m in mules for r in _rows(
        wh, "SELECT txn_id FROM transactions WHERE from_account = ? AND to_account = ?", (m, collector))]
    recruited = {holder(wh, a)["holder_id"] for a in mules + [collector]}
    ph = ", ".join("?" for _ in fwd)
    sess = _rows(wh, f"SELECT device_id, ip_address FROM logins WHERE txn_id IN ({ph})", tuple(fwd))
    devices = sorted({s["device_id"] for s in sess})
    ips = sorted({s["ip_address"] for s in sess})
    cands = Counter()
    for d in devices:
        for r in _rows(wh, "SELECT person_id FROM logins WHERE device_id = ?", (d,)):
            if r["person_id"] not in recruited:
                cands[r["person_id"]] += 1
    if not cands:
        for ip in ips:
            for r in _rows(wh, "SELECT person_id FROM logins WHERE ip_address = ?", (ip,)):
                if r["person_id"] not in recruited:
                    cands[r["person_id"]] += 1
    return {"mule_accounts": mules, "collector_account": collector,
            "controller_person_id": cands.most_common(1)[0][0] if cands else None, "victim_person_ids": victims}


def ato_detect(wh: Warehouse) -> tuple[list[str], list[str]]:
    rows = _rows(wh, "SELECT * FROM logins ORDER BY timestamp")
    first: dict[tuple, datetime] = {}
    resets: dict[tuple, list[datetime]] = defaultdict(list)
    for e in rows:
        k = (e["person_id"], e["device_id"])
        first.setdefault(k, _ts(e["timestamp"]))
        if e["event_type"] == "password_reset":
            resets[k].append(_ts(e["timestamp"]))
    txns, victims = [], []
    for e in rows:
        if e["event_type"] != "transfer" or e["ip_country"] == "US":
            continue
        k, ts = (e["person_id"], e["device_id"]), _ts(e["timestamp"])
        if ts - first[k] <= timedelta(hours=24) and any(timedelta(0) <= ts - r <= timedelta(hours=24) for r in resets[k]):
            txns.append(e["txn_id"])
            if e["person_id"] not in victims:
                victims.append(e["person_id"])
    return sorted(txns), sorted(victims)


def solve_ato(task: dict, wh: Warehouse) -> dict:
    txns, victims = ato_detect(wh)
    return {"fraudulent_txn_ids": txns, "victim_person_ids": victims}


def solve_round_trip(task: dict, wh: Warehouse) -> dict:
    inv = _one(wh, "SELECT * FROM transactions WHERE txn_id = ?", (task["inputs"]["txn_id"],))
    company = inv["to_account"]
    cycle = [inv["from_account"]]
    cur, m, ts = inv["from_account"], inv["amount"], inv["timestamp"]
    for _ in range(8):
        prev = _one(wh, "SELECT * FROM transactions WHERE to_account = ? AND timestamp < ? AND amount BETWEEN ? AND ? "
                        "ORDER BY timestamp DESC", (cur, ts, m, m * 1.06))
        if prev is None:
            break
        cur, m, ts = prev["from_account"], prev["amount"], prev["timestamp"]
        if cur == company:
            break
        cycle.append(cur)
    is_cycle = cur == company
    investor = holder(wh, inv["from_account"])["holder_id"]
    return {"round_trip": is_cycle, "cycle_accounts": [company] + cycle if is_cycle else [],
            "investor_ubo_person_id": top_ubo(wh, investor)}


# ------------------------------------------------------------- ownership
def solve_beneficial_ownership(task: dict, wh: Warehouse) -> dict:
    owners = ubo(wh, task["inputs"]["business_id"])
    return {"owners": [{"id": p, "value": round(v, 2)} for p, v in owners.items() if v >= 25 - 1e-9]}


def _spouses(wh: Warehouse, pid: str) -> list[str]:
    return [r["o"] for r in _rows(
        wh, "SELECT CASE WHEN person_id_1 = ? THEN person_id_2 ELSE person_id_1 END o FROM relationships WHERE "
            "relationship = 'spouse' AND (person_id_1 = ? OR person_id_2 = ?)", (pid, pid, pid))]


def solve_kickback(task: dict, wh: Warehouse) -> dict:
    u = task["inputs"]["business_id"]
    insiders = {r["owner_id"] for r in _rows(wh, "SELECT owner_id FROM ownership WHERE owned_id = ? AND "
                                                 "owner_id LIKE 'P%'", (u,))}
    insiders |= {r["person_id"] for r in _rows(wh, "SELECT person_id FROM persons WHERE employer_id = ?", (u,))}
    uacct = [r["account_id"] for r in _rows(wh, "SELECT account_id FROM accounts WHERE holder_id = ?", (u,))]
    ph = ", ".join("?" for _ in uacct)
    pays = _rows(wh, f"SELECT a.holder_id vendor, SUM(t.amount) total FROM transactions t JOIN accounts a ON "
                     f"a.account_id = t.to_account WHERE t.from_account IN ({ph}) AND a.holder_id LIKE 'B%' "
                     f"GROUP BY a.holder_id", tuple(uacct))
    for p in pays:
        bo = top_ubo(wh, p["vendor"])
        if not bo:
            continue
        if bo in insiders:
            return {"vendor_id": p["vendor"], "insider_person_id": bo, "beneficial_owner_person_id": bo,
                    "total_paid": round(p["total"], 2)}
        for sp in _spouses(wh, bo):
            if sp in insiders:
                return {"vendor_id": p["vendor"], "insider_person_id": sp, "beneficial_owner_person_id": bo,
                        "total_paid": round(p["total"], 2)}
    return {}


# ------------------------------------------------------------------- tax
def solve_unreported(task: dict, wh: Warehouse) -> dict:
    info = {r["payee_tin"]: r["s"] for r in _rows(wh, "SELECT payee_tin, SUM(amount) s FROM info_returns GROUP BY "
                                                      "payee_tin")}
    person_tins = {r["tin"] for r in _rows(wh, "SELECT tin FROM persons WHERE tin IS NOT NULL")}
    rets = _rows(wh, "SELECT * FROM tax_returns WHERE form_type = '1040'")
    by_tin = {}
    for r in rets:
        by_tin[r["filer_tin"]] = r
        if r["spouse_tin"]:
            by_tin[r["spouse_tin"]] = r
    out, seen = [], set()
    for tin, total in info.items():
        if tin not in person_tins:
            continue
        r = by_tin.get(tin)
        if r is None:
            short, key = total, tin
        else:
            if r["return_id"] in seen:
                continue
            seen.add(r["return_id"])
            combined = info.get(r["filer_tin"], 0) + (info.get(r["spouse_tin"], 0) if r["spouse_tin"] else 0)
            reported = r["wages"] + r["interest"] + r["dividends"] + r["business_gross_receipts"] + r["other_income"]
            short, key = combined - reported, r["filer_tin"]
        if short >= 10_000:
            out.append({"id": key, "value": round(short, 2)})
    return {"taxpayers": out}


def _income_of(wh: Warehouse, tin: str | None) -> float:
    if not tin:
        return 0.0
    r = _one(wh, "SELECT total_income FROM tax_returns WHERE filer_tin = ? OR spouse_tin = ?", (tin, tin))
    return r["total_income"] if r else 0.0


def solve_lifestyle(task: dict, wh: Warehouse) -> dict:
    rows = _rows(wh, "SELECT p.person_id, p.tin, "
                     "SUM(CASE WHEN a.purchase_date LIKE '2025%' THEN a.purchase_price - a.loan_amount ELSE 0 END) "
                     "outlay, SUM(CASE WHEN a.sale_date LIKE '2025%' THEN a.sale_price ELSE 0 END) sales "
                     "FROM assets a JOIN persons p ON p.person_id = a.owner_id GROUP BY p.person_id")
    out = [r["person_id"] for r in rows
           if r["outlay"] - _income_of(wh, r["tin"]) - r["sales"] > 250_000]
    return {"person_ids": out}


def solve_net_worth(task: dict, wh: Warehouse) -> dict:
    pid = task["inputs"]["person_id"]
    tin = _one(wh, "SELECT tin FROM persons WHERE person_id = ?", (pid,))["tin"]
    assets = _rows(wh, "SELECT * FROM assets WHERE owner_id = ?", (pid,))
    acquired = [a for a in assets if (a["purchase_date"] or "").startswith("2025")]
    outlay = sum(a["purchase_price"] - a["loan_amount"] for a in acquired)
    sales = sum(a["sale_price"] for a in assets if (a["sale_date"] or "").startswith("2025"))
    return {"assets_acquired": [a["asset_id"] for a in acquired],
            "unexplained_amount": round(outlay - _income_of(wh, tin) - sales, 2)}


def solve_preparer(task: dict, wh: Warehouse) -> dict:
    rows = _rows(wh, "SELECT r.return_id, r.preparer_id, r.refund_account_id, a.holder_id, pf.person_id filer, "
                     "ps.person_id spouse FROM tax_returns r JOIN accounts a ON a.account_id = r.refund_account_id "
                     "LEFT JOIN persons pf ON pf.tin = r.filer_tin LEFT JOIN persons ps ON ps.tin = r.spouse_tin "
                     "WHERE r.refund_account_id IS NOT NULL")
    bad = [r for r in rows if r["holder_id"] not in (r["filer"], r["spouse"])]
    prep = Counter(r["preparer_id"] for r in bad).most_common(1)[0][0]
    bad = [r for r in bad if r["preparer_id"] == prep]
    return {"preparer_id": prep, "diverted_return_ids": [r["return_id"] for r in bad],
            "refund_accounts": sorted({r["refund_account_id"] for r in bad})}


def solve_dependents(task: dict, wh: Warehouse) -> dict:
    rows = _rows(wh, "SELECT d.return_id, d.dependent_person_id, p.date_of_death FROM return_dependents d JOIN "
                     "persons p ON p.person_id = d.dependent_person_id")
    claims = Counter(r["dependent_person_id"] for r in rows)
    out = {r["return_id"] for r in rows
           if claims[r["dependent_person_id"]] > 1 or (r["date_of_death"] and r["date_of_death"] < "2025-01-01")}
    return {"return_ids": sorted(out)}


def solve_skimming(task: dict, wh: Warehouse) -> dict:
    rows = _rows(wh, "SELECT b.business_id, r.business_gross_receipts reported, "
                     "(SELECT COALESCE(SUM(t.amount), 0) FROM transactions t JOIN accounts a ON a.account_id = "
                     "t.to_account LEFT JOIN accounts fa ON fa.account_id = t.from_account WHERE a.holder_id = "
                     "b.business_id AND UPPER(COALESCE(t.memo, '')) NOT LIKE '%LOAN%' AND UPPER(COALESCE(t.memo, '')) "
                     "NOT LIKE '%CAPITAL%' AND COALESCE(fa.holder_id, '') <> b.business_id AND COALESCE(fa.holder_id, "
                     "'') NOT IN (SELECT owner_id FROM ownership WHERE owned_id = b.business_id)) revenue "
                     "FROM businesses b JOIN tax_returns r ON r.filer_tin = b.ein "
                     "WHERE b.industry IN ('restaurant', 'salon', 'laundromat', 'car wash')")
    return {"businesses": [{"id": r["business_id"], "value": round(r["revenue"] - r["reported"], 2)}
                           for r in rows if r["revenue"] > 0 and r["reported"] < 0.8 * r["revenue"]]}


# --------------------------------------------------------------- telecom
def _contacts(wh: Warehouse, phone: str, before: str | None = None, after: str | None = None) -> Counter:
    cond, params = "", []
    if before:
        cond += " AND timestamp < ?"
        params.append(before)
    if after:
        cond += " AND timestamp >= ?"
        params.append(after)
    rows = _rows(wh, f"SELECT callee o FROM calls WHERE caller = ?{cond} UNION ALL SELECT caller FROM calls WHERE "
                     f"callee = ?{cond}", tuple([phone] + params + [phone] + params))
    return Counter(r["o"] for r in rows)


def solve_contact_chaining(task: dict, wh: Warehouse) -> dict:
    crew = task["inputs"]["crew"]
    crew_phones, sets = [], []
    for pid in crew:  # a subscriber may also pay for family members' lines: union their phones' contacts
        phones = [r["phone_number"] for r in _rows(wh, "SELECT phone_number FROM phones WHERE subscriber_id = ?",
                                                   (pid,))]
        crew_phones += phones
        sets.append(set().union(*(set(_contacts(wh, p)) for p in phones)))
    common = set.intersection(*sets) - set(crew_phones)
    handler = sorted(common)[0]
    hc = _contacts(wh, handler)
    for p in crew_phones:
        hc.pop(p, None)
    top = hc.most_common(1)[0][0]
    sub = _one(wh, "SELECT subscriber_id FROM phones WHERE phone_number = ?", (top,))
    if sub and sub["subscriber_id"]:
        return {"handler_phone": handler, "boss_person_id": sub["subscriber_id"]}
    burner = top
    co = Counter()
    for c in _rows(wh, "SELECT timestamp, caller_tower_id FROM calls WHERE caller = ?", (burner,)):
        t = _ts(c["timestamp"])
        for o in _rows(wh, "SELECT DISTINCT caller FROM calls WHERE caller_tower_id = ? AND timestamp BETWEEN ? AND ? "
                           "AND caller <> ?", (c["caller_tower_id"], _iso(t - timedelta(minutes=15)),
                                               _iso(t + timedelta(minutes=15)), burner)):
            co[o["caller"]] += 1
    boss_phone = co.most_common(1)[0][0]
    boss = _one(wh, "SELECT subscriber_id FROM phones WHERE phone_number = ?", (boss_phone,))["subscriber_id"]
    return {"handler_phone": handler, "boss_phone": burner, "boss_person_id": boss}


def solve_burner_switch(task: dict, wh: Warehouse) -> dict:
    cur = task["inputs"]["old_phone"]
    chain: list[str] = []
    for _ in range(5):
        ph = _one(wh, "SELECT * FROM phones WHERE phone_number = ?", (cur,))
        d = ph["deactivation_date"]
        if not d:
            break
        before = set(_contacts(wh, cur, before=d))
        lo = (datetime.fromisoformat(d) - timedelta(days=3)).strftime("%Y-%m-%d")
        hi = (datetime.fromisoformat(d) + timedelta(days=10)).strftime("%Y-%m-%d")
        best, best_score = None, 0.0
        for c in _rows(wh, "SELECT phone_number FROM phones WHERE activation_date BETWEEN ? AND ? AND phone_number <> ?",
                       (lo, hi, cur)):
            after = set(_contacts(wh, c["phone_number"], after=d))
            if not after:
                continue
            score = len(before & after) + len(before & after) / len(before | after)
            if score > best_score:
                best, best_score = c["phone_number"], score
        if best is None:
            break
        chain.append(best)
        cur = best
    if "phone" in {f["name"] for f in task["answer_fields"]}:
        return {"phone": chain[0] if chain else None}
    return {"replacement_phones": chain, "current_phone": chain[-1] if chain else None}


# ------------------------------------------------------------- capstones
def solve_scam_capstone(task: dict, wh: Warehouse) -> dict:
    burner = task["inputs"]["burner"]
    calls = _rows(wh, "SELECT callee, timestamp FROM calls WHERE caller = ? ORDER BY timestamp", (burner,))
    victims, payments = [], []
    first_recipient = None
    for c in calls:
        owner = _one(wh, "SELECT subscriber_id FROM phones WHERE phone_number = ?", (c["callee"],))
        pid = owner["subscriber_id"] if owner and owner["subscriber_id"] else None
        if not pid:
            r = _one(wh, "SELECT person_id FROM persons WHERE phone = ?", (c["callee"],))
            pid = r["person_id"] if r else None
        if not pid:
            continue
        t = _ts(c["timestamp"])
        pay = _one(wh, "SELECT t.* FROM transactions t JOIN accounts a ON a.account_id = t.from_account WHERE "
                       "a.holder_id = ? AND t.timestamp > ? AND t.timestamp <= ? AND t.amount >= 1000 ORDER BY "
                       "t.timestamp", (pid, _iso(t), _iso(t + timedelta(hours=2))))
        if pay:
            payments.append(pay)
            if pid not in victims:
                victims.append(pid)
            if pid == task["inputs"]["victim"] and c["timestamp"] == task["inputs"]["call_time"]:
                first_recipient = pay["to_account"]
    mules = sorted({p["to_account"] for p in payments})
    ph = ", ".join("?" for _ in mules)
    coll = _one(wh, f"SELECT to_account, SUM(amount) s FROM transactions WHERE from_account IN ({ph}) GROUP BY "
                    f"to_account ORDER BY s DESC", tuple(mules))["to_account"]
    organizer = top_ubo(wh, holder(wh, coll)["holder_id"])
    paid = _one(wh, "SELECT COALESCE(SUM(t.amount), 0) s FROM transactions t JOIN accounts a ON a.account_id = "
                    "t.to_account WHERE t.from_account = ? AND a.holder_id = ?", (coll, organizer))["s"]
    return {"first_recipient_account": first_recipient, "collection_account": coll, "organizer_person_id": organizer,
            "victim_person_ids": victims, "total_victim_losses": round(sum(p["amount"] for p in payments), 2),
            "organizer_unreported_income": round(paid, 2)}


def solve_audit_capstone(task: dict, wh: Warehouse) -> dict:
    q = task["inputs"]["person_id"]
    qacct = [r["account_id"] for r in _rows(wh, "SELECT account_id FROM accounts WHERE holder_id = ?", (q,))]
    ph = ", ".join("?" for _ in qacct)
    wires = _rows(wh, f"SELECT from_account, SUM(amount) s FROM transactions WHERE to_account IN ({ph}) AND channel = "
                      f"'international_wire' GROUP BY from_account ORDER BY s DESC", tuple(qacct))
    funding, received = wires[0]["from_account"], wires[0]["s"]
    conduit_acct = _one(wh, "SELECT from_account, SUM(amount) s FROM transactions WHERE to_account = ? GROUP BY "
                            "from_account ORDER BY s DESC", (funding,))["from_account"]
    conduit = holder(wh, conduit_acct)["holder_id"]
    src = _one(wh, "SELECT from_account, SUM(amount) s FROM transactions WHERE to_account = ? GROUP BY from_account "
                   "ORDER BY s DESC", (conduit_acct,))
    nominee = _one(wh, "SELECT owner_id FROM ownership WHERE owned_id = ? AND ownership_pct IS NOT NULL ORDER BY "
                       "ownership_pct DESC", (conduit,))["owner_id"]
    return {"funding_account": funding, "conduit_vendor_id": conduit,
            "source_company_id": holder(wh, src["from_account"])["holder_id"], "nominee_person_id": nominee,
            "total_diverted": round(src["s"], 2), "unreported_income": round(received, 2)}


SOLVERS = {
    "layering": solve_layering, "structuring": solve_structuring, "smurfing": solve_smurfing,
    "mule_ring": solve_mule_ring, "account_takeover": solve_ato, "round_trip": solve_round_trip,
    "beneficial_ownership": solve_beneficial_ownership, "kickback": solve_kickback,
    "unreported_income": solve_unreported, "lifestyle": solve_lifestyle, "net_worth": solve_net_worth,
    "preparer_fraud": solve_preparer, "dependents": solve_dependents, "skimming": solve_skimming,
    "contact_chaining": solve_contact_chaining, "burner_switch": solve_burner_switch,
    "scam_capstone": solve_scam_capstone, "audit_capstone": solve_audit_capstone,
}


def solve(task: dict, wh: Warehouse) -> dict:
    return SOLVERS[task["scheme"]](task, wh)
