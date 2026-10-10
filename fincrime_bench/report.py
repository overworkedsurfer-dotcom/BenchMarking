"""Aggregate per-task results into run summaries, leaderboards and head-to-head comparisons."""

from __future__ import annotations

import json
import random
import statistics
from collections import defaultdict
from itertools import combinations
from pathlib import Path

CATEGORIES = ["aml", "tax", "ownership", "telecom", "investigation"]
CATEGORY_LABELS = {"aml": "AML", "tax": "Tax", "ownership": "Ownership", "telecom": "Telecom",
                   "investigation": "Investigation"}
DIFFICULTIES = ["easy", "medium", "hard"]
FAIL_STATUSES = ("api_error", "harness_error", "no_submission", "context_overflow")


def _per_task(records: list[dict]) -> dict[str, dict]:
    by: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by[r["task_id"]].append(r)
    out = {}
    for tid, rs in by.items():
        out[tid] = {"score": statistics.fmean(r["score"] for r in rs), "weight": rs[0]["weight"],
                    "category": rs[0]["category"], "difficulty": rs[0]["difficulty"], "n": len(rs)}
    return out


def weighted(items: list[tuple[float, float]]) -> float:
    tw = sum(w for _s, w in items)
    return 100.0 * sum(s * w for s, w in items) / tw if tw else 0.0


def bootstrap_ci(items: list[tuple[float, float]], n: int = 2000, seed: int = 0) -> tuple[float, float]:
    if len(items) < 2:
        v = weighted(items)
        return v, v
    rng = random.Random(seed)
    vals = sorted(weighted([rng.choice(items) for _ in items]) for _ in range(n))
    return vals[int(0.025 * n)], vals[int(0.975 * n) - 1]


def summarize(records: list[dict], tasks: list[dict]) -> dict:
    per = _per_task(records)
    task_meta = {t["task_id"]: t for t in tasks}
    for tid, t in task_meta.items():  # tasks never run count as zero
        per.setdefault(tid, {"score": 0.0, "weight": t["weight"], "category": t["category"],
                             "difficulty": t["difficulty"], "n": 0})
    items = [(v["score"], v["weight"]) for v in per.values()]
    lo, hi = bootstrap_ci(items)
    by_cat = {c: round(100 * statistics.fmean(v["score"] for v in per.values() if v["category"] == c), 2)
              for c in CATEGORIES if any(v["category"] == c for v in per.values())}
    by_diff = {d: round(100 * statistics.fmean(v["score"] for v in per.values() if v["difficulty"] == d), 2)
               for d in DIFFICULTIES if any(v["difficulty"] == d for v in per.values())}
    trials = sorted({r["trial"] for r in records})
    per_trial = []
    for k in trials:
        rs = {r["task_id"]: r for r in records if r["trial"] == k}
        per_trial.append(weighted([(rs[t]["score"] if t in rs else 0.0, per[t]["weight"]) for t in per]))
    tp = sum(r.get("set_tp", 0) for r in records)
    fp = sum(r.get("set_fp", 0) for r in records)
    fn = sum(r.get("set_fn", 0) for r in records)
    dh = sum(r.get("decoy_hits", 0) for r in records)
    dt = sum(r.get("decoy_total", 0) for r in records)
    stats = [r.get("stats") or {} for r in records]
    n = max(1, len(records))
    status = defaultdict(int)
    for r in records:
        status[r["status"]] += 1
    return {
        "score": round(weighted(items), 2),
        "ci95": [round(lo, 2), round(hi, 2)],
        "unweighted_score": round(100 * statistics.fmean(s for s, _w in items), 2) if items else 0.0,
        "by_category": by_cat,
        "by_difficulty": by_diff,
        "trial_scores": [round(x, 2) for x in per_trial],
        "trial_std": round(statistics.pstdev(per_trial), 2) if len(per_trial) > 1 else 0.0,
        "precision": round(tp / (tp + fp), 4) if tp + fp else None,
        "recall": round(tp / (tp + fn), 4) if tp + fn else None,
        "decoy_hit_rate": round(dh / dt, 4) if dt else None,
        "avg_tool_calls": round(sum(s.get("tool_calls", 0) for s in stats) / n, 2),
        "avg_turns": round(sum(s.get("turns", 0) for s in stats) / n, 2),
        "avg_total_tokens": round(sum(s.get("total_tokens", 0) for s in stats) / n, 1),
        "total_tokens": sum(s.get("total_tokens", 0) for s in stats),
        "avg_wall_time_s": round(sum(s.get("wall_time_s", 0) for s in stats) / n, 2),
        "status_counts": dict(status),
        "failures": sum(status[s] for s in FAIL_STATUSES),
        "tasks_run": sum(1 for v in per.values() if v["n"] > 0),
        "tasks_total": len(per),
        "task_scores": {tid: round(v["score"], 4) for tid, v in sorted(per.items())},
    }


