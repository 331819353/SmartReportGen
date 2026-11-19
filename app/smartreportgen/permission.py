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
from .connectors.sql import oracle_schema

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


def source_scene_tables(source: dict, principal: Principal | None = None) -> dict[str, dict]:
    """取得数据源开放给用户建场景的表，始终以管理员配置的标识符为准。"""
    if not source.get("enabled", True):
        return {}
    entries = (source.get("config") or {}).get("scenario_tables", [])
    if not isinstance(entries, list) or any(not isinstance(item, dict) or not isinstance(item.get("table_name"), str)
                                             for item in entries):
        return {}
    # 未传员工用于加载表的固定行范围。员工可选表还须匹配工号或角色。
    if principal is None:
        return {item["table_name"]: item for item in entries}
    if not principal.registered:
        return {}
    return {item["table_name"]: item for item in entries
            if principal.is_admin or principal.emp_no in item.get("employees", [])
            or set(principal.roles).intersection(item.get("roles", []))}


def access_for(p: Principal) -> Access:
    if p.is_admin:
        return Access(principal=p, all=True)
    acc = Access(principal=p)
    if not p.registered or not p.emp_no:
        return acc
    # 历史场景按原授权处理。个人场景的作者自动获得配置的表，受邀用户还须有场景授权。
    # 每次仍与数据源当前开放表取交集，撤销源权限后既有个人场景不再授予数据访问。
    rows = db.fetchall(
        """WITH grants AS (
               SELECT DISTINCT dataset_id, table_name FROM permission.table_grant
               WHERE (subject_type='employee' AND subject=%s) OR (subject_type='role' AND subject=ANY(%s))
           )
           SELECT d.id AS dataset_id, d.owner_emp_no, d.datasource_id, t.table_name,
                  CASE WHEN d.owner_emp_no IS NOT NULL THEN s.config END AS config
           FROM dataset.dataset d JOIN dataset.dataset_table t ON t.dataset_id=d.id
           LEFT JOIN datasource.datasource s ON s.id=d.datasource_id
           WHERE d.enabled AND (d.owner_emp_no IS NULL OR s.enabled)
             AND (d.owner_emp_no=%s OR EXISTS (
                 SELECT 1 FROM grants g WHERE g.dataset_id=d.id
                 AND (g.table_name='*' OR lower(g.table_name)=lower(t.table_name))
             ))""",
        (p.emp_no, p.roles or [], p.emp_no),
    )
    permitted = {}
    for row in rows:
        if row["owner_emp_no"] is not None:
            source_id = row["datasource_id"]
            if source_id not in permitted:
                permitted[source_id] = source_scene_tables(row, p)
            if row["table_name"] not in permitted[source_id]:
                continue
        acc.tables.setdefault(row["dataset_id"], set()).add(row["table_name"].lower())
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
        return (oracle_schema(cfg) or cfg.get("username") or "").lower() or None
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
