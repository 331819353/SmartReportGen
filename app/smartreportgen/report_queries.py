"""受控取数计划、官方指标和报告权限指纹。"""
from __future__ import annotations

from contextlib import nullcontext
from decimal import Decimal, localcontext
from hashlib import sha256
import json
from uuid import uuid4

import sqlglot
from sqlglot import exp
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_serializer, model_validator
from typing import Annotated, Any, Literal

from . import report_data
from .connectors import ConnectorError, get_connector
from .connectors.base import norm_value
from .connectors.sql import DEFAULT_PORTS
from .permission import DIALECT, PermissionDenied, access_for
from .scenario_filters import _table_name, effective_filters, query_in_scenario

Key = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")]
FieldName = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[^\x00-\x1f]+$")]
NonNullScalar = str | int | float | bool
Scalar = NonNullScalar | None
ParamValue = Scalar | list[Scalar]


def _check_scalar(value: Any, label: str = "条件") -> None:
    if isinstance(value, str) and (len(value) > 1000 or "\x00" in value):
        raise ValueError(f"{label}文本长度或字符无效")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Condition(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, populate_by_name=True)

    in_: list[Scalar] | None = Field(default=None, alias="in", min_length=1, max_length=200)
    not_in: list[Scalar] | None = Field(default=None, min_length=1, max_length=200)
    ne: Scalar = None
    gt: NonNullScalar | None = None
    gte: NonNullScalar | None = None
    lt: NonNullScalar | None = None
    lte: NonNullScalar | None = None

    @model_validator(mode="after")
    def validate_ops(self):
        if "ne" not in self.model_fields_set and all(getattr(self, op) is None for op in (
                "in_", "not_in", "gt", "gte", "lt", "lte")):
            raise ValueError("条件过滤至少需要指定一个比较算子")
        for values in (self.in_, self.not_in):
            if values is not None:
                for value in values:
                    _check_scalar(value)
        for op in ("ne", "gt", "gte", "lt", "lte"):
            _check_scalar(getattr(self, op))
        return self

    @model_serializer(mode="wrap")
    def serialize_ops(self, handler):
        # ne 显式为 null 表示 IS NOT NULL；序列化时省略其余未使用的算子。
        return {op: value for op, value in handler(self).items()
                if value is not None or (op == "ne" and "ne" in self.model_fields_set)}


class ParamBinding(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, populate_by_name=True)

    eq: Key | None = None
    ne: Key | None = None
    in_: Key | None = Field(default=None, alias="in")
    not_in: Key | None = None
    gt: Key | None = None
    gte: Key | None = None
    lt: Key | None = None
    lte: Key | None = None

    @model_validator(mode="after")
    def validate_bindings(self):
        if all(getattr(self, op) is None for op in ("eq", "ne", "in_", "not_in", "gt", "gte", "lt", "lte")):
            raise ValueError("参数条件至少需要绑定一个比较算子")
        return self

    @model_serializer(mode="wrap")
    def serialize_bindings(self, handler):
        # 模板和快照必须对同一组实际生效的参数绑定计算相同哈希。
        return {op: value for op, value in handler(self).items() if value is not None}


WhereValue = Scalar | list[Scalar] | Condition


def _validate_where_map(mapping: dict[str, Any], label: str) -> None:
    for value in mapping.values():
        if isinstance(value, list):
            if not 1 <= len(value) <= 200:
                raise ValueError(f"{label}列表长度须为 1 至 200")
            for item in value:
                _check_scalar(item, label)
        elif not isinstance(value, Condition):
            _check_scalar(value, label)


class SemanticMetric(StrictModel):
    table: FieldName
    op: Literal["sum", "avg", "min", "max", "count_distinct", "ratio_of_sums", "difference_of_sums", "growth_of_sums"]
    field: FieldName
    denominator: FieldName | None = None
    dimensions: list[FieldName] = Field(default_factory=list, max_length=8)
    filter_dimensions: list[FieldName] | None = Field(default=None, max_length=32)
    when: dict[FieldName, WhereValue] = Field(default_factory=dict, max_length=4)
    denominator_when: dict[FieldName, WhereValue] = Field(default_factory=dict, max_length=4)
    when_parameters: dict[FieldName, Key | ParamBinding] = Field(default_factory=dict, max_length=4)
    denominator_parameters: dict[FieldName, Key | ParamBinding] = Field(default_factory=dict, max_length=4)
    scale: Literal["number", "decimal", "percent"] = "number"
    description: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def validate_conditions(self):
        _validate_where_map(self.when, "官方指标分子条件")
        _validate_where_map(self.denominator_when, "官方指标分母条件")
        return self


