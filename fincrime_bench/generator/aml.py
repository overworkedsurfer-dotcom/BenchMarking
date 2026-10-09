"""Anti-money-laundering schemes planted into the bank transaction data."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from . import names as N
from .common import (add_task, adult_customer, field, fmt_money, light_activity, new_personal_account, odd_amount,
                     offshore_shell, us_shell)
from .world import World, money


def _young(p: dict) -> bool:
    return adult_customer(p, 19, 32)


def _domestic_channel(w: World) -> str:
    return w.rng.choice(["wire", "ach", "wire"])


# =============================================================== LAYERING
def plant_layering(w: World, task_id: str, difficulty: str, n_hops: int, exit_kind: str, month: int,
                   split_at: int | None = None, owner_depth: int = 1, distractors: bool = False) -> None:
    rng = w.rng
    victims = [b for b in w.businesses if b["_infra"] is None and b.get("_size", 0) >= 8
               and b["_ind"] not in N.CASH_INTENSIVE and b["business_id"] not in w.reserved]
    victim = rng.choice(victims)
    w.reserve(victim["business_id"])
    origin = w.checking_of(victim["business_id"])
    start_day = date(2025, month, rng.randint(3, 20))
    t0 = w.r_daytime(start_day, 9, 16)
    amount0 = odd_amount(w, 48_000, 240_000)

    def mule_account(kind: str) -> tuple[str, str]:
        """Returns (account, controlling person)."""
        m = w.pick(_young)[0]
        if kind == "person":
            acct = new_personal_account(w, m["person_id"], start_day - timedelta(days=rng.randint(20, 120)))
            light_activity(w, acct, m["person_id"], date(2025, 1, 1), date(2025, 12, 20), rng.randint(3, 7))
        elif kind == "existing":
            acct = w.checking_of(m["person_id"])
        else:
            inc = start_day - timedelta(days=rng.randint(60, 300))
            _bid, acct = us_shell(w, [(m["person_id"], 100.0)], m["person_id"], inc,
                                  industry=rng.choice(["consulting", "retail", "logistics"]))
        return acct, m["person_id"]

    kinds = []
    for i in range(n_hops):
        if i == 1 and difficulty != "easy":
            kinds.append("existing")
        else:
            kinds.append(rng.choice(["person", "shell", "person"]))
    nodes: list[list[str]] = []
    holders: dict[str, str] = {}
    for i, k in enumerate(kinds):
        count = 2 if split_at is not None and i == split_at else 1
        node = []
        for _ in range(count):
            acct, holder = mule_account(k if count == 1 else "person")
            node.append(acct)
            holders[acct] = holder
        nodes.append(node)

    beneficiary = w.pick(lambda p: adult_customer(p, 30, 62))[0]["person_id"]
    decoy_people: list[str] = []
    exit_acct: str
    if exit_kind == "cash":
        # last hop account: shell LLC whose member of record is a mule, signer is the beneficiary
        m = w.pick(_young)[0]["person_id"]
        inc = start_day - timedelta(days=rng.randint(40, 200))
        _bid, last = us_shell(w, [(m, 100.0)], beneficiary, inc, industry="consulting")
        nodes.append([last])
        holders[last] = m
        decoy_people.append(m)
        exit_acct = last
    else:
        if owner_depth == 1:
            _sid, offshore = offshore_shell(w, [(beneficiary, 100.0)])
        else:
            inc2 = w.r_date(date(2018, 1, 1), date(2022, 1, 1))
            s2, _ = offshore_shell(w, [], cc="CY", inc=inc2, open_account=False)
            if distractors:
                ns = w.pick(lambda p: adult_customer(p, 30, 70))[0]["person_id"]
                w.add_owner(ns, s2, "shareholder", 100.0, inc2, end=date(2024, 3, 1))
                w.add_owner(beneficiary, s2, "shareholder", 100.0, date(2024, 3, 1))
                decoy_people.append(ns)
            else:
                w.add_owner(beneficiary, s2, "shareholder", 100.0, inc2)
            _sid, offshore = offshore_shell(w, [(s2, 100.0)], cc=rng.choice(["VG", "SC", "BZ"]))
        decoy_people.extend(w.nominees)
        exit_acct = offshore

    # ---- move the money
    trail_accounts: list[str] = []
    w.add_txn(t0, origin, nodes[0][0], amount0, "wire", f"INVOICE {rng.randint(1000, 9999)} PAYMENT")
    frontier = [(nodes[0][0], amount0, t0)]
    trail_accounts.append(nodes[0][0])
    decoy_accounts: list[str] = []
    for i in range(1, len(nodes)):
        nxt = nodes[i]
        new_frontier = []
        if len(nxt) == 2:
            (a, m, t), = frontier
            share = rng.uniform(0.4, 0.6)
            kept = m * (1 - rng.uniform(0.004, 0.02))
            for acct, part in ((nxt[0], share), (nxt[1], 1 - share)):
                ts = t + timedelta(hours=rng.uniform(1.5, 20))
                amt = money(kept * part)
                w.add_txn(ts, a, acct, amt, _domestic_channel(w), rng.choice(["", "services", "consulting"]))
                new_frontier.append((acct, amt, ts))
        else:
            dest = nxt[0]
            if len(frontier) == 2:
                parts = []
                for a, m, t in frontier:
                    ts = t + timedelta(hours=rng.uniform(2, 26))
                    amt = money(m * (1 - rng.uniform(0.004, 0.02)))
                    w.add_txn(ts, a, dest, amt, _domestic_channel(w), "")
                    parts.append((amt, ts))
                new_frontier = [(dest, money(sum(p[0] for p in parts)), max(p[1] for p in parts))]
            else:
                (a, m, t), = frontier
                ts = t + timedelta(hours=rng.uniform(2, 30))
                amt = money(m * (1 - rng.uniform(0.005, 0.025)))
                w.add_txn(ts, a, dest, amt, _domestic_channel(w), rng.choice(["", "loan repayment", "invoice"]))
                new_frontier = [(dest, amt, ts)]
        for a, m, t in frontier:
            for kind in ("before", "after"):
                if not distractors or rng.random() > (0.7 if kind == "before" else 0.6):
                    continue
                other = w.checking_of(rng.choice([p for p in w.persons if w.checking_of(p["person_id"])
                                                  and p["person_id"] not in w.reserved][:600])["person_id"])
                if kind == "before":  # unrelated money passing through earlier
                    tx, amt = t - timedelta(days=rng.uniform(1.0, 3.0)), money(m * rng.uniform(0.9, 0.99))
                else:  # partial payment out of the account's own unrelated funds
                    tx, amt = t + timedelta(hours=rng.uniform(3, 60)), money(m * rng.uniform(0.08, 0.25))
                funder = w.checking_of(rng.choice([b for b in w.businesses if b["_infra"] is None
                                                   and b.get("_size", 0) > 0])["business_id"])
                w.add_txn(tx - timedelta(hours=rng.uniform(5, 40)), funder, a, money(amt * rng.uniform(1.01, 1.08)),
                          rng.choice(["wire", "ach"]), rng.choice(["invoice", "services", "loan", ""]))
                w.add_txn(tx, a, other, amt, _domestic_channel(w), "")
                decoy_accounts.append(other)
        frontier = new_frontier
        trail_accounts.extend(nxt)

    (last_acct, m, t), = frontier
    if exit_kind == "cash":
        remaining = m * rng.uniform(0.965, 0.99)
        k = max(2, min(5, int(remaining // 40_000) + 1))
        ts = t
        branch_city = rng.choice(list(w.city_state))
        for j in range(k):
            ts = ts + timedelta(hours=rng.uniform(2, 20))
            w.add_txn(ts, last_acct, None, money(remaining / k), "cash_withdrawal", "",
                      branch=w.branch_for(last_acct, branch_city), conducted_by=beneficiary)
    else:
        ts = t + timedelta(hours=rng.uniform(3, 30))
        w.add_txn(ts, last_acct, exit_acct, money(m * rng.uniform(0.975, 0.995)), "international_wire",
                  rng.choice(["TRADE SETTLEMENT", "INVESTMENT", "SERVICES AGREEMENT"]))
        trail_accounts.append(exit_acct)

    first_txn = [t for t in w.tables["transactions"] if t["from_account"] == origin and t["to_account"] == nodes[0][0]][-1]
    prompt = f"""
