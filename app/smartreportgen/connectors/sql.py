"""关系型数据库连接器：MySQL / StarRocks（MySQL 协议）/ PostgreSQL / Oracle。"""
from __future__ import annotations

import time
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL, Engine

from ..config import settings
from .base import Connector, ConnectorError, check_ident, ensure_readonly, norm_columns, norm_value

DEFAULT_PORTS = {"mysql": 3306, "starrocks": 9030, "postgresql": 5432, "oracle": 1521}


def _build_engine(ds_type: str, cfg: dict, password: str) -> Engine:
    host = cfg.get("host") or "localhost"
    port = int(cfg.get("port") or DEFAULT_PORTS[ds_type])
    user = cfg.get("username") or None
    t = settings.query_timeout
    opts: dict[str, Any] = {"pool_pre_ping": True, "pool_size": 2, "max_overflow": 4, "pool_recycle": 1800}
    if ds_type in ("mysql", "starrocks"):
        url = URL.create("mysql+pymysql", user, password or None, host, port, cfg.get("database") or None,
                         query={"charset": cfg.get("charset") or "utf8mb4"})
        opts["connect_args"] = {"connect_timeout": 10, "read_timeout": t}
    elif ds_type == "postgresql":
        url = URL.create("postgresql+psycopg", user, password or None, host, port, cfg.get("database") or None)
        pg_opts = f"-c statement_timeout={t * 1000} -c default_transaction_read_only=on"
        if cfg.get("search_path"):
            pg_opts += f" -c search_path={cfg['search_path']}"
        opts["connect_args"] = {"connect_timeout": 10, "options": pg_opts}
        if cfg.get("sslmode"):
            opts["connect_args"]["sslmode"] = cfg["sslmode"]
    elif ds_type == "oracle":
        import oracledb

        oracledb.defaults.fetch_lobs = False
        if cfg.get("service_name"):
            url = URL.create("oracle+oracledb", user, password or None, host, port,
                             query={"service_name": cfg["service_name"]})
        else:
            url = URL.create("oracle+oracledb", user, password or None, host, port, cfg.get("sid") or None)
        opts["connect_args"] = {"tcp_connect_timeout": 10}
    else:
        raise ConnectorError(f"不支持的类型 {ds_type}")
    engine = create_engine(url, **opts)
    if ds_type == "oracle":
        @event.listens_for(engine, "connect")
        def _set_timeout(dbapi_conn, _rec):  # noqa: ANN001
            dbapi_conn.call_timeout = t * 1000
    return engine


