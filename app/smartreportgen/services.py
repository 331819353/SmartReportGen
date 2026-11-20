"""API 与 MCP 共用的业务操作。"""
from __future__ import annotations

import re
from uuid import uuid4

from sqlglot import exp

from . import repo
from .auth import Principal
from .config import settings
from .connectors import ConnectorError, get_connector, profile
from .connectors.base import value_options
from .permission import DIALECT
from .report_document import inline_legacy_echarts, merge_report_documents, report_document_error
from .scenario_filters import _table_name, effective_filters, query_in_scenario, table_sql
from .telemetry import measure


def datasource_for_dataset(ds: dict) -> dict:
    if not ds.get("datasource_id"):
        raise repo.Invalid("该数据集未关联数据源")
    return repo.get_datasource(ds["datasource_id"], with_secret=True)


def describe_table(ds_row: dict, table: str, sample_rows: int = 5, profile_rows: int = 2000,
                   *, dataset: dict | None = None, allowed: set[str] | None = None,
                   include_count: bool = True, include_extrema: bool = True,
                   refresh_schema: bool = False) -> dict:
    conn = get_connector(ds_row)
    out: dict = {"datasource": ds_row["name"], "datasource_type": ds_row["type"], "table": table}
    try:
        out["columns"] = conn.cached_columns(table, refresh=refresh_schema)
    except ConnectorError as e:
        out["columns"] = []
        out["columns_error"] = str(e)
    # 场景画像的行数、样本、最值必须来自同一取值范围，不能再回查未经筛选的原表。
    item = next((t for t in dataset["tables"] if t["table_name"] == table), None) if dataset else None
    rules = effective_filters(item) if item else []
    source = table_sql(ds_row["type"], table) if rules else table

    def query(sql: str, n: int) -> dict:
        return query_in_scenario(ds_row, dataset, sql, n, conn, allowed) if rules else conn.query(sql, n)

    # MCP 的轻量模式跳过全范围统计；管理 API 仍沿用完整模式的默认行为。
    sample_rows = max(0, sample_rows)
    n = max(sample_rows, max(0, min(profile_rows, 2000)))
    out["row_count"] = None
    out["row_count_status"] = "not_requested"
    if rules:
        out["row_filters"] = rules
    if include_count:
        with measure("describe.count"):
            if rules:
                out["row_count"] = query(f"SELECT COUNT(*) FROM {source}", 1)["rows"][0][0]
            else:
                out["row_count"] = conn.count(table)
        out["row_count_status"] = "exact" if out["row_count"] is not None else "unavailable"
    # 明确请求零样本时不再隐式读取 2,000 行；字段结构来自独立元数据。
    sample = {"columns": [c["name"] for c in out["columns"]], "rows": []}
    if n:
        with measure("describe.sample"):
            sample = query(f"SELECT * FROM {source}", n) if rules else conn.sample(table, n)
    out["sample_columns"] = sample["columns"]
    out["sample_rows"] = sample["rows"][:sample_rows]
    with measure("describe.profile"):
        out["profile"] = profile(sample["columns"], sample["rows"])
    out["profile_sample_size"] = len(sample["rows"])
    out["profile_basis"] = f"基于前 {len(sample['rows'])} 行样本"
    out["extrema_status"] = "not_requested" if not include_extrema else "unavailable"
    out["coverage_note"] = "样本画像不证明业务覆盖、唯一键或全量指标；须通过直接事实查询核实。"
    # 样本只覆盖前若干行：对日期/数值列补充全表最值，避免误判时间范围
    span_cols = [p["column"] for p in out["profile"] if p["kind"] in ("datetime", "number")][:30]
    if include_extrema and span_cols and (rules or re.fullmatch(r"[\w.$#]+", table)):
        exprs = ", ".join(f"MIN({c}) AS mn_{i}, MAX({c}) AS mx_{i}" for i, c in enumerate(span_cols))
        try:
            with measure("describe.extrema"):
                row = query(f"SELECT {exprs} FROM {source}", 1)["rows"][0]
            for i, c in enumerate(span_cols):
                p = next(p for p in out["profile"] if p["column"] == c)
                p["full_min"], p["full_max"] = row[2 * i], row[2 * i + 1]
            out["profile_basis"] += "；日期/数值列另附取数范围内的最值 full_min/full_max"
            out["extrema_status"] = "exact"
        except (ConnectorError, IndexError):
            pass
    comments = {c["name"]: c.get("comment") for c in out["columns"]}
    for p in out["profile"]:
        if comments.get(p["column"]):
            p["comment"] = comments[p["column"]]
    return out


