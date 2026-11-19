"""受控取数：只投影直接事实，或按场景负责人登记的公式在数据源聚合。"""
from __future__ import annotations

from contextlib import nullcontext
from decimal import Decimal, localcontext
from hashlib import sha256
import json
from uuid import uuid4

import sqlglot
from sqlglot import exp
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from typing import Annotated, Literal

from . import report_data
from .connectors import ConnectorError, get_connector
from .connectors.base import norm_value
from .permission import DIALECT, PermissionDenied
from .scenario_filters import _table_name, effective_filters, query_in_scenario

Key = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")]
FieldName = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[^\x00-\x1f]+$")]
Scalar = str | int | float | bool | None


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class SemanticMetric(StrictModel):
    table: FieldName
    op: Literal["sum", "avg", "min", "max", "count_distinct", "ratio_of_sums", "difference_of_sums", "growth_of_sums"]
    field: FieldName
    denominator: FieldName | None = None
    dimensions: list[FieldName] = Field(default_factory=list, max_length=8)
    scale: Literal["number", "decimal", "percent"] = "number"
    description: str = Field(default="", max_length=1000)


def validate_metrics(raw: dict, tables: list[dict]) -> dict:
    if not isinstance(raw, dict) or len(raw) > 100:
        raise ValueError("官方指标须为对象，最多登记 100 项")
    names = {t["table_name"] for t in tables}
    metrics = TypeAdapter(dict[Key, SemanticMetric]).validate_python(raw)
    binary = {"ratio_of_sums", "difference_of_sums", "growth_of_sums"}
    for key, metric in metrics.items():
        if metric.table not in names or (metric.op in binary) != bool(metric.denominator):
            raise ValueError(f"官方指标 {key} 的表或参与计算字段配置无效")
        if metric.op in {"ratio_of_sums", "growth_of_sums"} and metric.scale != "decimal":
            raise ValueError(f"官方指标 {key} 的公式返回小数比例，scale 应为 decimal")
    return {key: item.model_dump() for key, item in metrics.items()}


class SortKey(StrictModel):
    field: FieldName
    direction: Literal["asc", "desc"] = "asc"


class QueryPlan(StrictModel):
    table: FieldName
    fields: list[FieldName] = Field(default_factory=list, max_length=60)
    metrics: list[Key] = Field(default_factory=list, max_length=30)
    dimensions: list[FieldName] = Field(default_factory=list, max_length=8)
    where: dict[FieldName, Scalar] = Field(default_factory=dict, max_length=20)
    # 参数只能替换值，无法替换表名、字段名或公式。
    parameters: dict[FieldName, Key] = Field(default_factory=dict, max_length=10)
    order_by: list[SortKey] = Field(default_factory=list, max_length=8)


def scope_for(dataset_id: int, dataset: dict, source: dict, tables: list[str]) -> dict:
    rules = {t["table_name"]: effective_filters(t) for t in dataset["tables"]}
    if not tables or any(t not in rules for t in tables):
        raise PermissionDenied("取数来源必须是当前场景登记的物理表")
    policy = json.dumps({t: rules[t] for t in sorted(tables)}, sort_keys=True, default=str, ensure_ascii=False)
    return {"dataset_id": dataset_id, "source_id": source.get("id", dataset.get("datasource_id")),
            "source_revision": str(source.get("updated_at", "")), "tables": sorted(tables),
            "scope_sha256": sha256(policy.encode()).hexdigest()}


def direct_projection(sql: str, ds_type: str) -> None:
    """兼容直接事实 SQL。聚合、算术和可拼造投影仅可通过受控计划进入报告。"""
    try:
        tree = sqlglot.parse_one(sql, read=DIALECT[ds_type])
    except sqlglot.errors.SqlglotError as exc:
        raise ConnectorError("报告快照的直接事实 SQL 无法解析") from exc
    if not isinstance(tree, exp.Select):
        raise ConnectorError("报告快照只接受直接事实 SELECT，派生请使用官方指标计划")
    for select in tree.find_all(exp.Select):
        if (not select.args.get("from_") or any(select.args.get(k) for k in
                ("group", "distinct", "having", "qualify", "limit", "offset"))
                or any(not isinstance(x.unalias(), (exp.Column, exp.Star)) for x in select.expressions)):
            raise ConnectorError("报告快照只允许投影直接事实字段，派生请使用官方指标计划")
    if any(tree.find_all(exp.AggFunc, exp.Window, exp.SetOperation, exp.Values, exp.Add, exp.Sub, exp.Mul, exp.Div)):
        raise ConnectorError("报告快照禁止自行计算业务指标，请使用官方指标计划")