# ------------------------------------------------------------------ longevity
CONTEXT_BUCKETS = [(0, 32_000, "<32k"), (32_000, 64_000, "32-64k"), (64_000, 96_000, "64-96k"),
                   (96_000, 10 ** 12, "96k+")]
ROUND_KINDS = ["case_file", "investigation", "recall", "synthesis"]
LOST_STATUSES = ("api_error", "context_overflow", "harness_error")


def round_context(r: dict) -> int:
    st = r.get("stats") or {}
    return int(st.get("context_tokens_start") or st.get("est_context_start") or 0)


def summarize_sessions(records: list[dict], sessions: list[dict]) -> dict:
    """Longevity metrics: how well a model keeps performing as one conversation grows past 32k tokens."""
    by_sr: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for rec in records:
        for r in rec["rounds"]:
            by_sr[(rec["session_id"], r["round"])].append(r)
    rounds: list[dict] = []
    for sx in sessions:  # rounds never run count as zero
        for rnd in sx["rounds"]:
            rs = by_sr.get((sx["session_id"], rnd["round"]), [])
            rounds.append({"session_id": sx["session_id"], "round": rnd["round"], "kind": rnd["kind"],
                           "source_task": rnd.get("source_task"), "weight": rnd["weight"],
                           "score": statistics.fmean(r["score"] for r in rs) if rs else 0.0,
                           "context": statistics.fmean(round_context(r) for r in rs) if rs else 0,
                           "ran": bool(rs) and any(r["status"] not in LOST_STATUSES for r in rs)})
    items = [(r["score"], r["weight"]) for r in rounds]
    lo, hi = bootstrap_ci(items)

    def mean100(rs: list[dict]):
        return round(100 * statistics.fmean(r["score"] for r in rs), 2) if rs else None

    by_ctx = {}
    for a, b, label in CONTEXT_BUCKETS:
        rs = [r for r in rounds if r["ran"] and r["context"] > 0 and a <= r["context"] < b]
        by_ctx[label] = {"score": mean100(rs), "rounds": len(rs)}
    firsts = [round_context(r) for rec in records for r in rec["rounds"][:1] if round_context(r)]
    src = "api" if any(rec.get("peak_context_source") == "api" for rec in records) else "estimate"
    all_rounds = [r for rec in records for r in rec["rounds"]]
    return {
        "score": round(weighted(items), 2) if items else 0.0,
        "ci95": [round(lo, 2), round(hi, 2)],
        "by_kind": {k: mean100([r for r in rounds if r["kind"] == k]) for k in ROUND_KINDS},
        "by_context": by_ctx,
        "by_position": {"rounds 1-3": mean100([r for r in rounds if r["round"] <= 3]),
                        "rounds 4-6": mean100([r for r in rounds if 4 <= r["round"] <= 6]),
                        "rounds 7+": mean100([r for r in rounds if r["round"] >= 7])},
        "peak_context_tokens": max((rec.get("peak_context_tokens", 0) for rec in records), default=0),
        "min_first_round_context": min(firsts) if firsts else 0,
        "context_source": src,
        "rounds_total": len(rounds),
        "rounds_lost": sum(1 for r in all_rounds if r["status"] in LOST_STATUSES),
        "session_scores": {rec["session_id"]: rec["score"] for rec in records},
        "in_session_task_scores": {r["source_task"]: round(r["score"], 4) for r in rounds if r["source_task"]},
        "round_scores": {f"{r['session_id']}/r{r['round']}": round(r["score"], 4) for r in rounds},
    }


