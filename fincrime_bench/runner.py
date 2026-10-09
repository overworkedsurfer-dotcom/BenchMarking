"""Run a model over benchmark tasks, score each submission and persist everything."""

from __future__ import annotations

import json
import re
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path

from .agent import Agent, AgentConfig
from .db import Warehouse, ensure_db
from .llm import ChatClient, ModelConfig
from .report import summarize
from .scoring import score_task
from .tools import Toolbox

BUILTIN_MODELS = ("oracle", "reference", "null")


@dataclass
class Benchmark:
    data_dir: Path
    tasks: list[dict]
    keys: dict[str, dict]
    manifest: dict


def load_benchmark(data_dir: str | Path) -> Benchmark:
    d = Path(data_dir)
    if not (d / "tasks.jsonl").exists():
        raise FileNotFoundError(f"{d} has no tasks.jsonl — run `fincrime-bench generate --out {d}` first")
    tasks = [json.loads(line) for line in open(d / "tasks.jsonl", encoding="utf-8") if line.strip()]
    keys = {}
    if (d / "answers.jsonl").exists():
        for line in open(d / "answers.jsonl", encoding="utf-8"):
            if line.strip():
                rec = json.loads(line)
                keys[rec["task_id"]] = rec
    manifest = json.loads((d / "manifest.json").read_text()) if (d / "manifest.json").exists() else {}
    return Benchmark(d, tasks, keys, manifest)


def select_tasks(tasks: list[dict], ids: list[str] | None = None, categories: list[str] | None = None,
                 difficulties: list[str] | None = None) -> list[dict]:
    out = tasks
    if ids:
        missing = set(ids) - {t["task_id"] for t in tasks}
        if missing:
            raise ValueError(f"unknown task ids: {sorted(missing)}")
        out = [t for t in out if t["task_id"] in ids]
    if categories:
        out = [t for t in out if t["category"] in categories]
    if difficulties:
        out = [t for t in out if t["difficulty"] in difficulties]
    return out


def slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "run"


def _builtin_submission(model: str, task: dict, bench: Benchmark, wh: Warehouse) -> dict:
    if model == "oracle":
        return bench.keys[task["task_id"]]["answer"]
    if model == "reference":
        from .solvers import solve
        return solve(task, wh)
    return {}


def run_model(bench: Benchmark, model: ModelConfig, out_dir: str | Path, agent_cfg: AgentConfig | None = None,
              tasks: list[dict] | None = None, trials: int = 1, concurrency: int = 4, resume: bool = True,
              log=print) -> dict:
    agent_cfg = agent_cfg or AgentConfig()
    tasks = tasks if tasks is not None else bench.tasks
    if not bench.keys:
        raise ValueError("answers.jsonl not found: cannot score")
    out = Path(out_dir)
    (out / "transcripts").mkdir(parents=True, exist_ok=True)
    db_path = ensure_db(bench.data_dir)
    run_info = {"model": model.public_dict(), "agent": asdict(agent_cfg), "data_dir": str(bench.data_dir),
                "dataset": {k: bench.manifest.get(k) for k in ("benchmark", "generator_version", "seed", "scale")},
                "trials": trials, "updated": time.strftime("%Y-%m-%dT%H:%M:%S")}
    (out / "run.json").write_text(json.dumps(run_info, indent=2))
    results_path = out / "results.jsonl"
    done: set[tuple[str, int]] = set()
    if resume and results_path.exists():
        kept = []
        for line in open(results_path, encoding="utf-8"):
            if line.strip():
                rec = json.loads(line)
                if rec["status"] != "api_error":
                    done.add((rec["task_id"], rec["trial"]))
                    kept.append(line if line.endswith("\n") else line + "\n")
        results_path.write_text("".join(kept))
    elif results_path.exists():
        results_path.unlink()
    jobs = [(t, k) for t in tasks for k in range(trials) if (t["task_id"], k) not in done]
    lock = threading.Lock()
    log(f"[{model.name}] {len(jobs)} job(s) to run ({len(done)} already done), concurrency={concurrency}")

    def job(task: dict, trial: int) -> dict:
        wh = Warehouse(db_path)
        try:
            t0 = time.time()
            if model.base_url == "builtin":
                result = {"submission": _builtin_submission(model.model, task, bench, wh), "status": "submitted",
                          "stats": {"turns": 0, "tool_calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                                    "total_tokens": 0, "api_errors": 0, "wall_time_s": 0.0}, "messages": []}
            else:
                agent = Agent(ChatClient(model), Toolbox(wh, agent_cfg.toolset, agent_cfg.max_tool_output_chars),
                              agent_cfg, tool_mode=model.tool_mode)
                result = agent.run(task)
            scored = score_task(task, bench.keys[task["task_id"]], result["submission"] or {})
            rec = {"task_id": task["task_id"], "trial": trial, "model": model.name, "category": task["category"],
                   "scheme": task["scheme"], "difficulty": task["difficulty"], "weight": task["weight"],
                   **scored, "status": result["status"], "stats": result["stats"],
                   "submission": result["submission"], "elapsed_s": round(time.time() - t0, 2)}
            transcript = {"task": task, "result": {k: v for k, v in rec.items() if k != "submission"},
                          "submission": result["submission"], "messages": result["messages"]}
        except Exception as e:  # keep the run going; record the failure
            rec = {"task_id": task["task_id"], "trial": trial, "model": model.name, "category": task["category"],
                   "scheme": task["scheme"], "difficulty": task["difficulty"], "weight": task["weight"],
                   "score": 0.0, "fields": {}, "decoy_hits": 0, "decoy_total": 0, "set_tp": 0, "set_fp": 0,
                   "set_fn": 0, "status": "harness_error", "stats": {}, "submission": None,
                   "error": f"{type(e).__name__}: {e}"}
            transcript = {"task": task, "error": traceback.format_exc()}
        finally:
            wh.close()
        with lock:
            with open(results_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            (out / "transcripts" / f"{task['task_id']}__t{trial}.json").write_text(
                json.dumps(transcript, indent=1, default=str))
        return rec

    finished = 0
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        futures = {ex.submit(job, t, k): (t["task_id"], k) for t, k in jobs}
        for fut in as_completed(futures):
            rec = fut.result()
            finished += 1
            st = rec.get("stats") or {}
            log(f"[{model.name}] {finished}/{len(jobs)} {rec['task_id']} t{rec['trial']}: score={rec['score']:.3f} "
                f"status={rec['status']} tools={st.get('tool_calls', 0)} tokens={st.get('total_tokens', 0)}")
    records = load_records(out)
    summary = summarize(records, tasks)
    summary["model"] = model.name
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def load_records(run_dir: str | Path) -> list[dict]:
    p = Path(run_dir) / "results.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in open(p, encoding="utf-8") if line.strip()]


def rescore(run_dir: str | Path, bench: Benchmark) -> dict:
    """Re-score stored submissions (e.g. after a scoring change) without calling the model again."""
    run_dir = Path(run_dir)
    tasks = {t["task_id"]: t for t in bench.tasks}
    records = load_records(run_dir)
    for rec in records:
        if rec["task_id"] in tasks and rec["status"] != "harness_error":
            rec.update(score_task(tasks[rec["task_id"]], bench.keys[rec["task_id"]], rec.get("submission") or {}))
    with open(run_dir / "results.jsonl", "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    ran = {r["task_id"] for r in records}
    summary = summarize(records, [t for t in bench.tasks if t["task_id"] in ran])
    summary["model"] = records[0]["model"] if records else run_dir.name
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary
