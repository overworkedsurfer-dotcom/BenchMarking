"""Longevity sessions: >=32k-token case files, many rounds in one conversation, validation and scoring."""

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from fincrime_bench.agent import AgentConfig
from fincrime_bench.generator.sessions import CHARS_PER_TOKEN, parse_case_file
from fincrime_bench.llm import ModelConfig
from fincrime_bench.runner import run_model
from fincrime_bench.sessions import case_file_text, validate_sessions


def test_sessions_are_long_and_multi_round(bench):
    assert len(bench.sessions) >= 4
    used = []
    for s in bench.sessions:
        assert len(s["rounds"]) >= 9
        assert len(case_file_text(s)) / CHARS_PER_TOKEN >= 32_000  # conservative: real tokenizers give more
        kinds = {r["kind"] for r in s["rounds"]}
        assert kinds == {"case_file", "investigation", "recall", "synthesis"}
        assert all(not r["tools"] for r in s["rounds"] if r["kind"] != "investigation")
        assert s["rounds"][-1]["kind"] == "synthesis"
        used += [r["source_task"] for r in s["rounds"] if r["source_task"]]
    assert sorted(used) == sorted(t["task_id"] for t in bench.tasks)  # every task appears exactly once


def test_case_file_round_trips(bench):
    s = bench.sessions[0]
    parsed = parse_case_file(case_file_text(s))
    assert {t: len(rows) for t, (_c, rows) in parsed.items()} == s["case_file_rows"]


def test_every_round_is_solvable_from_its_evidence(bench, warehouse, tmp_path):
    tasks = {t["task_id"]: t for t in bench.tasks}
    res = validate_sessions(bench.sessions, bench.session_keys, tasks, warehouse, tmp_path)
    bad = [(r["session_id"], r["round"], r["fields"]) for r in res if r["score"] < 0.9999]
    assert not bad


# ------------------------------------------------------------------ fake model
class FakeSessionLLM:
    """Plays through sessions: queries once in tool rounds, answers with the gold, reports realistic usage."""

    def __init__(self, bench, mode="native", misbehave=False, grow_limit=None):
        self.mode, self.misbehave, self.grow_limit = mode, misbehave, grow_limit
        self.first_chars = None
        self.by_title = {s["title"]: s for s in bench.sessions}
        self.keys = bench.session_keys
        self.requests = []

    def respond(self, body):
        msgs = body["messages"]
        chars = sum(len(m.get("content") or "") for m in msgs)
        self.first_chars = self.first_chars or chars
        if self.grow_limit and chars - self.first_chars > self.grow_limit:  # the window fills up mid-session
            return 400, {"error": {"message": "This model's maximum context length is exceeded."}}
        first = next(m["content"] for m in msgs if m["role"] == "user")
        sess = self.by_title[re.search(r"CASE FILE — (.+)", first).group(1)]
        last = max(i for i, m in enumerate(msgs) if m["role"] == "user" and re.search(r"ROUND \d+ of", m["content"]))
        k = int(re.search(r"ROUND (\d+) of", msgs[last]["content"]).group(1))
        rnd = sess["rounds"][k - 1]
        gold = self.keys[sess["session_id"]]["rounds"][k - 1]["answer"]
        after = msgs[last + 1:]
        results = sum(1 for m in after if m["role"] == "tool" or
                      (m["role"] == "user" and m["content"].startswith("Tool result")))
        if results == 0 and (rnd["tools"] or self.misbehave):
            call = ("sql_query", {"query": "SELECT COUNT(*) AS n FROM calls"})
        else:
            call = ("submit_answer", {"answer": gold})
        if self.mode == "text":
            msg = {"role": "assistant",
                   "content": f"```tool\n{json.dumps({'tool': call[0], 'arguments': call[1]})}\n```"}
        else:
            msg = {"role": "assistant", "content": None, "tool_calls": [
                {"id": f"c{len(msgs)}", "type": "function",
                 "function": {"name": call[0], "arguments": json.dumps(call[1])}}]}
        return 200, {"choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls"}],
                     "usage": {"prompt_tokens": int(chars / 2.2), "completion_tokens": 20,
                               "total_tokens": int(chars / 2.2) + 20}}


@pytest.fixture
def fake_server():
    holder = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            fake = holder["fake"]
            fake.requests.append(body)
            if not any(m["role"] == "user" and "CASE FILE" in (m.get("content") or "") for m in body["messages"]):
                code, payload = 200, {"choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"},
                                                   "finish_reason": "stop"}]}  # preflight ping
            else:
                code, payload = fake.respond(body)
            data = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield holder, f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()


@pytest.mark.parametrize("mode,misbehave", [("native", False), ("text", False), ("native", True)])
def test_session_end_to_end(fake_server, bench, tmp_path, mode, misbehave):
    holder, url = fake_server
    holder["fake"] = fake = FakeSessionLLM(bench, mode, misbehave)
    model = ModelConfig(name="fake", model="fake", base_url=url, api_key="k", tool_mode=mode, max_retries=0)
    sessions = bench.sessions[:2]
    summary = run_model(bench, model, tmp_path / "run", AgentConfig(), tasks=[], sessions=sessions,
                        concurrency=2, log=lambda *_: None)
    lv = summary["longevity"]
    assert lv["score"] == 100.0 and lv["rounds_lost"] == 0
    assert lv["min_first_round_context"] >= 32_000          # the very first question already sits past 32k
    assert lv["peak_context_tokens"] > lv["min_first_round_context"]  # and the conversation keeps growing
    assert lv["context_source"] == "api"
    recs = [json.loads(line) for line in open(tmp_path / "run" / "session_results.jsonl")]
    for rec in recs:
        ctx = [r["stats"]["context_tokens_start"] for r in rec["rounds"]]
        assert ctx == sorted(ctx)  # one growing conversation, never reset between rounds
        assert all(r["status"] == "submitted" for r in rec["rounds"])
    if mode == "native":
        # rounds without tools only offer submit_answer
        assert any(len(b.get("tools", [])) == 1 for b in fake.requests)
    if misbehave:
        t = json.loads((tmp_path / "run" / "transcripts" / "session_long_01__t0.json").read_text())
        assert any("tools are disabled" in (m.get("content") or "") for m in t["messages"])


def test_session_context_overflow_is_recorded(fake_server, bench, tmp_path):
    holder, url = fake_server
    holder["fake"] = FakeSessionLLM(bench, "native", grow_limit=2_500)
    model = ModelConfig(name="small-ctx", model="small", base_url=url, api_key="k", max_retries=0)
    summary = run_model(bench, model, tmp_path / "run", AgentConfig(), tasks=[], sessions=bench.sessions[:1],
                        log=lambda *_: None)
    lv = summary["longevity"]
    rec = json.loads(open(tmp_path / "run" / "session_results.jsonl").readline())
    assert rec["status"] == "context_overflow"
    assert lv["rounds_lost"] > 0 and 0 < lv["score"] < 100
    statuses = [r["status"] for r in rec["rounds"]]
    first_lost = statuses.index("context_overflow")
    assert all(s == "context_overflow" for s in statuses[first_lost:])
