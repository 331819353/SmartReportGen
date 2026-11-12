"""数据权限：根据令牌中的员工工号查询权限表，计算可访问的业务场景与数据表，并校验 SQL 引用的表。

授权模型（permission schema）：
- employee：工号、角色、是否管理员、是否启用
- table_grant：对“员工”或“角色”授予某数据集（业务场景）的某张表；table_name='*' 表示该数据集全部表
校验策略为 **默认拒绝（fail closed）**：SQL 无法解析、引用了未授权表、使用表函数，一律拒绝。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from . import db
from .auth import Principal

DIALECT = {"postgresql": "postgres", "mysql": "mysql", "starrocks": "starrocks", "oracle": "oracle", "api": "duckdb"}


class PermissionDenied(PermissionError):
    pass


@dataclass
class Access:
    principal: Principal
    all: bool = False
    tables: dict[int, set[str]] = field(default_factory=dict)  # dataset_id -> 表名（小写）

    def dataset_ids(self) -> list[int] | None:
        return None if self.all else sorted(self.tables)

    def can_dataset(self, dataset_id: int) -> bool:
        return self.all or bool(self.tables.get(int(dataset_id)))

    def can_table(self, dataset_id: int, table: str) -> bool:
        return self.all or table.lower() in self.tables.get(int(dataset_id), set())

    def tables_for_datasource(self, datasource_id: int) -> set[str] | None:
        """某数据源上可访问的全部表（跨数据集合并）；None 表示不限。"""
        if self.all:
            return None
        rows = db.fetchall("SELECT id FROM dataset.dataset WHERE datasource_id=%s", (datasource_id,))
        out: set[str] = set()
        for r in rows:
            out |= self.tables.get(r["id"], set())
        return out


def access_for(p: Principal) -> Access:
    if p.is_admin:
        return Access(principal=p, all=True)
    if not p.registered or not p.emp_no:
        return Access(principal=p)
    rows = db.fetchall(
        """SELECT g.dataset_id, g.table_name FROM permission.table_grant g
           JOIN dataset.dataset d ON d.id = g.dataset_id AND d.enabled
           WHERE (g.subject_type='employee' AND g.subject=%s) OR (g.subject_type='role' AND g.subject = ANY(%s))""",
        (p.emp_no, p.roles or []),
    )
    acc = Access(principal=p)
    wildcard = {r["dataset_id"] for r in rows if r["table_name"] == "*"}
    if wildcard:
        for t in db.fetchall("SELECT dataset_id, table_name FROM dataset.dataset_table WHERE dataset_id = ANY(%s)",
                             (list(wildcard),)):
            acc.tables.setdefault(t["dataset_id"], set()).add(t["table_name"].lower())
    # 显式表授权仅在该表仍属于数据集时生效
    explicit = [r for r in rows if r["table_name"] != "*"]
    if explicit:
        existing = {(t["dataset_id"], t["table_name"].lower()) for t in db.fetchall(
            "SELECT dataset_id, table_name FROM dataset.dataset_table WHERE dataset_id = ANY(%s)",
            (list({r["dataset_id"] for r in explicit}),))}
        for r in explicit:
            key = (r["dataset_id"], r["table_name"].lower())
            if key in existing:
                acc.tables.setdefault(key[0], set()).add(key[1])
    return acc


def describe(acc: Access) -> dict:
    """权限概览（管理台“权限查询”使用）。"""
    if acc.all:
        ds = db.fetchall("SELECT id, business_scenario FROM dataset.dataset ORDER BY id")
        return {"all": True, "datasets": [{"dataset_id": d["id"], "business_scenario": d["business_scenario"],
                                           "tables": ["*"]} for d in ds]}
    out = []
    for ds_id, tabs in sorted(acc.tables.items()):
        d = db.fetchone("SELECT business_scenario FROM dataset.dataset WHERE id=%s", (ds_id,))
        out.append({"dataset_id": ds_id, "business_scenario": d["business_scenario"] if d else "?",
                    "tables": sorted(tabs)})
    return {"all": False, "datasets": out}


# ------------------------------------------------------------ SQL 表引用校验
def referenced_tables(sql: str, ds_type: str) -> set[str]:
    """解析 SQL 中引用的物理表（排除 CTE）；无法解析或使用表函数时抛 PermissionDenied。"""
    try:
        trees = sqlglot.parse(sql, read=DIALECT.get(ds_type, None))
    except sqlglot.errors.ParseError as e:
        raise PermissionDenied(f"SQL 无法解析，出于安全考虑拒绝执行：{str(e).splitlines()[0][:200]}") from e
    trees = [t for t in trees if t is not None]
    if len(trees) != 1:
        raise PermissionDenied("只允许单条查询语句")
    tree = trees[0]
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    refs: set[str] = set()
    for t in tree.find_all(exp.Table):
        if not isinstance(t.this, exp.Identifier):
            raise PermissionDenied(f"不允许在 FROM 中使用表函数：{t.sql()[:80]}")
        name = t.name
        if not t.db and name.lower() in ctes:
            continue
        refs.add(".".join(p for p in (t.catalog, t.db, name) if p).lower())
    return refs


# 能在字符串里执行任意 SQL / 读取外部数据的函数：会绕过表级校验，一律禁止
_DANGEROUS_FN = re.compile(
    r"\b(query_to_xml\w*|table_to_xml\w*|cursor_to_xml\w*|schema_to_xml\w*|database_to_xml\w*|dblink\w*|"
    r"pg_read_\w+|pg_ls_dir|lo_\w+|dbms_\w+|utl_\w+|xmltype|load_file|sys_exec|sys_eval|query|query_table|"
    r"read_\w+|glob|httpfs\w*|getxml)\s*\(", re.I)
_STR = re.compile(r"'(?:[^']|'')*'")
_CMT = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)


def _check_functions(sql: str) -> None:
    bare = _STR.sub("''", _CMT.sub(" ", sql))
    m = _DANGEROUS_FN.search(bare)
    if m:
        raise PermissionDenied(f"出于数据权限安全考虑，不允许使用函数 {m.group(1)}()")


def _default_schema(ds_row: dict) -> str | None:
    cfg = ds_row.get("config") or {}
    t = ds_row["type"]
    if t in ("mysql", "starrocks"):
        return (cfg.get("database") or "").lower() or None
    if t == "postgresql":
        return (cfg.get("search_path") or "public").split(",")[0].strip().lower()
    if t == "oracle":
        return (cfg.get("schema") or cfg.get("username") or "").lower() or None
    return None


def check_sql(acc: Access, ds_row: dict, sql: str) -> None:
    allowed = acc.tables_for_datasource(ds_row["id"]) if ds_row.get("id") else None
    if allowed is None:
        return
    _check_functions(sql)
    default = _default_schema(ds_row)
    denied = []
    for ref in referenced_tables(sql, ds_row["type"]):
        if ref in allowed:
            continue
        if "." not in ref and default and f"{default}.{ref}" in allowed:
            continue
        if ref.count(".") == 1 and default and ref.split(".")[0] == default and ref.split(".")[1] in allowed:
            continue
        denied.append(ref)
    if denied:
        raise PermissionDenied(f"{acc.principal.label} 无权访问数据表：{', '.join(sorted(denied))}")