Business customer {victim['name']} ({victim['business_id']}) reports that payment {first_txn['txn_id']} — \
{fmt_money(amount0)} wired from its account {origin} on {start_day.isoformat()} — went to a fraudster after a \
business email compromise. The fraudsters moved the money onward quickly.

Trace the stolen funds forward through the transaction records until they either leave the domestic banking \
system (an international wire) or are withdrawn in cash. Funds may be split across accounts and recombined, and \
each intermediary typically keeps a small cut.

Report:
- accounts: every account that received the stolen funds after they left {origin} — the first recipient, all \
intermediaries, and (if the funds left the country) the foreign destination account.
- exit_account: the last account on the trail (the foreign destination account, or the account the cash was \
withdrawn from).
- beneficiary_person_id: the natural person who ultimately receives the benefit of the funds at the exit — for \
a cash exit, whoever collected the cash; for a foreign account, the ultimate beneficial owner of the account \
holder (not a director or other nominee).
"""
    fields = [
        field("accounts", "id_set", 0.5, "All account_ids that received the traced funds (order not scored)."),
        field("exit_account", "id", 0.2, "account_id where the trail exits."),
        field("beneficiary_person_id", "id", 0.3, "person_id of the ultimate beneficiary."),
    ]
    answer = {"accounts": trail_accounts, "exit_account": exit_acct, "beneficiary_person_id": beneficiary}
    add_task(w, task_id, "aml", "layering", difficulty, "Trace business-email-compromise proceeds", prompt, fields,
             answer, inputs={"txn_id": first_txn["txn_id"], "origin_account": origin, "exit_kind": exit_kind},
             decoys={"beneficiary_person_id": decoy_people, "accounts": decoy_accounts},
             notes=f"{len(trail_accounts)} accounts, exit={exit_kind}, split={split_at is not None}")


# ============================================================ STRUCTURING
def plant_structuring(w: World, task_id: str) -> None:
    rng = w.rng
    positives = w.pick(lambda p: adult_customer(p, 24, 64), 4)
    w.expect(task_id, "person_ids", [p["person_id"] for p in positives])
    for i, p in enumerate(positives):
        pid = p["person_id"]
        acct = w.checking_of(pid)
        accts = [acct]
        if i == 1:
            accts.append(new_personal_account(w, pid, date(2025, 1, 10)))
        start = w.r_date(date(2025, 2, 1), date(2025, 10, 1))
        n = rng.randint(7, 12)
        day = start
        cities = [p["_city"]]
        for _ in range(n):
            day = day + timedelta(days=rng.choice([1, 1, 2, 3, 4, 6]))
            a = rng.choice(accts)
            amt = money(rng.uniform(8_050, 9_950))
            w.add_txn(w.r_daytime(day, 9, 17), None, a, amt, "cash_deposit", "",
                      branch=w.branch_for(a, rng.choice(cities)), conducted_by=pid)
    # decoys: large reportable cash depositors and one-off near-threshold deposits
    big = w.pick(lambda p: adult_customer(p, 30, 70), 2)
    for p in big:
        acct = w.checking_of(p["person_id"])
        for _ in range(rng.randint(3, 5)):
            day = w.r_date(date(2025, 1, 15), date(2025, 12, 10))
            w.add_txn(w.r_daytime(day, 9, 17), None, acct, money(rng.uniform(12_500, 34_000)), "cash_deposit", "",
                      branch=w.branch_for(acct, p["_city"]), conducted_by=p["person_id"])
    oneoff = w.pick(lambda p: adult_customer(p, 25, 75), 3)
    for j, p in enumerate(oneoff):
        acct = w.checking_of(p["person_id"])
        k = 2 if j == 2 else 1
        for d in range(k):
            day = date(2025, 2 + d * 5, rng.randint(1, 27))
            w.add_txn(w.r_daytime(day, 9, 17), None, acct, money(rng.uniform(9_100, 9_900)), "cash_deposit",
                      "vehicle sale" if j == 0 else "", branch=w.branch_for(acct, p["_city"]),
                      conducted_by=p["person_id"])
    decoys = [p["person_id"] for p in big + oneoff]

    def gold() -> dict:
        return {"person_ids": structuring_rule(w)}

    prompt = """
