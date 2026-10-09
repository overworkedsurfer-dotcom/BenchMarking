import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "data" / "public"


@pytest.fixture(scope="session")
def bench():
    from fincrime_bench.runner import load_benchmark
    return load_benchmark(PUBLIC)


@pytest.fixture(scope="session")
def warehouse(bench, tmp_path_factory):
    from fincrime_bench.db import Warehouse, ensure_db
    db = ensure_db(bench.data_dir, tmp_path_factory.mktemp("cache"))
    wh = Warehouse(db)
    yield wh
    wh.close()


@pytest.fixture(scope="session")
def db_path(bench, tmp_path_factory):
    from fincrime_bench.db import ensure_db
    return ensure_db(bench.data_dir, tmp_path_factory.mktemp("cache2"))


def read_jsonl(path):
    return [json.loads(line) for line in open(path) if line.strip()]
