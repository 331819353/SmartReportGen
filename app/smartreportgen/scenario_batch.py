"""业务场景批量配置与 Excel（.xlsx）导入/导出引擎。"""
from __future__ import annotations

import datetime as dt
import io
import json
import math
import re
import zipfile
from typing import Any, Literal

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from psycopg.errors import UniqueViolation
from pydantic import ValidationError

from . import db, repo, report_queries, services
from .auth import Principal
from .connectors import ConnectorError
from .permission import source_scene_tables
from .scenario_filters import table_filters

MAX_BATCH_SIZE = 200
MAX_EXCEL_BYTES = 5 * 1024 * 1024
MAX_UNCOMPRESSED_ZIP_BYTES = 30 * 1024 * 1024

_INT_TYPE_RE = re.compile(
    r"\b(?:TINYINT|SMALLINT|MEDIUMINT|INT|INTEGER|BIGINT|HUGEINT|SERIAL|BIGSERIAL|SMALLSERIAL|INT2|INT4|INT8)\b"
)
_FLOAT_TYPE_RE = re.compile(r"\b(?:NUMERIC|DECIMAL|FLOAT|DOUBLE|REAL|NUMBER|BINARY_FLOAT|BINARY_DOUBLE|DEC)\b")
_BOOL_TYPE_RE = re.compile(r"\bBOOL(?:EAN)?\b")
_INT_LITERAL_RE = re.compile(r"^[+-]?\d+$")

_HEADER_STRIP_RE = re.compile(r"[（(][^）)]*[）)]\s*$")
_HEADER_TAIL_RE = re.compile(r"[\s*＊]+$")

_SCENE_HEADERS = ["业务场景*", "数据源*", "业务描述*", "分析思路", "标签", "启用", "授权角色", "授权工号"]
_TABLE_HEADERS = ["业务场景*", "表名*", "表描述", "行过滤条件"]
_METRIC_HEADERS = [
    "业务场景*", "指标标识*", "所属表*", "公式类型*", "主字段/分子*", "分母字段",
    "允许分组维度", "允许过滤维度", "比例尺度", "说明", "高级条件JSON",
]

_SCENE_ALIASES = {
    "业务场景": "business_scenario", "场景名称": "business_scenario", "business_scenario": "business_scenario",
    "数据源": "datasource", "数据源名称": "datasource", "数据源id": "datasource",
    "datasource": "datasource", "datasource_name": "datasource", "datasource_id": "datasource",
    "业务描述": "business_description", "描述": "business_description", "business_description": "business_description",
    "分析思路": "analysis_approach", "analysis_approach": "analysis_approach",
    "标签": "tags", "tags": "tags",
    "启用": "enabled", "状态": "enabled", "enabled": "enabled",
    "授权角色": "grant_roles", "grant_roles": "grant_roles",
    "授权工号": "grant_employees", "grant_employees": "grant_employees",
}
_TABLE_ALIASES = {
    "业务场景": "business_scenario", "场景名称": "business_scenario", "business_scenario": "business_scenario",
    "表名": "table_name", "数据表": "table_name", "table_name": "table_name",
    "表描述": "table_description", "描述": "table_description", "table_description": "table_description",
    "行过滤条件": "row_filters", "行过滤": "row_filters", "过滤条件": "row_filters",
    "取值范围": "row_filters", "row_filters": "row_filters",
}
_METRIC_ALIASES = {
    "业务场景": "business_scenario", "场景名称": "business_scenario", "business_scenario": "business_scenario",
    "指标标识": "name", "指标名": "name", "指标名称": "name", "metric": "name", "name": "name",
    "所属表": "table", "表名": "table", "table": "table",
    "公式类型": "op", "聚合方式": "op", "op": "op",
    "主字段/分子": "field", "主字段": "field", "分子字段": "field", "度量字段": "field", "field": "field",
    "分母字段": "denominator", "分母": "denominator", "denominator": "denominator",
    "允许分组维度": "dimensions", "分组维度": "dimensions", "dimensions": "dimensions",
    "允许过滤维度": "filter_dimensions", "过滤维度": "filter_dimensions", "filter_dimensions": "filter_dimensions",
    "比例尺度": "scale", "尺度": "scale", "scale": "scale",
    "说明": "description", "指标说明": "description", "描述": "description", "description": "description",
    "高级条件json": "extra_json", "高级条件": "extra_json", "extra_json": "extra_json",
}
_METRIC_EXTRA_KEYS = ("when", "denominator_when", "when_parameters", "denominator_parameters")


def _cell_str(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, int):
        return str(val)
    if isinstance(val, float):
        if math.isfinite(val) and val.is_integer():
            return str(int(val))
        return str(val)
    if isinstance(val, (dt.datetime, dt.date)):
        return val.isoformat()
    return str(val).strip()


def _cell_bool(val: Any, *, default: bool | None = None) -> bool | None:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)) and val in (0, 1):
        return bool(int(val))
    text = _cell_str(val).lower()
    if not text:
        return default
    if text in ("是", "y", "yes", "true", "1", "启用", "开"):
        return True
    if text in ("否", "n", "no", "false", "0", "停用", "关"):
        return False
    raise repo.Invalid(f"布尔值「{val}」无效，请填写 是/否 或 true/false")


def _split_list_cell(val: Any) -> list[str]:
    text = _cell_str(val)
    if not text:
        return []
    return [item.strip() for item in re.split(r"[,，、]+", text) if item.strip()]


def _split_quoted(text: str, seps: tuple[str, ...]) -> list[str]:
    """按分隔符拆分文本，忽略成对单/双引号内部的分隔符。"""
    parts: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if quote is not None:
            if ch == "\\" and i + 1 < n and text[i + 1] in (quote, "\\"):
                buf.append(ch)
                buf.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                if i + 1 < n and text[i + 1] == quote:
                    buf.append(ch)
                    buf.append(ch)
                    i += 2
                    continue
                quote = None
            buf.append(ch)
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch in seps:
            parts.append("".join(buf))
            buf.clear()
            i += 1
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def _unquote_token(tok: str) -> tuple[Any, bool]:
    """解析单个取值令牌，返回 (值, 是否为未加引号的简明字面量)。"""
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in ("'", '"'):
        q = tok[0]
        inner = tok[1:-1].replace(f"\\{q}", q).replace(q * 2, q)
        return inner, False
    if tok.lower() == "null":
        return None, False
    return tok, True