Banks must file a Currency Transaction Report (CTR) for cash transactions over $10,000. Structuring — deliberately \
breaking cash into amounts below the threshold to avoid a CTR — is a crime regardless of the source of the money.

Review all 2025 cash deposits and identify every person who conducted structured cash deposits, whether into \
their own accounts or into someone else's. A single near-threshold deposit is not structuring by itself, and \
large deposits that were reported are not structuring.

Report person_ids: the person_id of each person who conducted structured deposits.
"""
    fields = [field("person_ids", "id_set", 1.0, "person_ids of everyone who structured cash deposits.")]
    add_task(w, task_id, "aml", "structuring", "medium", "Find structured cash deposits", prompt, fields,
             None, decoys={"person_ids": decoys}, deferred=gold,
             notes="Rule: >=3 cash deposits in [7000,10000) conducted by the same person within 30 days.")


def structuring_rule(w: World) -> list[str]:
    by_person: dict[str, list[datetime]] = {}
    for t in w.tables["transactions"]:
        if t["channel"] == "cash_deposit" and t["conducted_by"] and 7_000 <= t["amount"] < 10_000:
            by_person.setdefault(t["conducted_by"], []).append(t["_ts"])
    out = []
    for pid, times in by_person.items():
        times.sort()
        for i in range(len(times)):
            j = i
            while j < len(times) and (times[j] - times[i]).days <= 30:
                j += 1
            if j - i >= 3:
                out.append(pid)
                break
    return sorted(out)


# ================================================================ SMURFING
def plant_smurfing(w: World, task_id: str, structuring_task_id: str = "aml_structuring_01") -> None:
    rng = w.rng
    boss = w.pick(lambda p: adult_customer(p, 30, 60))[0]
    relatives = [r for r in boss["_children"] + boss["_parents"] + [boss["_spouse"]] if r and r not in w.reserved
                 and w.P[r]["_age"] >= 21]
    nominee = relatives[0] if relatives else w.pick(lambda p: adult_customer(p, 21, 70))[0]["person_id"]
    w.reserve(nominee)
    inc = date(2024, rng.randint(3, 10), rng.randint(1, 28))
    shell_bid, acct = us_shell(w, [(nominee, 100.0)], boss["person_id"], inc,
                               industry=rng.choice(["retail", "logistics", "consulting"]))
    city = boss["_city"]
    smurfs = w.pick(lambda p: adult_customer(p, 19, 38) and p["_city"] == city, rng.randint(5, 6))
    w.expect(structuring_task_id, "person_ids", [s["person_id"] for s in smurfs])
    start = w.r_date(date(2025, 4, 1), date(2025, 8, 1))
    total = 0.0
    for s in smurfs:
        for _ in range(rng.randint(3, 4)):
            day = start + timedelta(days=rng.randint(0, 24))
            amt = money(rng.uniform(7_300, 9_900))
            total += amt
            w.add_txn(w.r_daytime(day, 9, 17), None, acct, amt, "cash_deposit", "",
                      branch=w.branch_for(acct, city), conducted_by=s["person_id"])
    # legit-looking inflow and outflows to the controller
    w.add_txn(w.r_daytime(start - timedelta(days=20)), w.checking_of(rng.choice(w.infra["auto_dealer"])), acct,
              odd_amount(w, 4_000, 9_000), "check", "VEHICLE CONSIGNMENT")
    out_total = total * 0.92
    for k in range(3):
        w.add_txn(w.r_daytime(start + timedelta(days=26 + 4 * k)), acct, w.checking_of(boss["person_id"]),
                  money(out_total / 3), "ach", "OWNER DRAW")
    prompt = f"""
