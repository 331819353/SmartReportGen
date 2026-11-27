"""数据权限：根据令牌中的员工工号查询权限表，计算可访问的业务场景与数据表，并校验 SQL 引用的表。

授权模型（permission schema）：
- employee：工号、角色、是否管理员、是否启用
- table_grant：对“员工”或“角色”授予某数据集（业务场景）的某张表；table_name='*' 表示该数据集全部表
SQL 表引用由 scenario_filters.query_in_scenario 按当前场景校验。本模块提供权限范围和共享校验规则。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

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
    scene_shares: dict[int, str] = field(default_factory=dict)  # 已核实完整表范围的场景分享
    share_only_datasets: set[int] = field(default_factory=set)  # 只有场景分享、无原有表授权

    def dataset_ids(self) -> list[int] | None:
        return None if self.all else sorted(self.tables)

    def can_dataset(self, dataset_id: int) -> bool:
        return self.all or bool(self.tables.get(int(dataset_id)))

    def can_table(self, dataset_id: int, table: str) -> bool:
        return self.all or table.lower() in self.tables.get(int(dataset_id), set())


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


def personal_scene_share_tables(source: dict, owner: dict | None) -> dict[str, dict]:
    """个人场景的授权始终限定在创建人当前可引用的来源目录内。"""
    if not owner or not owner["enabled"]:
        return {}
    return source_scene_tables(source, Principal(owner["emp_no"], is_admin=owner["is_admin"], roles=owner["roles"]))


def access_for(p: Principal, *, connection=None) -> Access:
    if p.is_admin:
        return Access(principal=p, all=True)
    acc = Access(principal=p)
    if not p.registered or not p.emp_no:
        return acc
    # 分享授予接收人场景内的表权限。原本的直接或角色授权仍独立计算。
    sql = (
        """WITH grants AS (
               SELECT DISTINCT dataset_id, table_name FROM permission.table_grant
               WHERE (subject_type='employee' AND subject=%s) OR (subject_type='role' AND subject=ANY(%s))
           ), shares AS (
               SELECT sh.dataset_id, sh.access_level, count(*) AS share_table_count
               FROM dataset.dataset_share sh JOIN dataset.dataset_table t ON t.dataset_id=sh.dataset_id
               WHERE sh.emp_no=%s GROUP BY sh.dataset_id, sh.access_level
           ), scoped AS (
               SELECT d.id AS dataset_id, d.owner_emp_no, d.datasource_id, t.table_name,
                      CASE WHEN d.owner_emp_no IS NOT NULL THEN s.config END AS config,
                      sh.access_level, sh.share_table_count,
                      CASE WHEN e.enabled THEN jsonb_build_object(
                          'emp_no', e.emp_no, 'enabled', e.enabled, 'is_admin', e.is_admin, 'roles', e.roles
                      ) END AS share_owner,
                      COALESCE(d.owner_emp_no=%s, false) OR EXISTS (
                          SELECT 1 FROM grants g WHERE g.dataset_id=d.id
                          AND (g.table_name='*' OR lower(g.table_name)=lower(t.table_name))
                      ) AS granted
               FROM dataset.dataset d JOIN dataset.dataset_table t ON t.dataset_id=d.id
               LEFT JOIN datasource.datasource s ON s.id=d.datasource_id
               LEFT JOIN shares sh ON sh.dataset_id=d.id AND s.enabled
               LEFT JOIN permission.employee e ON e.emp_no=d.owner_emp_no AND sh.dataset_id IS NOT NULL
               WHERE d.enabled AND (d.owner_emp_no IS NULL OR s.enabled)
           )
           SELECT * FROM scoped WHERE granted OR access_level IS NOT NULL"""
    )
    params = (p.emp_no, p.roles or [], p.emp_no, p.emp_no)
    rows = connection.execute(sql, params).fetchall() if connection is not None else db.fetchall(sql, params)
    permitted, owner_catalog, candidates = {}, {}, {}
    for row in rows:
        dataset_id, table = row["dataset_id"], row["table_name"]
        granted = row["granted"]
        owner = row["owner_emp_no"]
        if granted and owner is not None:
            source_id = row["datasource_id"]
            if source_id not in permitted:
                permitted[source_id] = source_scene_tables(row, p)
            granted = table in permitted[source_id]
        if granted:
            acc.tables.setdefault(dataset_id, set()).add(table.lower())
        if row["access_level"]:
            if owner is not None:
                key = (owner, row["datasource_id"])
                if key not in owner_catalog:
                    owner_catalog[key] = personal_scene_share_tables(row, row["share_owner"])
                if table not in owner_catalog[key]:
                    continue
            tables, _, _ = candidates.setdefault(dataset_id, ([], row["share_table_count"], row["access_level"]))
            tables.append(table.lower())
    for dataset_id, (tables, count, level) in candidates.items():
        if len(tables) == count:
            acc.scene_shares[dataset_id] = level
            if dataset_id not in acc.tables:
                acc.share_only_datasets.add(dataset_id)
            acc.tables.setdefault(dataset_id, set()).update(tables)
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
