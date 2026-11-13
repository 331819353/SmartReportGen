"""业务场景的逐表取值范围：在 JOIN、聚合和返回行数限制之前应用。"""
from __future__ import annotations

import math
import re
from uuid import uuid4

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers
from sqlglot.optimizer.scope import Scope, traverse_scope

from .connectors.base import ConnectorError, ensure_readonly
from .permission import DIALECT, PermissionDenied, _check_functions, _default_schema


def table_filters(rules: object, table: str, columns: list[dict]) -> list[dict]:
    """校验配置并保存源字段的实际拼写，避免 Oracle 大写或带引号的字段被改名。"""
    if not isinstance(rules, list) or len(rules) > 20:
        raise ConnectorError(f"{table}：筛选条件必须是数组，最多 20 个字段")
    fields = [c.get("source_name", c["name"]) for c in columns]
    out, used = [], set()
    for rule in rules:
        field = rule.get("column") if isinstance(rule, dict) else None
        if not isinstance(field, str) or not field or "\x00" in field:
            raise ConnectorError(f"{table}：请选择要筛选的字段")
        candidates = [f for f in fields if f == field] or [f for f in fields if f.casefold() == field.casefold()]
        if len(candidates) != 1:
            raise ConnectorError(f"{table}：无法唯一识别字段 {field}")
        field = candidates[0]
        if field in used:
            raise ConnectorError(f"{table}：字段 {field} 重复，请将取值填在同一条条件内")
        values = rule.get("values")
        if not isinstance(values, list) or not 1 <= len(values) <= 100:
            raise ConnectorError(f"{table}：字段 {field} 需要 1 到 100 个保留值")
        for value in values:
            if (value is not None and type(value) not in (str, int, float, bool)
                    or isinstance(value, str) and (len(value) > 1000 or "\x00" in value)
                    or isinstance(value, float) and not math.isfinite(value)):
                raise ConnectorError(f"{table}：字段 {field} 仅支持文本、数字、布尔值和空值")
        used.add(field)
        out.append({"column": field, "values": values})
    return out


def has_filters(dataset: dict | None) -> bool:
    return bool(dataset and any(t.get("row_filters") for t in dataset.get("tables", ())))


def _table_name(name: str, dialect: str) -> exp.Table:
    if dialect == "duckdb":
        # HTTP 接口的逻辑表名是单个标识符，即使名称含有点或连字符。
        return exp.table_(name, quoted=True)
    try:
        table = sqlglot.parse_one(name, read=dialect, into=exp.Table)
    except sqlglot.errors.SqlglotError as e:
        raise ConnectorError(f"无法识别场景数据表：{name}") from e
    if not all(isinstance(p, exp.Identifier) for p in table.parts) or table.comments:
        raise ConnectorError(f"无法识别场景数据表：{name}")
    return table


def table_sql(ds_type: str, table: str) -> str:
    return _table_name(table, DIALECT[ds_type]).sql(dialect=DIALECT[ds_type])


def _key(table: exp.Table, dialect: str) -> tuple[str, ...]:
    # DuckDB 对引号内外的标识符均不区分大小写；其余方言由解析器保留引用的语义。
    return tuple(p.name.casefold() if dialect == "duckdb" else p.name for p in table.parts)


_ISO_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9}Z?)?\Z")


def _oracle_time_bind(dtype: str, value: str, bind: exp.Expr) -> tuple[object, exp.Expr]:
    # 候选时间使用固定 ISO 格式；保留旧规则的其他写法，并且不转换形似日期的文本列。
    base_format = 'YYYY-MM-DD"T"HH24:MI:SS'
    if dtype == "DATE":
        return value, exp.Anonymous(this="TO_DATE", expressions=[bind, exp.Literal.string(base_format)])
    if dtype.startswith("TIMESTAMP"):
        fmt = base_format + (".FF" if "." in value else "")
        if value.endswith("Z") and "TIME ZONE" in dtype:
            return value[:-1] + "+00:00", exp.Anonymous(
                this="TO_TIMESTAMP_TZ", expressions=[bind, exp.Literal.string(fmt + "TZH:TZM")])
        if not value.endswith("Z"):
            return value, exp.Anonymous(this="TO_TIMESTAMP", expressions=[bind, exp.Literal.string(fmt)])
    return value, bind


