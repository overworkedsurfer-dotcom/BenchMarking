"""Telecom schemes: contact chaining to a hidden boss and burner-phone switches."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from .common import add_task, adult_customer, field
from .world import World, d2s


def _single_registered_phone(w: World, p: dict) -> bool:
    if len(p["_phones"]) != 1:
        return False
    ph = w.PHONE[p["_phones"][0]]
    return ph["subscriber_id"] == p["person_id"] and ph["_deact"] is None


def _phone_ok(w: World, lo: int = 20, hi: int = 60):
    return lambda p: adult_customer(p, lo, hi) and _single_registered_phone(w, p)


def _rand_ts(w: World, start: date, end: date) -> datetime:
    return w.r_daytime(w.r_date(start, end), 8, 23)


def _contact_phones(w: World, pid: str, when: date) -> list[str]:
    out = []
    for (a, b), _r in w.call_edges.items():
        other = b if a == pid else a if b == pid else None
        if other:
            ph = w.active_phone(other, when)
            if ph:
                out.append(ph)
    return out


# ============================================================ CONTACT CHAIN
def plant_contact_chain(w: World, task_id: str, difficulty: str) -> None:
    rng = w.rng
    crew = []
    households: list[int] = []
    while len(crew) < 4:
        p = w.pick(_phone_ok(w, 20, 45))[0]
        if p["_hh"] in households:
            continue
        households.append(p["_hh"])
        crew.append(p)
    boss = w.pick(lambda p: _phone_ok(w, 30, 62)(p) and p["_hh"] not in households)[0]
    city = boss["_city"]
    start, end = date(2025, rng.randint(1, 3), rng.randint(1, 28)), date(2025, rng.randint(10, 12), rng.randint(1, 28))
    handler = w.new_phone(None, "prepaid", start - timedelta(days=rng.randint(3, 20)))
    handler_tower = w.tower_in(city)
    crew_call_times: list[datetime] = []
    for c in crew:
        cp = c["_phones"][0]
        for _ in range(rng.randint(18, 34)):
            ts = _rand_ts(w, start, end)
            if rng.random() < 0.5:
                w.add_call(ts, cp, handler, tower=w.tower_in(c["_city"]))
            else:
                w.add_call(ts, handler, cp, tower=handler_tower if rng.random() < 0.7 else w.tower_in(city))
            crew_call_times.append(ts)
    boss_phone = boss["_phones"][0]
    decoys: dict[str, list[str]] = {}
    if difficulty == "medium":
        for _ in range(rng.randint(26, 40)):
            base = rng.choice(crew_call_times)
            ts = base + timedelta(minutes=rng.randint(5, 40)) if rng.random() < 0.6 else _rand_ts(w, start, end)
            if rng.random() < 0.55:
                w.add_call(ts, handler, boss_phone, tower=handler_tower)
            else:
                w.add_call(ts, boss_phone, handler, tower=w.tower_in(city))
        target_phone = boss_phone
    else:
        burner = w.new_phone(None, "prepaid", start - timedelta(days=rng.randint(1, 15)))
        partners = _contact_phones(w, boss["person_id"], start) or [rng.choice(list(w.PHONE))]
        for _ in range(rng.randint(32, 44)):
            base = rng.choice(crew_call_times)
            ts = base + timedelta(minutes=rng.randint(5, 50)) if rng.random() < 0.6 else _rand_ts(w, start, end)
            if rng.random() < 0.75:
                tower = w.tower_in(city)
                w.add_call(ts, burner, handler, tower=tower)
                if rng.random() < 0.8:
                    delta = timedelta(minutes=rng.choice([-1, 1]) * rng.randint(2, 14))
                    when = ts + delta
                    callee = rng.choice(partners)
                    if callee != boss_phone and w.PHONE[callee]["_act"] <= when.date():
                        w.add_call(when, boss_phone, callee, tower=tower)
            else:
                w.add_call(ts, handler, burner, tower=handler_tower)
        target_phone = burner
    # handler also has a few unrelated contacts
    noise = [p for p in w.persons if p["_phones"] and p["person_id"] not in w.reserved and p["_age"] >= 18]
    noise_ids = []
    for p in rng.sample(noise, 3):
        noise_ids.append(p["person_id"])
        for _ in range(rng.randint(1, 3)):
            ts = _rand_ts(w, start, end)
            ph = w.active_phone(p["person_id"], ts.date())
            if ph:
                w.add_call(ts, handler, ph, tower=handler_tower)
    decoys["boss_person_id"] = noise_ids
    names = ", ".join(f"{w.full_name(c['person_id'])} ({c['person_id']})" for c in crew)
    if difficulty == "medium":
        prompt = f"""