Account {acct} (held by {w.B[shell_bid]['name']}, {shell_bid}) was flagged after a teller noticed several \
different people depositing cash into it.

Identify the individuals who structured cash deposits into this account, and the natural person who controls \
and benefits from the account.

Report:
- smurf_person_ids: person_ids of the individuals who made the structured cash deposits.
- controller_person_id: person_id of the person who controls and benefits from the account.
"""
    fields = [
        field("smurf_person_ids", "id_set", 0.6, "person_ids of the depositors (smurfs)."),
        field("controller_person_id", "id", 0.4, "person_id of the controller/beneficiary."),
    ]
    add_task(w, task_id, "aml", "smurfing", "easy", "Smurfing into a flagged account", prompt, fields,
             {"smurf_person_ids": [s["person_id"] for s in smurfs], "controller_person_id": boss["person_id"]},
             inputs={"account_id": acct}, decoys={"controller_person_id": [nominee]},
             notes="Controller is the authorized signer who receives the owner draws; member of record is a nominee.")


# ================================================================ MULE RING
def plant_mule_ring(w: World, task_id: str, difficulty: str, link: str, month: int) -> None:
    rng = w.rng
    if link == "ip":
        ctrl = w.pick(lambda p: adult_customer(p, 24, 50) and p["_online"] and len(w.hh_members[p["_hh"]]) == 1)[0]
    else:
        ctrl = w.pick(lambda p: adult_customer(p, 24, 50) and p["_online"])[0]
    cid = ctrl["person_id"]
    start = date(2025, month, rng.randint(1, 10))
    end = start + timedelta(days=rng.randint(38, 55))
    shared_device = w.new_device()
    collector = w.pick(_young)[0]["person_id"]
    k_acct = new_personal_account(w, collector, start - timedelta(days=rng.randint(30, 90)))
    light_activity(w, k_acct, collector, date(2025, 1, 1), date(2025, 12, 20), 3)
    _sid, offshore = offshore_shell(w, [(foreign_owner(w), 100.0)], cc=rng.choice(["AE", "CY", "PA"]))
    n_mules = rng.randint(4, 6)
    mules = w.pick(_young, n_mules)
    mule_accts = []
    for m in mules:
        a = new_personal_account(w, m["person_id"], start - timedelta(days=rng.randint(15, 110)))
        light_activity(w, a, m["person_id"], date(2025, 1, 1), date(2025, 12, 20), rng.randint(2, 5))
        mule_accts.append(a)
        # small legit transfers from family into the mule account (not scam money)
        fam = [x for x in w.hh_members[m["_hh"]] if x != m["person_id"] and w.checking_of(x)]
        for f in fam[:1]:
            w.add_txn(w.r_daytime(w.r_date(start, end)), w.checking_of(f), a, rng.choice([50, 80, 120, 200]),
                      "p2p", "for books")
        # mules log in from their own phone occasionally
        own_dev = w.new_device()
        for _ in range(rng.randint(1, 3)):
            ts = w.r_dt(datetime(2025, 1, 1), datetime(2025, 12, 31))
            w.add_login(ts, m["person_id"], own_dev, w.mobile_ip(), "US", "login")
    victims = w.pick(lambda p: adult_customer(p, 66, 92) and p["_online"], rng.randint(6, 8))
    mule_devices = {m["person_id"]: w.new_device() for m in mules}

    def op_session(pid: str, ts: datetime, event: str, txn: str | None = None) -> None:
        if link == "device":
            w.add_login(ts, pid, shared_device, w.mobile_ip(), "US", event, txn)
        else:
            dev = mule_devices.get(pid) or shared_device
            w.add_login(ts, pid, dev, ctrl["_home_ip"], "US", event, txn)

    victim_txns = []
    inflow_k = []
    plan = [v for v in victims for _ in range(rng.randint(1, 3))]
    rng.shuffle(plan)
    for k, v in enumerate(plan):
        vacct = w.checking_of(v["person_id"])
        mi = k if k < len(mules) else rng.randrange(len(mules))  # every mule receives victim money
        ts = w.r_daytime(w.r_date(start, end - timedelta(days=4)), 9, 19)
        amt = odd_amount(w, 1_800, 14_500)
        tid = w.add_txn(ts, vacct, mule_accts[mi], amt, rng.choice(["p2p", "wire"]),
                        rng.choice(["investment", "customs fee", "for you", "loan", "release fee"]))
        if v["_online"]:
            w.add_login(ts - timedelta(minutes=rng.randint(2, 15)), v["person_id"], v["_devices"][0],
                        v["_home_ip"], "US", "add_payee")
            w.add_login(ts, v["person_id"], v["_devices"][0], v["_home_ip"], "US", "transfer", tid)
        victim_txns.append((tid, v["person_id"], amt, ts))
        fts = ts + timedelta(hours=rng.uniform(0.7, 20))
        famt = money(amt * rng.uniform(0.86, 0.93))
        ftid = w.add_txn(fts, mule_accts[mi], k_acct, famt, "p2p", "")
        op_session(mules[mi]["person_id"], fts - timedelta(minutes=rng.randint(1, 6)), "login")
        op_session(mules[mi]["person_id"], fts, "transfer", ftid)
        inflow_k.append((famt, fts))
    # collector forwards weekly offshore
    inflow_k.sort(key=lambda x: x[1])
    wk_start = start + timedelta(days=7)
    pending = 0.0
    idx = 0
    while wk_start <= end + timedelta(days=10):
        while idx < len(inflow_k) and inflow_k[idx][1].date() < wk_start:
            pending += inflow_k[idx][0]
            idx += 1
        if pending > 1_000:
            ts = w.r_daytime(wk_start, 10, 15)
            tid = w.add_txn(ts, k_acct, offshore, money(pending * 0.96), "international_wire", "TRADE SETTLEMENT")
            op_session(collector, ts, "transfer", tid)
            pending = 0.0
        wk_start += timedelta(days=7)
    # the controller uses the same device / IP for their own banking
    for _ in range(rng.randint(3, 5)):
        ts = w.r_dt(datetime(start.year, start.month, start.day), datetime(end.year, end.month, end.day))
        if link == "device":
            w.add_login(ts, cid, shared_device, w.mobile_ip(), "US", "login")
        else:
            w.add_login(ts, cid, ctrl["_devices"][0], ctrl["_home_ip"], "US", "login")

    first = victim_txns[0]
    v0 = w.P[first[1]]
    first_mule = w.txn_by_id[first[0]]["to_account"]
    prompt = f"""
