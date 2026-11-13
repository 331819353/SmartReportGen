"""API 与 MCP 共用的业务操作。"""
from __future__ import annotations

import re

from . import repo
from .auth import Principal
from .config import settings
from .connectors import ConnectorError, get_connector, profile
from .renderer import render
from .report_document import inline_legacy_echarts, merge_report_documents, report_document_error
from .scenario_filters import query_in_scenario, table_sql


def datasource_for_dataset(ds: dict) -> dict:
    if not ds.get("datasource_id"):
        raise repo.Invalid("该数据集未关联数据源")
    return repo.get_datasource(ds["datasource_id"], with_secret=True)


def describe_table(ds_row: dict, table: str, sample_rows: int = 5, profile_rows: int = 2000,
                   *, dataset: dict | None = None, allowed: set[str] | None = None) -> dict:
    conn = get_connector(ds_row)
    out: dict = {"datasource": ds_row["name"], "datasource_type": ds_row["type"], "table": table}
    try:
        out["columns"] = conn.columns(table)
    except ConnectorError as e:
        out["columns"] = []
        out["columns_error"] = str(e)
    # 场景画像的行数、样本、最值必须来自同一取值范围，不能再回查未经筛选的原表。
    item = next((t for t in dataset["tables"] if t["table_name"] == table), None) if dataset else None
    rules = item.get("row_filters") if item else []
    source = table_sql(ds_row["type"], table) if rules else table

    def query(sql: str, n: int) -> dict:
        return query_in_scenario(ds_row, dataset, sql, n, conn, allowed) if rules else conn.query(sql, n)

    n = max(sample_rows, profile_rows)
    if rules:
        out["row_filters"] = rules
        out["row_count"] = query(f"SELECT COUNT(*) FROM {source}", 1)["rows"][0][0]
        sample = query(f"SELECT * FROM {source}", n)
    else:
        out["row_count"] = conn.count(table)
        sample = conn.sample(table, n)
    out["sample_columns"] = sample["columns"]
    out["sample_rows"] = sample["rows"][:sample_rows]
    out["profile"] = profile(sample["columns"], sample["rows"])
    out["profile_basis"] = f"基于前 {len(sample['rows'])} 行样本"
    # 样本只覆盖前若干行：对日期/数值列补充全表最值，避免误判时间范围
    span_cols = [p["column"] for p in out["profile"] if p["kind"] in ("datetime", "number")][:30]
    if span_cols and (rules or re.fullmatch(r"[\w.$#]+", table)):
        exprs = ", ".join(f"MIN({c}) AS mn_{i}, MAX({c}) AS mx_{i}" for i, c in enumerate(span_cols))
        try:
            row = query(f"SELECT {exprs} FROM {source}", 1)["rows"][0]
            for i, c in enumerate(span_cols):
                p = next(p for p in out["profile"] if p["column"] == c)
                p["full_min"], p["full_max"] = row[2 * i], row[2 * i + 1]
            out["profile_basis"] += "；日期/数值列另附取数范围内的最值 full_min/full_max"
        except (ConnectorError, IndexError):
            pass
    comments = {c["name"]: c.get("comment") for c in out["columns"]}
    for p in out["profile"]:
        if comments.get(p["column"]):
            p["comment"] = comments[p["column"]]
    return out


def run_query(ds_row: dict, sql: str, limit: int = 200,
              *, dataset: dict | None = None, allowed: set[str] | None = None) -> dict:
    limit = max(1, min(int(limit or 200), 5000))
    conn = get_connector(ds_row)
    res = query_in_scenario(ds_row, dataset, sql, limit, conn, allowed) if dataset else conn.query(sql, limit)
    return {"datasource": ds_row["name"], "dialect": ds_row["type"], **res, "row_count": len(res["rows"])}


def render_template_to_report(tpl: dict, name: str | None = None, guard=None, created_by: str | None = None) -> dict:
    """渲染并存储报告。样式取模板创建人本人的业务场景规则；报告归属 created_by（默认同模板创建人）。"""
    # 业务 Markdown 是 agent 的开发依据，只有结构化模板可以由服务端执行。
    if tpl.get("template_type") == "business":
        raise repo.Invalid("请让 agent 依据该业务模板编写完整 HTML，再使用 save_report 保存报告")
    html, meta = render(tpl["markdown"], dataset_id=tpl.get("dataset_id"), inline_echarts=True,
                        guard=guard, owner=tpl.get("created_by"))
    rep = repo.save_report(name or tpl["name"], html, tpl["id"], tpl["version"], meta,
                           created_by=created_by or tpl.get("created_by"),
                           source={**tpl, "source_owner": tpl.get("created_by")})
    rep["warnings"] = meta.get("warnings", [])
    return rep


def revise_report(report_id: int, principal: Principal, name: str, *, base_version: int | None = None,
                  html: str | None = None, markdown: str | None = None, change_note: str = "",
                  expected_version: int | None = None, guard=None) -> dict:
    """在已确认的快照上编辑正文，或重新执行该版本的 Markdown 查询。"""
    if (html is None) == (markdown is None):
        raise repo.Invalid("请提供 HTML 或 Markdown 中的一种")
    if markdown is None:
        return repo.update_report(report_id, principal, name, html, base_version,
                                  change_note=change_note, expected_version=expected_version)
    base = repo.get_report(report_id, principal, with_html=False, version=base_version, with_source=True)
    if base["access_level"] == "view":
        raise repo.PermissionDenied("无权编辑该报告")
    if not base["source_available"]:
        raise repo.Invalid("当前版本没有可编辑的 Markdown 源码，请修改该版本的 HTML")
    # 渲染沿用基准版本的业务场景，且每条查询都检查当前操作者的数据权限。
    html, meta = render(markdown, dataset_id=base["dataset_id"], inline_echarts=True,
                        guard=guard, owner=base["source_owner"])
    source = {**base, "markdown": markdown}
    return repo.update_report(base["id"], principal, name, html, base["revision"],
                              change_note=change_note, expected_version=expected_version, rendered=(meta, source))


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
