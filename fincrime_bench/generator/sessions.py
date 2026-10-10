"""Longevity sessions: long multi-round investigations in a single conversation.

Each session opens with a CASE FILE — an extract of warehouse records large enough to put the conversation past
32k tokens from the first question — followed by ~10 rounds asked one at a time:

* case_file rounds   tools disabled; must be answered by reading the case file (sometimes many rounds later)
* investigation      tools enabled; an existing benchmark task asked mid-conversation
* recall             tools disabled; restate a fact from an early question or a finding from an earlier round
* synthesis          tools disabled; combine findings from several earlier rounds

Every benchmark task appears in exactly one session, so in-session scores can be compared with standalone scores.
The case file is rendered as parseable tables; ``validate`` parses it back and runs the reference solvers on the
extract alone to prove the evidence the model sees is sufficient.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from ..schema import TABLES
from .world import World

CHARS_PER_TOKEN = 2.6  # conservative for tabular text: real tokenizers give ~1.7-2.3 chars/token
DEFAULT_DOSSIER_TOKENS = 32_000

ROLE_DESCRIPTIONS = {
    "beneficiary_person_id": "beneficiary",
    "controller_person_id": "controller",
    "organizer_person_id": "organizer",
    "boss_person_id": "boss",
    "insider_person_id": "insider",
    "beneficial_owner_person_id": "beneficial owner of the vendor",
    "investor_ubo_person_id": "investor's ultimate beneficial owner",
    "nominee_person_id": "nominee",
    "person_ids": "flagged individuals",
}
PREMISES = {  # scheme -> (input key, field type, what the round-1 question named)
    "layering": ("txn_id", "id", "the txn_id of the stolen payment you were asked to trace"),
    "contact_chaining": ("crew", "id_set", "the person_ids of the crew members named in the question"),
    "net_worth": ("person_id", "id", "the person_id of the taxpayer selected for the net-worth audit"),
    "beneficial_ownership": ("business_id", "id",
                             "the business_id of the company whose beneficial owners you were asked to identify"),
}
FINDINGS = {
    "exit_account": "the exit account of the stolen-funds trail",
    "collector_account": "the collection account of the money-mule network",
    "preparer_id": "the preparer_id of the fraudulent tax preparer",
    "vendor_id": "the business_id of the vendor used to divert company funds",
}

SESSION_PLANS = [
    {"session_id": "long_01", "title": "Laundering desk: stolen wires, smurfing and offshore investors",
     "rounds": [("case_file", "aml_layering_03"), ("investigation", "aml_layering_01"),
                ("investigation", "aml_layering_02"), ("case_file", "aml_smurfing_01"),
                ("investigation", "aml_roundtrip_01"), ("investigation", "own_ubo_02"),
                ("case_file", "aml_layering_04"), ("recall", 1), ("recall", (3, "exit_account")),
                ("synthesis", [(1, "beneficiary_person_id"), (4, "controller_person_id"),
                               (5, "investor_ubo_person_id"), (7, "beneficiary_person_id")])]},
    {"session_id": "long_02", "title": "Telecom and fraud-ring desk",
     "rounds": [("case_file", "tel_chain_01"), ("investigation", "aml_mule_01"), ("investigation", "tel_chain_02"),
                ("case_file", "tel_burner_01"), ("investigation", "x_capstone_01"),
                ("investigation", "tel_burner_02"), ("recall", 1), ("recall", (2, "collector_account")),
                ("synthesis", [(1, "boss_person_id"), (2, "controller_person_id"), (3, "boss_person_id"),
                               (5, "organizer_person_id")])]},
    {"session_id": "long_03", "title": "Tax desk",
     "rounds": [("case_file", "tax_networth_01"), ("investigation", "tax_unreported_01"),
                ("case_file", "tax_dependents_01"), ("investigation", "tax_skimming_01"),
                ("investigation", "tax_lifestyle_01"), ("case_file", "tax_preparer_01"),
                ("investigation", "x_capstone_02"), ("recall", 1), ("recall", (6, "preparer_id")),
                ("synthesis", [(5, "person_ids"), (7, "nominee_person_id")])]},
    {"session_id": "long_04", "title": "Corporate registry and account-security desk",
     "rounds": [("case_file", "own_ubo_01"), ("investigation", "aml_ato_01"), ("case_file", "aml_structuring_01"),
                ("investigation", "own_kickback_01"), ("investigation", "aml_mule_02"),
                ("case_file", "own_ubo_03"), ("investigation", "own_kickback_02"), ("recall", 1),
                ("recall", (4, "vendor_id")),
                ("synthesis", [(4, "insider_person_id"), (5, "controller_person_id"), (7, "insider_person_id"),
                               (7, "beneficial_owner_person_id")])]},
]


# ====================================================================== evidence
class Evidence:
    """Rows selected for a case file, keyed by table and row identity (dedupes overlapping selections)."""

    def __init__(self, w: World):
        self.w = w
        self.rows: dict[str, dict[int, dict]] = {name: {} for name in TABLES}
        self._chars = 0

    def add(self, table: str, rows) -> None:
        bucket = self.rows[table]
        for r in rows:
            if id(r) not in bucket:
                bucket[id(r)] = r
                self._chars += len(render_row(table, r)) + 1

    def chars(self) -> int:
        return self._chars

    # -------- closures: referenced entities get their registry / KYC rows
    def close(self) -> None:
        w = self.w
        accts = {r["from_account"] for r in self.rows["transactions"].values()} | \
                {r["to_account"] for r in self.rows["transactions"].values()}
        accts |= {r["refund_account_id"] for r in self.rows["tax_returns"].values()}
        accts.discard(None)
        self.add("accounts", [w.A[a] for a in accts])
        people = {r["conducted_by"] for r in self.rows["transactions"].values()}
        for a in self.rows["accounts"].values():
            people |= {a["holder_id"], a["authorized_signer_id"]}
        for o in self.rows["ownership"].values():
            people |= {o["owner_id"], o["owned_id"]}
        for ph in self.rows["phones"].values():
            people.add(ph["subscriber_id"])
        for d in self.rows["return_dependents"].values():
            people.add(d["dependent_person_id"])
        for r in self.rows["tax_returns"].values():
            for tin in (r["filer_tin"], r["spouse_tin"]):
                if tin and tin in w.TIN:
                    people.add(w.TIN[tin])
        for a in self.rows["assets"].values():
            people.add(a["owner_id"])
        people.discard(None)
        self.add("persons", [w.P[p] for p in people if p in w.P])
        self.add("businesses", [w.B[b] for b in people if b in w.B])
        nums = {c["caller"] for c in self.rows["calls"].values()} | {c["callee"] for c in self.rows["calls"].values()}
        self.add("phones", [w.PHONE[n] for n in nums])


def _index(w: World) -> dict:
    """Lazy lookup indexes (built once per world) so case-file assembly stays fast."""
    if not hasattr(w, "_session_index"):
        by_acct, by_num, by_owned, by_payee = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
        for t in w.tables["transactions"]:
            for a in {t["from_account"], t["to_account"]} - {None}:
                by_acct[a].append(t)
        for c in w.tables["calls"]:
            by_num[c["caller"]].append(c)
            if c["callee"] != c["caller"]:
                by_num[c["callee"]].append(c)
        for o in w.tables["ownership"]:
            by_owned[o["owned_id"]].append(o)
        for i in w.tables["info_returns"]:
            by_payee[i["payee_tin"]].append(i)
        w._session_index = {"acct": by_acct, "num": by_num, "owned": by_owned, "payee": by_payee}
    return w._session_index


def _ownership_up(w: World, entity: str, ev: Evidence, depth: int = 0) -> None:
    if depth > 8:
        return
    rows = _index(w)["owned"].get(entity, [])
    ev.add("ownership", rows)
    for o in rows:
        if o["owner_id"].startswith("B"):
            _ownership_up(w, o["owner_id"], ev, depth + 1)


def _calls_of(w: World, numbers: set[str]) -> list[dict]:
    idx = _index(w)["num"]
    return [c for n in sorted(numbers) for c in idx.get(n, [])]


def _tax_unit(w: World, r: dict, ev: Evidence) -> None:
    """A return with both filers' information returns (complete, so matching stays correct)."""
    ev.add("tax_returns", [r])
    tins = {r["filer_tin"], r["spouse_tin"]} - {None}
    ev.add("info_returns", [i for t in sorted(tins) for i in _index(w)["payee"].get(t, [])])