def _parse_row_filters_internal(raw: Any) -> tuple[list[dict[str, Any]], list[list[bool]]]:
    """解析行过滤单元格，同时保留各值是否可按列类型自动转型的标记。"""
    if raw is None:
        return [], []
    if isinstance(raw, list):
        rules = [dict(r) if isinstance(r, dict) else r for r in raw]
        masks = [[False] * len(r.get("values", [])) if isinstance(r, dict) and isinstance(r.get("values"), list) else []
                 for r in rules]
        return rules, masks
    if not isinstance(raw, str):
        raise repo.Invalid("行过滤条件须为对象数组或规则文本")
    text = raw.strip()
    if not text:
        return [], []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except ValueError as exc:
            raise repo.Invalid(f"行过滤 JSON 格式错误：{exc}") from exc
        if not isinstance(parsed, list):
            raise repo.Invalid("行过滤 JSON 须为数组")
        rules = [dict(r) if isinstance(r, dict) else r for r in parsed]
        masks = [[False] * len(r.get("values", [])) if isinstance(r, dict) and isinstance(r.get("values"), list) else []
                 for r in rules]
        return rules, masks

    merged: dict[str, dict[str, Any]] = {}
    for clause in _split_quoted(text, (";", "；", "\n")):
        clause = clause.strip()
        if not clause:
            continue
        sep_idx = -1
        quote: str | None = None
        for idx, ch in enumerate(clause):
            if ch in ("'", '"'):
                quote = None if quote == ch else (ch if quote is None else quote)
            elif quote is None and ch in ("=", ":", "："):
                sep_idx = idx
                break
        if sep_idx <= 0:
            raise repo.Invalid(f"行过滤条件格式无效：「{clause}」，请使用 列名=值1,值2 或 JSON 数组")
        col = clause[:sep_idx].strip()
        val_part = clause[sep_idx + 1:].strip()
        if not col or not val_part:
            raise repo.Invalid(f"行过滤条件格式无效：「{clause}」")
        tokens = [t.strip() for t in _split_quoted(val_part, (",", "，", "、")) if t.strip()]
        if not tokens:
            raise repo.Invalid(f"行过滤字段「{col}」缺少保留值")
        key = col.casefold()
        bucket = merged.setdefault(key, {"column": col, "values": [], "shorthand": [], "seen": set()})
        for tok in tokens:
            val, is_shorthand = _unquote_token(tok)
            dedup_key = (type(val), val)
            if dedup_key in bucket["seen"]:
                continue
            bucket["seen"].add(dedup_key)
            bucket["values"].append(val)
            bucket["shorthand"].append(is_shorthand)
    rules = [{"column": b["column"], "values": b["values"]} for b in merged.values()]
    masks = [b["shorthand"] for b in merged.values()]
    return rules, masks


def parse_row_filters_cell(raw: Any) -> list[dict[str, Any]]:
    """解析行过滤单元格（支持 JSON 数组与 `列=值1,值2; 列2=NULL` 简明表达式）。"""
    rules, _ = _parse_row_filters_internal(raw)
    return rules


def _coerce_shorthand_value(val: Any, is_shorthand: bool, col_type: str) -> Any:
    if not is_shorthand or not isinstance(val, str):
        return val
    dtype = (col_type or "").upper()
    if _BOOL_TYPE_RE.search(dtype):
        low = val.lower()
        if low in ("true", "1"):
            return True
        if low in ("false", "0"):
            return False
        return val
    if _INT_TYPE_RE.search(dtype):
        if _INT_LITERAL_RE.fullmatch(val):
            return int(val)
        return val
    if _FLOAT_TYPE_RE.search(dtype):
        if _INT_LITERAL_RE.fullmatch(val):
            return int(val)
        try:
            num = float(val)
            return num if math.isfinite(num) else val
        except ValueError:
            return val
    return val


def _apply_shorthand_types(
    rules: list[dict[str, Any]], masks: list[list[bool]], columns: list[dict]
) -> list[dict[str, Any]]:
    col_types = {(c.get("source_name") or c["name"]).casefold(): c.get("type", "") for c in columns}
    out: list[dict[str, Any]] = []
    for idx, rule in enumerate(rules):
        if not isinstance(rule, dict):
            out.append(rule)
            continue
        col = rule.get("column")
        values = rule.get("values")
        rule_mask = masks[idx] if idx < len(masks) else []
        if not isinstance(col, str) or not isinstance(values, list) or not any(rule_mask):
            out.append({"column": col, "values": values} if "column" in rule or "values" in rule else dict(rule))
            continue
        dtype = col_types.get(col.casefold(), "")
        coerced: list[Any] = []
        seen: set[tuple[type, Any]] = set()
        for v_idx, v in enumerate(values):
            flag = rule_mask[v_idx] if v_idx < len(rule_mask) else False
            nv = _coerce_shorthand_value(v, flag, dtype)
            dedup_key = (type(nv), nv)
            if dedup_key not in seen:
                seen.add(dedup_key)
                coerced.append(nv)
        out.append({"column": col, "values": coerced})
    return out


def _normalize_header(text: Any) -> str:
    raw = _cell_str(text)
    if not raw:
        return ""
    stripped = _HEADER_STRIP_RE.sub("", raw).strip()
    stripped = _HEADER_TAIL_RE.sub("", stripped).strip()
    return stripped.lower()


