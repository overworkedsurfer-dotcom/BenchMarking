"""Every task must be solvable from the data alone: the reference solvers must score 1.0."""

import pytest

from conftest import read_jsonl, PUBLIC

from fincrime_bench.scoring import score_task
from fincrime_bench.solvers import solve

TASKS = read_jsonl(PUBLIC / "tasks.jsonl")


@pytest.mark.parametrize("task", TASKS, ids=[t["task_id"] for t in TASKS])
def test_reference_solver_scores_perfectly(task, bench, warehouse):
    result = score_task(task, bench.keys[task["task_id"]], solve(task, warehouse))
    assert result["score"] == pytest.approx(1.0), result["fields"]


@pytest.mark.slow
@pytest.mark.parametrize("seed", [3, 11])
def test_other_seeds_are_valid(seed, tmp_path):
    from fincrime_bench.db import Warehouse, ensure_db
    from fincrime_bench.generator import generate
    from fincrime_bench.runner import load_benchmark
    generate(seed=seed, out_dir=tmp_path)
    b = load_benchmark(tmp_path)
    wh = Warehouse(ensure_db(tmp_path))
    for t in b.tasks:
        r = score_task(t, b.keys[t["task_id"]], solve(t, wh))
        assert r["score"] == pytest.approx(1.0), (t["task_id"], r["fields"])