def ev_layering(w: World, task: dict, ans: dict, key: dict, ev: Evidence) -> None:
    t0 = w.txn_by_id[task["inputs"]["txn_id"]]["_ts"]
    lo, hi = t0 - timedelta(days=6), t0 + timedelta(days=21)
    accts = set(ans["accounts"]) | {task["inputs"]["origin_account"]} | set(key["decoys"].get("accounts", []))
    rng = w.rng
    pool = [a["account_id"] for a in w.tables["accounts"] if a["country"] == "US" and a["account_type"] != "internal"]
    accts |= set(rng.sample(pool, 12))
    ev.add("transactions", [t for t in w.tables["transactions"] if lo <= t["_ts"] <= hi
                            and (t["from_account"] in accts or t["to_account"] in accts)])
    holder = w.A[ans["exit_account"]]["holder_id"]
    if holder.startswith("B"):
        _ownership_up(w, holder, ev)


def ev_smurfing(w: World, task: dict, ans: dict, key: dict, ev: Evidence) -> None:
    acct = task["inputs"]["account_id"]
    ev.add("transactions", _index(w)["acct"].get(acct, []))
    _ownership_up(w, w.A[acct]["holder_id"], ev)


def ev_chain(w: World, task: dict, ans: dict, key: dict, ev: Evidence) -> None:
    crew = set(task["inputs"]["crew"])
    nums = {p["phone_number"] for p in w.tables["phones"] if p["subscriber_id"] in crew} | {ans["handler_phone"]}
    ev.add("calls", _calls_of(w, nums))


