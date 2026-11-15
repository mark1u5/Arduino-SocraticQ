"""RawTree client for SocraticQ Agentic Cortex.

Implements the official RawTree HTTP API (by Tinybird):
- Base URL: https://api.rawtree.com
- Ingestion: POST /v1/tables/{table} with JSON payload
- Query: POST /v1/query with {"sql": "..."}

Features:
1. Strict schema validation for frozen contracts (telemetry_events, agent_insights).
2. Schema-free insertion for evolving tables (e.g. agent_traces, rollups).
3. Automatic offline local SQLite fallback mirror so developers and dashboards (Dev 4)
   are never blocked by venue credentials or network dropouts.
4. query_df helper for direct pandas DataFrame creation.
"""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    import requests
except ImportError:
    requests = None

from contracts.schemas import (
    AgentInsight,
    TelemetryEvent,
    validate_agent_insight,
    validate_telemetry_event,
)


def _load_dotenv() -> None:
    """Load repo-root .env into os.environ via setdefault if present."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


class RawTreeClient:
    """Official RawTree API client with automatic offline SQLite fallback."""

    DEFAULT_API_URL = "https://api.rawtree.com"

    def __init__(
        self,
        api_url: Optional[str] = None,
        api_key: Optional[str] = None,
        database: Optional[str] = None,
        local_db_path: Optional[str | Path] = None,
        force_offline: bool = False,
    ) -> None:
        _load_dotenv()
        raw_url = api_url or os.getenv("RAWTREE_API_URL", self.DEFAULT_API_URL)
        self.api_url = raw_url.rstrip("/")
        self.api_key = api_key or os.getenv("RAWTREE_API_KEY", "")
        self.database = database or os.getenv("RAWTREE_TEAM_SCHEMA") or os.getenv("RAWTREE_DATABASE", "")
        self.force_offline = force_offline

        # Local SQLite database fallback path. RAWTREE_LOCAL_DB lets several
        # processes (chat server, autonomous agent, Tukey dashboard) share one
        # store without code coordination; default preserves historic path.
        if local_db_path is None:
            env_db = os.getenv("RAWTREE_LOCAL_DB", "").strip()
            self.local_db_path = Path(env_db) if env_db else Path(__file__).resolve().parent / "local_rawtree.db"
        else:
            self.local_db_path = Path(local_db_path)

        # Check if live RawTree API credentials are configured
        self._is_live_configured = bool(
            not self.force_offline
            and self.api_key
            and not self.api_key.startswith("<")
            and not self.api_url.startswith("https://<")
        )

        # Always initialize local fallback DB structure
        self._init_local_db()

    @property
    def is_live(self) -> bool:
        """Return True if live RawTree credentials are active."""
        return self._is_live_configured

    def _get_sqlite_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.local_db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        # Register PostgreSQL / ClickHouse timestamp functions for SQLite
        # compatibility. The format matches TelemetryEvent.ts_utc exactly
        # ("YYYY-MM-DDTHH:MM:SSZ") so string comparisons between NOW() and
        # stored timestamps order correctly.
        conn.create_function(
            "NOW", 0, lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        conn.create_function(
            "now", 0, lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        return conn

    def _init_local_db(self) -> None:
        """Initialize SQLite tables matching schema.sql."""
        schema_file = Path(__file__).resolve().parent / "schema.sql"
        if schema_file.exists():
            self.create_tables_from_file(schema_file, target="local")

    def create_tables_from_file(
        self, schema_file: str | Path, target: str = "auto"
    ) -> None:
        """Execute DDL statements from schema.sql against local or live RawTree."""
        sql_content = Path(schema_file).read_text(encoding="utf-8")

        if target in ("local", "auto") or not self._is_live_configured:
            # Adapt types for SQLite execution
            sqlite_ddl = (
                sql_content.replace("DOUBLE PRECISION", "REAL")
                .replace("TIMESTAMP NOT NULL", "TEXT NOT NULL")
                .replace("BOOLEAN", "INTEGER")
            )
            with self._get_sqlite_conn() as conn:
                conn.executescript(sqlite_ddl)
                conn.commit()

        if self._is_live_configured and target in ("live", "auto"):
            # RawTree is schema-free (tables created automatically on first insert),
            # but DDL can still be executed via query endpoint.
            try:
                self.query_sql(sql_content)
            except Exception as e:
                print(f"[RawTreeClient] Note: DDL execution on live RawTree: {e}")

    def insert_telemetry_event(self, event: TelemetryEvent) -> bool:
        """Insert a student telemetry event into `telemetry_events`."""
        validate_telemetry_event(dict(event))
        return self._insert_generic("telemetry_events", dict(event))

    def insert_agent_insight(self, insight: AgentInsight) -> bool:
        """Insert an agent intervention/hypothesis into `agent_insights`."""
        validate_agent_insight(dict(insight))
        return self._insert_generic("agent_insights", dict(insight))

    def insert_agent_trace(self, trace: dict[str, Any]) -> bool:
        """Insert an agent self-monitoring trace row into `agent_traces`."""
        return self._insert_generic("agent_traces", trace)

    def insert_rows(self, table: str, rows: list[dict[str, Any]]) -> bool:
        """Insert multiple JSON rows into any table."""
        success = True
        for row in rows:
            if not self._insert_generic(table, row):
                success = False
        return success

    def _http_post_json(
        self, url: str, payload: dict[str, Any], params: dict[str, str], timeout: int
    ) -> tuple[int, str]:
        """POST JSON using requests if available, otherwise stdlib urllib.request."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if requests is not None:
            resp = requests.post(url, json=payload, params=params, headers=headers, timeout=timeout)
            return resp.status_code, resp.text
        full_url = f"{url}?{urllib.parse.urlencode(params)}" if params else url
        req = urllib.request.Request(
            full_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", errors="replace")

    def _insert_generic(self, table: str, row: dict[str, Any]) -> bool:
        """Insert a dictionary into RawTree via POST /v1/tables/{table} or local fallback."""
        if self._is_live_configured:
            try:
                url = f"{self.api_url}/v1/tables/{table}"
                params = {"database": self.database} if self.database else {}
                status, text = self._http_post_json(url, row, params, timeout=5)
                if status in (200, 201):
                    # Also mirror locally for instant local availability
                    self._insert_local(table, row)
                    return True
                else:
                    print(f"[RawTreeClient] RawTree insert HTTP {status}: {text}")
            except Exception as e:
                print(f"[RawTreeClient] Live insert error: {e}. Falling back to local DB.")

        return self._insert_local(table, row)

    def _insert_local(self, table: str, row: dict[str, Any]) -> bool:
        """Insert row into local SQLite mirror, creating columns dynamically if needed."""
        with self._get_sqlite_conn() as conn:
            # Convert booleans to 0/1 for SQLite
            cleaned = {}
            for k, v in row.items():
                if isinstance(v, bool):
                    cleaned[k] = 1 if v else 0
                else:
                    cleaned[k] = v

            keys = list(cleaned.keys())
            placeholders = ", ".join(f":{k}" for k in keys)
            cols = ", ".join(keys)
            query = f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({placeholders})"
            try:
                conn.execute(query, cleaned)
                conn.commit()
                return True
            except sqlite3.OperationalError as e:
                # If table or column missing in SQLite, create table dynamically
                if "no such table" in str(e).lower():
                    col_defs = ", ".join(f"{k} TEXT" for k in keys)
                    conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({col_defs})")
                    conn.execute(query, cleaned)
                    conn.commit()
                    return True
                raise

    def query_sql(
        self, sql: str, params: Optional[tuple[Any, ...] | list[Any] | dict[str, Any]] = None
    ) -> list[dict[str, Any]]:
        """Execute a SQL query (SQL-out) against RawTree or local SQLite fallback."""
        if self._is_live_configured:
            try:
                url = f"{self.api_url}/v1/query"
                qparams = {"database": self.database} if self.database else {}
                live_sql = sql
                if isinstance(params, (tuple, list)) and "?" in live_sql:
                    parts = live_sql.split("?")
                    formatted_parts: list[str] = []
                    for i, val in enumerate(params):
                        if i < len(parts) - 1:
                            if val is None:
                                rep = "NULL"
                            elif isinstance(val, bool):
                                rep = "TRUE" if val else "FALSE"
                            elif isinstance(val, (int, float)):
                                rep = str(val)
                            else:
                                rep = "'" + str(val).replace("'", "''") + "'"
                            formatted_parts.append(parts[i])
                            formatted_parts.append(rep)
                    if len(parts) > len(params):
                        formatted_parts.append("?".join(parts[len(params):]))
                    live_sql = "".join(formatted_parts)
                status, text = self._http_post_json(url, {"sql": live_sql}, qparams, timeout=10)
                if status == 200:
                    data = json.loads(text)
                    if isinstance(data, list):
                        return data
                    if isinstance(data, dict):
                        if "data" in data and isinstance(data["data"], list):
                            return data["data"]
                        if "rows" in data and isinstance(data["rows"], list):
                            return data["rows"]
                else:
                    print(f"[RawTreeClient] Query HTTP {status}: {text}")
            except Exception as e:
                print(f"[RawTreeClient] Live query error: {e}. Executing on local mirror.")

        # Local SQLite execution
        with self._get_sqlite_conn() as conn:
            # Adapt timestamp intervals for SQLite. Use the same ISO-Z format
            # as stored ts_utc values so string comparisons remain correct.
            adapted_sql = (
                sql.replace(
                    "NOW() - INTERVAL '2 hours'",
                    "strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-2 hours')",
                )
                .replace(
                    "NOW() - INTERVAL '3 hours'",
                    "strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-3 hours')",
                )
                .replace(
                    "NOW() - INTERVAL '24 hours'",
                    "strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-24 hours')",
                )
                .replace(
                    "now() - INTERVAL 1 HOUR",
                    "strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-1 hour')",
                )
            )
            cursor = conn.execute(adapted_sql, params or ())
            if cursor.description is None:
                conn.commit()
                return []
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def query_df(self, sql: str) -> Any:
        """Convenience method for Dev 4: return query results as a pandas DataFrame."""
        try:
            import pandas as pd
        except ImportError:
            raise ImportError("pandas is required for query_df. Install with pip install pandas.")

        rows = self.query_sql(sql)
        return pd.DataFrame(rows)
