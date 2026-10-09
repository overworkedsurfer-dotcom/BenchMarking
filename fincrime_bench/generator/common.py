"""Helpers shared by scheme planters: task registration and shell-entity factories."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Callable

from . import names as N
from .world import World, d2s, money

DIFFICULTY_WEIGHT = {"easy": 1, "medium": 2, "hard": 3}


def field(name: str, ftype: str, weight: float, description: str, **params) -> dict:
    """One scored answer field. Types: id, id_set, number, bool, id_number_map."""
    return {"name": name, "type": ftype, "weight": weight, "description": description, **params}


def add_task(w: World, task_id: str, category: str, scheme: str, difficulty: str, title: str, prompt: str,
             fields: list[dict], answer: dict | None = None, inputs: dict | None = None,
             decoys: dict | None = None, notes: str = "", deferred: Callable[[], dict] | None = None) -> None:
    w.tasks.append({
        "task_id": task_id, "category": category, "scheme": scheme, "difficulty": difficulty,
        "weight": DIFFICULTY_WEIGHT[difficulty], "title": title, "prompt": prompt.strip(),
        "answer_fields": fields, "inputs": inputs or {},
    })
    w.answers.append({"task_id": task_id, "answer": answer, "decoys": decoys or {}, "notes": notes})
    if deferred is not None:
        w.deferred[task_id] = deferred


def answer_of(w: World, task_id: str) -> dict:
    for a in w.answers:
        if a["task_id"] == task_id:
            return a
    raise KeyError(task_id)


def fmt_money(x: float) -> str:
    return f"${x:,.2f}"


def odd_amount(w: World, lo: float, hi: float) -> float:
    """Non-round amount (avoid trivially searchable round numbers)."""
    return money(w.rng.uniform(lo, hi)) + w.rng.choice([0.0, 0.17, 0.43, 0.61, 0.88])


# ------------------------------------------------------------------ entities
def shell_name(w: World, suffix: str) -> str:
    return f"{w.rng.choice(N.SHELL_WORDS)} {w.rng.choice(N.SHELL_SUFFIX)} {suffix}"


def foreign_person(w: World, cc: str) -> str:
    addr = w.rng.choice(w.offshore_addrs[cc])
    p = w.new_person(w.rng.choice(N.FIRST_NAMES), w.rng.choice(N.LAST_NAMES), w.rng.randint(35, 70), addr,
                     w.ADDR[addr]["city"], cc, occupation="Corporate Services")
    del w.TIN[p["tin"]]
    p["tin"] = None
    p["_foreign"] = True
    w.reserve(p["person_id"])
    return p["person_id"]


def nominee_director(w: World) -> str:
    if not w.nominees:
        for cc in ("CY", "VG", "PA"):
            w.nominees.append(foreign_person(w, cc))
        # nominee directors also sit on some legitimate offshore subsidiaries
        for b in w.businesses:
            if b.get("_offshore_sub"):
                w.add_owner(w.rng.choice(w.nominees), b["business_id"], "director", None,
                            date.fromisoformat(b["incorporation_date"]))
    return w.rng.choice(w.nominees)


def offshore_shell(w: World, owners: list[tuple[str, float]] | None = None, cc: str | None = None,
                   inc: date | None = None, with_director: bool = True, open_account: bool = True,
                   entity_type: str | None = None, name: str | None = None) -> tuple[str, str | None]:
    rng = w.rng
    spec = [o for o in N.OFFSHORE if o[0] == cc][0] if cc else rng.choice(N.OFFSHORE)
    cc, _label, bank, etype = spec
    etype = entity_type or etype
    inc = inc or w.r_date(date(2019, 1, 1), date(2024, 9, 1))
    b = w.new_business(name or shell_name(w, etype), etype, rng.choice(["holding company", "holding company", "logistics",
                                                                         "consulting"]),
                       cc, rng.choice(w.offshore_addrs[cc]), inc, _shell=True, _files_return=False)
    bid = b["business_id"]
    w.reserve(bid)
    for owner, pct in owners or []:
        role = "beneficiary" if etype in ("Trust", "Foundation") else "shareholder"
        w.add_owner(owner, bid, role, pct, inc)
    if with_director:
        w.add_owner(nominee_director(w), bid, "director", None, inc)
    acct = None
    if open_account:
        acct = w.new_account(bid, bank, cc, "business_checking", inc + timedelta(days=rng.randint(10, 60)))
    return bid, acct


def us_shell(w: World, owners: list[tuple[str, float]], signer: str | None, inc: date,
             industry: str = "consulting", state: str | None = None, name: str | None = None,
             bank: str | None = None) -> tuple[str, str]:
    rng = w.rng
    state = state or rng.choice(["WY", "DE"])
    b = w.new_business(name or shell_name(w, "LLC"), "LLC", industry, state, rng.choice(w.reg_agent_addrs), inc,
                       _shell=True, _files_return=False)
    bid = b["business_id"]
    w.reserve(bid)
    for owner, pct in owners:
        w.add_owner(owner, bid, "member", pct, inc)
    acct = w.new_account(bid, bank or w._pick_bank(), "US", "business_checking",
                         inc + timedelta(days=rng.randint(5, 40)), signer=signer)
    return bid, acct


def new_personal_account(w: World, pid: str, opened: date, signer: str | None = None) -> str:
    return w.new_account(pid, w._pick_bank(), "US", "checking", opened, signer=signer)


def light_activity(w: World, acct: str, pid: str, start: date, end: date, n: int = 5) -> None:
    """A handful of small ordinary transactions so a fresh account does not look empty."""
    rng = w.rng
    p = w.P[pid]
    for _ in range(n):
        day = w.r_date(start, end)
        r = rng.random()
        if r < 0.4:
            w.add_txn(w.r_daytime(day, 7, 22), acct, None, rng.choice([40, 60, 80, 100, 200]), "cash_withdrawal",
                      "ATM", branch=w.branch_for(acct, p["_city"] if p["_city"] in w.city_state else "Austin"))
        elif r < 0.7:
            w.add_txn(w.r_daytime(day), acct, w.checking_of(rng.choice(w.infra["utility"])), rng.uniform(40, 160),
                      "bill_payment", "UTILITY")
        else:
            src = rng.choice([q for q in w.persons if w.checking_of(q["person_id"]) and q["person_id"] != pid][:400])
            w.add_txn(w.r_daytime(day), w.checking_of(src["person_id"]), acct, rng.choice([25, 40, 60, 100, 150]),
                      "p2p", rng.choice(["dinner", "thanks", "gas money", ""]))


def adult_customer(p: dict, lo: int = 21, hi: int = 70) -> bool:
    return (lo <= p["_age"] <= hi and bool(p.get("_customer")) and p["_city"] in CITY_SET
            and p.get("_status") != "child" and p.get("date_of_death") is None)


CITY_SET = {c for c, _s, _w in N.CITIES}


def ts_between(w: World, start: date, end: date) -> datetime:
    return w.r_daytime(w.r_date(start, end))


__all__ = ["field", "add_task", "answer_of", "fmt_money", "odd_amount", "shell_name", "foreign_person",
           "nominee_director", "offshore_shell", "us_shell", "new_personal_account", "light_activity",
           "adult_customer", "ts_between", "d2s", "money"]


def person_owners(w: World, bid: str, _depth: int = 0) -> list[str]:
    """Natural persons holding any equity/beneficial interest in ``bid``, directly or through entities."""
    out: list[str] = []
    if _depth > 6:
        return out
    for o in w.tables["ownership"]:
        if o["owned_id"] == bid and o["ownership_pct"] is not None:
            if o["owner_id"].startswith("P"):
                out.append(o["owner_id"])
            else:
                out.extend(person_owners(w, o["owner_id"], _depth + 1))
    return out


def find_officer(w: World, biz_ok, person_ok, titles=("CFO", "Procurement Director", "Controller")) -> tuple[str, str]:
    """An (business_id, person_id) officer pair; promotes an employee to officer if none qualifies."""
    rows = [o for o in w.tables["ownership"] if o["role"] == "officer" and o["title"] in titles
            and o["owner_id"] not in w.reserved and o["owned_id"] not in w.reserved
            and w.B[o["owned_id"]]["_infra"] is None and biz_ok(w.B[o["owned_id"]]) and person_ok(w.P[o["owner_id"]])]
    if rows:
        o = w.rng.choice(rows)
        return o["owned_id"], o["owner_id"]
    cands = [(b["business_id"], e) for b in w.businesses if b["business_id"] not in w.reserved and b["_infra"] is None
             and biz_ok(b) for e in b["_employees"] if e not in w.reserved and person_ok(w.P[e])]
    bid, pid = w.rng.choice(cands)
    title = w.rng.choice(list(titles))
    w.add_owner(pid, bid, "officer", None, w.r_date(date(2018, 1, 1), date(2023, 12, 31)), title=title)
    w.P[pid]["_title"] = (bid, title)
    return bid, pid


def remove_insider_conflicts(w: World, company: str, keep: list[str]) -> None:
    """Re-route background payments from ``company`` to vendors owned by its staff (or their spouses)."""
    staff = set(w.B[company]["_employees"])
    staff |= {o["owner_id"] for o in w.tables["ownership"] if o["owned_id"] == company and o["owner_id"].startswith("P")}
    staff |= {w.P[s]["_spouse"] for s in list(staff) if w.P[s]["_spouse"]}
    accts = set(w.accounts_of[company])
    holder = {a["account_id"]: a["holder_id"] for a in w.tables["accounts"]}
    others = [w.checking_of(b["business_id"]) for b in w.businesses if b["_infra"] is None and b.get("_size", 0) > 0
              and b["business_id"] != company and w.checking_of(b["business_id"])]
    for t in w.tables["transactions"]:
        if t["from_account"] in accts and t["to_account"]:
            h = holder[t["to_account"]]
            if h.startswith("B") and h not in keep and staff & set(person_owners(w, h)):
                t["from_account"] = w.rng.choice([a for a in others if a != t["to_account"]])