def _header_map(row: tuple[Any, ...], aliases: dict[str, str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, cell in enumerate(row):
        key = aliases.get(_normalize_header(cell))
        if key and key not in mapping:
            mapping[key] = idx
    return mapping


def _get_col(row: tuple[Any, ...], col_map: dict[str, int], key: str) -> Any:
    idx = col_map.get(key)
    if idx is None or idx >= len(row):
        return None
    return row[idx]


def _is_empty_row(row: tuple[Any, ...]) -> bool:
    return all(_cell_str(cell) == "" for cell in row)


def _format_validation_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors()[:3]:
        loc = ".".join(str(x) for x in err.get("loc", ()))
        msg = err.get("msg", "格式无效")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "；".join(parts) or str(exc)


def _resolve_datasource(
    item: dict[str, Any],
    ds_by_id: dict[int, dict],
    ds_by_name: dict[str, dict],
    ds_by_lower: dict[str, list[dict]],
    principal: Principal,
) -> dict:
    raw_id = item.get("datasource_id")
    raw_name = item.get("datasource_name") if item.get("datasource_name") is not None else item.get("datasource")
    by_id: dict | None = None
    by_name: dict | None = None

    if raw_id is not None and raw_id != "":
        if type(raw_id) is not int or raw_id < 1:
            raise repo.Invalid("datasource_id 须为正整数")
        by_id = ds_by_id.get(raw_id)
        if by_id is None:
            raise repo.Invalid(
                f"数据源不存在（id={raw_id}）" if principal.is_admin else "数据源不存在或未向当前账号开放建场景权限"
            )

    if raw_name is not None and str(raw_name).strip():
        name_text = str(raw_name).strip()
        by_name = ds_by_name.get(name_text)
        if by_name is None:
            matches = ds_by_lower.get(name_text.casefold(), [])
            if len(matches) == 1:
                by_name = matches[0]
            elif len(matches) > 1:
                raise repo.Invalid(f"数据源名称「{name_text}」不区分大小写存在多个匹配项，请使用精确名称")
        if by_name is None and name_text.isdigit():
            by_name = ds_by_id.get(int(name_text))
        if by_name is None:
            raise repo.Invalid(
                f"数据源不存在：{name_text}" if principal.is_admin else "数据源不存在或未向当前账号开放建场景权限"
            )

    if by_id is not None and by_name is not None and by_id["id"] != by_name["id"]:
        raise repo.Invalid("datasource_id 与 datasource_name 指向不同的数据源")

    source = by_id or by_name
    if source is None:
        raise repo.Invalid("请指定数据源（datasource_id 或 datasource_name）")
    if not principal.is_admin and (not source.get("enabled", True) or not source_scene_tables(source, principal)):
        raise repo.Invalid("数据源不存在或未向当前账号开放建场景权限")
    return source


def _normalize_grants(
    item: dict[str, Any],
    principal: Principal,
    registered_employees: set[str],
    table_names: set[str],
) -> list[dict[str, Any]]:
    raw_roles = item.get("grant_roles")
    raw_emps = item.get("grant_employees")
    raw_grants = item.get("grants")
    entries: list[tuple[str, str, list[str]]] = []

    if raw_roles is not None:
        if isinstance(raw_roles, str):
            roles = _split_list_cell(raw_roles)
        elif isinstance(raw_roles, list) and all(isinstance(r, str) for r in raw_roles):
            roles = [r.strip() for r in raw_roles if r.strip()]
        else:
            raise repo.Invalid("grant_roles 须为字符串数组")
        for r in roles:
            entries.append(("role", r, ["*"]))

    if raw_emps is not None:
        if isinstance(raw_emps, str):
            emps = _split_list_cell(raw_emps)
        elif isinstance(raw_emps, list) and all(isinstance(e, str) for e in raw_emps):
            emps = [e.strip() for e in raw_emps if e.strip()]
        else:
            raise repo.Invalid("grant_employees 须为字符串数组")
        for e in emps:
            entries.append(("employee", e, ["*"]))

    if raw_grants is not None:
        if not isinstance(raw_grants, list) or any(not isinstance(g, dict) for g in raw_grants):
            raise repo.Invalid("grants 须为对象数组")
        for g in raw_grants:
            st = g.get("subject_type")
            subj = (g.get("subject") or "").strip() if isinstance(g.get("subject"), str) else ""
            if st not in ("employee", "role") or not subj:
                raise repo.Invalid("grants 项须指定有效的 subject_type（employee/role）与 subject")
            tables = g.get("tables", ["*"])
            if tables is None:
                tables = ["*"]
            if not isinstance(tables, list) or not tables or not all(isinstance(t, str) and t.strip() for t in tables):
                raise repo.Invalid("请指定要授权的数据表；全部表请显式提交 [\"*\"]")
            chosen = ["*"] if "*" in tables else list(dict.fromkeys(t.strip() for t in tables))
            entries.append((st, subj, chosen))

    if not entries:
        return []
    if not principal.is_admin:
        raise repo.Invalid("仅管理员可配置场景的角色或员工授权；个人场景创建后本人自动拥有访问权限")

    merged_by_subject: dict[tuple[str, str], list[str]] = {}
    for st, subj, tables in entries:
        if st == "employee" and subj not in registered_employees:
            raise repo.Invalid(f"工号 {subj} 未登记，请先在员工中添加")
        if tables != ["*"]:
            bad = [t for t in tables if t not in table_names]
            if bad:
                raise repo.Invalid(f"授权表不在场景表清单中：{bad}")
        existing = merged_by_subject.setdefault((st, subj), [])
        if "*" in tables or "*" in existing:
            merged_by_subject[(st, subj)] = ["*"]
        else:
            for t in tables:
                if t not in existing:
                    existing.append(t)

    return [
        {"subject_type": st, "subject": subj, "tables": tbls}
        for (st, subj), tbls in merged_by_subject.items()
    ]


def _prepare_batch_tables(
    raw_tables: Any,
    ds_id: int,
    current: dict | None,
    shorthand_masks: list[list[list[bool]]] | None,
    get_columns,
) -> list[dict[str, Any]]:
    if not isinstance(raw_tables, list) or any(not isinstance(t, dict) for t in raw_tables):
        raise repo.Invalid("数据表须为对象数组")
    same_source = current is not None and current["datasource_id"] == ds_id
    old_rules = {t["table_name"]: t["row_filters"] for t in current.get("tables", [])} if same_source else {}
    prepared: list[dict[str, Any]] = []
    for idx, t in enumerate(raw_tables):
        name = t.get("table_name")
        if not isinstance(name, str) or not name.strip():
            raise repo.Invalid("数据表名不能为空")
        name = name.strip()
        desc = t.get("table_description")
        if desc is not None and not isinstance(desc, str):
            raise repo.Invalid(f"{name}：表描述须为文本")
        raw_rf = t.get("row_filters", [])
        if isinstance(raw_rf, str):
            rules, masks = _parse_row_filters_internal(raw_rf)
        else:
            rules, masks = _parse_row_filters_internal(raw_rf)
            if shorthand_masks is not None and idx < len(shorthand_masks):
                masks = shorthand_masks[idx]
        if not isinstance(rules, list) or len(rules) > 20:
            raise repo.Invalid(f"{name}：筛选条件必须是数组，最多 20 个字段")
        has_shorthand = any(any(m) for m in masks)
        changed = has_shorthand or (
            bool(rules) and json.dumps(rules, sort_keys=True) != json.dumps(old_rules.get(name), sort_keys=True)
        )
        if changed:
            try:
                columns = get_columns(ds_id, name)
                if has_shorthand:
                    rules = _apply_shorthand_types(rules, masks, columns)
                rules = table_filters(rules, name, columns)
            except ConnectorError as exc:
                raise repo.Invalid(f"无法校验数据表 {name} 的筛选条件：{exc}") from exc
        prepared.append({
            "table_name": name,
            "table_description": (desc or "").strip() if isinstance(desc, str) else "",
            "row_filters": rules,
        })
    return prepared


def batch_save_scenarios(
    items: list[dict[str, Any]],
    principal: Principal,
    *,
    mode: Literal["upsert", "create_only"] = "upsert",
    dry_run: bool = False,
) -> dict[str, Any]:
    """批量校验并保存业务场景（支持 upsert / create_only 及 dry_run 预检，单批事务提交与批量向量化）。"""
    if mode not in ("upsert", "create_only"):
        raise repo.Invalid("mode 必须是 upsert 或 create_only")
    if not isinstance(items, list) or not items:
        raise repo.Invalid("请提供至少一个业务场景配置")
    if len(items) > MAX_BATCH_SIZE:
        raise repo.Invalid(f"单批最多支持导入 {MAX_BATCH_SIZE} 个业务场景")

    datasources = repo.list_datasources()
    ds_by_id = {d["id"]: d for d in datasources}
    ds_by_name = {d["name"]: d for d in datasources}
    ds_by_lower: dict[str, list[dict]] = {}
    for d in datasources:
        ds_by_lower.setdefault(d["name"].casefold(), []).append(d)

    existing_rows = db.fetchall(
        "SELECT id, business_scenario, business_description, analysis_approach, datasource_id, "
        "tags, enabled, semantic_metrics, owner_emp_no FROM dataset.dataset"
    )
    existing_by_id = {r["id"]: r for r in existing_rows}
    existing_by_name = {r["business_scenario"]: r for r in existing_rows}

    need_existing_tables = any(
        isinstance(it, dict) and (
            it.get("dataset_id") is not None
            or (isinstance(it.get("business_scenario"), str) and it["business_scenario"].strip() in existing_by_name)
        )
        for it in items
    )
    existing_tables_by_ds: dict[int, list[dict]] = {}
    if need_existing_tables and existing_rows:
        for trow in db.fetchall(
            "SELECT id, dataset_id, table_name, table_description, sort, row_filters "
            "FROM dataset.dataset_table ORDER BY dataset_id, sort, id"
        ):
            existing_tables_by_ds.setdefault(trow["dataset_id"], []).append(trow)

    has_employee_grants = any(
        isinstance(it, dict) and (
            it.get("grant_employees")
            or any(isinstance(g, dict) and g.get("subject_type") == "employee" for g in (it.get("grants") or []))
        )
        for it in items
    )
    registered_employees = (
        {r["emp_no"] for r in db.fetchall("SELECT emp_no FROM permission.employee")}
        if has_employee_grants
        else set()
    )

    connector_cache: dict[int, Any] = {}
    columns_cache: dict[tuple[int, str], list[dict]] = {}

    def _get_columns(ds_id: int, table_name: str) -> list[dict]:
        key = (ds_id, table_name)
        if key not in columns_cache:
            if ds_id not in connector_cache:
                connector_cache[ds_id] = services.get_connector(repo.get_datasource(ds_id, with_secret=True))
            columns_cache[key] = connector_cache[ds_id].columns(table_name)
        return columns_cache[key]

    seen_names: set[str] = set()
    seen_ids: set[int] = set()
    prepared_ops: list[dict[str, Any]] = []
    item_results: list[dict[str, Any]] = []
    flat_errors: list[dict[str, Any]] = []
    create_count = 0
    update_count = 0
    warning_count = 0

    for idx, raw_item in enumerate(items):
        if not isinstance(raw_item, dict):
            msg = "场景配置项必须是对象"
            row_num = idx + 1
            item_results.append({
                "index": idx, "row": row_num, "action": "error", "dataset_id": None,
                "business_scenario": "", "datasource_id": None, "datasource_name": "",
                "table_count": 0, "metric_count": 0, "grants": [], "warnings": [], "errors": [msg],
            })
            flat_errors.append({"row": row_num, "business_scenario": "", "message": msg})
            continue

        row_num = raw_item.get("_row") or (idx + 1)
        item_errors: list[str] = list(raw_item.get("_parse_errors") or [])
        item_warnings: list[str] = []
        raw_name = raw_item.get("business_scenario")
        sc_name = raw_name.strip() if isinstance(raw_name, str) else ""
        if not sc_name or "\x00" in sc_name:
            item_errors.append("业务场景须为有效文本")
        elif sc_name in seen_names:
            item_errors.append(f"本批次中业务场景名称重复：{sc_name}")
        if sc_name:
            seen_names.add(sc_name)

        target_id = raw_item.get("dataset_id")
        cur: dict | None = None
        action: Literal["create", "update"] = "create"
        if target_id is not None:
            if type(target_id) is not int or target_id < 1:
                item_errors.append("dataset_id 须为正整数")
            else:
                if target_id in seen_ids:
                    item_errors.append(f"本批次中重复更新同一业务场景（dataset_id={target_id}）")
                seen_ids.add(target_id)
                cur = existing_by_id.get(target_id)
                if cur is None:
                    item_errors.append(f"业务场景不存在（dataset_id={target_id}）")
                elif not principal.is_admin and cur["owner_emp_no"] != principal.emp_no:
                    item_errors.append("只能管理本人创建的业务场景")
                else:
                    action = "update"
                    if sc_name and sc_name != cur["business_scenario"]:
                        conflict = existing_by_name.get(sc_name)
                        if conflict is not None and conflict["id"] != cur["id"]:
                            item_errors.append(f"业务场景名称「{sc_name}」已被其他场景占用")
        elif sc_name and sc_name in existing_by_name:
            cur = existing_by_name[sc_name]
            if mode == "create_only":
                item_errors.append(f"业务场景「{sc_name}」已存在（当前模式为仅新增）")
            elif not principal.is_admin and cur["owner_emp_no"] != principal.emp_no:
                item_errors.append(f"业务场景名称「{sc_name}」已被其他场景占用，无权覆盖，请更换名称")
            else:
                action = "update"
                if cur["id"] in seen_ids:
                    item_errors.append(f"本批次中重复更新同一业务场景（dataset_id={cur['id']}）")
                seen_ids.add(cur["id"])

        cur_tables: list[dict] = []
        if cur is not None:
            cur_tables = [
                {
                    "table_name": t["table_name"],
                    "table_description": t.get("table_description") or "",
                    "row_filters": t.get("row_filters") or [],
                }
                for t in existing_tables_by_ds.get(cur["id"], [])
            ]

        source: dict | None = None
        if raw_item.get("datasource_id") is not None or raw_item.get("datasource_name") is not None or raw_item.get("datasource") is not None:
            try:
                source = _resolve_datasource(raw_item, ds_by_id, ds_by_name, ds_by_lower, principal)
            except repo.Invalid as exc:
                item_errors.append(str(exc))
        elif cur is not None and cur.get("datasource_id") in ds_by_id:
            source = ds_by_id[cur["datasource_id"]]
        else:
            item_errors.append("请指定数据源（datasource_id 或 datasource_name）")

        if cur is not None:
            desc = raw_item["business_description"] if raw_item.get("business_description") is not None else cur["business_description"]
            approach = raw_item["analysis_approach"] if raw_item.get("analysis_approach") is not None else cur["analysis_approach"]
            tags = raw_item["tags"] if raw_item.get("tags") is not None else list(cur.get("tags") or [])
            enabled = raw_item["enabled"] if raw_item.get("enabled") is not None else cur.get("enabled", True)
            raw_metrics = raw_item["semantic_metrics"] if raw_item.get("semantic_metrics") is not None else dict(cur.get("semantic_metrics") or {})
            tables_replaced = "tables" in raw_item and raw_item["tables"] is not None
            raw_tables = raw_item["tables"] if tables_replaced else cur_tables
        else:
            desc = raw_item.get("business_description")
            approach = raw_item.get("analysis_approach") if raw_item.get("analysis_approach") is not None else ""
            tags = raw_item.get("tags") if raw_item.get("tags") is not None else []
            enabled = raw_item.get("enabled") if raw_item.get("enabled") is not None else True
            raw_metrics = raw_item.get("semantic_metrics") if raw_item.get("semantic_metrics") is not None else {}
            tables_replaced = True
            raw_tables = raw_item.get("tables") if raw_item.get("tables") is not None else []

        prepared_tables: list[dict[str, Any]] = []
        validated_metrics: dict[str, Any] = {}
        normalized_grants: list[dict[str, Any]] = []

        if source is not None:
            candidate = {
                "business_scenario": sc_name,
                "business_description": desc,
                "analysis_approach": approach,
                "datasource_id": source["id"],
                "tags": tags,
                "enabled": enabled,
                "tables": raw_tables,
            }
            try:
                repo._validate_dataset(candidate)
            except repo.Invalid as exc:
                item_errors.append(str(exc))

            # 个人场景（普通员工创建，或原有个人场景）须校验表在数据源开放清单中
            personal = (not principal.is_admin) or (cur is not None and cur["owner_emp_no"] is not None)
            if personal and isinstance(raw_tables, list) and all(
                isinstance(t, dict) and isinstance(t.get("table_name"), str) for t in raw_tables
            ):
                catalog = source_scene_tables(source, principal if not principal.is_admin else None)
                chosen_names = {t["table_name"].strip() for t in raw_tables if t["table_name"].strip()}
                if not chosen_names and raw_tables == []:
                    item_errors.append("请选择至少一张有权限的数据表或视图")
                elif chosen_names and not chosen_names.issubset(catalog):
                    item_errors.append("个人场景只能选择数据源已授权开放的表或视图")

            try:
                shorthand_masks = raw_item.get("_shorthand_masks") if tables_replaced else None
                if tables_replaced or (cur is not None and cur["datasource_id"] != source["id"]):
                    prepared_tables = _prepare_batch_tables(
                        raw_tables, source["id"], cur, shorthand_masks, _get_columns
                    )
                    tables_replaced = True
                else:
                    prepared_tables = cur_tables
            except repo.Invalid as exc:
                item_errors.append(str(exc))
                prepared_tables = []

        # 即使上面已有报错，仍尽量校验官方指标和授权，以便一次反馈全部问题
        tables_for_check = prepared_tables if prepared_tables else (
            [t for t in raw_tables if isinstance(t, dict) and isinstance(t.get("table_name"), str)]
            if isinstance(raw_tables, list) else []
        )
        try:
            validated_metrics = report_queries.validate_metrics(raw_metrics, tables_for_check)
        except ValidationError as exc:
            item_errors.append(_format_validation_error(exc))
        except ValueError as exc:
            item_errors.append(str(exc))

        try:
            normalized_grants = _normalize_grants(
                raw_item, principal, registered_employees,
                {t["table_name"].strip() for t in tables_for_check if t.get("table_name")},
            )
        except repo.Invalid as exc:
            item_errors.append(str(exc))

        if not item_errors and not prepared_tables and principal.is_admin:
            item_warnings.append("未配置关联数据表，保存后需补充数据表才能用于取数")

        if item_errors:
            for msg in item_errors:
                flat_errors.append({"row": row_num, "business_scenario": sc_name, "message": msg})
        else:
            if action == "create":
                create_count += 1
            else:
                update_count += 1
            prepared_ops.append({
                "index": idx,
                "action": action,
                "cur": cur,
                "business_scenario": sc_name,
                "business_description": desc.strip(),
                "analysis_approach": (approach or "").strip(),
                "datasource_id": source["id"],
                "tags": [t.strip() for t in (tags or []) if t.strip()],
                "enabled": enabled,
                "semantic_metrics": validated_metrics,
                "tables": prepared_tables,
                "tables_replaced": tables_replaced,
                "grants": normalized_grants,
            })

        warning_count += len(item_warnings)
        item_results.append({
            "index": idx,
            "row": row_num,
            "action": "error" if item_errors else action,
            "dataset_id": cur["id"] if cur is not None else None,
            "business_scenario": sc_name,
            "datasource_id": source["id"] if source is not None else None,
            "datasource_name": source["name"] if source is not None else "",
            "table_count": len(prepared_tables) if not item_errors else len(tables_for_check),
            "metric_count": len(validated_metrics),
            "grants": normalized_grants,
            "warnings": item_warnings,
            "errors": item_errors,
        })

    grants_added = 0
    if not dry_run and not flat_errors:
        saved_ids: list[int] = []
        owner = principal.emp_no if not principal.is_admin else None
        try:
            with db.conn() as c, c.transaction():
                for op in prepared_ops:
                    if op["action"] == "create":
                        row = c.execute(
                            "INSERT INTO dataset.dataset(business_scenario, business_description, analysis_approach, "
                            "datasource_id, tags, enabled, semantic_metrics, owner_emp_no) "
                            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                            (op["business_scenario"], op["business_description"], op["analysis_approach"],
                             op["datasource_id"], op["tags"], op["enabled"], db.jsonb(op["semantic_metrics"]), owner),
                        ).fetchone()
                        ds_id = row["id"]
                        repo._save_tables(c, ds_id, op["tables"])
                    else:
                        cur = op["cur"]
                        ds_id = cur["id"]
                        c.execute(
                            "UPDATE dataset.dataset SET business_scenario=%s, business_description=%s, "
                            "analysis_approach=%s, datasource_id=%s, tags=%s, enabled=%s, semantic_metrics=%s, "
                            "updated_at=now() WHERE id=%s",
                            (op["business_scenario"], op["business_description"], op["analysis_approach"],
                             op["datasource_id"], op["tags"], op["enabled"], db.jsonb(op["semantic_metrics"]), ds_id),
                        )
                        if op["tables_replaced"]:
                            repo._save_tables(c, ds_id, op["tables"])
                        if cur["business_scenario"] != op["business_scenario"]:
                            c.execute(
                                "UPDATE style_rule.style_rule SET business_scenario=%s WHERE business_scenario=%s",
                                (op["business_scenario"], cur["business_scenario"]),
                            )
                    for grant in op["grants"]:
                        inserted = c.execute(
                            "INSERT INTO permission.table_grant(subject_type, subject, dataset_id, table_name) "
                            "SELECT %s,%s,%s,table_name FROM unnest(%s::text[]) AS names(table_name) "
                            "ON CONFLICT DO NOTHING RETURNING id",
                            (grant["subject_type"], grant["subject"], ds_id, grant["tables"]),
                        ).fetchall()
                        grants_added += len(inserted)
                    saved_ids.append(ds_id)
                    item_results[op["index"]]["dataset_id"] = ds_id
        except UniqueViolation as exc:
            raise repo.Invalid("并发写入时出现业务场景名称冲突，请刷新后重试") from exc
        repo.reindex_datasets(saved_ids)

    return {
        "ok": len(flat_errors) == 0,
        "dry_run": dry_run,
        "mode": mode,
        "summary": {
            "total": len(item_results),
            "create_count": create_count,
            "update_count": update_count,
            "error_count": len(flat_errors),
            "warning_count": warning_count,
            "grants_added": grants_added,
        },
        "items": item_results,
        "errors": flat_errors,
    }


def _style_sheet(ws, headers: list[str], widths: list[int]) -> None:
    fill = PatternFill(start_color="DCE6FB", end_color="DCE6FB", fill_type="solid")
    font = Font(bold=True, color="1A2740")
    align = Alignment(vertical="center", wrap_text=True)
    ws.append(headers)
    for col_idx, width in enumerate(widths, start=1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = fill
        cell.font = font
        cell.alignment = align
        ws.column_dimensions[get_column_letter(col_idx)].width = width
    ws.row_dimensions[1].height = 24
    ws.freeze_panes = "A2"


def _workbook_bytes(wb: openpyxl.Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_template_xlsx(principal: Principal) -> bytes:
    """生成业务场景批量导入模板（含 3 张空数据表 + 1 张动态填写说明与可用数据源清单）。"""
    wb = openpyxl.Workbook()
    ws_scene = wb.active
    ws_scene.title = "业务场景"
    _style_sheet(ws_scene, _SCENE_HEADERS, [26, 22, 42, 46, 22, 10, 22, 22])

    ws_table = wb.create_sheet("场景数据表")
    _style_sheet(ws_table, _TABLE_HEADERS, [26, 30, 32, 46])

    ws_metric = wb.create_sheet("官方指标")
    _style_sheet(ws_metric, _METRIC_HEADERS, [26, 22, 26, 18, 20, 20, 24, 24, 14, 30, 36])

    ws_guide = wb.create_sheet("填写说明")
    _style_sheet(ws_guide, ["模块 / 项目", "填写规则与示例"], [28, 92])
    guide_rows = [
        ("工作表关系", "「业务场景」为主表；「场景数据表」和「官方指标」通过「业务场景*」名称关联，同一场景可填多张表和多个指标。"),
        ("导入模式", "支持「同名更新（Upsert）」与「仅新增」。普通员工仅可新增个人场景或覆盖本人创建的场景。"),
        ("分隔符约定", "「标签」「授权角色」「授权工号」「允许分组维度」「允许过滤维度」多值请用英文或中文逗号分隔。"),
        ("启用状态", "填写 是/否（或 true/false），留空时新建默认启用、更新保持原状。"),
        ("行过滤条件（简明写法）", "分号隔开字段、逗号隔开值，NULL 表示空值。例：region=华东,华南,NULL; status=1；需保留前导零可加引号：code=\"001\"。"),
        ("行过滤条件（JSON写法）", "以 [ 开头按 JSON 数组解析，例：[{\"column\":\"region\",\"values\":[\"华东\",\"华南\"]}]。"),
        ("公式类型枚举", "sum、avg、min、max、count_distinct、ratio_of_sums（须填分母）、difference_of_sums（须填分母）、growth_of_sums（须填分母）。"),
        ("比例尺度枚举", "number（数值）、decimal（小数比例）、percent（百分数）。比率和增长率公式固定使用 decimal。"),
        ("允许过滤维度", "留空表示沿用分组维度；填 [] 表示禁止额外过滤维度；多字段用逗号分隔。"),
        ("授权列说明", "仅管理员可填写「授权角色」与「授权工号」（对整场景授权）。普通员工创建的个人场景本人自动拥有访问权限，请留空。"),
    ]
    for k, v in guide_rows:
        ws_guide.append([k, v])

    ws_guide.append(["", ""])
    ws_guide.append(["可用数据源与开放表参考", "以下为当前账号可见的数据源及开放表清单，填写「数据源*」和「表名*」时可直接复制使用："])
    ws_guide.append(["数据源名称（ID）", "类型 / 状态 / 可用表清单"])
    for row in repo.list_datasources():
        if not row.get("enabled", True) and not principal.is_admin:
            continue
        catalog = source_scene_tables(row) if principal.is_admin else source_scene_tables(row, principal)
        if not principal.is_admin and not catalog:
            continue
        status = "启用" if row.get("enabled", True) else "停用"
        if catalog:
            table_notes = []
            for tname, entry in catalog.items():
                desc = entry.get("description") or ""
                rfs = entry.get("row_filters") or []
                rf_txt = "；固定范围：" + "，".join(f"{r['column']}={','.join(map(str, r['values']))}" for r in rfs) if rfs else ""
                table_notes.append(f"{tname}{f'（{desc}）' if desc else ''}{rf_txt}")
            detail = f"{row['type']} | {status} | 开放表：" + "；".join(table_notes)
        else:
            detail = f"{row['type']} | {status} | 管理员可用全部物理表（可在管理台或 MCP list_datasource_tables 查看）"
        ws_guide.append([f"{row['name']} (ID:{row['id']})", detail])

    for row_idx in range(2, ws_guide.max_row + 1):
        for col_idx in (1, 2):
            ws_guide.cell(row=row_idx, column=col_idx).alignment = Alignment(vertical="top", wrap_text=True)

    return _workbook_bytes(wb)


def export_scenarios_xlsx(
    principal: Principal,
    *,
    q: str | None = None,
    ids: list[int] | None = None,
) -> bytes:
    """导出当前账号可管理的业务场景为 .xlsx（格式与导入模板完全一致，支持无损往返）。"""
    rows = repo.list_datasets(q=q)
    id_set = set(ids) if ids is not None else None
    selected = []
    for r in rows:
        if id_set is not None and r["id"] not in id_set:
            continue
        if principal.is_admin or r["owner_emp_no"] == principal.emp_no:
            selected.append(r)

    full_datasets = [repo.get_dataset(r["id"]) for r in selected]
    grants_by_ds: dict[int, dict[str, list[str]]] = {}
    if principal.is_admin and full_datasets:
        ds_ids = [d["id"] for d in full_datasets]
        for g in db.fetchall(
            "SELECT dataset_id, subject_type, subject FROM permission.table_grant "
            "WHERE dataset_id = ANY(%s) AND table_name='*' ORDER BY subject_type, subject",
            (ds_ids,),
        ):
            bucket = grants_by_ds.setdefault(g["dataset_id"], {"role": [], "employee": []})
            bucket[g["subject_type"]].append(g["subject"])

    wb = openpyxl.Workbook()
    ws_scene = wb.active
    ws_scene.title = "业务场景"
    _style_sheet(ws_scene, _SCENE_HEADERS, [26, 22, 42, 46, 22, 10, 22, 22])

    ws_table = wb.create_sheet("场景数据表")
    _style_sheet(ws_table, _TABLE_HEADERS, [26, 30, 32, 46])

    ws_metric = wb.create_sheet("官方指标")
    _style_sheet(ws_metric, _METRIC_HEADERS, [26, 22, 26, 18, 20, 20, 24, 24, 14, 30, 36])

    for ds in full_datasets:
        g = grants_by_ds.get(ds["id"], {"role": [], "employee": []})
        ws_scene.append([
            ds["business_scenario"],
            ds.get("datasource_name") or (str(ds["datasource_id"]) if ds.get("datasource_id") else ""),
            ds.get("business_description") or "",
            ds.get("analysis_approach") or "",
            ", ".join(ds.get("tags") or []),
            "是" if ds.get("enabled", True) else "否",
            ", ".join(g["role"]) if principal.is_admin else "",
            ", ".join(g["employee"]) if principal.is_admin else "",
        ])
        for t in ds.get("tables", []):
            rf = t.get("row_filters") or []
            ws_table.append([
                ds["business_scenario"],
                t["table_name"],
                t.get("table_description") or "",
                json.dumps(rf, ensure_ascii=False, separators=(",", ":")) if rf else "",
            ])
        for m_name, m in (ds.get("semantic_metrics") or {}).items():
            fdims = m.get("filter_dimensions")
            if fdims is None:
                fdims_str = ""
            elif fdims == []:
                fdims_str = "[]"
            else:
                fdims_str = ", ".join(fdims)
            extra = {k: m[k] for k in _METRIC_EXTRA_KEYS if k in m and m[k] is not None}
            ws_metric.append([
                ds["business_scenario"],
                m_name,
                m.get("table", ""),
                m.get("op", "sum"),
                m.get("field", ""),
                m.get("denominator") or "",
                ", ".join(m.get("dimensions") or []),
                fdims_str,
                m.get("scale", "number"),
                m.get("description") or "",
                json.dumps(extra, ensure_ascii=False, separators=(",", ":")) if extra else "",
            ])

    return _workbook_bytes(wb)


def _open_workbook(raw: bytes) -> openpyxl.Workbook:
    if not raw:
        raise repo.Invalid("请上传非空的 .xlsx 文件")
    if len(raw) > MAX_EXCEL_BYTES:
        raise repo.Invalid(f"Excel 文件不能超过 {MAX_EXCEL_BYTES // (1024 * 1024)} MB")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            total_uncompressed = sum(info.file_size for info in zf.infolist())
    except zipfile.BadZipFile as exc:
        raise repo.Invalid("请上传有效的 .xlsx 格式 Excel 文件") from exc
    if total_uncompressed > MAX_UNCOMPRESSED_ZIP_BYTES:
        raise repo.Invalid("Excel 文件解压后体积过大，请拆分后导入")
    try:
        return openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise repo.Invalid("无法解析 .xlsx 文件，请使用系统提供的导入模板") from exc


def _find_sheet(wb: openpyxl.Workbook, candidates: tuple[str, ...]):
    for name in candidates:
        if name in wb.sheetnames:
            return wb[name], name
    lower_map = {s.strip().lower(): s for s in wb.sheetnames}
    for name in candidates:
        if name.lower() in lower_map:
            real = lower_map[name.lower()]
            return wb[real], real
    return None, ""


def _parse_scenarios_xlsx(raw: bytes, *, keep_internal: bool = False) -> tuple[list[dict[str, Any]], list[str]]:
    wb = _open_workbook(raw)
    try:
        ws_scene, _ = _find_sheet(wb, ("业务场景", "scenarios"))
        if ws_scene is None:
            raise repo.Invalid("Excel 中缺少「业务场景」工作表")

        scene_rows = ws_scene.iter_rows(values_only=True)
        header = next(scene_rows, None)
        if header is None:
            raise repo.Invalid("「业务场景」工作表缺少表头")
        col_map = _header_map(header, _SCENE_ALIASES)
        if "business_scenario" not in col_map:
            raise repo.Invalid("「业务场景」工作表缺少「业务场景」列")

        items: list[dict[str, Any]] = []
        by_name: dict[str, dict[str, Any]] = {}
        workbook_errors: list[str] = []

        for row_idx, row in enumerate(scene_rows, start=2):
            if _is_empty_row(row):
                continue
            sc_name = _cell_str(_get_col(row, col_map, "business_scenario"))
            ds_val = _cell_str(_get_col(row, col_map, "datasource"))
            desc = _cell_str(_get_col(row, col_map, "business_description"))
            parse_errors: list[str] = []

            item: dict[str, Any] = {
                "business_scenario": sc_name,
                "business_description": desc,
                "datasource_name": ds_val,
            }
            if "analysis_approach" in col_map:
                item["analysis_approach"] = _cell_str(_get_col(row, col_map, "analysis_approach"))
            if "tags" in col_map:
                item["tags"] = _split_list_cell(_get_col(row, col_map, "tags"))
            if "enabled" in col_map:
                try:
                    enabled = _cell_bool(_get_col(row, col_map, "enabled"), default=None)
                    if enabled is not None:
                        item["enabled"] = enabled
                except repo.Invalid as exc:
                    parse_errors.append(str(exc))
            if "grant_roles" in col_map:
                roles = _split_list_cell(_get_col(row, col_map, "grant_roles"))
                if roles:
                    item["grant_roles"] = roles
            if "grant_employees" in col_map:
                emps = _split_list_cell(_get_col(row, col_map, "grant_employees"))
                if emps:
                    item["grant_employees"] = emps

            if keep_internal:
                item["_row"] = row_idx
                item["_parse_errors"] = parse_errors
                item["_shorthand_masks"] = []
            else:
                item["row"] = row_idx
                for err in parse_errors:
                    workbook_errors.append(f"「业务场景」第 {row_idx} 行：{err}")
            items.append(item)
            if sc_name and sc_name not in by_name:
                by_name[sc_name] = item

        ws_table, table_sheet_name = _find_sheet(wb, ("场景数据表", "tables"))
        if ws_table is not None:
            for item in items:
                item["tables"] = []
            t_rows = ws_table.iter_rows(values_only=True)
            t_header = next(t_rows, None)
            if t_header is not None:
                t_map = _header_map(t_header, _TABLE_ALIASES)
                if "business_scenario" not in t_map or "table_name" not in t_map:
                    raise repo.Invalid(f"「{table_sheet_name}」工作表缺少「业务场景」或「表名」列")
                for row_idx, row in enumerate(t_rows, start=2):
                    if _is_empty_row(row):
                        continue
                    sc_name = _cell_str(_get_col(row, t_map, "business_scenario"))
                    if not sc_name:
                        workbook_errors.append(f"「{table_sheet_name}」第 {row_idx} 行缺少业务场景名称")
                        continue
                    target = by_name.get(sc_name)
                    if target is None:
                        workbook_errors.append(
                            f"「{table_sheet_name}」第 {row_idx} 行引用的业务场景「{sc_name}」未在「业务场景」工作表中出现"
                        )
                        continue
                    t_name = _cell_str(_get_col(row, t_map, "table_name"))
                    if not t_name:
                        msg = f"「{table_sheet_name}」第 {row_idx} 行缺少表名"
                        if keep_internal:
                            target["_parse_errors"].append(msg)
                        else:
                            workbook_errors.append(msg)
                        continue
                    t_desc = _cell_str(_get_col(row, t_map, "table_description"))
                    rf_raw = _get_col(row, t_map, "row_filters")
                    try:
                        rules, masks = _parse_row_filters_internal(_cell_str(rf_raw) if rf_raw is not None else "")
                    except repo.Invalid as exc:
                        msg = f"「{table_sheet_name}」第 {row_idx} 行（{t_name}）：{exc}"
                        if keep_internal:
                            target["_parse_errors"].append(msg)
                        else:
                            workbook_errors.append(msg)
                        continue
                    target["tables"].append({
                        "table_name": t_name,
                        "table_description": t_desc,
                        "row_filters": rules,
                    })
                    if keep_internal:
                        target["_shorthand_masks"].append(masks)

        ws_metric, metric_sheet_name = _find_sheet(wb, ("官方指标", "metrics"))
        if ws_metric is not None:
            for item in items:
                item["semantic_metrics"] = {}
            m_rows = ws_metric.iter_rows(values_only=True)
            m_header = next(m_rows, None)
            if m_header is not None:
                m_map = _header_map(m_header, _METRIC_ALIASES)
                required_cols = ("business_scenario", "name", "table", "op", "field")
                if any(k not in m_map for k in required_cols):
                    raise repo.Invalid(
                        f"「{metric_sheet_name}」工作表须包含「业务场景」「指标标识」「所属表」「公式类型」「主字段/分子」列"
                    )
                for row_idx, row in enumerate(m_rows, start=2):
                    if _is_empty_row(row):
                        continue
                    sc_name = _cell_str(_get_col(row, m_map, "business_scenario"))
                    if not sc_name:
                        workbook_errors.append(f"「{metric_sheet_name}」第 {row_idx} 行缺少业务场景名称")
                        continue
                    target = by_name.get(sc_name)
                    if target is None:
                        workbook_errors.append(
                            f"「{metric_sheet_name}」第 {row_idx} 行引用的业务场景「{sc_name}」未在「业务场景」工作表中出现"
                        )
                        continue
                    m_name = _cell_str(_get_col(row, m_map, "name"))
                    m_table = _cell_str(_get_col(row, m_map, "table"))
                    m_op = _cell_str(_get_col(row, m_map, "op")) or "sum"
                    m_field = _cell_str(_get_col(row, m_map, "field"))
                    if not m_name or not m_table or not m_field:
                        msg = f"「{metric_sheet_name}」第 {row_idx} 行缺少指标标识、所属表或主字段"
                        if keep_internal:
                            target["_parse_errors"].append(msg)
                        else:
                            workbook_errors.append(msg)
                        continue
                    if m_name in target["semantic_metrics"]:
                        msg = f"「{metric_sheet_name}」第 {row_idx} 行指标标识重复：{m_name}"
                        if keep_internal:
                            target["_parse_errors"].append(msg)
                        else:
                            workbook_errors.append(msg)
                        continue
                    scale = _cell_str(_get_col(row, m_map, "scale"))
                    if not scale:
                        scale = "decimal" if m_op in ("ratio_of_sums", "growth_of_sums") else "number"
                    metric_obj: dict[str, Any] = {
                        "table": m_table,
                        "op": m_op,
                        "field": m_field,
                        "dimensions": _split_list_cell(_get_col(row, m_map, "dimensions")),
                        "scale": scale,
                        "description": _cell_str(_get_col(row, m_map, "description")),
                    }
                    den = _cell_str(_get_col(row, m_map, "denominator"))
                    if den:
                        metric_obj["denominator"] = den
                    fdims_txt = _cell_str(_get_col(row, m_map, "filter_dimensions"))
                    if fdims_txt == "[]":
                        metric_obj["filter_dimensions"] = []
                    elif fdims_txt:
                        metric_obj["filter_dimensions"] = _split_list_cell(fdims_txt)
                    extra_raw = _cell_str(_get_col(row, m_map, "extra_json"))
                    if extra_raw:
                        try:
                            extra_obj = json.loads(extra_raw)
                        except ValueError as exc:
                            msg = f"「{metric_sheet_name}」第 {row_idx} 行（{m_name}）高级条件 JSON 无效：{exc}"
                            if keep_internal:
                                target["_parse_errors"].append(msg)
                            else:
                                workbook_errors.append(msg)
                            continue
                        if not isinstance(extra_obj, dict):
                            msg = f"「{metric_sheet_name}」第 {row_idx} 行（{m_name}）高级条件须为 JSON 对象"
                            if keep_internal:
                                target["_parse_errors"].append(msg)
                            else:
                                workbook_errors.append(msg)
                            continue
                        for k, v in extra_obj.items():
                            metric_obj[k] = v
                    target["semantic_metrics"][m_name] = metric_obj

        return items, workbook_errors
    finally:
        wb.close()


def parse_scenarios_xlsx(raw: bytes) -> tuple[list[dict[str, Any]], list[str]]:
    """解析上传的业务场景 Excel，返回 (场景配置列表, 工作簿级错误列表)。"""
    return _parse_scenarios_xlsx(raw, keep_internal=False)


def import_scenarios_xlsx(
    raw: bytes,
    principal: Principal,
    *,
    mode: Literal["upsert", "create_only"] = "upsert",
    dry_run: bool = False,
) -> dict[str, Any]:
    """解析并导入业务场景 Excel。存在工作簿级错误时即使 dry_run=False 也不写入数据库。"""
    items, workbook_errors = _parse_scenarios_xlsx(raw, keep_internal=True)
    if not items and not workbook_errors:
        raise repo.Invalid("Excel 中未找到可导入的业务场景数据行")
    if items:
        res = batch_save_scenarios(items, principal, mode=mode, dry_run=dry_run or bool(workbook_errors))
    else:
        res = {
            "ok": False,
            "dry_run": dry_run,
            "mode": mode,
            "summary": {
                "total": 0, "create_count": 0, "update_count": 0,
                "error_count": 0, "warning_count": 0, "grants_added": 0,
            },
            "items": [],
            "errors": [],
        }
    if workbook_errors:
        wb_err_items = [{"row": None, "business_scenario": "", "message": msg} for msg in workbook_errors]
        res["errors"] = [*wb_err_items, *res["errors"]]
        res["summary"]["error_count"] += len(wb_err_items)
        res["ok"] = False
    return res
