"""Run a model over benchmark tasks, score each submission and persist everything."""

from __future__ import annotations

import json
import re
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .agent import Agent, AgentConfig, _empty_stats
from .db import Warehouse, ensure_db
from .llm import ChatClient, ModelConfig, preflight
from .report import summarize, summarize_sessions
from .scoring import score_task
from .sessions import case_file_warehouse, load_sessions, score_round
from .tools import Toolbox

BUILTIN_MODELS = ("oracle", "reference", "null")


@dataclass
class Benchmark:
    data_dir: Path
    tasks: list[dict]
    keys: dict[str, dict]
    manifest: dict
    sessions: list[dict] = field(default_factory=list)
    session_keys: dict[str, dict] = field(default_factory=dict)


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
    sessions, session_keys = load_sessions(d)
    return Benchmark(d, tasks, keys, manifest, sessions, session_keys)


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


def _resume(path: Path, key, resume: bool) -> set:
    """Keep finished records (dropping API failures, which are retried) and return their keys."""
    done: set = set()
    if resume and path.exists():
        kept = []
        for line in open(path, encoding="utf-8"):
            if line.strip():
                rec = json.loads(line)
                if rec["status"] != "api_error":
                    done.add(key(rec))
                    kept.append(line if line.endswith("\n") else line + "\n")
        path.write_text("".join(kept))
    elif path.exists():
        path.unlink()
    return done