# ---------------------------------------------------------------- leaderboard
def _load_run(run_dir: Path) -> tuple[str, list[dict]]:
    p = run_dir / "results.jsonl"
    records = [json.loads(line) for line in open(p, encoding="utf-8") if line.strip()] if p.exists() else []
    name = run_dir.name
    if (run_dir / "run.json").exists():
        name = json.loads((run_dir / "run.json").read_text())["model"]["name"]
    elif records:
        name = records[0]["model"]
    return name, records


def _load_sessions(run_dir: Path) -> list[dict]:
    p = run_dir / "session_results.jsonl"
    return [json.loads(line) for line in open(p, encoding="utf-8") if line.strip()] if p.exists() else []


def _fmt(v, pct: bool = False) -> str:
    if v is None:
        return "–"
    return f"{100 * v:.1f}%" if pct else f"{v:.1f}"


def leaderboard(run_dirs: list[str | Path]) -> tuple[str, list[dict]]:
    runs, session_runs = [], []
    for d in run_dirs:
        d = Path(d)
        if (d / "results.jsonl").exists() or (d / "session_results.jsonl").exists():
            name, recs = _load_run(d)
            runs.append((name, recs))
            session_runs.append((name, recs, _load_sessions(d)))
    if not runs:
        raise ValueError("no runs with results.jsonl found")
    # common task universe: union of tasks seen in any run (missing tasks score 0)
    meta: dict[str, dict] = {}
    for _n, recs in runs:
        for r in recs:
            meta.setdefault(r["task_id"], {"task_id": r["task_id"], "weight": r["weight"], "category": r["category"],
                                           "difficulty": r["difficulty"]})
    tasks = sorted(meta.values(), key=lambda t: t["task_id"])
    rows = []
    for name, recs in runs:
        if not recs:
            continue
        s = summarize(recs, tasks)
        s["model"] = name
        rows.append(s)
    rows.sort(key=lambda s: -s["score"])
    if not rows:
        return _longevity_table(session_runs, {}), []

    lines = ["# FinCrimeBench leaderboard", "",
             f"{len(tasks)} tasks · score = difficulty-weighted mean task score × 100 (easy 1, medium 2, hard 3) · "
             "95% CI from task bootstrap", ""]
    cats = [c for c in CATEGORIES if any(t["category"] == c for t in tasks)]
    hdr = ["Rank", "Model", "Score", "95% CI"] + [CATEGORY_LABELS[c] for c in cats] + \
          ["Precision", "Recall", "Decoy hits", "Tools/task", "Tokens/task", "Failures", "Coverage"]
    lines.append("| " + " | ".join(hdr) + " |")
    lines.append("|" + "|".join("---" for _ in hdr) + "|")
    for i, s in enumerate(rows, 1):
        cells = [str(i), s["model"], f"**{s['score']:.1f}**", f"{s['ci95'][0]:.1f}–{s['ci95'][1]:.1f}"]
        cells += [_fmt(s["by_category"].get(c)) for c in cats]
        cells += [_fmt(s["precision"], True), _fmt(s["recall"], True), _fmt(s["decoy_hit_rate"], True),
                  f"{s['avg_tool_calls']:.1f}", f"{s['avg_total_tokens']:,.0f}", str(s["failures"]),
                  f"{s['tasks_run']}/{s['tasks_total']}"]
        lines.append("| " + " | ".join(cells) + " |")

    lines += ["", "## Per-task scores", ""]
    hdr = ["Task", "Difficulty"] + [s["model"] for s in rows]
    lines.append("| " + " | ".join(hdr) + " |")
    lines.append("|" + "|".join("---" for _ in hdr) + "|")
    for t in tasks:
        cells = [t["task_id"], t["difficulty"]] + [f"{s['task_scores'].get(t['task_id'], 0):.2f}" for s in rows]
        lines.append("| " + " | ".join(cells) + " |")

    if len(rows) > 1:
        lines += ["", "## Head-to-head", "",
                  "Wins/losses/ties count tasks where one model scored at least 0.05 higher. Δ is the weighted score "
                  "difference (A − B) with a paired-bootstrap 95% CI; if the CI excludes 0 the gap is significant.",
                  "", "| A | B | A wins | B wins | Ties | Δ (A−B) | 95% CI |", "|---|---|---|---|---|---|---|"]
        for a, b in combinations(rows, 2):
            pairs = [(a["task_scores"].get(t["task_id"], 0.0), b["task_scores"].get(t["task_id"], 0.0), t["weight"])
                     for t in tasks]
            wins = sum(1 for x, y, _w in pairs if x - y >= 0.05)
            losses = sum(1 for x, y, _w in pairs if y - x >= 0.05)
            delta = weighted([(x, w) for x, _y, w in pairs]) - weighted([(y, w) for _x, y, w in pairs])
            rng = random.Random(1)
            boots = []
            for _ in range(2000):
                smp = [rng.choice(pairs) for _ in pairs]
                boots.append(weighted([(x, w) for x, _y, w in smp]) - weighted([(y, w) for _x, y, w in smp]))
            boots.sort()
            lines.append(f"| {a['model']} | {b['model']} | {wins} | {losses} | {len(pairs) - wins - losses} | "
                         f"{delta:+.1f} | {boots[50]:+.1f} to {boots[1949]:+.1f} |")
    standalone = {s["model"]: s["task_scores"] for s in rows}
    return "\n".join(lines) + "\n" + _longevity_table(session_runs, standalone), rows