def ev_burner(w: World, task: dict, ans: dict, key: dict, ev: Evidence) -> None:
    old = task["inputs"]["old_phone"]
    d = w.PHONE[old]["_deact"]
    cands = {p["phone_number"] for p in w.tables["phones"]
             if d - timedelta(days=3) <= p["_act"] <= d + timedelta(days=10)}
    ev.add("calls", _calls_of(w, cands | {old}))
    ev.add("phones", [w.PHONE[n] for n in cands | {old}])


def ev_networth(w: World, task: dict, ans: dict, key: dict, ev: Evidence) -> None:
    pid = task["inputs"]["person_id"]
    ev.add("assets", [a for a in w.tables["assets"] if a["owner_id"] == pid])
    rid = w.return_of.get(w.P[pid]["tin"])
    if rid:
        ev.add("tax_returns", [w.returns_by_id[rid]])


def ev_dependents(w: World, task: dict, ans: dict, key: dict, ev: Evidence) -> None:
    rng = w.rng
    with_deps = sorted({d["return_id"] for d in w.tables["return_dependents"]})
    rids = set(ans["return_ids"]) | set(key["decoys"].get("return_ids", [])) | set(rng.sample(with_deps, 70))
    ev.add("tax_returns", [w.returns_by_id[r] for r in rids])
    ev.add("return_dependents", [d for d in w.tables["return_dependents"] if d["return_id"] in rids])


def ev_preparer(w: World, task: dict, ans: dict, key: dict, ev: Evidence) -> None:
    rng = w.rng
    preps = {ans["preparer_id"]} | set(key["decoys"].get("preparer_id", []))
    rets = [r for r in w.tables["tax_returns"] if r["preparer_id"] in preps]
    others = [r for r in w.tables["tax_returns"] if r["refund_account_id"] and r["preparer_id"] not in preps]
    ev.add("tax_returns", rets + rng.sample(others, 60))


def ev_ubo(w: World, task: dict, ans: dict, key: dict, ev: Evidence) -> None:
    _ownership_up(w, task["inputs"]["business_id"], ev)


def ev_structuring(w: World, task: dict, ans: dict, key: dict, ev: Evidence) -> None:
    ev.add("transactions", [t for t in w.tables["transactions"]
                            if t["channel"] == "cash_deposit" and t["amount"] >= 7_000])


EVIDENCE = {
    "layering": ev_layering, "smurfing": ev_smurfing, "contact_chaining": ev_chain, "burner_switch": ev_burner,
    "net_worth": ev_networth, "dependents": ev_dependents, "preparer_fraud": ev_preparer,
    "beneficial_ownership": ev_ubo, "structuring": ev_structuring,
}


