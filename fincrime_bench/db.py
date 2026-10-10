"""Load the CSV dataset into a cached SQLite warehouse and run sandboxed read-only queries."""

from __future__ import annotations

import csv
import hashlib
import os
import sqlite3
import time
from pathlib import Path

from .schema import ALL_TABLES, TABLES

INDEXES = [
    ("transactions", "from_account"), ("transactions", "to_account"), ("transactions", "timestamp"),
    ("transactions", "conducted_by"), ("calls", "caller"), ("calls", "callee"), ("calls", "timestamp"),
    ("calls", "caller_tower_id"), ("logins", "person_id"), ("logins", "device_id"), ("logins", "txn_id"),
    ("logins", "ip_address"), ("accounts", "holder_id"), ("accounts", "authorized_signer_id"),
    ("ownership", "owner_id"), ("ownership", "owned_id"), ("info_returns", "payee_tin"),
    ("info_returns", "payer_tin"), ("tax_returns", "filer_tin"), ("tax_returns", "spouse_tin"),
    ("tax_returns", "preparer_id"), ("return_dependents", "dependent_person_id"),
    ("return_dependents", "return_id"), ("persons", "tin"), ("persons", "phone"), ("businesses", "ein"),
    ("phones", "subscriber_id"), ("assets", "owner_id"), ("relationships", "person_id_1"),
    ("relationships", "person_id_2"),
]

GRAPH_SQL = [
    """INSERT INTO graph_edges SELECT holder_id, account_id, 'holds', NULL, 1, open_date, close_date,
       account_type || ' @ ' || bank_name || ' (' || country || ')' FROM accounts""",
    """INSERT INTO graph_edges SELECT authorized_signer_id, account_id, 'signer', NULL, 1, open_date, close_date, NULL
       FROM accounts WHERE authorized_signer_id IS NOT NULL""",
    """INSERT INTO graph_edges SELECT owner_id, owned_id, 'owns', ownership_pct, 1, start_date, end_date,
       role || COALESCE(' until ' || end_date, '') FROM ownership WHERE ownership_pct IS NOT NULL""",
    """INSERT INTO graph_edges SELECT owner_id, owned_id, 'control', NULL, 1, start_date, end_date,
       role || COALESCE(':' || title, '') || COALESCE(' until ' || end_date, '') FROM ownership
       WHERE ownership_pct IS NULL""",
    """INSERT INTO graph_edges SELECT person_id_1, person_id_2, 'relationship', NULL, 1, NULL, NULL, relationship
       FROM relationships""",
    """INSERT INTO graph_edges SELECT employer_id, person_id, 'employs', NULL, 1, NULL, NULL, occupation
       FROM persons WHERE employer_id IS NOT NULL""",
    """INSERT INTO graph_edges SELECT subscriber_id, phone_number, 'subscriber', NULL, 1, activation_date,
       deactivation_date, plan_type FROM phones WHERE subscriber_id IS NOT NULL""",
    """INSERT INTO graph_edges SELECT person_id, phone, 'kyc_phone', NULL, 1, NULL, NULL, NULL FROM persons
       WHERE phone IS NOT NULL""",
    """INSERT INTO graph_edges SELECT from_account, to_account, 'transfer', ROUND(SUM(amount), 2), COUNT(*),
       MIN(timestamp), MAX(timestamp), GROUP_CONCAT(DISTINCT channel) FROM transactions
       WHERE from_account IS NOT NULL AND to_account IS NOT NULL GROUP BY from_account, to_account""",
    """INSERT INTO graph_edges SELECT caller, callee, 'called', SUM(duration_sec), COUNT(*), MIN(timestamp),
       MAX(timestamp), NULL FROM calls GROUP BY caller, callee""",
    """INSERT INTO graph_edges SELECT person_id, device_id, 'uses_device', NULL, COUNT(*), MIN(timestamp),
       MAX(timestamp), GROUP_CONCAT(DISTINCT ip_country) FROM logins GROUP BY person_id, device_id""",
]


def _fingerprint(data_dir: Path) -> str:
    h = hashlib.sha256()
    for name in sorted(TABLES):
        p = data_dir / f"{name}.csv"
        st = p.stat()
        h.update(f"{name}:{st.st_size}:{int(st.st_mtime)}".encode())
    return h.hexdigest()[:16]


def _convert(v: str, ctype: str):
    if v == "":
        return None
    if ctype == "REAL":
        return float(v)
    if ctype == "INTEGER":
        return int(v)
    return v


def _csv_rows(data_dir: Path):
    for name, spec in TABLES.items():
        names = [c[0] for c in spec["columns"]]
        with open(data_dir / f"{name}.csv", newline="", encoding="utf-8") as f:
            rd = csv.reader(f)
            header = next(rd)
            if header != names:
                raise ValueError(f"{name}.csv header mismatch: {header} != {names}")
            yield name, rd


