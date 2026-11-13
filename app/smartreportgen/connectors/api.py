"""HTTP API 数据源：拉取 JSON 记录，载入内存 DuckDB 后用 SQL 查询。

config 示例：
{
  "base_url": "https://example.com/api",
  "headers": {"X-Tenant": "a"},
  "auth_header": "Authorization",       # 令牌放在哪个请求头（默认 Authorization: Bearer <token>）
  "auth_scheme": "Bearer",              # 为空则直接放 token
  "records_path": "data.items",         # 默认记录所在 JSON 路径
  "timeout": 30,
  "cache_seconds": 60,
  "endpoints": {                        # 逻辑表名 -> 接口定义（未定义的表名直接当作路径）
    "tickets": {"path": "/tickets", "method": "GET", "params": {}, "body": null, "records_path": "data"}
  }
}
SQL 中使用逻辑表名，如 SELECT channel, count(*) FROM tickets GROUP BY 1（DuckDB 方言）。
"""
from __future__ import annotations

import json
import re
import tempfile
import threading
import time
from typing import Any

import duckdb
import httpx

from .base import Connector, ConnectorError, ensure_readonly, norm_columns, norm_value, value_options

_TABLE_REF = re.compile(r'\b(?:from|join)\s+("?)([\w./-]+)\1', re.I)


def _dig(obj: Any, path: str | None) -> Any:
    if not path:
        return obj
    for p in path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(p)
        elif isinstance(obj, list) and p.isdigit():
            obj = obj[int(p)]
        else:
            return None
    return obj


def _flatten(rec: dict, prefix: str = "", depth: int = 0) -> dict:
    out: dict = {}
    for k, v in rec.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict) and depth < 2:
            out.update(_flatten(v, f"{key}_", depth + 1))
        elif isinstance(v, (list, dict)):
            out[key] = json.dumps(v, ensure_ascii=False)
        else:
            out[key] = v
    return out