def _query_sql(plan: QueryPlan, ds: dict, source: dict, connector, values: dict, allowed):
    if plan.table not in {t["table_name"] for t in ds["tables"]} or (
            allowed is not None and plan.table.lower() not in allowed):
        raise PermissionDenied(f"无权查询场景表 {plan.table}")
    columns = {c.get("source_name", c["name"]) for c in connector.cached_columns(plan.table)}
    def col(name):
        if name not in columns:
            raise ConnectorError(f"表 {plan.table} 缺少字段 {name}")
        return exp.column(name, quoted=True)

    if not (plan.fields or plan.metrics) or (plan.metrics and plan.fields) or (plan.fields and plan.dimensions):
        raise ConnectorError("每组计划选择 fields 直取，或选择 metrics 与 dimensions 受控聚合")
    names = (plan.dimensions + plan.metrics) if plan.metrics else plan.fields
    if len(set(n.lower() for n in names)) != len(names):
        raise ConnectorError("输出字段名重复")
    projections = [col(name) for name in (plan.dimensions if plan.metrics else plan.fields)]
    aggregates = {"sum": exp.Sum, "min": exp.Min, "max": exp.Max}
    scales, derived = {}, {}
    for key in plan.metrics:
        item = (ds.get("semantic_metrics") or {}).get(key)
        if not item or item["table"] != plan.table or not set(plan.dimensions + list(plan.where) + list(plan.parameters)) <= set(item["dimensions"]):
            raise ConnectorError(f"官方指标 {key} 未登记当前表、分组或筛选维度")
        op, field = item["op"], col(item["field"])
        if op in aggregates:
            formula = aggregates[op](this=field)
        elif op == "count_distinct":
            formula = exp.Count(this=exp.Distinct(expressions=[field]))
        else:
            # 某些引擎的定点数除法会降为 DOUBLE。查询只计算登记的分子/分母，
            # 再用服务端 Decimal 生成官方派生字段，始终先应用场景权限和分组。
            lhs = exp.Sum(this=field.copy())
            rhs = exp.Count(this=field.copy()) if op == "avg" else exp.Sum(this=col(item["denominator"]))
            numerator = f"srg::{len(derived)}:num"
            denominator = f"srg::{len(derived)}:den"
            if any(name in columns for name in (numerator, denominator)):
                raise ConnectorError("源字段与官方指标内部别名冲突")
            projections.extend((exp.alias_(lhs, numerator, quoted=True), exp.alias_(rhs, denominator, quoted=True)))
            derived[key] = (op, numerator, denominator)
            scales[key] = item["scale"]
            continue
        projections.append(exp.alias_(formula, key, quoted=True))
        scales[key] = item["scale"]
    fixed = dict(plan.where)
    for name, var in plan.parameters.items():
        if name in fixed or var not in values:
            raise ConnectorError(f"缺少参数 {var} 或字段 {name} 被重复筛选")
        fixed[name] = values[var]
    dialect = DIALECT[source["type"]]
    tree = exp.select(*projections).from_(_table_name(plan.table, dialect))
    # 查询参数与场景行范围通过同一连接器绑定，保留引号与百分号的字面含义。
    marker = f"rq_{uuid4().hex}_"
    params = {}
    for name, value in fixed.items():
        if value is None:
            tree = tree.where(col(name).is_(exp.Null()))
        else:
            bind = f"q{len(params)}"
            params[bind] = value
            tree = tree.where(col(name).eq(exp.Var(this=marker + bind)))
    if plan.dimensions:
        tree = tree.group_by(*(col(name) for name in plan.dimensions))
    return tree.sql(dialect=dialect), (marker, params), scales, derived