def _physical_tables(tree: exp.Expr) -> list[tuple[Scope, exp.Table]]:
    refs, seen = [], set()
    for scope in traverse_scope(tree):
        for table in [*scope.tables, scope.expression]:
            if not isinstance(table, exp.Table) or id(table) in seen:
                continue
            if not all(isinstance(p, exp.Identifier) for p in table.parts):
                raise PermissionDenied("场景查询不支持将表函数用作数据表")
            seen.add(id(table))
            # CTE 只在其可见作用域内遮蔽物理表；另一个子查询里的同名 CTE 不影响这里。
            source = scope.sources.get(table.alias_or_name)
            cte = scope.cte_sources.get(table.name)
            if table.db or table.catalog or cte is None or source is not cte:
                refs.append((scope, table))
    if any(id(t) not in seen for t in tree.find_all(exp.Table)):
        raise PermissionDenied("无法确定 SQL 中所有数据表的作用域")
    return refs


def query_in_scenario(ds_row: dict, dataset: dict, sql: str, limit: int, connector,
                      allowed: set[str] | None = None) -> dict:
    """先确认场景及表范围，再用参数化子查询替换所有物理表引用。"""
    ds_type = ds_row["type"]
    dialect = DIALECT[ds_type]
    sql = ensure_readonly(sql)
    _check_functions(sql)
    try:
        trees = sqlglot.parse(sql, read=dialect)
        if len(trees) != 1 or not isinstance(trees[0], exp.Query):
            raise PermissionDenied("场景查询只支持单条 SELECT / WITH")
        tree = normalize_identifiers(trees[0], dialect=dialect)
        if any(tree.find_all(exp.Placeholder, exp.Parameter, exp.UDTF, exp.Into, exp.Lock)):
            raise PermissionDenied("场景查询不支持外部参数、表函数或写入语句")
        refs = _physical_tables(tree)
    except sqlglot.errors.SqlglotError as e:
        raise PermissionDenied("无法安全解析场景查询") from e

    default = _default_schema(ds_row)
    if ds_type == "oracle" and default:
        default = default.upper()
    cfg = ds_row.get("config") or {}
    if ds_type in ("mysql", "starrocks"):
        default = cfg.get("database") or None
    elif ds_type == "postgresql":
        path = (cfg.get("search_path") or "public").split(",")[0].strip()
        default = path[1:-1].replace('""', '"') if path.startswith('"') and path.endswith('"') else default

    scope_tables: dict[tuple[str, ...], list[tuple[dict, exp.Table]]] = {}
    for item in dataset["tables"]:
        table = normalize_identifiers(_table_name(item["table_name"], dialect), dialect=dialect)
        name = _key(table, dialect)
        keys = {name}
        if default:
            if len(name) == 1:
                keys.add((default, *name))
            elif len(name) == 2 and name[0] == default:
                keys.add((name[1],))
        for key in keys:
            scope_tables.setdefault(key, []).append((item, table))

    params, api_tables, oracle_types = {}, [], {}
    marker = f"sr_{uuid4().hex}_"
    replacements = []
    for scope, table in refs:
        name = _key(table, dialect)
        matches = scope_tables.get(name, [])
        if not matches:
            raise PermissionDenied(f"数据表或视图 {'.'.join(name)} 未加入该业务场景")
        if len(matches) > 1:
            raise PermissionDenied(f"数据表或视图 {'.'.join(name)} 在该业务场景中的配置有歧义")
        item, configured = matches[0]
        if allowed is not None and item["table_name"].lower() not in allowed:
            raise PermissionDenied(f"无权访问数据表或视图：{item['table_name']}")
        api_tables.append(item["table_name"])
        rules = item.get("row_filters")
        if ds_type == "api":
            # DuckDB 在 CTE 定义内也会拦截同名物理表；明确使用内存库的 main Schema。
            table.set("db", exp.to_identifier("main"))
        if not rules:
            continue
        # 需要保留数据库特有表修饰符的查询，不能静默退回原表或改变其语义。
        if any(table.args.get(arg) for arg in table.args.keys() - {"this", "db", "catalog", "alias", "joins", "only"}):
            raise PermissionDenied(f"配置了筛选的数据表 {item['table_name']} 不支持当前 SQL 的表修饰语法")
        alias = table.args.get("alias") or exp.TableAlias(this=table.this.copy())
        inner = table.copy()
        inner.set("alias", None)
        inner.set("joins", None)
        # 把简写绑定到场景指定的 Schema，避免搜索路径中的同名对象被当成配置表。
        if not table.db and (configured.db or default):
            inner.set("db", configured.args["db"].copy() if configured.db else exp.to_identifier(default, quoted=True))
        if ds_type == "api":
            inner.set("this", configured.this.copy())
        where = []
        for rule in rules:
            col = exp.column(rule["column"], quoted=True)
            values = []
            for value in rule["values"]:
                if value is not None:
                    key = f"p{len(params)}"
                    bind = exp.Var(this=marker + key)
                    if ds_type == "oracle" and isinstance(value, str) and _ISO_TIME.fullmatch(value):
                        name = item["table_name"]
                        if name not in oracle_types:
                            oracle_types[name] = {c["source_name"]: c["type"].upper() for c in connector.columns(name)}
                        value, bind = _oracle_time_bind(oracle_types[name].get(rule["column"], ""), value, bind)
                    params[key] = value
                    values.append(bind)
            cond = col.isin(*values) if values else None
            if None in rule["values"]:
                is_null = col.copy().is_(exp.Null())
                cond = exp.or_(cond, is_null) if cond is not None else is_null
            where.append(cond)
        replacement = exp.select("*").from_(inner).where(*where).subquery(alias=alias.copy())
        replacements.append((scope, table, replacement, _key(configured, dialect)))

    if not replacements and ds_type != "api":
        return connector.query(sql, limit, tables=list(dict.fromkeys(api_tables)))

    for scope, table, _, configured_name in replacements:
        if not table.args.get("alias"):
            # 派生表保留原表名作为别名；schema.table.column 与 schema.table.* 改用该别名。
            name = _key(table, dialect)
            for col in (*scope.columns, *scope.stars):
                if col.db and tuple(p.name for p in col.parts[:-1]) in (name, configured_name):
                    col.set("db", None)
                    col.set("catalog", None)
                    col.set("table", table.this.copy())
    # 先改列限定符，再从最内层表开始替换，避免丢失括号内附带的 JOIN。
    for _, table, replacement, _ in reversed(replacements):
        replacement.set("joins", table.args.get("joins"))
        table.replace(replacement)
    try:
        rendered = tree.sql(dialect=dialect, comments=False, unsupported_level=sqlglot.ErrorLevel.RAISE)
    except sqlglot.errors.SqlglotError as e:
        raise PermissionDenied("当前 SQL 语法无法安全应用场景数据过滤") from e
    if params:
        # psycopg / PyMySQL 把 % 当作占位语法；先转义用户 SQL 里的 %，再放入我们生成的绑定变量。
        if ds_type in ("postgresql", "mysql", "starrocks"):
            rendered = rendered.replace("%", "%%")
            fmt = "%%(%s)s"
        else:
            fmt = ":%s" if ds_type == "oracle" else "$%s"
        rendered = re.sub(rf"\b{marker}(p\d+)\b", lambda m: fmt % m[1], rendered)
    return connector.query(rendered, limit, params=params, tables=list(dict.fromkeys(api_tables)))
