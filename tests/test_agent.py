"""End-to-end agent loop against a scripted fake OpenAI-compatible server."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from fincrime_bench.agent import AgentConfig, parse_text_action
from fincrime_bench.db import QueryError
from fincrime_bench.llm import ModelConfig
from fincrime_bench.runner import run_model


class FakeLLM:
    """Scripted model: run a query, profile an entity, then submit the gold answer."""

    def __init__(self, bench, mode="native"):
        self.mode = mode
        self.gold = {t["prompt"]: bench.keys[t["task_id"]]["answer"] for t in bench.tasks}
        self.requests = []
        self.fail_first = 0

    def answer_for(self, messages):
        user = next(m["content"] for m in messages if m["role"] == "user")
        for prompt, ans in self.gold.items():
            if user.startswith(prompt):
                return ans
        raise AssertionError("unknown task prompt")

    def respond(self, body):
        msgs = body["messages"]
        n_results = sum(1 for m in msgs if m["role"] == "tool" or
                        (m["role"] == "user" and str(m["content"]).startswith("Tool result")))
        if n_results == 0:
            calls = [("sql_query", {"query": "SELECT COUNT(*) AS n FROM persons"})]
        elif n_results == 1:
            calls = [("describe_table", {"table": "transactions"}), ("graph_neighbors", {"node_id": "P000000"})]
        else:
            calls = [("submit_answer", {"answer": self.answer_for(msgs)})]
        if self.mode == "text":
            name, args = calls[0]
            content = f"Thinking...\n```tool\n{json.dumps({'tool': name, 'arguments': args})}\n```"
            msg = {"role": "assistant", "content": content}
        else:
            msg = {"role": "assistant", "content": None, "tool_calls": [
                {"id": f"call_{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
                for i, (n, a) in enumerate(calls)]}
        return {"id": "x", "object": "chat.completion", "model": body["model"],
                "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}}


@pytest.fixture
def server(bench):
    fake = FakeLLM(bench)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            fake.requests.append((self.path, dict(self.headers), body))
            if fake.fail_first > 0:
                fake.fail_first -= 1
                self.send_response(429)
                self.send_header("Retry-After", "0")
                self.end_headers()
                self.wfile.write(b'{"error": "rate limited"}')
                return
            data = json.dumps(fake.respond(body)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    yield fake, f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()


@pytest.mark.parametrize("mode", ["native", "text"])
def test_end_to_end_run(server, bench, tmp_path, mode):
    fake, url = server
    fake.mode = mode
    fake.fail_first = 1  # exercise retry on HTTP 429
    model = ModelConfig(name=f"fake-{mode}", model="fake-model", base_url=url, api_key="sk-test",
                        params={"temperature": 0}, tool_mode=mode, max_retries=2)
    tasks = [t for t in bench.tasks if t["task_id"] in ("aml_layering_01", "own_ubo_02", "tax_unreported_01")]
    summary = run_model(bench, model, tmp_path / "run", AgentConfig(max_tool_calls=10), tasks, concurrency=2,
                        log=lambda *_: None)
    assert summary["score"] == 100.0
    assert summary["failures"] == 0
    path, headers, body = fake.requests[-1]
    assert path == "/v1/chat/completions"
    assert headers["Authorization"] == "Bearer sk-test"
    assert body["model"] == "fake-model" and body["temperature"] == 0
    assert ("tools" in body) == (mode == "native")
    recs = [json.loads(line) for line in open(tmp_path / "run" / "results.jsonl")]
    assert {r["task_id"] for r in recs} == {t["task_id"] for t in tasks}
    assert all(r["stats"]["tool_calls"] >= 1 for r in recs)
    transcript = json.loads((tmp_path / "run" / "transcripts" / "aml_layering_01__t0.json").read_text())
    tool_outputs = [m["content"] for m in transcript["messages"]
                    if m["role"] == "tool" or (m["role"] == "user" and m["content"].startswith("Tool result"))]
    assert any("2410" in c or "n\n" in c for c in tool_outputs)  # the persons count came back
    # resume: nothing left to do, no new requests
    n = len(fake.requests)
    run_model(bench, model, tmp_path / "run", AgentConfig(max_tool_calls=10), tasks, log=lambda *_: None)
    assert len(fake.requests) == n


def test_parse_text_action():
    assert parse_text_action('```tool\n{"tool": "sql_query", "arguments": {"query": "SELECT 1"}}\n```') == \
        ("sql_query", {"query": "SELECT 1"})
    assert parse_text_action('I will call {"name": "list_tables", "args": {}} now') == ("list_tables", {})
    assert parse_text_action("no action here") is None


def test_sql_sandbox_is_read_only(warehouse):
    for bad in ["DROP TABLE persons", "INSERT INTO persons(person_id) VALUES ('x')", "ATTACH DATABASE ':memory:' AS x",
                "PRAGMA writable_schema = 1", "CREATE TABLE t(x)", "SELECT 1; DROP TABLE persons"]:
        with pytest.raises(QueryError):
            warehouse.query(bad)
    cols, rows, _ = warehouse.query("SELECT COUNT(*) FROM persons")
    assert rows[0][0] > 1000


def test_query_time_limit(db_path):
    from fincrime_bench.db import Warehouse
    wh = Warehouse(db_path, time_limit_s=0.5)
    with pytest.raises(QueryError, match="time limit"):
        wh.query("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c")


def test_tools_produce_output(warehouse, bench):
    from fincrime_bench.tools import Toolbox
    tb = Toolbox(warehouse)
    t = next(t for t in bench.tasks if t["task_id"] == "own_ubo_03")
    target = t["inputs"]["business_id"]
    gold = bench.keys["own_ubo_03"]["answer"]["owners"][0]["id"]
    assert "registry roles" in tb.call("entity_profile", {"entity_id": target})
    assert "owns" in tb.call("graph_neighbors", {"node_id": target, "direction": "in"})
    paths = tb.call("find_paths", {"source": gold, "target": target, "edge_types": ["owns"], "max_hops": 5})
    assert gold in paths and target in paths
    assert tb.call("sql_query", {"query": "SELEC nonsense"}).startswith("SQL ERROR")
    assert tb.call("nope", {}).startswith("ERROR")
    assert "transactions" in tb.call("list_tables", {})
