"""Command-line interface: ``fincrime-bench <command>`` (or ``python -m fincrime_bench``)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

DEFAULT_DATA = "data/public"


def _parse_kv(items: list[str] | None) -> dict:
    out = {}
    for it in items or []:
        if "=" not in it:
            raise SystemExit(f"expected key=value, got {it!r}")
        k, v = it.split("=", 1)
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


def _load_config(path: str) -> dict:
    p = Path(path)
    if p.suffix == ".toml":
        import tomllib
        return tomllib.loads(p.read_text())
    return json.loads(p.read_text())


def _model_from_entry(entry: dict, defaults: dict):
    from .llm import ModelConfig
    e = {**defaults, **entry}
    e["params"] = {**defaults.get("params", {}), **entry.get("params", {})}
    key = e.get("api_key")
    if not key and e.get("api_key_env"):
        key = os.environ.get(e["api_key_env"])
        if key is None and e.get("base_url") != "builtin":
            print(f"warning: env var {e['api_key_env']} is not set (model {e.get('name')})", file=sys.stderr)
    return ModelConfig(name=e.get("name") or e["model"], model=e["model"],
                       base_url=e.get("base_url", "https://api.openai.com/v1"), api_key=key,
                       params=e["params"], headers=e.get("headers", {}), tool_mode=e.get("tool_mode", "native"),
                       timeout_s=float(e.get("timeout_s", 600)), max_retries=int(e.get("max_retries", 6)))


# ------------------------------------------------------------------ commands
def cmd_generate(a) -> int:
    from .generator import generate
    m = generate(seed=a.seed, out_dir=a.out, scale=a.scale)
    print(f"wrote {a.out}: {m['tasks']} tasks, " + ", ".join(f"{k}={v['rows']}" for k, v in m["tables"].items()))
    return 0


def cmd_validate(a) -> int:
    from .db import Warehouse, ensure_db
    from .runner import load_benchmark
    from .scoring import score_task
    from .solvers import solve
    bench = load_benchmark(a.data)
    wh = Warehouse(ensure_db(bench.data_dir), time_limit_s=300)
    bad = 0
    for t in bench.tasks:
        r = score_task(t, bench.keys[t["task_id"]], solve(t, wh))
        flag = "ok " if r["score"] >= 0.9999 else "FAIL"
        bad += flag == "FAIL"
        print(f"{flag} {t['task_id']:20s} {r['score']:.3f}")
        if flag == "FAIL":
            print("     ", {k: v for k, v in r["fields"].items() if v["score"] < 1})
    print(f"\n{len(bench.tasks) - bad}/{len(bench.tasks)} tasks solved by the reference solvers")
    return 1 if bad else 0


def cmd_tasks(a) -> int:
    from .runner import load_benchmark
    from .tools import answer_format
    bench = load_benchmark(a.data)
    if a.show:
        t = next((t for t in bench.tasks if t["task_id"] == a.show), None)
        if not t:
            print(f"no task {a.show}")
            return 1
        print(f"# {t['task_id']} — {t['title']} [{t['category']}/{t['difficulty']}]\n\n{t['prompt']}\n\n"
              f"{answer_format(t)}")
        return 0
    print(f"{'task_id':20s} {'category':14s} {'difficulty':10s} title")
    for t in bench.tasks:
        print(f"{t['task_id']:20s} {t['category']:14s} {t['difficulty']:10s} {t['title']}")
    return 0


def cmd_run(a) -> int:
    from .agent import AgentConfig
    from .llm import ModelConfig
    from .runner import load_benchmark, run_model, select_tasks, slug
    bench = load_benchmark(a.data)
    tasks = select_tasks(bench.tasks, a.tasks, a.category, a.difficulty)
    if not tasks:
        print("no tasks selected")
        return 1
    models: list[ModelConfig] = []
    if a.models_config:
        cfg = _load_config(a.models_config)
        defaults = cfg.get("defaults", {})
        for entry in cfg["models"]:
            if a.only and entry.get("name", entry["model"]) not in a.only:
                continue
            models.append(_model_from_entry(entry, defaults))
    elif a.model:
        base = a.base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        if a.model in ("oracle", "reference", "null") and not a.base_url:
            base = "builtin"
        key = a.api_key or os.environ.get(a.api_key_env or "OPENAI_API_KEY")
        models.append(ModelConfig(name=a.name or a.model, model=a.model, base_url=base, api_key=key,
                                  params=_parse_kv(a.param), headers=_parse_kv(a.header), tool_mode=a.tool_mode,
                                  timeout_s=a.timeout))
    else:
        print("give --model (with --base-url/--api-key) or --models-config")
        return 2
    agent_cfg = AgentConfig(max_tool_calls=a.max_tool_calls, max_turns=a.max_turns, toolset=a.toolset,
                            max_tool_output_chars=a.max_tool_output_chars)
    summaries = []
    for m in models:
        out = Path(a.out) / (a.run_name if a.run_name and len(models) == 1 else slug(m.name))
        s = run_model(bench, m, out, agent_cfg, tasks, trials=a.trials, concurrency=a.concurrency,
                      resume=not a.no_resume)
        summaries.append((m.name, s, out))
        print(f"\n== {m.name}: score {s['score']:.1f} (95% CI {s['ci95'][0]:.1f}-{s['ci95'][1]:.1f}) "
              f"by category {s['by_category']} -> {out}")
    if len(summaries) > 1:
        from .report import leaderboard
        md, _ = leaderboard([o for _n, _s, o in summaries])
        (Path(a.out) / "LEADERBOARD.md").write_text(md)
        print("\n" + md)
    return 0


def cmd_rescore(a) -> int:
    from .runner import load_benchmark, rescore
    bench = load_benchmark(a.data)
    for d in a.run_dirs:
        s = rescore(d, bench)
        print(f"{d}: score {s['score']:.1f}")
    return 0


def cmd_leaderboard(a) -> int:
    from .report import leaderboard
    dirs = []
    for d in a.run_dirs:
        p = Path(d)
        if (p / "results.jsonl").exists():
            dirs.append(p)
        elif p.is_dir():
            dirs += sorted(x for x in p.iterdir() if (x / "results.jsonl").exists())
    md, rows = leaderboard(dirs)
    if a.out:
        Path(a.out).write_text(md)
        Path(a.out).with_suffix(".json").write_text(json.dumps(rows, indent=2))
    print(md)
    return 0


def cmd_schema(a) -> int:
    from .schema import ALL_TABLES
    for name, spec in ALL_TABLES.items():
        print(f"## {name}\n\n{spec['description']}\n\n| column | type | description |\n|---|---|---|")
        for c, t, d in spec["columns"]:
            print(f"| `{c}` | {t} | {d} |")
        print()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fincrime-bench", description="Financial-crime data-analysis benchmark for LLMs.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("generate", help="generate a synthetic world + tasks + answer key")
    p.add_argument("--seed", type=int, default=20251)
    p.add_argument("--scale", type=float, default=1.0, help="population scale (1.0 = ~2,400 persons)")
    p.add_argument("--out", default=DEFAULT_DATA)
    p.set_defaults(fn=cmd_generate)

    p = sub.add_parser("validate", help="check every task is solvable by the reference solvers")
    p.add_argument("--data", default=DEFAULT_DATA)
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("tasks", help="list tasks or show one prompt")
    p.add_argument("--data", default=DEFAULT_DATA)
    p.add_argument("--show", help="task_id to print")
    p.set_defaults(fn=cmd_tasks)

    p = sub.add_parser("run", help="run model(s) on the benchmark")
    p.add_argument("--data", default=DEFAULT_DATA)
    p.add_argument("--model", help="model id sent to the API (or builtin: oracle | reference | null)")
    p.add_argument("--name", help="display name for results (default: model id)")
    p.add_argument("--base-url", help="OpenAI-compatible base URL, e.g. https://api.openai.com/v1 "
                                      "(default $OPENAI_BASE_URL)")
    p.add_argument("--api-key", help="API key (prefer --api-key-env)")
    p.add_argument("--api-key-env", help="env var holding the API key (default OPENAI_API_KEY)")
    p.add_argument("--param", action="append", help="extra request body param key=value (JSON values), "
                                                     "e.g. temperature=0 max_tokens=4096")
    p.add_argument("--header", action="append", help="extra HTTP header key=value")
    p.add_argument("--tool-mode", choices=["native", "text"], default="native",
                   help="native function calling, or text JSON action blocks for models without tool support")
    p.add_argument("--timeout", type=float, default=600.0, help="per-request timeout seconds")
    p.add_argument("--models-config", help="TOML/JSON file listing several models (see models.example.toml)")
    p.add_argument("--only", nargs="*", help="with --models-config: run only these model names")
    p.add_argument("--tasks", nargs="*", help="task ids to run (default all)")
    p.add_argument("--category", nargs="*", help="filter: aml tax ownership telecom investigation")
    p.add_argument("--difficulty", nargs="*", help="filter: easy medium hard")
    p.add_argument("--trials", type=int, default=1, help="repeat each task N times")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--max-tool-calls", type=int, default=40)
    p.add_argument("--max-turns", type=int, default=60)
    p.add_argument("--max-tool-output-chars", type=int, default=8000, help="truncate each tool result")
    p.add_argument("--toolset", choices=["full", "sql"], default="full",
                   help="full = SQL + graph helpers; sql = SQL only")
    p.add_argument("--out", default="results")
    p.add_argument("--run-name", help="output sub-directory (single model only)")
    p.add_argument("--no-resume", action="store_true", help="discard previous results in the output directory")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("rescore", help="re-score stored submissions")
    p.add_argument("run_dirs", nargs="+")
    p.add_argument("--data", default=DEFAULT_DATA)
    p.set_defaults(fn=cmd_rescore)

    p = sub.add_parser("leaderboard", help="compare runs")
    p.add_argument("run_dirs", nargs="+", help="run directories, or a parent directory containing runs")
    p.add_argument("--out", help="write markdown here (and .json alongside)")
    p.set_defaults(fn=cmd_leaderboard)

    p = sub.add_parser("schema", help="print the data dictionary as markdown")
    p.set_defaults(fn=cmd_schema)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