A check-fraud crew has been identified: {names}.

Using call detail records (contact chaining), identify:
- handler_phone: the phone number that coordinates the crew (the number all crew members are in contact with).
- boss_person_id: the person the coordinator reports to.
"""
        fields = [
            field("handler_phone", "id", 0.4, "phone_number of the coordinator."),
            field("boss_person_id", "id", 0.6, "person_id of the boss."),
        ]
        answer = {"handler_phone": handler, "boss_person_id": boss["person_id"]}
    else:
        prompt = f"""
A check-fraud crew has been identified: {names}.

The crew is directed by a coordinator who in turn takes instructions from a boss. The boss is believed to use an \
anonymous prepaid phone for crime business while carrying their personal phone at the same time.

Using call detail records, identify:
- handler_phone: the coordinator's phone number (the number all crew members are in contact with).
- boss_phone: the boss's anonymous phone number.
- boss_person_id: the person who is the boss.
"""
        fields = [
            field("handler_phone", "id", 0.25, "phone_number of the coordinator."),
            field("boss_phone", "id", 0.25, "phone_number of the boss's anonymous phone."),
            field("boss_person_id", "id", 0.5, "person_id of the boss."),
        ]
        answer = {"handler_phone": handler, "boss_phone": target_phone, "boss_person_id": boss["person_id"]}
    add_task(w, task_id, "telecom", "contact_chaining", difficulty, "Contact chaining to the boss", prompt, fields,
             answer, inputs={"crew": [c["person_id"] for c in crew]}, decoys=decoys,
             notes="hard: boss identified by co-location of the burner with the boss's personal phone (same tower, "
                   "minutes apart).")


# ================================================================== BURNER
def _rewrite_calls(w: World, old: str, new: str, since: date) -> int:
    n = 0
    for c in w.tables["calls"]:
        if c["_ts"].date() >= since:
            if c["caller"] == old:
                c["caller"] = new
                n += 1
            if c["callee"] == old:
                c["callee"] = new
                n += 1
    return n


def _switch(w: World, pid: str, when: date) -> str:
    p = w.P[pid]
    old = [n for n in p["_phones"] if w.PHONE[n]["_deact"] is None][-1]
    new = w.new_phone(None, "prepaid", when, None, user=pid)
    w.PHONE[old]["_deact"] = when
    w.PHONE[old]["deactivation_date"] = d2s(when)
    _rewrite_calls(w, old, new, when)
    return new


def _partner_stats(w: World, phone: str) -> dict[str, list[datetime]]:
    out: dict[str, list[datetime]] = defaultdict(list)
    for c in w.tables["calls"]:
        if c["caller"] == phone:
            out[c["callee"]].append(c["_ts"])
        elif c["callee"] == phone:
            out[c["caller"]].append(c["_ts"])
    return out


def plant_burner(w: World, task_id: str, difficulty: str) -> None:
    rng = w.rng
    for _attempt in range(200):
        s = w.pick(_phone_ok(w, 22, 60), reserve=False)[0]
        stats = _partner_stats(w, s["_phones"][0])
        d1 = date(2025, rng.randint(6, 7) if difficulty == "medium" else 5, rng.randint(1, 28))
        before = [k for k, ts in stats.items() if any(t.date() < d1 for t in ts)]
        after_calls = sum(1 for ts in stats.values() for t in ts if t.date() >= d1)
        rels = [r for r in s["_children"] + s["_parents"] if r not in w.reserved and _single_registered_phone(w, w.P[r])
                and w.P[r]["_hh"] != s["_hh"]]
        if len(before) >= 7 and after_calls >= 16 and rels:
            break
    else:
        raise RuntimeError("no burner candidate")
    w.reserve(s["person_id"])
    x1 = s["_phones"][0]
    # the suspect's inner circle keeps calling them throughout the year (and follows them to new numbers)
    partners = [(rate, b if a == s["person_id"] else a) for (a, b), rate in w.call_edges.items()
                if s["person_id"] in (a, b)]
    core = [pid for _r, pid in sorted(partners, key=lambda x: (-x[0], x[1])) if w.P[pid]["_phones"]][:6]
    for pid in core:
        for _ in range(rng.randint(9, 15)):
            ts = _rand_ts(w, date(2025, 1, 5), date(2025, 12, 28))
            mine, theirs = w.active_phone(s["person_id"], ts.date()), w.active_phone(pid, ts.date())
            if not mine or not theirs:
                continue
            if rng.random() < 0.5:
                w.add_call(ts, mine, theirs, tower=w.tower_in(s["_city"]))
            else:
                w.add_call(ts, theirs, mine, tower=w.tower_in(w.P[pid]["_city"]))
    x2 = _switch(w, s["person_id"], d1)
    phones = [x2]
    if difficulty == "hard":
        d2 = d1 + timedelta(days=rng.randint(70, 100))
        x3 = _switch(w, s["person_id"], d2)
        phones.append(x3)
    # decoy 1: a relative also moves to a new prepaid number a few days later
    rel = rels[0]
    w.reserve(rel)
    rel_new = _switch(w, rel, d1 + timedelta(days=rng.randint(2, 6)))
    # decoy 2: a fresh prepaid number activated the next day that touches a couple of the same contacts
    stray = w.new_phone(None, "prepaid", d1 + timedelta(days=1))
    for k in rng.sample(before, 1) + [rng.choice(list(w.PHONE))]:
        for _ in range(rng.randint(1, 3)):
            ts = _rand_ts(w, d1 + timedelta(days=2), date(2025, 12, 20))
            owner = w.PHONE[k]["_user"]
            if owner and w.active_phone(owner, ts.date()) == k:
                w.add_call(ts, stray, k, tower=w.tower_in(s["_city"]))
    decoy_phones = [rel_new, stray]
    if difficulty == "medium":
        prompt = f"""
{w.full_name(s['person_id'])} ({s['person_id']}) stopped using phone {x1} on {d1.isoformat()}. Investigators \
believe they switched to a new number to avoid detection.

Report phone: the phone number they most likely switched to.
"""
        fields = [field("phone", "id", 1.0, "phone_number of the replacement phone.")]
        answer = {"phone": x2}
        decoys = {"phone": decoy_phones}
    else:
        prompt = f"""
{w.full_name(s['person_id'])} ({s['person_id']}) stopped using phone {x1} on {d1.isoformat()} and is believed to \
have rotated through one or more replacement numbers during the rest of 2025.

Report:
- replacement_phones: every phone number they used after {x1}.
- current_phone: the number they were using at the end of 2025.
"""
        fields = [
            field("replacement_phones", "id_set", 0.4, "phone_numbers used after the original phone."),
            field("current_phone", "id", 0.6, "phone_number in use at end of 2025."),
        ]
        answer = {"replacement_phones": phones, "current_phone": phones[-1]}
        decoys = {"replacement_phones": decoy_phones, "current_phone": decoy_phones}
    add_task(w, task_id, "telecom", "burner_switch", difficulty, "Find the replacement phone", prompt, fields, answer,
             inputs={"person_id": s["person_id"], "old_phone": x1}, decoys=decoys,
             notes="Replacement identified by contact-set overlap with the old phone.")