def _longevity_table(session_runs: list[tuple[str, list[dict], list[dict]]], standalone: dict) -> str:
    """Leaderboard section for the multi-round, >=32k-token sessions."""
    lrows = []
    seen: dict[str, dict] = {}
    for _name, _recs, srecs in session_runs:
        for rec in srecs:
            seen.setdefault(rec["session_id"], {"session_id": rec["session_id"],
                                                "rounds": [{"round": r["round"], "kind": r["kind"],
                                                            "source_task": r["source_task"], "weight": r["weight"]}
                                                           for r in rec["rounds"]]})
    sessions = [seen[k] for k in sorted(seen)]
    for name, _recs, srecs in session_runs:
        if not srecs:
            continue
        ls = summarize_sessions(srecs, sessions)
        st = standalone.get(name, {})
        pairs = [(v, st[t]) for t, v in ls["in_session_task_scores"].items() if t in st]
        ls["delta"] = round(100 * statistics.fmean(a - b for a, b in pairs), 1) if pairs else None
        ls["model"] = name
        lrows.append(ls)
    if not lrows:
        return ""
    lrows.sort(key=lambda r: -r["score"])
    out = ["", "## Longevity (multi-round sessions, >=32k-token context)", "",
           "Each session is one conversation: a case file of at least 32k tokens, then ~10 rounds of questions. "
           "Case-file, recall and synthesis rounds have tools disabled. Context buckets use the prompt size at the "
           "start of each round (API-reported when available). Δ standalone = in-session minus standalone score on "
           "the same tasks (negative means the model degrades in long conversations).", ""]
    hdr = ["Rank", "Model", "Longevity", "95% CI", "Case file", "Investigation", "Recall", "Synthesis", "<32k",
           "32-64k", "64-96k", "96k+", "Late rounds (7+)", "Δ standalone", "Peak context", "Rounds lost"]
    out.append("| " + " | ".join(hdr) + " |")
    out.append("|" + "|".join("---" for _ in hdr) + "|")
    for i, r in enumerate(lrows, 1):
        bk = r["by_kind"]
        ctx = r["by_context"]
        cells = [str(i), r["model"], f"**{r['score']:.1f}**", f"{r['ci95'][0]:.1f}–{r['ci95'][1]:.1f}"]
        cells += [_fmt(bk.get(k)) for k in ROUND_KINDS]
        cells += [_fmt(ctx[label]["score"]) + (f" ({ctx[label]['rounds']})" if ctx[label]["rounds"] else "")
                  for _a, _b, label in CONTEXT_BUCKETS]
        cells += [_fmt(r["by_position"]["rounds 7+"]), "–" if r["delta"] is None else f"{r['delta']:+.1f}",
                  f"{r['peak_context_tokens']:,}" + ("" if r["context_source"] == "api" else " (est)"),
                  f"{r['rounds_lost']}/{r['rounds_total']}"]
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out) + "\n"
