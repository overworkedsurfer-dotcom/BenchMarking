"""Agent loop: drives a model through an investigation with tools until it submits an answer."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

from .llm import ChatClient, LLMError
from .schema import compact_schema
from .tools import TOOL_DEFS, TOOLSETS, Toolbox, answer_format, submit_tool, tool_specs


@dataclass
class AgentConfig:
    max_tool_calls: int = 40       # investigative tool calls (submit_answer not counted)
    max_turns: int = 60            # model calls
    toolset: str = "full"          # full | sql
    max_tool_output_chars: int = 8_000
    max_nudges: int = 3
    session_tool_calls: int = 20   # per round in longevity sessions
    session_max_turns: int = 30    # per round in longevity sessions
    session_tool_output_chars: int = 6_000


SYSTEM_PROMPT = """You are an expert financial-crimes investigator (anti-money-laundering, fraud and tax-evasion \
analytics) with read access to an investigative data warehouse. All people, companies and records are synthetic. \
The data covers calendar year 2025 (tax returns are for tax year 2025).

Work methodically: explore the relevant tables, follow the evidence across sources (bank transactions, \
online-banking logs, call records, corporate registry, tax filings, asset records), and verify before you conclude. \
Accusing people the evidence does not support is penalised just like missing culprits.

Warehouse tables (use describe_table for column meanings):
{schema}

Rules:
- You may make at most {max_tool_calls} tool calls before you must submit.
- Use exact IDs from the data. Monetary values are USD.
- Timestamps are text 'YYYY-MM-DDTHH:MM:SS' (with a T); dates are 'YYYY-MM-DD'. SQLite LIKE is case-insensitive.
- Finish by calling submit_answer exactly once with your final answer."""

TEXT_PROTOCOL = """

You interact with tools by replying with exactly one tool call per message, formatted as a JSON object in a \
fenced block labelled tool, for example:
```tool
{{"tool": "sql_query", "arguments": {{"query": "SELECT COUNT(*) FROM persons"}}}}
```
You will receive the tool result in the next message. Available tools:
{tool_list}
To finish, call submit_answer the same way: {{"tool": "submit_answer", "arguments": {{"answer": {{...}}}}}}"""


SESSION_NOTE = """