def validate_metrics(raw: dict, tables: list[dict]) -> dict:
    if not isinstance(raw, dict) or len(raw) > 100:
        raise ValueError("官方指标须为对象，最多登记 100 项")
    names = {t["table_name"] for t in tables}
    metrics = TypeAdapter(dict[Key, SemanticMetric]).validate_python(raw)
    binary = {"ratio_of_sums", "difference_of_sums", "growth_of_sums"}
    for key, metric in metrics.items():
        if metric.table not in names or (metric.op in binary) != bool(metric.denominator):
            raise ValueError(f"官方指标 {key} 的表或参与计算字段配置无效")
        if not metric.denominator and (metric.denominator_when or metric.denominator_parameters):
            raise ValueError(f"官方指标 {key} 没有分母，不能配置分母条件")
        if metric.op in {"ratio_of_sums", "growth_of_sums"} and metric.scale != "decimal":
            raise ValueError(f"官方指标 {key} 的公式返回小数比例，scale 应为 decimal")
    out = {}
    for key, item in metrics.items():
        dumped = item.model_dump(by_alias=True)
        for opt in ("filter_dimensions", "when", "denominator_when", "when_parameters", "denominator_parameters"):
            if not dumped.get(opt) and opt not in item.model_fields_set:
                dumped.pop(opt, None)
        out[key] = dumped
    return out


class SortKey(StrictModel):
    field: FieldName
    direction: Literal["asc", "desc"] = "asc"


class JoinPlan(StrictModel):
    table: FieldName
    on: dict[FieldName, FieldName] = Field(min_length=1, max_length=4)
    fields: list[FieldName] = Field(default_factory=list, max_length=20)
    type: Literal["left", "inner"] = "left"


class QueryPlan(StrictModel):
    table: FieldName
    fields: list[FieldName] = Field(default_factory=list, max_length=60)
    metrics: list[Key] = Field(default_factory=list, max_length=30)
    dimensions: list[FieldName] = Field(default_factory=list, max_length=8)
    where: dict[FieldName, WhereValue] = Field(default_factory=dict, max_length=20)
    # 参数只能替换值，无法替换表名、字段名或公式。
    parameters: dict[FieldName, Key | ParamBinding] = Field(default_factory=dict, max_length=10)
    order_by: list[SortKey] = Field(default_factory=list, max_length=8)
    joins: list[JoinPlan] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def validate_where(self):
        _validate_where_map(self.where, "查询过滤")
        return self