def run_model(bench: Benchmark, model: ModelConfig, out_dir: str | Path, agent_cfg: AgentConfig | None = None,
              tasks: list[dict] | None = None, trials: int = 1, concurrency: int = 4, resume: bool = True,
              log=print, preflight_check: bool = True, sessions: list[dict] | None = None) -> dict:
    """Run standalone tasks and (optionally) longevity sessions for one model, then summarise."""
    agent_cfg = agent_cfg or AgentConfig()
    tasks = tasks if tasks is not None else bench.tasks
    sessions = sessions or []
    if not bench.keys:
        raise ValueError("answers.jsonl not found: cannot score")
    out = Path(out_dir)
    (out / "transcripts").mkdir(parents=True, exist_ok=True)
    db_path = ensure_db(bench.data_dir)
    builtin = model.base_url == "builtin"
    run_info = {"model": model.public_dict(), "agent": asdict(agent_cfg), "data_dir": str(bench.data_dir),
                "dataset": {k: bench.manifest.get(k) for k in ("benchmark", "generator_version", "seed", "scale")},
                "trials": trials, "updated": time.strftime("%Y-%m-%dT%H:%M:%S")}
    (out / "run.json").write_text(json.dumps(run_info, indent=2))
    results_path, session_path = out / "results.jsonl", out / "session_results.jsonl"
    done = _resume(results_path, lambda r: (r["task_id"], r["trial"]), resume)
    s_done = _resume(session_path, lambda r: (r["session_id"], r["trial"]), resume)
    jobs = [("session", sx, k) for sx in sessions for k in range(trials) if (sx["session_id"], k) not in s_done]
    jobs += [("task", t, k) for t in tasks for k in range(trials) if (t["task_id"], k) not in done]
    if jobs and not builtin and preflight_check:
        preflight(model)
    lock = threading.Lock()
    log(f"[{model.name}] {len(jobs)} job(s) to run ({len(done) + len(s_done)} already done), "
        f"concurrency={concurrency}")

    def task_job(task: dict, trial: int) -> dict:
        wh = Warehouse(db_path)
        try:
            t0 = time.time()
            if builtin:
                result = {"submission": _builtin_submission(model.model, task, bench, wh), "status": "submitted",
                          "stats": {**_empty_stats(), "wall_time_s": 0.0}, "messages": []}
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

    def session_job(sess: dict, trial: int) -> dict:
        wh = Warehouse(db_path)
        sid = sess["session_id"]
        golds = {g["round"]: g for g in bench.session_keys[sid]["rounds"]}
        try:
            t0 = time.time()
            if builtin:
                result = {"rounds": [{"round": r["round"], "status": "submitted", "stats": _empty_stats(),
                                      "submission": _builtin_round(model.model, sess, r, golds, bench, wh)}
                                     for r in sess["rounds"]], "messages": []}
            else:
                agent = Agent(ChatClient(model), Toolbox(wh, agent_cfg.toolset, agent_cfg.session_tool_output_chars),
                              agent_cfg, tool_mode=model.tool_mode)
                result = agent.run_session(sess)
            rec = score_session(sess, golds, result, model.name, trial)
            rec["elapsed_s"] = round(time.time() - t0, 2)
            transcript = {"session_id": sid, "result": rec, "messages": result["messages"]}
        except Exception as e:
            rec = {"session_id": sid, "trial": trial, "model": model.name, "score": 0.0, "status": "harness_error",
                   "rounds": [], "peak_context_tokens": 0, "error": f"{type(e).__name__}: {e}"}
            transcript = {"session_id": sid, "error": traceback.format_exc()}
        finally:
            wh.close()
        with lock:
            with open(session_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            (out / "transcripts" / f"session_{sid}__t{trial}.json").write_text(
                json.dumps(transcript, indent=1, default=str))
        return rec

    finished = 0
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        futures = [ex.submit(session_job if kind == "session" else task_job, item, k) for kind, item, k in jobs]
        for fut in as_completed(futures):
            rec = fut.result()
            finished += 1
            if "session_id" in rec:
                n_ok = sum(1 for r in rec["rounds"] if r["status"] == "submitted")
                log(f"[{model.name}] {finished}/{len(jobs)} session {rec['session_id']} t{rec['trial']}: "
                    f"score={rec['score']:.3f} status={rec['status']} rounds={n_ok}/{len(rec['rounds'])} "
                    f"peak_context={rec['peak_context_tokens']:,}")
            else:
                st = rec.get("stats") or {}
                log(f"[{model.name}] {finished}/{len(jobs)} {rec['task_id']} t{rec['trial']}: "
                    f"score={rec['score']:.3f} status={rec['status']} tools={st.get('tool_calls', 0)} "
                    f"tokens={st.get('total_tokens', 0)}")
    summary = summarize(load_records(out), tasks) if tasks else {}
    s_records = load_session_records(out)
    if sessions or s_records:
        summary["longevity"] = summarize_sessions(s_records, sessions or bench.sessions)
    summary["model"] = model.name
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def _builtin_round(model: str, sess: dict, rnd: dict, golds: dict, bench: Benchmark, wh: Warehouse):
    if model == "oracle":
        return golds[rnd["round"]]["answer"]
    if model == "reference":
        from .solvers import solve
        if rnd["kind"] == "case_file":
            cf = case_file_warehouse(sess, bench.data_dir / ".cache")
            try:
                return solve(next(t for t in bench.tasks if t["task_id"] == rnd["source_task"]), cf)
            finally:
                cf.close()
        if rnd["kind"] == "investigation":
            return solve(next(t for t in bench.tasks if t["task_id"] == rnd["source_task"]), wh)
        return golds[rnd["round"]]["answer"]
    return {}


def score_session(sess: dict, golds: dict, result: dict, model_name: str, trial: int) -> dict:
    rounds, peak, src = [], 0, "estimate"
    for rnd, res in zip(sess["rounds"], result["rounds"]):
        sc = score_round(rnd, golds[rnd["round"]], res["submission"])
        st = res["stats"]
        if st.get("context_tokens_max"):
            src = "api"
        peak = max(peak, st.get("context_tokens_max") or st.get("est_context_max") or 0)
        rounds.append({"round": rnd["round"], "kind": rnd["kind"], "source_task": rnd.get("source_task"),
                       "tools": rnd["tools"], "weight": rnd["weight"], **sc, "status": res["status"],
                       "stats": st, "submission": res["submission"]})
    tw = sum(r["weight"] for r in rounds)
    aborted = next((r["status"] for r in rounds if r["status"] in ("api_error", "context_overflow")), None)
    return {"session_id": sess["session_id"], "trial": trial, "model": model_name,
            "score": round(sum(r["weight"] * r["score"] for r in rounds) / tw, 4) if tw else 0.0,
            "status": aborted or "completed", "rounds": rounds, "peak_context_tokens": peak,
            "peak_context_source": src}


def load_session_records(run_dir: str | Path) -> list[dict]:
    p = Path(run_dir) / "session_results.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in open(p, encoding="utf-8") if line.strip()]


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
    summary = summarize(records, [t for t in bench.tasks if t["task_id"] in ran]) if records else {}
    s_records = load_session_records(run_dir)
    if s_records:
        sess_by_id = {sx["session_id"]: sx for sx in bench.sessions}
        for rec in s_records:
            sx = sess_by_id.get(rec["session_id"])
            if not sx or rec["status"] == "harness_error":
                continue
            golds = {g["round"]: g for g in bench.session_keys[rec["session_id"]]["rounds"]}
            result = {"rounds": [{"submission": r["submission"], "status": r["status"], "stats": r["stats"]}
                                 for r in rec["rounds"]]}
            rec.update({k: v for k, v in score_session(sx, golds, result, rec["model"], rec["trial"]).items()
                        if k not in ("elapsed_s",)})
        with open(run_dir / "session_results.jsonl", "w", encoding="utf-8") as f:
            for rec in s_records:
                f.write(json.dumps(rec) + "\n")
        ran_s = {r["session_id"] for r in s_records}
        summary["longevity"] = summarize_sessions(s_records, [sx for sx in bench.sessions if sx["session_id"] in ran_s])
    summary["model"] = (records or s_records)[0]["model"] if (records or s_records) else run_dir.name
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary
