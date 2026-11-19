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
from decimal import Decimal
import re
from contextlib import contextmanager
from contextvars import ContextVar
import threading
import time
from typing import Any

import duckdb
import httpx

from .base import Connector, ConnectorError, ensure_readonly, norm_columns, norm_value, value_options
from ..config import settings
from ..telemetry import measure

_TABLE_REF = re.compile(r'\b(?:from|join)\s+("?)([\w./-]+)\1', re.I)


def _dig(obj: Any, path: str | None, default=None) -> Any:
    if not path:
        return obj
    for p in path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(p, default)
        elif isinstance(obj, list) and p.isdigit():
            obj = obj[int(p)] if int(p) < len(obj) else default
        else:
            return default
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
        self._cache: dict[str, tuple[float, list[dict], str | None, bool]] = {}
        self._loaded: dict[str, list[dict]] = {}
        self._lock = threading.RLock()
        self._pinned = ContextVar("api_report_tables", default=None)
        self._database = duckdb.connect(":memory:", config={"enable_external_access": False})

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
        pinned = self._pinned.get()
        if pinned is not None and table in pinned:
            return pinned[table]
        ttl = float(self.cfg.get("cache_seconds", 60))
        with self._lock:
            hit = self._cache.get(table)
            if hit and time.monotonic() - hit[0] < ttl:
                return hit[1]
            records, revision, complete = self._fetch_pages(table)
            self._cache[table] = (time.monotonic(), records, revision, complete)
            return records

    def _fetch_pages(self, table: str) -> tuple[list[dict], str | None, bool]:
        ep = self._endpoint(table)
        path = ep.get("path") or table
        url = path if path.startswith("http") else f"{(self.cfg.get('base_url') or '').rstrip('/')}/{path.lstrip('/')}"
        pager = ep.get("pagination") or {}
        cursor_name, cursor_path = pager.get("cursor_param"), pager.get("next_cursor_path")
        total_path = pager.get("total_path")
        revision_path = ep.get("revision_path")
        page_name, offset_name = pager.get("page_param"), pager.get("offset_param")
        if bool(cursor_name) != bool(cursor_path):
            raise ConnectorError("API 游标分页必须同时配置 cursor_param 和 next_cursor_path")
        if sum(bool(option) for option in (cursor_name, page_name, offset_name)) > 1 or (
                (page_name or offset_name) and not total_path):
            raise ConnectorError("API 页码或偏移分页必须独立配置并提供 total_path")
        size_name = pager.get("page_size_param")
        page_size = min(10000, max(1, int(pager.get("page_size", 100))))
        maximum = min(1000, max(1, int(pager.get("max_pages", 100))))
        max_rows = min(1000000, max(1, int(ep.get("max_records", 200000))))
        params = dict(ep.get("params") or {})
        if size_name:
            params[size_name] = page_size
        start_page = int(pager.get("start_page", 1))
        deadline = time.monotonic() + settings.query_timeout
        seen, records, expected, revision = set(), [], None, None
        for index in range(maximum):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ConnectorError(f"接口表 {table} 分页取数超过查询时限")
            if page_name:
                params[page_name] = start_page + index
            if offset_name:
                params[offset_name] = len(records)
            try:
                with measure("api.fetch"):
                    response = httpx.request(ep["method"].upper(), url, headers=self._headers(),
                                             params=params or None, json=ep.get("body"),
                                             timeout=min(remaining, float(self.cfg.get("timeout", 30))))
                    response.raise_for_status()
                    # 有显式列类型时保留 JSON 十进制原值，再由 DuckDB 按声明类型解析。
                    data = (json.loads(response.content, parse_float=Decimal)
                            if ep.get("column_types") else response.json())
            except (httpx.HTTPError, ValueError) as exc:
                raise ConnectorError(f"接口表 {table} 请求或 JSON 解码失败") from exc
            recs = _dig(data, ep.get("records_path"))
            if isinstance(recs, dict):
                if recs and all(isinstance(v, list) for v in recs.values()):
                    if len({len(v) for v in recs.values()}) != 1:
                        raise ConnectorError(f"接口表 {table} 的列式响应长度不一致")
                    recs = [dict(zip(recs, vals)) for vals in zip(*recs.values())]
                else:
                    recs = [recs]
            if not isinstance(recs, list):
                raise ConnectorError(f"未在路径 {ep.get('records_path')!r} 找到记录数组")
            if revision_path:
                version = _dig(data, revision_path)
                if type(version) not in (str, int) or version == "" or (index and str(version) != revision):
                    raise ConnectorError(f"接口表 {table} 的源数据版本缺失或分页期间发生变化")
                revision = str(version)
            if total_path:
                total = _dig(data, total_path)
                if type(total) is not int or total < 0 or (index and total != expected):
                    raise ConnectorError(f"接口表 {table} 分页总行数无效或发生变化")
                expected = total
            if len(records) + len(recs) > max_rows:
                raise ConnectorError(f"接口表 {table} 超过取数上限，请缩小接口范围")
            records.extend(_flatten(x) if isinstance(x, dict) else {"value": x} for x in recs)
            if (page_name or offset_name) and len(records) < expected:
                if not recs:
                    raise ConnectorError(f"接口表 {table} 在达到总行数前返回空页")
                continue
            missing = object()
            cursor = _dig(data, cursor_path, missing) if cursor_path else None
            if cursor is missing:
                raise ConnectorError(f"接口表 {table} 缺少已配置的下一页游标字段")
            if cursor is None or cursor == "":
                if expected is not None and len(records) != expected:
                    raise ConnectorError(f"接口表 {table} 返回 {len(records)} 行，和总数 {expected} 不符")
                # 未配置合同的接口可以探查，但报告不能把一次 HTTP 响应等同于全量。
                complete = bool(cursor_path or total_path or ep.get("complete") is True)
                return records, revision, complete and (not index or bool(revision) or ep.get("immutable") is True)
            if type(cursor) not in (str, int) or cursor in seen:
                raise ConnectorError(f"接口表 {table} 返回无效或重复游标")
            seen.add(cursor)
            params[cursor_name] = cursor
        raise ConnectorError(f"接口表 {table} 超过最大分页数，未获得完整结果")

    @contextmanager
    def report_batch(self, tables: set[str]):
        # 固定本次读取的接口响应，批次内任何一条 SQL 都不会触发数据刷新。
        with self._lock:
            pinned = {table: self.fetch(table) for table in sorted(tables)}
            for table in tables:
                if not self._cache[table][3]:
                    raise ConnectorError(f"接口表 {table} 未声明完整响应或分页合同")
            versions = {self._cache[table][2] for table in tables}
            if len(tables) > 1 and (len(versions) != 1 or None in versions):
                raise ConnectorError("多个 API 接口必须提供相同的上游数据版本")
            token = self._pinned.set(pinned)
            try:
                yield "api_source_revision" if versions - {None} else "api_fixed_payload"
            finally:
                self._pinned.reset(token)

    @contextmanager
    def _duck(self, tables: list[str]):
        # 同一连接器复用已解析的内存表，刷新时原子替换；查询不再反复写临时磁盘。
        with self._lock:
            con = self._database
            for table in tables:
                records = self.fetch(table)
                if self._loaded.get(table) is records:
                    continue
                name = '"' + table.replace('"', '""') + '"'
                raw = [json.dumps(row, ensure_ascii=False, default=str) for row in records]
                declared = self._endpoint(table).get("column_types") or {}
                if records or declared:
                    schema = con.execute("SELECT json_group_structure(item::JSON) FROM unnest(?::VARCHAR[]) t(item)", [raw]).fetchone()[0]
                    inferred = json.loads(schema)
                    fields = {**(inferred if isinstance(inferred, dict) else {}), **declared}
                    if not fields:
                        fields = {"value": "VARCHAR"}
                    con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT rec.* FROM "
                                "(SELECT json_transform_strict(item::JSON, ?) AS rec FROM unnest(?::VARCHAR[]) t(item))",
                                [json.JSONEncoder().encode(fields), raw])
                else:
                    con.execute(f'CREATE OR REPLACE TABLE {name} (value VARCHAR)')
                self._loaded[table] = records
            yield con

    def close(self) -> None:
        with self._lock:
            self._database.close()

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
                # 完整性与查询内容取自同一缓存状态，避免刚好发生刷新时错配。
                complete = bool(tables) and all(self._cache.get(table, (None, None, None, False))[3]
                                                for table in tables)
        except ConnectorError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ConnectorError(f"API 数据查询失败（DuckDB 方言）：{str(e).splitlines()[0][:400]}") from e
        return {"columns": norm_columns(cols), "rows": rows[:limit], "truncated": len(rows) > limit,
                "source_complete": complete}

    def sample(self, table: str, n: int) -> dict:
        recs = self.fetch(table)[:n]
        cols = list(dict.fromkeys(k for r in recs for k in r))
        return {"columns": norm_columns(cols), "rows": [[norm_value(r.get(c)) for c in cols] for r in recs],
                "truncated": False}

    def count(self, table: str) -> int | None:
        return len(self.fetch(table))