Customer {w.full_name(v0['person_id'])} ({v0['person_id']}), age {v0['_age']}, reported being the victim of an \
online romance/investment scam. On {first[3].date().isoformat()} they sent {fmt_money(first[2])} \
(transaction {first[0]}) to account {first_mule}.

Investigate the money-mule network behind this account using transactions and the online-banking audit log.

Report:
- mule_accounts: account_ids of all first-tier mule accounts (accounts that received money directly from scam \
victims and passed it on); do not include the collection account.
- collector_account: the domestic account into which the mule accounts consolidated the proceeds.
- controller_person_id: the natural person operating the mule network — the person controlling the mule \
accounts' online banking, who is not one of the recruited account holders.
- victim_person_ids: person_ids of all scam victims who sent money into the mule accounts (not people making \
small unrelated transfers).
"""
    fields = [
        field("mule_accounts", "id_set", 0.35, "account_ids of the mule accounts."),
        field("collector_account", "id", 0.15, "account_id of the collection account."),
        field("controller_person_id", "id", 0.3, "person_id of the operator."),
        field("victim_person_ids", "id_set", 0.2, "person_ids of all victims."),
    ]
    answer = {"mule_accounts": mule_accts, "collector_account": k_acct, "controller_person_id": cid,
              "victim_person_ids": sorted({v[1] for v in victim_txns})}
    add_task(w, task_id, "aml", "mule_ring", difficulty, "Unmask a money-mule network", prompt, fields, answer,
             inputs={"txn_id": first[0]},
             decoys={"controller_person_id": [collector] + [m["person_id"] for m in mules]},
             notes=f"controller linked via shared {link}")


# ========================================================= ACCOUNT TAKEOVER
def plant_ato(w: World, task_id: str) -> None:
    rng = w.rng
    victims = w.pick(lambda p: adult_customer(p, 30, 85) and p["_online"] and p["_salary"] > 35_000, 3)
    w.expect(task_id, "victim_person_ids", [v["person_id"] for v in victims])
    att_devices = [w.new_device(), w.new_device()]
    for i, v in enumerate(victims):
        pid = v["person_id"]
        vacct = w.checking_of(pid)
        dev = att_devices[0] if i < 2 else att_devices[1]
        cc = rng.choice(N.ATTACKER_COUNTRIES)
        ip = w.foreign_ip()
        t = w.r_dt(datetime(2025, 2, 1), datetime(2025, 11, 20))
        w.add_login(t, pid, dev, ip, cc, "password_reset")
        step = t + timedelta(minutes=rng.randint(1, 9), seconds=rng.randint(0, 59))
        w.add_login(step, pid, dev, ip, cc, "login")
        if rng.random() < 0.6:
            step += timedelta(minutes=rng.randint(1, 12), seconds=rng.randint(0, 59))
            w.add_login(step, pid, dev, ip, cc, "change_phone")
        mule = w.pick(_young)[0]
        macct = new_personal_account(w, mule["person_id"], t.date() - timedelta(days=rng.randint(10, 60)))
        light_activity(w, macct, mule["person_id"], date(2025, 1, 1), date(2025, 12, 20), 3)
        step += timedelta(minutes=rng.randint(2, 25), seconds=rng.randint(0, 59))
        w.add_login(step, pid, dev, ip, cc, "add_payee")
        k = rng.choice([1, 2])
        ts = step + timedelta(minutes=rng.randint(1, 15), seconds=rng.randint(0, 59))
        for _ in range(k):
            amt = odd_amount(w, 3_800, 19_000)
            tid = w.add_txn(ts, vacct, macct, amt, rng.choice(["wire", "p2p"]), "")
            w.add_login(ts, pid, dev, ip, cc, "transfer", tid)
            w.expect(task_id, "fraudulent_txn_ids", [tid])
            ts += timedelta(minutes=rng.randint(5, 40))
        w.add_txn(ts + timedelta(hours=rng.uniform(1, 5)), macct, None, money(amt * 0.9), "cash_withdrawal", "ATM",
                  branch=w.branch_for(macct, mule["_city"]))
    # decoy A: new phone + password reset + new payee + large transfer to a car dealer, from home IP
    d = w.pick(lambda p: adult_customer(p, 28, 70) and p["_online"] and p["_salary"] > 60_000)[0]
    nd = w.new_device()
    t = w.r_dt(datetime(2025, 3, 1), datetime(2025, 10, 1))
    w.add_login(t, d["person_id"], nd, d["_home_ip"], "US", "password_reset")
    w.add_login(t + timedelta(minutes=10), d["person_id"], nd, d["_home_ip"], "US", "add_payee")
    dealer = rng.choice(w.infra["auto_dealer"])
    price = odd_amount(w, 16_000, 24_000)
    tid = w.add_txn(t + timedelta(minutes=14), w.checking_of(d["person_id"]), w.checking_of(dealer), price, "wire",
                    "VEHICLE PURCHASE")
    w.add_login(t + timedelta(minutes=14), d["person_id"], nd, d["_home_ip"], "US", "transfer", tid)
    w.add_asset("vehicle", w._vehicle_desc(price), d["person_id"], t.date(), price, "cash")
    for _ in range(3):
        ts = w.r_dt(t + timedelta(days=2), t + timedelta(days=60))
        w.add_login(ts, d["person_id"], nd, d["_home_ip"], "US", "login")
    # decoy B: traveller on usual device abroad pays a new payee
    tr = w.pick(lambda p: adult_customer(p, 25, 70) and p["_online"])[0]
    friend = w.pick(lambda p: adult_customer(p, 25, 70))[0]
    t = w.r_dt(datetime(2025, 5, 1), datetime(2025, 9, 1))
    cc = rng.choice(N.FOREIGN_TRAVEL_COUNTRIES)
    w.add_login(t, tr["person_id"], tr["_devices"][0], w.foreign_ip(), cc, "login")
    w.add_login(t + timedelta(minutes=4), tr["person_id"], tr["_devices"][0], w.foreign_ip(), cc, "add_payee")
    tid = w.add_txn(t + timedelta(minutes=6), w.checking_of(tr["person_id"]), w.checking_of(friend["person_id"]),
                    odd_amount(w, 300, 900), "p2p", "villa share")
    w.add_login(t + timedelta(minutes=6), tr["person_id"], tr["_devices"][0], w.foreign_ip(), cc, "transfer", tid)

    def gold() -> dict:
        txns, victims_ = ato_rule(w)
        return {"fraudulent_txn_ids": txns, "victim_person_ids": victims_}

    prompt = """