class SQLConnector(Connector):
    def __init__(self, ds: dict, secret: str):
        super().__init__(ds, secret)
        self.type = ds["type"]
        self.engine = _build_engine(self.type, self.cfg, secret)

    # ---------------- helpers ----------------
    def _run(self, sql: str, params: Any = None, limit: int | None = None) -> tuple[list[str], list[list]]:
        try:
            with self.engine.connect() as c:
                if params is None:
                    res = c.execution_options(no_parameters=True).exec_driver_sql(sql)
                else:
                    res = c.exec_driver_sql(sql, params)
                cols = list(res.keys())
                raw = res.fetchmany(limit) if limit else res.fetchall()
                return cols, [[norm_value(v) for v in r] for r in raw]
        except ConnectorError:
            raise
        except Exception as e:  # noqa: BLE001
            msg = str(getattr(e, "orig", None) or e).strip().splitlines()[0][:500]
            raise ConnectorError(f"{self.type} 查询失败：{msg}") from e

    def _wrap_limit(self, sql: str, n: int) -> str:
        if self.type == "oracle":
            return f"SELECT * FROM ({sql}) WHERE ROWNUM <= {int(n)}"
        return f"SELECT * FROM ({sql}) _rq LIMIT {int(n)}"

    def _split(self, table: str) -> tuple[str | None, str]:
        parts = [p.strip('"').strip("`") for p in table.split(".")]
        return (parts[-2], parts[-1]) if len(parts) >= 2 else (None, parts[-1])

    # ---------------- API ----------------
    def test(self) -> dict:
        t0 = time.time()
        sql = {"oracle": "SELECT banner FROM v$version WHERE ROWNUM = 1"}.get(self.type, "SELECT version()")
        try:
            _, rows = self._run(sql)
        except ConnectorError:
            _, rows = self._run("SELECT 1 FROM dual" if self.type == "oracle" else "SELECT 1")
        return {"ok": True, "version": str(rows[0][0]) if rows else "", "latency_ms": int((time.time() - t0) * 1000)}

    def list_tables(self, schema: str | None = None) -> list[dict]:
        if self.type in ("mysql", "starrocks"):
            sql = ("SELECT table_schema, table_name, table_comment FROM information_schema.tables "
                   "WHERE table_schema = COALESCE(%s, DATABASE()) ORDER BY table_name")
            _, rows = self._run(sql, (schema or self.cfg.get("database") or None,))
        elif self.type == "postgresql":
            sql = ("SELECT n.nspname, c.relname, obj_description(c.oid) FROM pg_class c "
                   "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind IN ('r','v','m','p','f') "
                   "AND n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%%' "
                   "AND (%s::text IS NULL OR n.nspname = %s::text) ORDER BY 1, 2")
            _, rows = self._run(sql, (schema, schema))
        else:
            owner = (schema or self.cfg.get("schema") or self.cfg.get("username") or "").upper()
            sql = ("SELECT t.owner, t.table_name, c.comments FROM all_tables t LEFT JOIN all_tab_comments c "
                   "ON c.owner = t.owner AND c.table_name = t.table_name WHERE t.owner = :1 ORDER BY t.table_name")
            _, rows = self._run(sql, (owner,))
        return [{"schema": r[0], "table": r[1], "name": f"{r[0]}.{r[1]}", "comment": r[2] or ""} for r in rows]

    def columns(self, table: str) -> list[dict]:
        check_ident(table)
        schema, name = self._split(table)
        if self.type in ("mysql", "starrocks"):
            sql = ("SELECT column_name, column_type, is_nullable, column_comment FROM information_schema.columns "
                   "WHERE table_schema = COALESCE(%s, DATABASE()) AND table_name = %s ORDER BY ordinal_position")
            _, rows = self._run(sql, (schema, name))
        elif self.type == "postgresql":
            sql = ("SELECT a.attname, format_type(a.atttypid, a.atttypmod), NOT a.attnotnull, "
                   "col_description(a.attrelid, a.attnum) FROM pg_attribute a "
                   "WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped ORDER BY a.attnum")
            _, rows = self._run(sql, (table,))
        else:
            owner = (schema or self.cfg.get("schema") or self.cfg.get("username") or "").upper()
            sql = ("SELECT c.column_name, c.data_type, c.nullable, m.comments FROM all_tab_columns c "
                   "LEFT JOIN all_col_comments m ON m.owner = c.owner AND m.table_name = c.table_name "
                   "AND m.column_name = c.column_name WHERE c.owner = :1 AND c.table_name = :2 ORDER BY c.column_id")
            _, rows = self._run(sql, (owner, name.upper()))
        if not rows:
            cols, _ = self._run(self._wrap_limit(f"SELECT * FROM {table}", 0))
            return [{"name": c.lower(), "type": "", "nullable": None, "comment": ""} for c in cols]
        return [
            {"name": str(r[0]).lower(), "type": str(r[1]), "nullable": r[2] in (True, "YES", "Y"), "comment": r[3] or ""}
            for r in rows
        ]

    def query(self, sql: str, limit: int) -> dict:
        s = ensure_readonly(sql)
        cols, rows = self._run(self._wrap_limit(s, limit + 1))
        return {"columns": norm_columns(cols), "rows": rows[:limit], "truncated": len(rows) > limit}

    def sample(self, table: str, n: int) -> dict:
        check_ident(table)
        return self.query(f"SELECT * FROM {table}", n)

    def count(self, table: str) -> int | None:
        check_ident(table)
        try:
            _, rows = self._run(f"SELECT COUNT(*) FROM {table}")
            return int(rows[0][0])
        except ConnectorError:
            return None

    def close(self) -> None:
        self.engine.dispose()