def build_db_from(tables, db_path: str | Path) -> None:
    """Build a warehouse from ``(table_name, iterable of string rows in schema column order)`` pairs."""
    tmp = Path(f"{db_path}.tmp-{os.getpid()}-{time.time_ns()}")
    conn = sqlite3.connect(tmp)
    try:
        for name, spec in ALL_TABLES.items():
            cols = ", ".join(f'"{c[0]}" {c[1]}' for c in spec["columns"])
            conn.execute(f'CREATE TABLE "{name}" ({cols})')
        for name, raw_rows in tables:
            types = [c[1] for c in TABLES[name]["columns"]]
            rows = ([_convert(v, t) for v, t in zip(row, types)] for row in raw_rows)
            ph = ", ".join("?" for _ in types)
            conn.executemany(f'INSERT INTO "{name}" VALUES ({ph})', rows)
        for sql in GRAPH_SQL:
            conn.execute(sql)
        for table, col in INDEXES:
            conn.execute(f'CREATE INDEX "ix_{table}_{col}" ON "{table}" ("{col}")')
        conn.execute("CREATE INDEX ix_graph_src ON graph_edges (src)")
        conn.execute("CREATE INDEX ix_graph_dst ON graph_edges (dst)")
        conn.commit()
        conn.execute("ANALYZE")
        conn.commit()
    finally:
        conn.close()
    os.replace(tmp, db_path)


def build_db(data_dir: str | Path, db_path: str | Path) -> None:
    build_db_from(_csv_rows(Path(data_dir)), db_path)


def ensure_db(data_dir: str | Path, cache_dir: str | Path | None = None) -> Path:
    data_dir = Path(data_dir)
    cache = Path(cache_dir) if cache_dir else data_dir / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    db_path = cache / f"warehouse-{_fingerprint(data_dir)}.sqlite"
    if not db_path.exists():
        build_db(data_dir, db_path)
    return db_path


# ----------------------------------------------------------------- sandbox
_ALLOWED_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                    getattr(sqlite3, "SQLITE_RECURSIVE", 33)}
_ALLOWED_PRAGMAS = {"table_info", "table_xinfo", "index_list", "index_info"}


def _authorizer(action, arg1, arg2, dbname, source):
    if action in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and (arg1 or "").lower() in _ALLOWED_PRAGMAS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


class QueryError(Exception):
    pass


class Warehouse:
    """A read-only connection to the warehouse with time and row limits."""

    def __init__(self, db_path: str | Path, time_limit_s: float = 20.0):
        self.db_path = Path(db_path)
        self.time_limit_s = time_limit_s
        self.conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, check_same_thread=False)
        self.conn.set_authorizer(_authorizer)
        self._deadline = 0.0
        self.conn.set_progress_handler(self._progress, 20_000)

    def _progress(self) -> int:
        return 1 if time.monotonic() > self._deadline else 0

    def close(self) -> None:
        self.conn.close()

    def query(self, sql: str, params: tuple = (), max_rows: int = 200) -> tuple[list[str], list[tuple], bool]:
        """Returns (columns, rows, truncated)."""
        self._deadline = time.monotonic() + self.time_limit_s
        try:
            cur = self.conn.execute(sql, params)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchmany(max_rows + 1)
        except sqlite3.OperationalError as e:
            if "interrupted" in str(e):
                raise QueryError(f"query exceeded the {self.time_limit_s:.0f}s time limit; filter on indexed "
                                 f"columns or aggregate first") from e
            raise QueryError(str(e)) from e
        except (sqlite3.DatabaseError, sqlite3.Warning, ValueError) as e:
            raise QueryError(str(e)) from e
        truncated = len(rows) > max_rows
        return cols, rows[:max_rows], truncated

    def row_counts(self) -> dict[str, int]:
        out = {}
        for name in ALL_TABLES:
            out[name] = self.conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        return out


def format_table(cols: list[str], rows: list[tuple], truncated: bool, max_cell: int = 400) -> str:
    def cell(v) -> str:
        if v is None:
            return ""
        if isinstance(v, float):
            s = f"{v:.2f}" if abs(v - round(v)) > 1e-9 or abs(v) >= 1e6 else str(int(round(v)))
        else:
            s = str(v)
        s = s.replace("\n", " ").replace("|", "/")
        return s if len(s) <= max_cell else s[: max_cell - 3] + "..."

    lines = [" | ".join(cols)]
    lines += [" | ".join(cell(v) for v in r) for r in rows]
    footer = f"({len(rows)} row{'s' if len(rows) != 1 else ''}"
    footer += "; more rows exist — refine the query or raise max_rows)" if truncated else ")"
    lines.append(footer)
    return "\n".join(lines)