class APIConnector(Connector):
    type = "api"

    def __init__(self, ds: dict, secret: str):
        super().__init__(ds, secret)
        self._cache: dict[str, tuple[float, list[dict]]] = {}
        self._lock = threading.Lock()

    def _endpoint(self, table: str) -> dict:
        eps = self.cfg.get("endpoints") or {}
        ep = dict(eps.get(table) or {"path": table})
        ep.setdefault("method", self.cfg.get("method") or "GET")
        ep.setdefault("records_path", self.cfg.get("records_path"))
        return ep

    def _headers(self) -> dict:
        h = {"Accept": "application/json", **(self.cfg.get("headers") or {})}
        if self.secret:
            name = self.cfg.get("auth_header") or "Authorization"
            scheme = self.cfg.get("auth_scheme", "Bearer")
            h[name] = f"{scheme} {self.secret}".strip() if scheme else self.secret
        return h

    def fetch(self, table: str) -> list[dict]:
        ttl = float(self.cfg.get("cache_seconds", 60))
        with self._lock:
            hit = self._cache.get(table)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
        ep = self._endpoint(table)
        path = ep.get("path") or table
        url = path if path.startswith("http") else f"{(self.cfg.get('base_url') or '').rstrip('/')}/{path.lstrip('/')}"
        try:
            r = httpx.request(
                ep["method"].upper(), url, headers=self._headers(), params=ep.get("params") or None,
                json=ep.get("body"), timeout=float(self.cfg.get("timeout", 30)),
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:  # noqa: BLE001
            raise ConnectorError(f"API 请求失败 {url}: {e}") from e
        recs = _dig(data, ep.get("records_path"))
        if isinstance(recs, dict):
            # {"col": [..], ...} 列式结构
            if recs and all(isinstance(v, list) for v in recs.values()):
                keys = list(recs)
                recs = [dict(zip(keys, vals)) for vals in zip(*recs.values())]
            else:
                recs = [recs]
        if not isinstance(recs, list):
            raise ConnectorError(f"未在路径 {ep.get('records_path')!r} 找到记录数组")
        recs = [_flatten(x) if isinstance(x, dict) else {"value": x} for x in recs]
        with self._lock:
            self._cache[table] = (time.time(), recs)
        return recs

    def _duck(self, tables: list[str]) -> duckdb.DuckDBPyConnection:
        con = duckdb.connect(":memory:")
        for t in tables:
            recs = self.fetch(t)
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=True, encoding="utf-8") as f:
                for rec in recs:
                    f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
                f.flush()
                # 双写引号以保留逻辑表名，同时禁止把接口名解释成 SQL。
                safe = t.replace('"', '""')
                if recs:
                    con.execute(f"CREATE TABLE \"{safe}\" AS SELECT * FROM read_json_auto('{f.name}', format='newline_delimited')")
                else:
                    con.execute(f'CREATE TABLE "{safe}" (value VARCHAR)')
        # 数据载入后禁止访问本地文件与网络（read_csv('/etc/passwd') 之类），该设置不可再打开
        con.execute("SET enable_external_access = false")
        return con

    def test(self) -> dict:
        t0 = time.time()
        eps = list((self.cfg.get("endpoints") or {}).keys())
        if eps:
            n = len(self.fetch(eps[0]))
            return {"ok": True, "version": f"接口 {eps[0]} 返回 {n} 条记录", "latency_ms": int((time.time() - t0) * 1000)}
        r = httpx.get(self.cfg.get("base_url") or "", headers=self._headers(), timeout=10)
        return {"ok": r.status_code < 500, "version": f"HTTP {r.status_code}", "latency_ms": int((time.time() - t0) * 1000)}

    def list_tables(self, schema: str | None = None) -> list[dict]:
        eps = self.cfg.get("endpoints") or {}
        return [{"schema": None, "table": k, "name": k, "comment": v.get("description", ""), "kind": "table"}
                for k, v in eps.items()]

    def columns(self, table: str) -> list[dict]:
        with self._duck([table]) as con:
            name = table.replace('"', '""')
            rows = con.execute(f'DESCRIBE "{name}"').fetchall()
        return [{"name": r[0].lower(), "source_name": r[0], "type": r[1], "nullable": True, "comment": ""} for r in rows]

    def distinct_values(self, table: str, column: str, search: str, limit: int, offset: int) -> dict:
        # 使用与正式查询相同的 DuckDB 推断类型，避免把接口记录里的日期或数字当作另一种类型。
        eps = self.cfg.get("endpoints") or {}
        if eps and table not in eps:
            raise ConnectorError(f"未配置接口表 {table}")
        name, col = ['"' + x.replace('"', '""') + '"' for x in (table, column)]
        try:
            with self._duck([table]) as con:
                fields = con.execute(f"DESCRIBE {name}").fetchall()
                if not any(r[0] == column for r in fields):
                    raise ConnectorError(f"接口表 {table} 中没有字段 {column}")
                sql = f"SELECT candidate FROM (SELECT DISTINCT {col} AS candidate FROM {name}) vals"
                params = [search.lower()] if search else []
                if search:
                    sql += " WHERE STRPOS(LOWER(CAST(candidate AS VARCHAR)), ?) > 0"
                sql += " ORDER BY candidate NULLS FIRST LIMIT ? OFFSET ?"
                rows = con.execute(sql, [*params, limit + 1, offset]).fetchall()
        except duckdb.Error as e:
            raise ConnectorError(f"无法读取候选值：{str(e).splitlines()[0]}") from e
        return value_options(rows, limit)

    def query(self, sql: str, limit: int, *, params: dict | None = None, tables: list[str] | None = None) -> dict:
        s = sql
        if tables is None:
            s = ensure_readonly(sql)
            tables = list(dict.fromkeys(m.group(2) for m in _TABLE_REF.finditer(s)))
            # 数据源调试保留原取表逻辑；场景查询直接使用作用域解析后的物理表。
            ctes = {m.group(1).lower() for m in re.finditer(r"(\w+)\s+as\s*\(", s, re.I)}
            tables = [t for t in tables if t.lower() not in ctes]
        eps = self.cfg.get("endpoints") or {}
        if eps:  # 已声明接口时只加载声明过的逻辑表（避免把 EXTRACT(x FROM col) 误判为表）
            tables = [t for t in tables if t in eps]
        try:
            with self._duck(tables) as con:
                cur = con.execute(f"SELECT * FROM ({s}) _rq LIMIT {int(limit) + 1}", params or {})
                cols = [d[0] for d in cur.description]
                rows = [[norm_value(v) for v in r] for r in cur.fetchall()]
        except ConnectorError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ConnectorError(f"API 数据查询失败（DuckDB 方言）：{str(e).splitlines()[0][:400]}") from e
        return {"columns": norm_columns(cols), "rows": rows[:limit], "truncated": len(rows) > limit}

    def sample(self, table: str, n: int) -> dict:
        recs = self.fetch(table)[:n]
        cols = list(dict.fromkeys(k for r in recs for k in r))
        return {"columns": norm_columns(cols), "rows": [[norm_value(r.get(c)) for c in cols] for r in recs],
                "truncated": False}

    def count(self, table: str) -> int | None:
        return len(self.fetch(table))
