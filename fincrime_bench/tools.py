"""Tools exposed to the model under test, plus their executor."""

from __future__ import annotations

import json
import re
from collections import deque

from .db import QueryError, Warehouse, format_table
from .schema import ALL_TABLES

SQL_TOOLS = ["list_tables", "describe_table", "sql_query"]
GRAPH_TOOLS = ["entity_profile", "graph_neighbors", "find_paths"]
TOOLSETS = {"sql": SQL_TOOLS, "full": SQL_TOOLS + GRAPH_TOOLS}


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }}


TOOL_DEFS = {
    "list_tables": _fn("list_tables", "List all warehouse tables with row counts and descriptions.", {}, []),
    "describe_table": _fn(
        "describe_table", "Show a table's columns (type and meaning) and a few sample rows.",
        {"table": {"type": "string", "description": "Table name."}}, ["table"]),
    "sql_query": _fn(
        "sql_query",
        "Run one read-only SQLite SELECT (CTEs and window functions allowed) against the warehouse. "
        "Results are returned as a pipe-separated table. Aggregate in SQL rather than paging raw rows.",
        {"query": {"type": "string", "description": "A single SQLite SELECT statement."},
         "max_rows": {"type": "integer", "description": "Maximum rows to return (default 50, max 200)."}},
        ["query"]),
    "entity_profile": _fn(
        "entity_profile",
        "Summarise everything directly linked to one entity id: person (P...), business (B...), account "
        "(AC...), phone number (+1555...), device (DV...), transaction (TX...), tax return (RT...), preparer "
        "(PTN...), asset (AS...), address (AD...), or a TIN/EIN.",
        {"entity_id": {"type": "string", "description": "The id to profile."}}, ["entity_id"]),
    "graph_neighbors": _fn(
        "graph_neighbors",
        "List edges adjacent to a node in the derived graph_edges view (holds, signer, owns, control, "
        "relationship, employs, subscriber, kyc_phone, transfer, called, uses_device).",
        {"node_id": {"type": "string"},
         "direction": {"type": "string", "enum": ["out", "in", "both"], "description": "Default both."},
         "edge_types": {"type": "array", "items": {"type": "string"}, "description": "Optional filter."},
         "limit": {"type": "integer", "description": "Default 50, max 200."}},
        ["node_id"]),
    "find_paths": _fn(
        "find_paths",
        "Find shortest connection paths between two nodes in graph_edges (edges traversed in either "
        "direction unless directed=true). Very high-degree hub nodes are not expanded.",
        {"source": {"type": "string"}, "target": {"type": "string"},
         "max_hops": {"type": "integer", "description": "Default 4, max 6."},
         "edge_types": {"type": "array", "items": {"type": "string"}, "description": "Optional filter."},
         "directed": {"type": "boolean", "description": "Follow edges only src->dst. Default false."}},
        ["source", "target"]),
}

FIELD_JSON_TYPES = {
    "id": {"type": "string"},
    "id_set": {"type": "array", "items": {"type": "string"}},
    "number": {"type": "number"},
    "bool": {"type": "boolean"},
    "id_number_map": {"type": "array", "items": {"type": "object", "properties": {
        "id": {"type": "string"}, "value": {"type": "number"}}, "required": ["id", "value"]}},
}

FIELD_HUMAN_TYPES = {
    "id": "ID string",
    "id_set": "list of ID strings",
    "number": "number",
    "bool": "true or false",
    "id_number_map": 'list of {"id": ID string, "value": number}',
}


def submit_tool(task: dict) -> dict:
    props = {}
    for f in task["answer_fields"]:
        props[f["name"]] = {**FIELD_JSON_TYPES[f["type"]], "description": f["description"]}
    return _fn("submit_answer", "Submit your final answer. Call exactly once when the investigation is complete.",
               {"answer": {"type": "object", "properties": props,
                           "required": [f["name"] for f in task["answer_fields"]]}},
               ["answer"])