def source_target_hash(source: dict, tables: list[str], *, version: int = 2) -> str:
    """只对决定物理数据源位置与接口契约的字段计算指纹；改密码、备注或无关表授权不影响历史报告。"""
    ds_type = source.get("type")
    cfg = source.get("config") or {}
    if ds_type == "api":
        eps = cfg.get("endpoints") or {}
        contract_fields = ("path", "method", "params", "body", "records_path", "pagination",
                           "revision_path", "complete", "immutable")
        if version >= 2:
            contract_fields += ("column_types",)
        selected_eps = {
            t: {k: eps[t].get(k) for k in contract_fields}
            for t in sorted(tables) if t in eps and isinstance(eps.get(t), dict)
        }
        target = {
            "type": ds_type,
            "base_url": cfg.get("base_url") or "",
            "records_path": cfg.get("records_path"),
            "method": cfg.get("method") or "GET",
            "endpoints": selected_eps,
        }
    else:
        schema = cfg.get("schema")
        target = {
            "type": ds_type,
            "host": cfg.get("host") or "",
            "port": cfg.get("port"),
            "database": cfg.get("database") or "",
            "schema": schema.strip().upper() if ds_type == "oracle" and isinstance(schema, str) else (schema or ""),
            "service_name": cfg.get("service_name") or "",
            "sid": cfg.get("sid") or "",
            "search_path": cfg.get("search_path") or "",
        }
        if version >= 2:
            target["host"] = target["host"] or "localhost"
            target["port"] = int(target["port"] or DEFAULT_PORTS.get(ds_type, 0))
            # 登录用户在部分连接配置中同时决定默认数据库或 Schema。
            if ds_type == "oracle" and not target["schema"]:
                target["schema"] = (cfg.get("username") or "").upper()
            if ds_type == "postgresql" and (not target["database"] or not target["search_path"]
                                           or '"$user"' in target["search_path"]):
                target["default_namespace_user"] = cfg.get("username") or ""
    return sha256(json.dumps(target, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def scope_for(dataset_id: int, dataset: dict, source: dict, tables: list[str]) -> dict:
    rules = {t["table_name"]: effective_filters(t) for t in dataset["tables"]}
    if not tables or any(t not in rules for t in tables):
        raise PermissionDenied("取数来源必须是当前场景登记的物理表")
    policy = json.dumps({t: rules[t] for t in sorted(tables)}, sort_keys=True, default=str, ensure_ascii=False)
    return {"dataset_id": dataset_id, "source_id": source.get("id", dataset.get("datasource_id")),
            "source_revision": str(source.get("updated_at", "")),
            "source_target": source_target_hash(source, tables),
            "source_target_version": 2,
            "tables": sorted(tables),
            "scope_sha256": sha256(policy.encode()).hexdigest()}


def validate_scopes(scopes: list[dict], principal) -> None:
    """交付前验证当前表授权、物理来源指纹及场景行范围，改密码或无关配置不影响历史报告。"""
    from . import repo

    acc = access_for(principal)
    datasets, sources = {}, {}
    for recorded in scopes:
        ds_id = recorded["dataset_id"]
        if not principal.registered or not all(acc.can_table(ds_id, t) for t in recorded["tables"]):
            raise PermissionDenied("当前账号无权访问该报告版本包含的数据表")
        try:
            if ds_id not in datasets:
                datasets[ds_id] = repo.get_dataset(ds_id)
            dataset = datasets[ds_id]
            ds_source = dataset.get("datasource_id")
            if ds_source != recorded["source_id"] or not dataset["enabled"]:
                raise PermissionDenied("报告来源已变化，请按当前数据权限重新取数")
            if ds_source not in sources:
                sources[ds_source] = repo.get_datasource(ds_source)
            source = sources[ds_source]
            current = scope_for(ds_id, dataset, source, recorded["tables"])
            # 没有历史指纹时只能核实同一数据源修订，不能用今天的地址替历史快照作证。
            if "source_target" in recorded:
                version = recorded.get("source_target_version", 1)
                same_target = version in (1, 2) and recorded["source_target"] == (
                    current["source_target"] if version == 2 else source_target_hash(source, recorded["tables"], version=1))
            else:
                same_target = bool(current["source_revision"]) and current["source_revision"] == recorded.get("source_revision")
            if (not source["enabled"]
                    or current["dataset_id"] != recorded.get("dataset_id")
                    or current["source_id"] != recorded.get("source_id")
                    or current["tables"] != recorded.get("tables")
                    or current["scope_sha256"] != recorded.get("scope_sha256")
                    or not same_target):
                raise PermissionDenied("报告数据源或行范围已变化，请重新取数")
        except repo.NotFound as exc:
            raise PermissionDenied("报告来源已删除，无法核实该版本的数据权限") from exc


def _predicates_for(col_expr: exp.Expr, cond: Any, bind_fn) -> list[exp.Expr]:
    out: list[exp.Expr] = []
    if cond is None:
        out.append(col_expr.copy().is_(exp.Null()))
    elif isinstance(cond, (str, int, float, bool)):
        out.append(col_expr.copy().eq(bind_fn(cond)))
    elif isinstance(cond, list):
        non_null = [v for v in cond if v is not None]
        expr = col_expr.copy().isin(*(bind_fn(v) for v in non_null)) if non_null else None
        if None in cond:
            is_null = col_expr.copy().is_(exp.Null())
            expr = exp.or_(expr, is_null) if expr is not None else is_null
        if expr is not None:
            out.append(expr)
    elif isinstance(cond, Condition):
        if cond.in_ is not None:
            out.extend(_predicates_for(col_expr, cond.in_, bind_fn))
        if cond.not_in is not None:
            non_null = [v for v in cond.not_in if v is not None]
            if non_null:
                out.append(exp.Not(this=exp.Paren(this=col_expr.copy().isin(*(bind_fn(v) for v in non_null)))))
            if None in cond.not_in:
                out.append(col_expr.copy().is_(exp.Not(this=exp.Null())))
        if "ne" in cond.model_fields_set:
            if cond.ne is None:
                out.append(col_expr.copy().is_(exp.Not(this=exp.Null())))
            else:
                out.append(col_expr.copy().neq(bind_fn(cond.ne)))
        if cond.gt is not None:
            out.append(exp.GT(this=col_expr.copy(), expression=bind_fn(cond.gt)))
        if cond.gte is not None:
            out.append(exp.GTE(this=col_expr.copy(), expression=bind_fn(cond.gte)))
        if cond.lt is not None:
            out.append(exp.LT(this=col_expr.copy(), expression=bind_fn(cond.lt)))
        if cond.lte is not None:
            out.append(exp.LTE(this=col_expr.copy(), expression=bind_fn(cond.lte)))
    return out


def _param_names(binding: Key | ParamBinding) -> set[str]:
    if isinstance(binding, str):
        return {binding}
    return {getattr(binding, op) for op in ("eq", "ne", "in_", "not_in", "gt", "gte", "lt", "lte")
            if getattr(binding, op) is not None}


_PARAMETERS = TypeAdapter(dict[FieldName, Key | ParamBinding])


def _parameter_predicates(column, binding, values: dict, bind) -> list[exp.Expr]:
    bindings = {"eq": binding} if isinstance(binding, str) else binding.model_dump()
    predicates, comparison = [], {}
    for op, name in bindings.items():
        if name not in values:
            raise ConnectorError(f"缺少参数 {name}")
        value = values[name]
        if op in ("in_", "not_in"):
            if not isinstance(value, list):
                raise ConnectorError(f"参数 {name} 须为 1 至 200 个元素的数组")
        elif isinstance(value, list) or (op in ("gt", "gte", "lt", "lte") and value is None):
            raise ConnectorError(f"参数 {name} 须为标量值，范围比较不可为空")
        if op == "eq":
            predicates.extend(_predicates_for(column, value, bind))
        else:
            comparison[op] = value
    if comparison:
        predicates.extend(_predicates_for(column, Condition(**comparison), bind))
    return predicates


def _query_sql(plan: QueryPlan, ds: dict, source: dict, connector, values: dict, allowed):
    ds_tables = {t["table_name"] for t in ds["tables"]}
    if plan.table not in ds_tables or (allowed is not None and plan.table.lower() not in allowed):
        raise PermissionDenied(f"无权查询场景表 {plan.table}")
    columns = {c.get("source_name", c["name"]) for c in connector.cached_columns(plan.table)}
    main_alias = "_main" if plan.joins else None

    def col(name, table_alias=main_alias, allowed_cols=columns, table_name=plan.table):
        if name not in allowed_cols:
            raise ConnectorError(f"表 {table_name} 缺少字段 {name}")
        return exp.column(name, table=table_alias, quoted=True)

    join_specs = []
    join_checks = []
    join_fields: list[str] = []
    for idx, join in enumerate(plan.joins):
        if join.table not in ds_tables or (allowed is not None and join.table.lower() not in allowed):
            raise PermissionDenied(f"无权关联场景表 {join.table}")
        j_cols = {c.get("source_name", c["name"]) for c in connector.cached_columns(join.table)}
        j_alias = f"_j{idx}"
        on_exprs = []
        for left_col, right_col in join.on.items():
            left_expr = col(left_col, main_alias, columns, plan.table)
            right_expr = col(right_col, j_alias, j_cols, join.table)
            on_exprs.append(left_expr.eq(right_expr))
        for jf in join.fields:
            col(jf, j_alias, j_cols, join.table)
            join_fields.append(jf)
        guard = f"srg::join:{idx}"
        if any(c.lower() == guard for c in j_cols):
            raise ConnectorError("维表字段与关联校验别名冲突")
        join_checks.append((guard, join.table))
        join_specs.append((join, j_alias, j_cols, on_exprs, guard))

    if not (plan.fields or plan.metrics) or (plan.metrics and plan.fields) or (plan.fields and plan.dimensions):
        raise ConnectorError("每组计划选择 fields 直取，或选择 metrics 与 dimensions 受控聚合")
    names = (plan.dimensions + join_fields + plan.metrics) if plan.metrics else (plan.fields + join_fields)
    normalized_names = {n.lower() for n in names}
    if len(normalized_names) != len(names) or any(g in normalized_names for g, _ in join_checks):
        raise ConnectorError("输出字段名重复")
    projections = [col(name) for name in (plan.dimensions if plan.metrics else plan.fields)]
    for join, j_alias, j_cols, _, guard in join_specs:
        projections.extend(col(jf, j_alias, j_cols, join.table) for jf in join.fields)
        checked = exp.column(guard, table=j_alias, quoted=True)
        projections.append(exp.alias_(exp.Max(this=checked) if plan.metrics else checked, guard, quoted=True))

    marker = f"rq_{uuid4().hex}_"
    params: dict[str, Any] = {}

    def bind(value):
        idx_key = f"q{len(params)}"
        params[idx_key] = value
        return exp.Var(this=marker + idx_key)

    def metric_target(field_name: str, when_raw: dict, parameters_raw: dict) -> exp.Expr:
        target = col(field_name)
        # 本期和基期参数分别限制聚合输入；外层 WHERE 依旧先约束整个事实集。
        parsed = TypeAdapter(dict[FieldName, WhereValue]).validate_python(when_raw)
        preds = [p for fname, fcond in parsed.items() for p in _predicates_for(col(fname), fcond, bind)]
        for fname, binding in _PARAMETERS.validate_python(parameters_raw).items():
            preds.extend(_parameter_predicates(col(fname), binding, values, bind))
        if not preds:
            return target
        cond_expr = preds[0]
        for extra in preds[1:]:
            cond_expr = exp.and_(cond_expr, extra)
        return exp.Case(ifs=[exp.If(this=cond_expr, true=target)], default=exp.Null())

    aggregates = {"sum": exp.Sum, "min": exp.Min, "max": exp.Max}
    scales, derived = {}, {}
    joined_group_keys = {field for join in plan.joins if join.fields for field in join.on}
    for key in plan.metrics:
        item = (ds.get("semantic_metrics") or {}).get(key)
        if not item or item["table"] != plan.table:
            raise ConnectorError(f"官方指标 {key} 未登记当前表、分组或筛选维度")
        allowed_group = set(item["dimensions"])
        allowed_filter = (allowed_group | set(item["filter_dimensions"])) if item.get("filter_dimensions") is not None else columns
        if (not (set(plan.dimensions) | joined_group_keys) <= allowed_group
                or not (set(plan.where) | set(plan.parameters)) <= allowed_filter):
            raise ConnectorError(f"官方指标 {key} 未登记当前表、分组或筛选维度")
        op = item["op"]
        when_raw = item.get("when") or {}
        den_when_raw = item.get("denominator_when") or {}
        num_expr = metric_target(item["field"], when_raw, item.get("when_parameters") or {})
        if op in aggregates:
            formula = aggregates[op](this=num_expr)
        elif op == "count_distinct":
            formula = exp.Count(this=exp.Distinct(expressions=[num_expr]))
        else:
            # 某些引擎的定点数除法会降为 DOUBLE。查询只计算登记的分子/分母，
            # 再用服务端 Decimal 生成官方派生字段，始终先应用场景权限和分组。
            lhs = exp.Sum(this=num_expr)
            if op == "avg":
                rhs = exp.Count(this=num_expr.copy())
            else:
                rhs = exp.Sum(this=metric_target(item["denominator"], den_when_raw,
                                                item.get("denominator_parameters") or {}))
            numerator = f"srg::{len(derived)}:num"
            denominator = f"srg::{len(derived)}:den"
            if any(name in columns or name in names for name in (numerator, denominator)):
                raise ConnectorError("源字段与官方指标内部别名冲突")
            projections.extend((exp.alias_(lhs, numerator, quoted=True), exp.alias_(rhs, denominator, quoted=True)))
            derived[key] = (op, numerator, denominator)
            scales[key] = item["scale"]
            continue
        projections.append(exp.alias_(formula, key, quoted=True))
        scales[key] = item["scale"]

    where_exprs: list[exp.Expr] = []
    for name, cond in plan.where.items():
        where_exprs.extend(_predicates_for(col(name), cond, bind))

    for name, binding in plan.parameters.items():
        if name in plan.where and (isinstance(binding, str) or not isinstance(plan.where[name], Condition)):
            raise ConnectorError(f"缺少参数或字段 {name} 被重复筛选")
        where_exprs.extend(_parameter_predicates(col(name), binding, values, bind))

    dialect = DIALECT[source["type"]]
    main_tbl = _table_name(plan.table, dialect)
    if main_alias:
        main_tbl.set("alias", exp.TableAlias(this=exp.to_identifier(main_alias, quoted=True)))
    tree = exp.select(*projections).from_(main_tbl)
    for join, j_alias, _, on_exprs, guard in join_specs:
        j_tbl = _table_name(join.table, dialect)
        # 在同一读取语句、同一授权行范围内计数。只有实际匹配到重复键时才拒绝结果。
        keys = list(dict.fromkeys(join.on.values()))
        fields = list(dict.fromkeys([*keys, *join.fields]))
        multiplicity = exp.Window(this=exp.Count(this=exp.Star()),
                                  partition_by=[exp.column(k, quoted=True) for k in keys])
        j_view = exp.select(*(exp.column(k, quoted=True) for k in fields),
                            exp.alias_(multiplicity, guard, quoted=True)).from_(j_tbl).subquery(
                                exp.to_identifier(j_alias, quoted=True))
        on_cond = on_exprs[0]
        for extra in on_exprs[1:]:
            on_cond = exp.and_(on_cond, extra)
        tree = tree.join(j_view, on=on_cond, join_type=join.type.upper())
    for w_expr in where_exprs:
        tree = tree.where(w_expr)
    if plan.metrics and (plan.dimensions or join_fields):
        group_exprs = [col(name) for name in plan.dimensions]
        for join, j_alias, j_cols, _, _ in join_specs:
            group_exprs.extend(col(jf, j_alias, j_cols, join.table) for jf in join.fields)
        tree = tree.group_by(*group_exprs)
    return tree.sql(dialect=dialect), (marker, params), scales, derived, join_checks


def _check_join_keys(result: dict, checks: list[tuple[str, str]]) -> None:
    if not checks:
        return
    indexes = {result["columns"].index(field): table for field, table in checks}
    for row in result["rows"]:
        for index, table in indexes.items():
            value = row[index]
            if value is not None and Decimal(value["$decimal"] if isinstance(value, dict) else str(value)) > 1:
                raise ConnectorError(f"维表 {table} 在授权范围内的匹配键不唯一，关联会重复计算事实")
    # 关联校验列只在服务端使用，不能进入快照或报告。
    keep = [i for i in range(len(result["columns"])) if i not in indexes]
    result["columns"] = [result["columns"][i] for i in keep]
    result["rows"] = [[row[i] for i in keep] for row in result["rows"]]


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
    dumped = plan.model_dump(by_alias=True)
    if not dumped.get("joins"):
        dumped.pop("joins", None)
    dumped["where"] = {
        k: (v.model_dump(by_alias=True, exclude_unset=True) if isinstance(v, Condition) else v)
        for k, v in plan.where.items()
    }
    dumped["parameters"] = {
        k: (v.model_dump(by_alias=True, exclude_unset=True) if isinstance(v, ParamBinding) else v)
        for k, v in plan.parameters.items()
    }
    return sha256(json.dumps(dumped, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def capture(dataset_id: int, dataset: dict, source: dict, allowed, owner: str,
            plans: dict[str, QueryPlan], values: dict, limit: int, sample_rows: int = 5) -> dict:
    values = TypeAdapter(dict[Key, ParamValue], config=ConfigDict(strict=True, allow_inf_nan=False)).validate_python(values)
    for v in values.values():
        items = v if isinstance(v, list) else [v]
        if isinstance(v, list) and not 1 <= len(v) <= 200:
            raise ConnectorError("列表参数须包含 1 至 200 个元素")
        if any(isinstance(item, str) and (len(item) > 1000 or "\x00" in item) for item in items):
            raise ConnectorError("取数参数的文本长度或字符无效")
    connector = get_connector(source)
    declared = {var for plan in plans.values() for binding in plan.parameters.values() for var in _param_names(binding)}
    official_metrics = dataset.get("semantic_metrics") or {}
    for name in {name for plan in plans.values() for name in plan.metrics}:
        metric = official_metrics.get(name) or {}
        for field in ("when_parameters", "denominator_parameters"):
            for binding in _PARAMETERS.validate_python(metric.get(field) or {}).values():
                declared.update(_param_names(binding))
    if set(values) != declared:
        raise ConnectorError("实际参数名须与取数计划声明一致")
    if len(plans) > 1 and source["type"] == "starrocks":
        raise ConnectorError("StarRocks 多条查询无法保证同一读取时点。请使用单条官方指标计划或源端固定版本事实视图")
    batch, pending = str(uuid4()), {}
    api_tables = {plan.table for plan in plans.values()} | {
        join.table for plan in plans.values() for join in plan.joins
    }
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
            sql, binds, scales, derived, join_checks = _query_sql(plan, dataset, source, connector, values, allowed)
            result = query_in_scenario(source, dataset, sql, limit, connector, allowed, query_binds=binds)
            if result["truncated"]:
                raise ConnectorError(f"取数计划 {key} 超过 {limit} 行，请缩小期间、字段或采用官方聚合指标")
            _check_join_keys(result, join_checks)
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
