"""Agent loop: drives a model through an investigation with tools until it submits an answer."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

from .llm import ChatClient, LLMError
from .schema import compact_schema
from .tools import TOOL_DEFS, TOOLSETS, Toolbox, answer_format, tool_specs


@dataclass
class AgentConfig:
    max_tool_calls: int = 40       # investigative tool calls (submit_answer not counted)
    max_turns: int = 60            # model calls
    toolset: str = "full"          # full | sql
    max_tool_output_chars: int = 8_000
    max_nudges: int = 3


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


def build_system_prompt(cfg: AgentConfig, task: dict, text_mode: bool) -> str:
    sp = SYSTEM_PROMPT.format(schema=compact_schema(), max_tool_calls=cfg.max_tool_calls)
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
class Agent:
    def __init__(self, client: ChatClient, toolbox: Toolbox, cfg: AgentConfig, tool_mode: str = "native"):
        self.client = client
        self.toolbox = toolbox
        self.cfg = cfg
        self.text_mode = tool_mode == "text"

    def run(self, task: dict) -> dict:
        cfg = self.cfg
        names = [f["name"] for f in task["answer_fields"]]
        messages = [{"role": "system", "content": build_system_prompt(cfg, task, self.text_mode)},
                    {"role": "user", "content": build_user_prompt(task)}]
        tools = None if self.text_mode else tool_specs(cfg.toolset, task)
        stats = {"turns": 0, "tool_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                 "api_errors": 0, "invalid_submissions": 0, "nudges": 0}
        submission = None
        status = "max_turns"
        t0 = time.time()

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

        while stats["turns"] < cfg.max_turns:
            stats["turns"] += 1
            try:
                resp = self.client.chat(messages, tools=tools)
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
                result = self._exec(name, args, stats)
                note = self._budget_note(stats)
                messages.append({"role": "user", "content": f"Tool result ({name}):\n{result}{note}"})
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
                known = TOOLSETS[cfg.toolset]
                if action and action[0] in known:  # model wrote the tool call as text: honour it
                    result = self._exec(action[0], action[1], stats)
                    messages.append({"role": "user", "content": f"Tool result ({action[0]}):\n{result}"
                                     f"{self._budget_note(stats)}\n(Prefer native tool calls.)"})
                    continue
                ans = extract_answer_from_text(content, task)
                if ans is not None and try_submit(ans) is None:
                    status = "submitted"
                    break
                if stats["nudges"] >= cfg.max_nudges:
                    status = "no_submission"
                    break
                stats["nudges"] += 1
                messages.append({"role": "user", "content": "Continue the investigation with the tools, or call "
                                 "submit_answer with your final answer."})
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
                    result = self._exec(name, args, stats)
                messages.append({"role": "tool", "tool_call_id": call_id, "content": result})
            if done:
                status = "submitted"
                break
            if messages[-1]["role"] == "tool":
                messages[-1]["content"] += self._budget_note(stats)
        if submission is None and status == "max_turns":
            status = "no_submission"
        stats["wall_time_s"] = round(time.time() - t0, 2)
        return {"submission": submission, "status": status, "stats": stats, "messages": messages}

    def _exec(self, name: str, args: dict, stats: dict) -> str:
        if stats["tool_calls"] >= self.cfg.max_tool_calls:
            return "ERROR: tool budget exhausted. Call submit_answer now."
        stats["tool_calls"] += 1
        return self.toolbox.call(name, args)

    def _budget_note(self, stats: dict) -> str:
        left = self.cfg.max_tool_calls - stats["tool_calls"]
        if left <= 0:
            return "\n\n[Tool budget exhausted. Submit your answer now with submit_answer.]"
        if left <= 5:
            return f"\n\n[{left} tool calls left.]"
        return ""