def tool_specs(toolset: str, task: dict) -> list[dict]:
    return [TOOL_DEFS[n] for n in TOOLSETS[toolset]] + [submit_tool(task)]


def answer_format(task: dict) -> str:
    lines = ['Submit with submit_answer({"answer": {...}}) using these fields:']
    for f in task["answer_fields"]:
        lines.append(f'- "{f["name"]}" ({FIELD_HUMAN_TYPES[f["type"]]}): {f["description"]}')
    return "\n".join(lines)


# ======================================================================
class Toolbox:
    def __init__(self, wh: Warehouse, toolset: str = "full", max_output_chars: int = 8_000):
        self.wh = wh
        self.allowed = TOOLSETS[toolset]
        self.max_output_chars = max_output_chars

    def call(self, name: str, args: dict) -> str:
        if name not in self.allowed:
            return f"ERROR: unknown tool '{name}'. Available: {', '.join(self.allowed + ['submit_answer'])}"
        if not isinstance(args, dict):
            return "ERROR: arguments must be a JSON object."
        try:
            out = getattr(self, f"t_{name}")(**args)
        except TypeError as e:
            return f"ERROR: bad arguments for {name}: {e}"
        except QueryError as e:
            return f"SQL ERROR: {e}"
        except Exception as e:  # never crash the run on a tool bug
            return f"ERROR: {type(e).__name__}: {e}"
        if len(out) > self.max_output_chars:
            out = out[: self.max_output_chars] + f"\n... [output truncated at {self.max_output_chars} characters]"
        return out

    # ---------------------------------------------------------------- SQL
    def t_list_tables(self) -> str:
        counts = self.wh.row_counts()
        return "\n".join(f"{n} ({counts[n]} rows): {s['description']}" for n, s in ALL_TABLES.items())

    def t_describe_table(self, table: str) -> str:
        if table not in ALL_TABLES:
            return f"ERROR: no table '{table}'. Tables: {', '.join(ALL_TABLES)}"
        spec = ALL_TABLES[table]
        lines = [f"{table}: {spec['description']}", "columns:"]
        lines += [f"  {c} {t} — {d}" for c, t, d in spec["columns"]]
        cols, rows, _ = self.wh.query(f'SELECT * FROM "{table}" LIMIT 3', max_rows=3)
        lines.append("sample rows:")
        lines.append(format_table(cols, rows, False))
        return "\n".join(lines)

    def t_sql_query(self, query: str, max_rows: int = 50) -> str:
        try:
            max_rows = max(1, min(int(max_rows), 200))
        except (TypeError, ValueError):
            max_rows = 50
        cols, rows, truncated = self.wh.query(query, max_rows=max_rows)
        if not cols:
            return "(statement returned no result set)"
        return format_table(cols, rows, truncated)

    # -------------------------------------------------------------- graph
    def _q(self, sql: str, params: tuple = (), max_rows: int = 25) -> str:
        cols, rows, trunc = self.wh.query(sql, params, max_rows=max_rows)
        return format_table(cols, rows, trunc) if rows else "(none)"

    def _one(self, sql: str, params: tuple):
        cols, rows, _ = self.wh.query(sql, params, max_rows=1)
        return dict(zip(cols, rows[0])) if rows else None

    def t_entity_profile(self, entity_id: str) -> str:
        e = str(entity_id).strip()
        if re.fullmatch(r"9\d\d-00-\d{4}", e):
            row = self._one("SELECT person_id FROM persons WHERE tin = ?", (e,))
            if not row:
                return f"No person with TIN {e}."
            e = row["person_id"]
        elif re.fullmatch(r"00-\d{7}", e):
            row = self._one("SELECT business_id FROM businesses WHERE ein = ?", (e,))
            if not row:
                return f"No business with EIN {e}."
            e = row["business_id"]
        prefixes = [("PTN", self._preparer), ("AC", self._account), ("AD", self._simple("addresses", "address_id")),
                    ("AS", self._simple("assets", "asset_id")), ("TX", self._txn), ("RT", self._return),
                    ("DV", self._device), ("IR", self._simple("info_returns", "info_return_id")),
                    ("CL", self._simple("calls", "call_id")), ("LG", self._simple("logins", "event_id")),
                    ("BR", self._simple("branches", "branch_id")), ("TW", self._simple("cell_towers", "tower_id")),
                    ("+", self._phone), ("P", self._person), ("B", self._business)]
        for pre, fn in prefixes:
            if e.startswith(pre):
                return fn(e)
        return f"Unrecognised id format: {e}"

    def _simple(self, table: str, col: str):
        def f(e: str) -> str:
            return self._q(f'SELECT * FROM "{table}" WHERE "{col}" = ?', (e,), 5)
        return f

    def _person(self, e: str) -> str:
        p = self._one("SELECT * FROM persons WHERE person_id = ?", (e,))
        if not p:
            return f"No person {e}."
        out = [f"PERSON {e}: " + json.dumps(p)]
        out.append("address: " + self._q("SELECT * FROM addresses WHERE address_id = ?", (p["address_id"],), 1))
        out.append("accounts held or signed:\n" + self._q(
            "SELECT account_id, bank_name, country, account_type, holder_id, authorized_signer_id, open_date, "
            "close_date FROM accounts WHERE holder_id = ? OR authorized_signer_id = ?", (e, e)))
        out.append("phones (carrier subscriber):\n" + self._q(
            "SELECT phone_number, plan_type, activation_date, deactivation_date FROM phones WHERE subscriber_id = ?",
            (e,)))
        out.append("registry roles:\n" + self._q(
            "SELECT o.owned_id, b.name, o.role, o.ownership_pct, o.title, o.start_date, o.end_date FROM ownership o "
            "JOIN businesses b ON b.business_id = o.owned_id WHERE o.owner_id = ?", (e,)))
        out.append("family:\n" + self._q(
            "SELECT person_id_1, relationship, person_id_2 FROM relationships WHERE person_id_1 = ? OR "
            "person_id_2 = ?", (e, e)))
        if p.get("tin"):
            out.append("tax returns (as filer or spouse):\n" + self._q(
                "SELECT return_id, form_type, filing_status, total_income, refund_amount, preparer_id FROM "
                "tax_returns WHERE filer_tin = ? OR spouse_tin = ?", (p["tin"], p["tin"])))
            out.append("information returns received:\n" + self._q(
                "SELECT form_type, COUNT(*) n, ROUND(SUM(amount), 2) total FROM info_returns WHERE payee_tin = ? "
                "GROUP BY form_type", (p["tin"],)))
        out.append("assets:\n" + self._q(
            "SELECT asset_id, asset_type, purchase_date, purchase_price, financing, sale_date FROM assets "
            "WHERE owner_id = ?", (e,)))
        out.append("online-banking devices:\n" + self._q(
            "SELECT device_id, COUNT(*) events, MIN(timestamp) first, MAX(timestamp) last, "
            "GROUP_CONCAT(DISTINCT ip_country) countries FROM logins WHERE person_id = ? GROUP BY device_id", (e,)))
        return "\n".join(out)

    def _business(self, e: str) -> str:
        b = self._one("SELECT * FROM businesses WHERE business_id = ?", (e,))
        if not b:
            return f"No business {e}."
        out = [f"BUSINESS {e}: " + json.dumps(b)]
        out.append("address: " + self._q("SELECT * FROM addresses WHERE address_id = ?", (b["address_id"],), 1))
        out.append("registry roles held IN this entity:\n" + self._q(
            "SELECT owner_id, role, ownership_pct, title, start_date, end_date FROM ownership WHERE owned_id = ?",
            (e,)))
        out.append("stakes this entity holds in others:\n" + self._q(
            "SELECT owned_id, role, ownership_pct, start_date, end_date FROM ownership WHERE owner_id = ?", (e,)))
        out.append("accounts:\n" + self._q(
            "SELECT account_id, bank_name, country, authorized_signer_id, open_date FROM accounts WHERE holder_id = ?",
            (e,)))
        out.append("employees in persons table: " + self._q(
            "SELECT COUNT(*) n FROM persons WHERE employer_id = ?", (e,), 1))
        out.append("tax returns:\n" + self._q(
            "SELECT return_id, form_type, business_gross_receipts, business_expenses, total_income FROM tax_returns "
            "WHERE filer_tin = ?", (b["ein"],)))
        return "\n".join(out)

    def _account(self, e: str) -> str:
        a = self._one("SELECT * FROM accounts WHERE account_id = ?", (e,))
        if not a:
            return f"No account {e}."
        out = [f"ACCOUNT {e}: " + json.dumps(a)]
        out.append("flows:\n" + self._q(
            "SELECT 'in' dir, COUNT(*) n, ROUND(SUM(amount), 2) total, MIN(timestamp) first, MAX(timestamp) last "
            "FROM transactions WHERE to_account = ? UNION ALL SELECT 'out', COUNT(*), ROUND(SUM(amount), 2), "
            "MIN(timestamp), MAX(timestamp) FROM transactions WHERE from_account = ?", (e, e)))
        out.append("top incoming counterparties:\n" + self._q(
            "SELECT COALESCE(from_account, '(cash)') counterparty, COUNT(*) n, ROUND(SUM(amount), 2) total "
            "FROM transactions WHERE to_account = ? GROUP BY 1 ORDER BY total DESC", (e,), 8))
        out.append("top outgoing counterparties:\n" + self._q(
            "SELECT COALESCE(to_account, '(cash)') counterparty, COUNT(*) n, ROUND(SUM(amount), 2) total "
            "FROM transactions WHERE from_account = ? GROUP BY 1 ORDER BY total DESC", (e,), 8))
        return "\n".join(out)

    def _txn(self, e: str) -> str:
        return self._q(
            "SELECT t.*, fa.holder_id from_holder, ta.holder_id to_holder FROM transactions t "
            "LEFT JOIN accounts fa ON fa.account_id = t.from_account LEFT JOIN accounts ta ON "
            "ta.account_id = t.to_account WHERE t.txn_id = ?", (e,), 1)

    def _return(self, e: str) -> str:
        out = [self._q("SELECT * FROM tax_returns WHERE return_id = ?", (e,), 1)]
        out.append("dependents:\n" + self._q(
            "SELECT d.dependent_person_id, p.dob, p.date_of_death FROM return_dependents d JOIN persons p ON "
            "p.person_id = d.dependent_person_id WHERE d.return_id = ?", (e,)))
        return "\n".join(out)

    def _preparer(self, e: str) -> str:
        out = [self._q("SELECT * FROM preparers WHERE preparer_id = ?", (e,), 1)]
        out.append("returns prepared: " + self._q(
            "SELECT COUNT(*) n, ROUND(AVG(refund_amount), 2) avg_refund FROM tax_returns WHERE preparer_id = ?",
            (e,), 1))
        return "\n".join(out)

    def _device(self, e: str) -> str:
        return self._q(
            "SELECT person_id, COUNT(*) events, MIN(timestamp) first, MAX(timestamp) last, "
            "GROUP_CONCAT(DISTINCT ip_country) countries, GROUP_CONCAT(DISTINCT event_type) events_seen "
            "FROM logins WHERE device_id = ? GROUP BY person_id", (e,))

    def _phone(self, e: str) -> str:
        out = ["PHONE: " + self._q("SELECT * FROM phones WHERE phone_number = ?", (e,), 1)]
        out.append("persons with this KYC phone:\n" + self._q("SELECT person_id FROM persons WHERE phone = ?", (e,)))
        out.append("call summary:\n" + self._q(
            "SELECT COUNT(*) n, MIN(timestamp) first, MAX(timestamp) last FROM calls WHERE caller = ? OR callee = ?",
            (e, e), 1))
        out.append("top contacts:\n" + self._q(
            "SELECT other, COUNT(*) n, MIN(ts) first, MAX(ts) last FROM (SELECT callee other, timestamp ts FROM "
            "calls WHERE caller = ? UNION ALL SELECT caller, timestamp FROM calls WHERE callee = ?) GROUP BY other "
            "ORDER BY n DESC", (e, e), 15))
        return "\n".join(out)

    def t_graph_neighbors(self, node_id: str, direction: str = "both", edge_types: list | None = None,
                          limit: int = 50) -> str:
        limit = max(1, min(int(limit or 50), 200))
        parts, params = [], []
        if direction in ("out", "both"):
            parts.append("SELECT 'out' dir, dst AS neighbor, edge_type, weight, count, first_seen, last_seen, detail "
                         "FROM graph_edges WHERE src = ?")
            params.append(node_id)
        if direction in ("in", "both"):
            parts.append("SELECT 'in' dir, src AS neighbor, edge_type, weight, count, first_seen, last_seen, detail "
                         "FROM graph_edges WHERE dst = ?")
            params.append(node_id)
        if not parts:
            return "ERROR: direction must be out, in or both."
        sql = " UNION ALL ".join(parts)
        if edge_types:
            ph = ", ".join("?" for _ in edge_types)
            sql = f"SELECT * FROM ({sql}) WHERE edge_type IN ({ph})"
            params += list(edge_types)
        sql += " ORDER BY COALESCE(weight, 0) DESC, count DESC"
        cols, rows, trunc = self.wh.query(sql, tuple(params), max_rows=limit)
        return format_table(cols, rows, trunc) if rows else f"No edges for {node_id}."

    def t_find_paths(self, source: str, target: str, max_hops: int = 4, edge_types: list | None = None,
                     directed: bool = False) -> str:
        max_hops = max(1, min(int(max_hops or 4), 6))
        hub_limit, node_budget = 400, 6000
        type_clause, type_params = "", ()
        if edge_types:
            type_clause = f" AND edge_type IN ({', '.join('?' for _ in edge_types)})"
            type_params = tuple(edge_types)

        def neighbors(n: str) -> list[tuple[str, str, str]]:
            sql = f"SELECT dst, edge_type, 'out' FROM graph_edges WHERE src = ?{type_clause}"
            params: tuple = (n,) + type_params
            if not directed:
                sql += f" UNION ALL SELECT src, edge_type, 'in' FROM graph_edges WHERE dst = ?{type_clause}"
                params += (n,) + type_params
            _cols, rows, trunc = self.wh.query(sql, params, max_rows=hub_limit)
            return [] if trunc else [tuple(r) for r in rows]

        parents: dict[str, list[tuple[str, str, str]]] = {source: []}
        depth = {source: 0}
        frontier = deque([source])
        found = False
        expanded = 0
        while frontier and not found:
            level = list(frontier)
            frontier.clear()
            for n in level:
                if depth[n] >= max_hops:
                    continue
                expanded += 1
                if expanded > node_budget:
                    break
                for nb, et, d in neighbors(n):
                    if nb is None:
                        continue
                    if nb not in depth:
                        depth[nb] = depth[n] + 1
                        parents[nb] = [(n, et, d)]
                        frontier.append(nb)
                    elif depth[nb] == depth[n] + 1:
                        parents[nb].append((n, et, d))
                    if nb == target:
                        found = True
        if target not in parents:
            return f"No path found from {source} to {target} within {max_hops} hops (hubs with >{hub_limit} edges " \
                   f"are not expanded)."
        paths: list[list[str]] = []

        def walk(n: str, suffix: list[str]) -> None:
            if len(paths) >= 5:
                return
            if n == source:
                paths.append([source] + suffix)
                return
            for p, et, d in parents[n]:
                arrow = f"-[{et}]->" if d == "out" else f"<-[{et}]-"
                walk(p, [arrow, n] + suffix)

        walk(target, [])
        return "\n".join(" ".join(p) for p in paths)
