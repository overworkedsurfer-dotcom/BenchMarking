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


# ---------------------------------------------------------------- leaderboard
def _load_run(run_dir: Path) -> tuple[str, list[dict]]:
    records = [json.loads(line) for line in open(run_dir / "results.jsonl", encoding="utf-8") if line.strip()]
    name = run_dir.name
    if (run_dir / "run.json").exists():
        name = json.loads((run_dir / "run.json").read_text())["model"]["name"]
    elif records:
        name = records[0]["model"]
    return name, records


def _fmt(v, pct: bool = False) -> str:
    if v is None:
        return "–"
    return f"{100 * v:.1f}%" if pct else f"{v:.1f}"


def leaderboard(run_dirs: list[str | Path]) -> tuple[str, list[dict]]:
    runs = []
    for d in run_dirs:
        d = Path(d)
        if (d / "results.jsonl").exists():
            runs.append(_load_run(d))
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
        s = summarize(recs, tasks)
        s["model"] = name
        rows.append(s)
    rows.sort(key=lambda s: -s["score"])

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
    return "\n".join(lines) + "\n", rows