# ------------------------------------------------------------------ noise
def add_noise(w: World, ev: Evidence, kinds: list[str], target_chars: int, minimum: dict[str, int]) -> None:
    """Bury the evidence among unrelated but complete records, then pad until the size target is reached."""
    rng = w.rng
    accounts = [a["account_id"] for a in w.tables["accounts"] if a["country"] == "US"
                and a["account_type"] != "internal"]
    phones = list(w.PHONE)
    companies = [b["business_id"] for b in w.businesses]
    returns = [r for r in w.tables["tax_returns"] if r["form_type"] == "1040"]
    small_cash = [t for t in w.tables["transactions"] if t["channel"] == "cash_deposit" and t["amount"] < 7_000]
    schedule = [k for k, n in minimum.items() for _ in range(n)]
    ev.close()
    guard = 0
    while schedule or (ev.chars() < target_chars and guard < 400):
        guard += 1
        kind = schedule.pop(0) if schedule else kinds[guard % len(kinds)]
        if kind == "transactions":
            acct = rng.choice(accounts)
            start = datetime(2025, rng.randint(1, 11), rng.randint(1, 28))
            ev.add("transactions", [t for t in _index(w)["acct"].get(acct, [])
                                    if start <= t["_ts"] <= start + timedelta(days=28)])
        elif kind == "calls":
            ev.add("calls", _calls_of(w, {rng.choice(phones)}))
        elif kind == "ownership":
            for _ in range(5):
                _ownership_up(w, rng.choice(companies), ev)
        elif kind == "tax":
            for _ in range(3):
                _tax_unit(w, rng.choice(returns), ev)
        elif kind == "cash":
            ev.add("transactions", rng.sample(small_cash, 40))
        elif kind == "assets":
            p = rng.choice(w.persons)
            ev.add("assets", [a for a in w.tables["assets"] if a["owner_id"] == p["person_id"]])
        ev.close()  # referenced accounts / KYC rows count towards the size


NOISE_KINDS = {
    "long_01": ["transactions", "ownership"],
    "long_02": ["calls"],
    "long_03": ["tax", "assets"],
    "long_04": ["ownership", "cash"],
}
NOISE_MINIMUM = {  # iterations of each noise kind regardless of size, so evidence never dominates a table
    "long_01": {"ownership": 4, "transactions": 2},
    "long_02": {"calls": 8},
    "long_03": {"assets": 40, "tax": 10},
    "long_04": {"ownership": 8, "cash": 1},
}


# ---------------------------------------------------------------- rendering
def _fmt(v, ctype: str) -> str:
    if v is None:
        return ""
    if ctype == "REAL":
        return f"{float(v):.2f}"
    return str(v).replace("|", "/").replace("\n", " ")


def render_row(table: str, r: dict) -> str:
    return "|".join(_fmt(r.get(c), t) for c, t, _d in TABLES[table]["columns"])


_SORT = {"transactions": ("timestamp", "txn_id"), "calls": ("timestamp", "call_id"),
         "ownership": ("owned_id", "owner_id", "role", "start_date"), "relationships": ("person_id_1", "person_id_2"),
         "return_dependents": ("return_id", "dependent_person_id")}


def render_case_file(title: str, ev: Evidence) -> str:
    parts = [
        f"CASE FILE — {title}",
        "This is an extract of warehouse records compiled for the open matters on this desk. It mixes evidence with "
        "unrelated records. Where a record type appears, the extract is complete for the entities the case-file "
        "questions concern. Tables are pipe-separated with a header row; empty fields are NULL. Column meanings are "
        "the same as in the warehouse schema.",
    ]
    for table, spec in TABLES.items():
        rows = list(ev.rows[table].values())
        if not rows:
            continue
        cols = [c for c, _t, _d in spec["columns"]]
        keys = _SORT.get(table, (cols[0],))
        rows.sort(key=lambda r: tuple("" if r.get(k) is None else str(r.get(k)) for k in keys))
        parts.append(f"=== TABLE {table} ({len(rows)} rows) ===")
        parts.append("|".join(cols))
        parts.extend(render_row(table, r) for r in rows)
        parts.append(f"=== END {table} ===")
    parts.append("END OF CASE FILE")
    return "\n".join(parts)


def parse_case_file(text: str) -> dict[str, tuple[list[str], list[list[str]]]]:
    """Inverse of render_case_file: table -> (columns, rows of strings)."""
    out: dict[str, tuple[list[str], list[list[str]]]] = {}
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("=== TABLE "):
            name = line[len("=== TABLE "):].split(" ")[0]
            cols = lines[i + 1].split("|")
            rows = []
            i += 2
            while not lines[i].startswith(f"=== END {name}"):
                rows.append(lines[i].split("|"))
                i += 1
            out[name] = (cols, rows)
        i += 1
    return out


# ------------------------------------------------------------------ assembly
def _header(k: int, n: int, kind: str) -> str:
    mode = {
        "case_file": "Answer from the CASE FILE at the start of this conversation. Tools are DISABLED for this round.",
        "investigation": "Tools are ENABLED for this round; the full warehouse is available.",
        "recall": "Answer from this conversation. Tools are DISABLED for this round.",
        "synthesis": "Answer from this conversation. Tools are DISABLED for this round.",
    }[kind]
    return f"ROUND {k} of {n} — {mode}"


