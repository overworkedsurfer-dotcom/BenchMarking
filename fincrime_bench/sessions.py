"""Runtime support for longevity sessions: loading, case-file warehouses, validation and scoring helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .db import Warehouse, build_db_from
from .generator.sessions import parse_case_file
from .schema import TABLES
from .scoring import score_task


def load_sessions(data_dir: str | Path) -> tuple[list[dict], dict[str, dict]]:
    d = Path(data_dir)
    if not (d / "sessions.jsonl").exists():
        return [], {}
    sessions = [json.loads(line) for line in open(d / "sessions.jsonl", encoding="utf-8") if line.strip()]
    keys = {}
    if (d / "session_answers.jsonl").exists():
        for line in open(d / "session_answers.jsonl", encoding="utf-8"):
            if line.strip():
                rec = json.loads(line)
                keys[rec["session_id"]] = rec
    return sessions, keys


def case_file_text(session: dict) -> str:
    pre = session["preamble"]
    return pre[pre.index("CASE FILE"):]


def case_file_warehouse(session: dict, cache_dir: str | Path) -> Warehouse:
    """A warehouse containing ONLY what the case file shows (parsed back from the rendered text)."""
    text = case_file_text(session)
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    db_path = cache / f"casefile-{session['session_id']}-{hashlib.sha256(text.encode()).hexdigest()[:12]}.sqlite"
    if not db_path.exists():
        parsed = parse_case_file(text)
        for name, (cols, _rows) in parsed.items():
            expected = [c[0] for c in TABLES[name]["columns"]]
            if cols != expected:
                raise ValueError(f"case file table {name} has columns {cols}, expected {expected}")
        build_db_from(((name, rows) for name, (_cols, rows) in parsed.items()), db_path)
    return Warehouse(db_path, time_limit_s=300)


def round_task(rnd: dict) -> dict:
    return {"answer_fields": rnd["answer_fields"]}


def score_round(rnd: dict, gold: dict, submission) -> dict:
    return score_task(round_task(rnd), gold, submission or {})


def validate_sessions(sessions: list[dict], keys: dict[str, dict], tasks: dict[str, dict], full: Warehouse,
                      cache_dir: str | Path) -> list[dict]:
    """Solve every round from the evidence available to the model in that round.

    case_file rounds are solved by the reference solver on a warehouse built from the case-file text alone;
    investigation rounds on the full warehouse; recall/synthesis golds must agree with the rounds they cite."""
    from .solvers import solve
    out = []
    for s in sessions:
        golds = {g["round"]: g for g in keys[s["session_id"]]["rounds"]}
        cf = case_file_warehouse(s, cache_dir) if any(r["kind"] == "case_file" for r in s["rounds"]) else None
        for r in s["rounds"]:
            if r["kind"] == "case_file":
                sub = solve(tasks[r["source_task"]], cf)
            elif r["kind"] == "investigation":
                sub = solve(tasks[r["source_task"]], full)
            else:
                sub = golds[r["round"]]["answer"]  # derived from cited rounds' golds at generation time
            res = score_round(r, golds[r["round"]], sub)
            out.append({"session_id": s["session_id"], "round": r["round"], "kind": r["kind"],
                        "source_task": r.get("source_task"), "score": res["score"], "fields": res["fields"]})
        if cf:
            cf.close()
    return out