Several customers may have had their online banking taken over in 2025: a criminal gains access to the \
customer's credentials and moves money out to an account they control.

Using the online-banking audit log together with the transaction records, identify every fraudulent transfer \
resulting from an account takeover, and the customers who were victimised. Be careful not to flag customers \
who were merely travelling, changed phones, or made a legitimate large purchase. Money-mule accounts operated \
with their holders' cooperation are a different typology and are not account takeovers.

Report:
- fraudulent_txn_ids: txn_ids of all transfers made by account-takeover attackers.
- victim_person_ids: person_ids of the customers whose accounts were taken over.
"""
    fields = [
        field("fraudulent_txn_ids", "id_set", 0.6, "txn_ids of the fraudulent transfers."),
        field("victim_person_ids", "id_set", 0.4, "person_ids of the takeover victims."),
    ]
    add_task(w, task_id, "aml", "account_takeover", "medium", "Detect account takeovers", prompt, fields, None,
             decoys={"victim_person_ids": [d["person_id"], tr["person_id"]]}, deferred=gold,
             notes="Rule: transfer from a device first seen for that customer within 24h, foreign IP, "
                   "session preceded by password_reset.")


def ato_rule(w: World) -> tuple[list[str], list[str]]:
    first_seen: dict[tuple[str, str], datetime] = {}
    resets: dict[tuple[str, str], list[datetime]] = {}
    for e in sorted(w.tables["logins"], key=lambda e: e["_ts"]):
        key = (e["person_id"], e["device_id"])
        first_seen.setdefault(key, e["_ts"])
        if e["event_type"] == "password_reset":
            resets.setdefault(key, []).append(e["_ts"])
    txns, victims = [], []
    for e in w.tables["logins"]:
        if e["event_type"] != "transfer" or e["ip_country"] == "US":
            continue
        key = (e["person_id"], e["device_id"])
        fresh = (e["_ts"] - first_seen[key]) <= timedelta(hours=24)
        reset = any(timedelta(0) <= e["_ts"] - r <= timedelta(hours=24) for r in resets.get(key, []))
        if fresh and reset:
            txns.append(e["txn_id"])
            if e["person_id"] not in victims:
                victims.append(e["person_id"])
    return sorted(txns), sorted(victims)


# ============================================================= ROUND TRIP
def plant_round_trip(w: World, task_id: str) -> None:
    rng = w.rng
    cands = [b for b in w.businesses if b["_infra"] is None and b.get("_size", 0) >= 6
             and b["_ind"] in ("construction", "logistics", "manufacturing", "software")
             and b["business_id"] not in w.reserved
             and any(o.startswith("P") and pct >= 50 for o, pct in b["_owners"])]
    x = rng.choice(cands)
    w.reserve(x["business_id"])
    owner = [o for o, pct in x["_owners"] if o.startswith("P") and pct >= 50][0]
    w.reserve(owner)
    xacct = w.checking_of(x["business_id"])
    sib = [r for r in w.P[owner]["_children"] + w.P[owner]["_parents"] + [w.P[owner]["_spouse"]]
           if r and r not in w.reserved and w.P[r]["_age"] >= 21]
    nominee = sib[0] if sib else w.pick(lambda p: adult_customer(p, 25, 70))[0]["person_id"]
    w.reserve(nominee)
    s1, a1 = us_shell(w, [(nominee, 100.0)], nominee, date(2024, rng.randint(2, 8), rng.randint(1, 28)),
                      industry="consulting")
    inc = w.r_date(date(2020, 1, 1), date(2023, 6, 1))
    s2, a2 = offshore_shell(w, [(owner, 100.0)], cc="CY", inc=inc)
    s3, a3 = offshore_shell(w, [(s2, 100.0)], cc="VG", inc=inc + timedelta(days=90))
    amt = odd_amount(w, 280_000, 620_000)
    t = w.r_daytime(w.r_date(date(2025, 3, 1), date(2025, 6, 30)), 10, 15)
    w.add_txn(t, xacct, a1, amt, "wire", "CONSULTING SERVICES Q2")
    t2 = t + timedelta(days=rng.uniform(1, 3))
    amt2 = money(amt * rng.uniform(0.975, 0.99))
    w.add_txn(t2, a1, a2, amt2, "international_wire", "ADVISORY FEE")
    t3 = t2 + timedelta(days=rng.uniform(2, 6))
    amt3 = money(amt2 * rng.uniform(0.98, 0.995))
    w.add_txn(t3, a2, a3, amt3, "international_wire", "INTERCOMPANY")
    t4 = t3 + timedelta(days=rng.uniform(3, 9))
    amt4 = money(amt3 * rng.uniform(0.985, 0.998))
    tid = w.add_txn(t4, a3, xacct, amt4, "international_wire", "EQUITY INVESTMENT - SHARE SUBSCRIPTION")
    # noise on the shells
    w.add_txn(t2 + timedelta(days=1), w.checking_of(rng.choice(w.infra["utility"])), a1, odd_amount(w, 50, 300), "ach",
              "REFUND")
    _, other_off = offshore_shell(w, [(foreign_owner(w), 100.0)], cc="CY")
    w.add_txn(t3 - timedelta(days=1), other_off, a2, money(amt3 * rng.uniform(0.3, 0.5)), "international_wire",
              "LOAN")
    prompt = f"""
{x['name']} ({x['business_id']}) included in a 2025 bank loan application an "equity investment" of \
{fmt_money(amt4)} received on {t4.date().isoformat()} (transaction {tid}) from \
{w.B[s3]['name']} ({s3}), an offshore investor.

Determine whether the investment money actually originated from {x['name']} itself (round-tripping).

Report:
- round_trip: true if the funds originated from the company itself, else false.
- cycle_accounts: if it is a round trip, every account the money passed through, including the company's own \
account {xacct}.
- investor_ubo_person_id: the natural person who is the ultimate beneficial owner of the investor {s3}.
"""
    fields = [
        field("round_trip", "bool", 0.2, "true/false."),
        field("cycle_accounts", "id_set", 0.5, "account_ids in the cycle (including the company's account)."),
        field("investor_ubo_person_id", "id", 0.3, "person_id of the investor's ultimate beneficial owner."),
    ]
    add_task(w, task_id, "aml", "round_trip", "hard", "Is this investment round-tripped?", prompt, fields,
             {"round_trip": True, "cycle_accounts": [xacct, a1, a2, a3], "investor_ubo_person_id": owner},
             inputs={"txn_id": tid}, decoys={"investor_ubo_person_id": [nominee] + w.nominees},
             notes="X -> US shell (nominee member) -> CY shell (owned by X's owner) -> BVI shell (owned by CY) -> X")


def foreign_owner(w: World) -> str:
    from .common import foreign_person
    return foreign_person(w, w.rng.choice(["CY", "AE", "PA"]))