def build_sessions(w: World, dossier_tokens: int = DEFAULT_DOSSIER_TOKENS) -> tuple[list[dict], list[dict]]:
    tasks = {t["task_id"]: t for t in w.tasks}
    keys = {a["task_id"]: a for a in w.answers}
    sessions, answers = [], []
    target_chars = int(dossier_tokens * CHARS_PER_TOKEN)
    for plan in SESSION_PLANS:
        ev = Evidence(w)
        n = len(plan["rounds"])
        rounds, golds = [], []
        for k, (kind, arg) in enumerate(plan["rounds"], 1):
            if kind in ("case_file", "investigation"):
                t, key = tasks[arg], keys[arg]
                if kind == "case_file":
                    EVIDENCE[t["scheme"]](w, t, key["answer"], key, ev)
                rounds.append({"round": k, "kind": kind, "tools": kind == "investigation", "source_task": arg,
                               "weight": t["weight"], "prompt": f"{_header(k, n, kind)}\n\n{t['prompt']}",
                               "answer_fields": t["answer_fields"]})
                golds.append({"round": k, "answer": key["answer"], "decoys": key["decoys"]})
            elif kind == "recall" and isinstance(arg, int):
                src = rounds[arg - 1]
                t = tasks[src["source_task"]]
                in_key, ftype, label = PREMISES[t["scheme"]]
                gold = t["inputs"][in_key]
                prompt = (f"{_header(k, n, kind)}\n\nThe question in round {arg} named a specific item. What was "
                          f"{label}?")
                rounds.append({"round": k, "kind": "recall", "tools": False, "source_task": None, "weight": 1,
                               "prompt": prompt, "recall_of": arg,
                               "answer_fields": [{"name": "answer", "type": ftype, "weight": 1.0,
                                                  "description": label}]})
                golds.append({"round": k, "answer": {"answer": gold}, "decoys": {}})
            elif kind == "recall":
                src_round, fname = arg
                src = rounds[src_round - 1]
                f = next(f for f in src["answer_fields"] if f["name"] == fname)
                prompt = (f"{_header(k, n, kind)}\n\nIn round {src_round} you determined {FINDINGS[fname]}. "
                          f"Restate it exactly.")
                rounds.append({"round": k, "kind": "recall", "tools": False, "source_task": None, "weight": 1,
                               "prompt": prompt, "recall_of": src_round,
                               "answer_fields": [{**f, "name": "answer", "weight": 1.0}]})
                golds.append({"round": k, "answer": {"answer": golds[src_round - 1]["answer"][fname]},
                              "decoys": {"answer": golds[src_round - 1]["decoys"].get(fname, [])}})
            else:  # synthesis
                gold, decoys, parts = [], [], []
                for src_round, fname in arg:
                    v = golds[src_round - 1]["answer"][fname]
                    gold += v if isinstance(v, list) else [v]
                    decoys += golds[src_round - 1]["decoys"].get(fname, [])
                    parts.append(f"round {src_round} ({ROLE_DESCRIPTIONS[fname]})")
                gold = sorted(set(gold))
                decoys = sorted(set(decoys) - set(gold))
                prompt = (f"{_header(k, n, kind)}\n\nFinal synthesis. List every person_id you identified in "
                          f"{', '.join(parts[:-1])} and {parts[-1]}. Use the correct answers as you now understand "
                          f"them.")
                rounds.append({"round": k, "kind": "synthesis", "tools": False, "source_task": None, "weight": 2,
                               "prompt": prompt,
                               "answer_fields": [{"name": "person_ids", "type": "id_set", "weight": 1.0,
                                                  "description": "All person_ids from the listed rounds."}]})
                golds.append({"round": k, "answer": {"person_ids": gold}, "decoys": {"person_ids": decoys}})
        ev.close()
        add_noise(w, ev, NOISE_KINDS[plan["session_id"]], target_chars, NOISE_MINIMUM[plan["session_id"]])
        preamble = render_case_file(plan["title"], ev)
        intro = (f"You are starting a long investigative session of {n} rounds. Below is a CASE FILE, followed by "
                 f"the first question. Later questions arrive one at a time in this same conversation. Some rounds "
                 f"can only be answered from the case file or from earlier rounds, so keep track of what you find.")
        sessions.append({
            "session_id": plan["session_id"], "title": plan["title"], "category": "longevity",
            "preamble": f"{intro}\n\n{preamble}",
            "dossier_chars": len(preamble), "dossier_est_tokens": int(len(preamble) / CHARS_PER_TOKEN),
            "case_file_rows": {t: len(rs) for t, rs in ev.rows.items() if rs},
            "rounds": rounds,
        })
        answers.append({"session_id": plan["session_id"], "rounds": golds})
    return sessions, answers

