"""API 与 MCP 共用的业务操作。"""
from __future__ import annotations

import re

from . import repo
from .config import settings
from .connectors import ConnectorError, get_connector, profile
from .renderer import render


def datasource_for_dataset(dataset_id: int) -> dict:
    ds = repo.get_dataset(dataset_id)
    if not ds.get("datasource_id"):
        raise repo.Invalid("该数据集未关联数据源")
    return repo.get_datasource(ds["datasource_id"], with_secret=True)


def describe_table(ds_row: dict, table: str, sample_rows: int = 5, profile_rows: int = 2000) -> dict:
    conn = get_connector(ds_row)
    out: dict = {"datasource": ds_row["name"], "datasource_type": ds_row["type"], "table": table}
    try:
        out["columns"] = conn.columns(table)
    except ConnectorError as e:
        out["columns"] = []
        out["columns_error"] = str(e)
    out["row_count"] = conn.count(table)
    sample = conn.sample(table, max(sample_rows, profile_rows))
    out["sample_columns"] = sample["columns"]
    out["sample_rows"] = sample["rows"][:sample_rows]
    out["profile"] = profile(sample["columns"], sample["rows"])
    out["profile_basis"] = f"基于前 {len(sample['rows'])} 行样本"
    # 样本只覆盖前若干行：对日期/数值列补充全表最值，避免误判时间范围
    span_cols = [p["column"] for p in out["profile"] if p["kind"] in ("datetime", "number")][:30]
    if span_cols and re.fullmatch(r"[\w.$#]+", table):
        exprs = ", ".join(f"MIN({c}) AS mn_{i}, MAX({c}) AS mx_{i}" for i, c in enumerate(span_cols))
        try:
            row = conn.query(f"SELECT {exprs} FROM {table}", 1)["rows"][0]
            for i, c in enumerate(span_cols):
                p = next(p for p in out["profile"] if p["column"] == c)
                p["full_min"], p["full_max"] = row[2 * i], row[2 * i + 1]
            out["profile_basis"] += "；日期/数值列另附全表最值 full_min/full_max"
        except (ConnectorError, IndexError):
            pass
    comments = {c["name"]: c.get("comment") for c in out["columns"]}
    for p in out["profile"]:
        if comments.get(p["column"]):
            p["comment"] = comments[p["column"]]
    return out


def run_query(ds_row: dict, sql: str, limit: int = 200) -> dict:
    limit = max(1, min(int(limit or 200), 5000))
    res = get_connector(ds_row).query(sql, limit)
    return {"datasource": ds_row["name"], "dialect": ds_row["type"], **res, "row_count": len(res["rows"])}


def render_template_to_report(tpl_id: int, name: str | None = None, guard=None, created_by: str | None = None) -> dict:
    """渲染并存储报告。样式取模板创建人本人的业务场景规则；报告归属 created_by（默认同模板创建人）。"""
    tpl = repo.get_template(tpl_id)
    html, meta = render(tpl["markdown"], dataset_id=tpl.get("dataset_id"), guard=guard, owner=tpl.get("created_by"))
    rep = repo.save_report(name or tpl["name"], html, tpl_id, tpl["version"], meta,
                           created_by=created_by or tpl.get("created_by"))
    rep["warnings"] = meta.get("warnings", [])
    return rep


def mcp_endpoint() -> str:
    return settings.mcp_public_url