def _derive(result: dict, derived: dict) -> None:
    if not derived:
        return
    cols = result["columns"]
    inputs = {field for _, n, d in derived.values() for field in (n, d)}
    keep = [idx for idx, col in enumerate(cols) if col not in inputs]
    formulas = [(op, cols.index(n), cols.index(d)) for op, n, d in derived.values()]

    def decimal(value):
        return None if value is None else Decimal(value["$decimal"] if isinstance(value, dict) else str(value))

    def compute(op, left, right):
        a, b = decimal(left), decimal(right)
        if a is None or b is None or (not b and op != "difference_of_sums"):
            return None
        with localcontext() as ctx:
            # 为源端定点整数和阈值保留余量。循环小数以报告契约规定的精度保存。
            ctx.prec = 140
            numerator = a - b if op in ("growth_of_sums", "difference_of_sums") else a
            value = numerator if op == "difference_of_sums" else numerator / b
            return norm_value(value)

    result["columns"] = [cols[idx] for idx in keep] + list(derived)
    result["rows"] = [[row[idx] for idx in keep] + [compute(op, row[n], row[d]) for op, n, d in formulas]
                      for row in result["rows"]]


def _sort_result(result: dict, keys: list[SortKey]) -> None:
    # 超限已经拒绝；之后在完整结果上排序，缺值始终放在末尾。
    for key in reversed(keys):
        field = key.field.lower()
        if field not in result["columns"]:
            raise ConnectorError(f"排序字段 {field} 必须是计划的输出字段")
        index = result["columns"].index(field)
        def order(row):
            value = row[index]
            if type(value) in (int, float) or isinstance(value, dict):
                return 0, Decimal(value["$decimal"] if isinstance(value, dict) else str(value))
            return 1, str(value)

        present = [row for row in result["rows"] if row[index] is not None]
        absent = [row for row in result["rows"] if row[index] is None]
        result["rows"] = sorted(present, key=order, reverse=key.direction == "desc") + absent


def plan_hash(plan: QueryPlan) -> str:
    return sha256(json.dumps(plan.model_dump(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def capture(dataset_id: int, dataset: dict, source: dict, allowed, owner: str,
            plans: dict[str, QueryPlan], values: dict, limit: int, sample_rows: int = 5) -> dict:
    values = TypeAdapter(dict[Key, Scalar], config=ConfigDict(strict=True, allow_inf_nan=False)).validate_python(values)
    if any(isinstance(v, str) and (len(v) > 1000 or "\x00" in v) for v in values.values()):
        raise ConnectorError("取数参数的文本长度或字符无效")
    connector = get_connector(source)
    if set(values) != {var for plan in plans.values() for var in plan.parameters.values()}:
        raise ConnectorError("实际参数名须与取数计划声明一致")
    if len(plans) > 1 and source["type"] == "starrocks":
        raise ConnectorError("StarRocks 多条查询无法保证同一读取时点。请使用单条官方指标计划或源端固定版本事实视图")
    batch, pending = str(uuid4()), {}
    api_tables = {plan.table for plan in plans.values()}
    if not api_tables <= {table["table_name"] for table in dataset["tables"]} or (
            allowed is not None and any(table.lower() not in allowed for table in api_tables)):
        raise PermissionDenied("取数计划包含未授权表")
    if source["type"] == "api":
        # 多接口的本地缓存只有固定内容，不能代表上游全局事务。要求可校验的共同版本号。
        context = connector.report_batch(api_tables)
    elif len(plans) > 1:
        context = connector.report_batch()
    else:
        context = nullcontext("single_statement")
    with context as consistency:
        for key, plan in plans.items():
            sql, binds, scales, derived = _query_sql(plan, dataset, source, connector, values, allowed)
            result = query_in_scenario(source, dataset, sql, limit, connector, allowed, query_binds=binds)
            if result["truncated"]:
                raise ConnectorError(f"取数计划 {key} 超过 {limit} 行，请缩小期间、字段或采用官方聚合指标")
            _derive(result, derived)
            _sort_result(result, plan.order_by)
            origin = scope_for(dataset_id, dataset, source, result["tables_used"])
            pending[key] = (result, {"scope": origin, "batch": batch, "consistency": consistency, "scales": scales,
                                     "plan_sha256": plan_hash(plan),
                                     "official_metrics": {key: dataset["semantic_metrics"][key] for key in scales}})
    # 完成所有查询与校验后才返回任何快照 ID，避免部分失败被误用为完整批次。
    return {key: {**report_data.save_query(owner, result["columns"], result["rows"], sample_rows, origin=origin),
                  "row_count": len(result["rows"]), "tables_used": result["tables_used"], "consistency": consistency}
            for key, (result, origin) in pending.items()}