This is a long multi-round session. You first receive a CASE FILE (an extract of warehouse records), then \
questions one at a time in this same conversation. Each round says whether tools are enabled; when they are \
disabled, answer from the case file and the conversation so far. The tool-call limit applies per round. Later \
rounds may refer back to the case file or to earlier rounds, so keep track of your findings."""


def build_system_prompt(cfg: AgentConfig, task: dict | None, text_mode: bool, max_tool_calls: int | None = None,
                        session: bool = False) -> str:
    sp = SYSTEM_PROMPT.format(schema=compact_schema(), max_tool_calls=max_tool_calls or cfg.max_tool_calls)
    if session:
        sp += SESSION_NOTE
    if text_mode:
        lines = []
        for name in TOOLSETS[cfg.toolset]:
            fn = TOOL_DEFS[name]["function"]
            params = ", ".join(f"{k}: {v.get('type', 'any')}" for k, v in fn["parameters"]["properties"].items())
            lines.append(f"- {name}({params}): {fn['description']}")
        lines.append("- submit_answer(answer: object): submit the final answer.")
        sp += TEXT_PROTOCOL.format(tool_list="\n".join(lines))
    return sp


def build_user_prompt(task: dict) -> str:
    return f"{task['prompt']}\n\n{answer_format(task)}"


# ------------------------------------------------------------------ parsing
def _json_objects(text: str) -> list[dict]:
    """Extract top-level JSON objects from free text (handles nesting and strings)."""
    out, depth, start, in_str, esc = [], 0, None, False, False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"' and depth > 0:
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    obj = json.loads(text[start: i + 1])
                    if isinstance(obj, dict):
                        out.append(obj)
                except json.JSONDecodeError:
                    pass
                start = None
    return out


def parse_text_action(text: str) -> tuple[str, dict] | None:
    blocks = re.findall(r"```(?:tool|json)?\s*(\{.*?\})\s*```", text, flags=re.S)
    candidates = []
    for b in blocks:
        candidates.extend(_json_objects(b))
    candidates.extend(_json_objects(text))
    for obj in candidates:
        name = obj.get("tool") or obj.get("name") or obj.get("action")
        if isinstance(name, str):
            args = obj.get("arguments", obj.get("args", obj.get("parameters", obj.get("input", {}))))
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            return name, args if isinstance(args, dict) else {}
    return None


def extract_answer_from_text(text: str, task: dict) -> dict | None:
    names = {f["name"] for f in task["answer_fields"]}
    for obj in reversed(_json_objects(text or "")):
        cand = obj.get("answer") if isinstance(obj.get("answer"), dict) else obj
        if isinstance(cand, dict) and names & set(cand):
            return cand
    return None


def _parse_args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {"_raw": v}
    except json.JSONDecodeError:
        objs = _json_objects(raw)
        return objs[0] if objs else {"_unparseable": str(raw)[:500]}


def _clean_assistant(msg: dict) -> dict:
    out = {"role": "assistant", "content": msg.get("content") or ""}
    if msg.get("tool_calls"):
        out["tool_calls"] = [{"id": tc.get("id") or f"call_{i}", "type": "function",
                              "function": {"name": tc["function"]["name"],
                                           "arguments": tc["function"].get("arguments") or "{}"}}
                             for i, tc in enumerate(msg["tool_calls"])]
        if not out["content"]:
            out["content"] = None
    return out


# -------------------------------------------------------------------- agent
def _empty_stats() -> dict:
    return {"turns": 0, "tool_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            "api_errors": 0, "invalid_submissions": 0, "nudges": 0, "context_tokens_start": 0,
            "context_tokens_max": 0, "est_context_start": 0, "est_context_max": 0}


def estimate_tokens(messages: list[dict]) -> int:
    """Rough context size when the API reports no usage (chars / 3 over everything sent)."""
    n = 0
    for m in messages:
        n += len(m.get("content") or "")
        for tc in m.get("tool_calls") or []:
            n += len(tc["function"].get("arguments") or "")
    return int(n / 3.0)


class Agent:
    def __init__(self, client: ChatClient, toolbox: Toolbox, cfg: AgentConfig, tool_mode: str = "native"):
        self.client = client
        self.toolbox = toolbox
        self.cfg = cfg
        self.text_mode = tool_mode == "text"

    def run(self, task: dict) -> dict:
        """One standalone task in a fresh conversation."""
        messages = [{"role": "system", "content": build_system_prompt(self.cfg, task, self.text_mode)},
                    {"role": "user", "content": build_user_prompt(task)}]
        t0 = time.time()
        res = self._loop(messages, task, True, self.cfg.max_tool_calls, self.cfg.max_turns)
        res["stats"]["wall_time_s"] = round(time.time() - t0, 2)
        res["messages"] = messages
        return res

    def run_session(self, session: dict) -> dict:
        """A longevity session: the case file and every round share one ever-growing conversation."""
        cfg = self.cfg
        messages = [{"role": "system", "content": build_system_prompt(cfg, None, self.text_mode,
                                                                      max_tool_calls=cfg.session_tool_calls,
                                                                      session=True)}]
        rounds, aborted = [], None
        t0 = time.time()
        for i, rnd in enumerate(session["rounds"]):
            if aborted:  # the conversation cannot continue (e.g. it no longer fits the model's context)
                rounds.append({"round": rnd["round"], "submission": None, "status": aborted,
                               "stats": _empty_stats()})
                continue
            text = f"{rnd['prompt']}\n\n{answer_format(rnd)}"
            if i == 0:
                text = f"{session['preamble']}\n\n{text}"
            messages.append({"role": "user", "content": text})
            r0 = time.time()
            res = self._loop(messages, rnd, rnd["tools"], cfg.session_tool_calls, cfg.session_max_turns)
            res["stats"]["wall_time_s"] = round(time.time() - r0, 2)
            res["round"] = rnd["round"]
            rounds.append(res)
            if res["status"] in ("api_error", "context_overflow"):
                aborted = res["status"]
        return {"rounds": rounds, "messages": messages, "wall_time_s": round(time.time() - t0, 2)}

    # ------------------------------------------------------------------ core loop
    def _loop(self, messages: list[dict], task: dict, allow_tools: bool, max_tool_calls: int,
              max_turns: int) -> dict:
        """Drive the model until it submits an answer for ``task`` (appends to ``messages`` in place)."""
        cfg = self.cfg
        names = [f["name"] for f in task["answer_fields"]]
        if self.text_mode:
            tools = None
        else:
            tools = tool_specs(cfg.toolset, task) if allow_tools else [submit_tool(task)]
        stats = _empty_stats()
        submission = None
        status = "max_turns"

        def try_submit(ans) -> str | None:
            """Returns an error message for the model, or None if accepted."""
            nonlocal submission
            if isinstance(ans, dict) and isinstance(ans.get("answer"), str):
                try:
                    ans = {**ans, "answer": json.loads(ans["answer"])}
                except json.JSONDecodeError:
                    pass
            if isinstance(ans, str):
                try:
                    ans = json.loads(ans)
                except json.JSONDecodeError:
                    pass
            if isinstance(ans, dict) and isinstance(ans.get("answer"), dict):
                ans = ans["answer"]
            if not isinstance(ans, dict):
                stats["invalid_submissions"] += 1
                if stats["invalid_submissions"] >= 2:
                    submission = {}
                    return None
                return "ERROR: answer must be a JSON object with fields: " + ", ".join(names)
            missing = [n for n in names if n not in ans]
            if missing and stats["invalid_submissions"] == 0:
                stats["invalid_submissions"] += 1
                return f"ERROR: answer is missing fields {missing}. Resubmit with all fields: {', '.join(names)}"
            submission = ans
            return None

        def run_tool(name: str, args: dict) -> str:
            if not allow_tools:
                return ("ERROR: tools are disabled for this round. Answer from the case file and the conversation, "
                        "then call submit_answer.")
            if stats["tool_calls"] >= max_tool_calls:
                return "ERROR: tool budget exhausted. Call submit_answer now."
            stats["tool_calls"] += 1
            return self.toolbox.call(name, args)

        def budget_note() -> str:
            if not allow_tools:
                return ""
            left = max_tool_calls - stats["tool_calls"]
            if left <= 0:
                return "\n\n[Tool budget exhausted. Submit your answer now with submit_answer.]"
            if left <= 5:
                return f"\n\n[{left} tool calls left.]"
            return ""

        while stats["turns"] < max_turns:
            stats["turns"] += 1
            send = [m for m in messages if not m["role"].startswith("_")]
            est = estimate_tokens(send)
            stats["est_context_start"] = stats["est_context_start"] or est
            stats["est_context_max"] = max(stats["est_context_max"], est)
            try:
                resp = self.client.chat(send, tools=tools)
            except LLMError as e:
                stats["api_errors"] += 1
                overflow = e.status == 400 and re.search(r"context|too long|too many tokens|maximum.{0,40}tokens",
                                                         str(e), re.I)
                status = "context_overflow" if overflow else "api_error"
                messages.append({"role": "_error", "content": str(e)})
                break
            usage = resp.get("usage") or {}
            for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                stats[k] += int(usage.get(k) or 0)
            ctx = int(usage.get("prompt_tokens") or 0)
            stats["context_tokens_start"] = stats["context_tokens_start"] or ctx
            stats["context_tokens_max"] = max(stats["context_tokens_max"], ctx)
            choice = resp["choices"][0]
            msg = choice.get("message") or {}
            content = msg.get("content") or ""
            if isinstance(content, list):  # some gateways return content parts
                content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
                msg["content"] = content

            if self.text_mode:
                messages.append({"role": "assistant", "content": content})
                action = parse_text_action(content)
                if action is None:
                    ans = extract_answer_from_text(content, task)
                    if ans is not None and try_submit(ans) is None:
                        status = "submitted"
                        break
                    if stats["nudges"] >= cfg.max_nudges:
                        status = "no_submission"
                        break
                    stats["nudges"] += 1
                    messages.append({"role": "user", "content": "No tool call found. Reply with exactly one ```tool "
                                     "block (a tool call or submit_answer)."})
                    continue
                name, args = action
                if name == "submit_answer":
                    err = try_submit(args)
                    if err is None:
                        status = "submitted"
                        break
                    messages.append({"role": "user", "content": err})
                    continue
                result = run_tool(name, args)
                messages.append({"role": "user", "content": f"Tool result ({name}):\n{result}{budget_note()}"})
                continue

            # ---- native tool calling
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls and msg.get("function_call"):  # legacy single function_call
                tool_calls = [{"id": "call_legacy", "type": "function", "function": msg["function_call"]}]
                msg["tool_calls"] = tool_calls
            assistant = _clean_assistant(msg)
            messages.append(assistant)
            if not tool_calls:
                action = parse_text_action(content)
                if action and action[0] in TOOLSETS[cfg.toolset]:  # model wrote the tool call as text: honour it
                    result = run_tool(action[0], action[1])
                    messages.append({"role": "user", "content": f"Tool result ({action[0]}):\n{result}"
                                     f"{budget_note()}\n(Prefer native tool calls.)"})
                    continue
                ans = extract_answer_from_text(content, task)
                if ans is not None and try_submit(ans) is None:
                    status = "submitted"
                    break
                if stats["nudges"] >= cfg.max_nudges:
                    status = "no_submission"
                    break
                stats["nudges"] += 1
                hint = "with the tools" if allow_tools else "from the case file and the conversation"
                messages.append({"role": "user", "content": f"Continue {hint}, or call submit_answer with your "
                                 f"final answer."})
                continue
            done = False
            for i, tc in enumerate(tool_calls):
                fn = tc.get("function") or {}
                name = fn.get("name", "")
                args = _parse_args(fn.get("arguments"))
                call_id = assistant["tool_calls"][i]["id"]
                if done:
                    result = "Ignored: answer already submitted."
                elif name == "submit_answer":
                    err = try_submit(args)
                    if err is None:
                        done = True
                        result = "Answer received."
                    else:
                        result = err
                else:
                    result = run_tool(name, args)
                messages.append({"role": "tool", "tool_call_id": call_id, "content": result})
            if done:
                status = "submitted"
                break
            if messages[-1]["role"] == "tool":
                messages[-1]["content"] += budget_note()
        if submission is None and status == "max_turns":
            status = "no_submission"
        return {"submission": submission, "status": status, "stats": stats}