def source_scene_values(source: dict, table: dict, column: str, search: str, limit: int, offset: int) -> dict:
    """个人场景的取值选择器同样应用源行范围，然后去重、搜索和分页。"""
    name = table["table_name"]
    connector = get_connector(source)
    if not effective_filters(table):
        return connector.distinct_values(name, column, search, limit, offset)
    field = next((item for item in connector.cached_columns(name) if item["source_name"] == column), None)
    if not field:
        raise ConnectorError(f"表 {name} 中没有字段 {column}")
    kind = source["type"]
    dialect = DIALECT[kind]
    value = exp.column(column, quoted=True)
    if kind == "oracle":
        dtype = field["type"].upper()
        # Oracle 候选时间用可回传给绑定器的 ISO 格式。
        if dtype == "DATE" or dtype.startswith("TIMESTAMP"):
            pattern = 'YYYY-MM-DD"T"HH24:MI:SS' + (".FF9" if dtype.startswith("TIMESTAMP") else "")
            if "TIME ZONE" in dtype:
                value = exp.Anonymous(this="SYS_EXTRACT_UTC", expressions=[value])
                pattern += '"Z"'
            value = exp.Anonymous(this="TO_CHAR", expressions=[value, exp.Literal.string(pattern)])
    stmt = exp.select(exp.alias_(value.copy(), "candidate")).from_(_table_name(name, dialect)).distinct()
    bindings = None
    if search:
        marker = f"sv_{uuid4().hex}_"
        text_type = {"api": "VARCHAR", "postgresql": "TEXT", "oracle": "VARCHAR2(4000)",
                     "mysql": "CHAR", "starrocks": "VARCHAR"}[kind]
        arg = exp.func("LOWER", exp.cast(value.copy(), text_type, dialect=dialect))
        fn = "STRPOS" if kind in ("api", "postgresql") else "INSTR"
        match = exp.Anonymous(this=fn, expressions=[arg, exp.Var(this=marker + "q0")])
        stmt = stmt.where(exp.GT(this=match, expression=exp.Literal.number(0)))
        bindings = (marker, {"q0": search.lower()})
    candidate = exp.column("candidate")
    stmt = stmt.order_by(exp.Ordered(this=candidate, nulls_first=True)).limit(limit + 1).offset(offset)
    result = query_in_scenario(source, {"tables": [table]}, stmt.sql(dialect=dialect), limit + 1, connector,
                               {name.lower()}, query_binds=bindings)
    # 查询通道用标签保留 Decimal 和长整数。候选值接口用对应原值文本。
    rows = [[row[0]["$decimal"] if isinstance(row[0], dict) else row[0]] for row in result["rows"]]
    return value_options(rows, limit)


def run_query(ds_row: dict, sql: str, limit: int | None = None,
              *, dataset: dict | None = None, allowed: set[str] | None = None,
              snapshot: bool = False) -> dict:
    # 快照模式的行数上限适用于文件；普通查询继续使用原来的小结果集上限。
    max_rows = max(1, settings.query_max_rows) if snapshot else 5000
    limit = max(1, min(int(limit or (max_rows if snapshot else 200)), max_rows))
    conn = get_connector(ds_row)
    with measure("query.execute"):
        res = query_in_scenario(ds_row, dataset, sql, limit, conn, allowed) if dataset else conn.query(sql, limit)
    return {"datasource": ds_row["name"], "dialect": ds_row["type"], **res, "row_count": len(res["rows"])}


def revise_report(report_id: int, principal: Principal, name: str, *, base_version: int | None = None,
                  draft_id: str | None = None, preview_id: str | None = None, html: str | None = None,
                  dataset_id: int | None = None, change_note: str = "", expected_version: int | None = None) -> dict:
    """正文修订走浏览器验收，纯元数据更新沿用原版本内容与证据。"""
    if any(item is not None for item in (draft_id, preview_id, html)):
        from . import report_pipeline

        return report_pipeline.persist_report(name, principal, report_id=report_id, base_version=base_version,
                                              draft_id=draft_id, preview_id=preview_id, html=html,
                                              dataset_id=dataset_id, change_note=change_note,
                                              expected_version=expected_version)
    with measure("report.persist"):
        return repo.update_report(report_id, principal, name, None, base_version,
                                  change_note=change_note, expected_version=expected_version)


def merge_reports(principal: Principal, name: str, versions: list[tuple[int, int]]) -> str:
    """读取用户指定的快照，全部通过权限及单文件检查后才返回下载内容。"""
    name = name.strip()
    if not name or "\x00" in name:
        raise repo.Invalid("请输入有效的文件名称")
    reports, seen = [], set()
    for report_id, version in versions:
        report = repo.get_report(report_id, principal, version=version)
        # 迁移前的链接可能和当前 ID 指向同一份报告，按实际 ID 去重。
        if report["id"] in seen:
            raise repo.Invalid("请为每份报告只选择一个版本")
        seen.add(report["id"])
        if not report["meta"].get("single_file"):
            report["html"] = inline_legacy_echarts(report["html"])
            if error := report_document_error(report["html"], legacy=True):
                raise repo.Invalid(f"「{report['name']}」版本 {version} 无法离线合并：{error}")
        reports.append(report)
    return merge_report_documents(name, reports)


def mcp_endpoint() -> str:
    return settings.mcp_public_url
