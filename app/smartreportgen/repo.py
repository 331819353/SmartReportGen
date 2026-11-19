"""各类元数据的增删改查与检索索引维护。"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from . import crypto, db, report_data
from .auth import Principal
from .config import settings
from .connectors import SUPPORTED_TYPES, ConnectorError, drop_connector, get_connector
from .connectors.sql import oracle_schema
from .embedding import EmbeddingError, embed_many, to_pgvector
from .permission import PermissionDenied, source_scene_tables
from .report_document import report_document_error
from .scenario_filters import table_filters, table_sql
from .textproc import add_words, tokenize

log = logging.getLogger(__name__)


class NotFound(LookupError):
    pass


class Invalid(ValueError):
    pass


def _one(row: dict | None, what: str) -> dict:
    if not row:
        raise NotFound(f"{what}不存在")
    return row


# ============================ 数据源 ============================
DS_COLS = "id, name, type, config, description, enabled, created_at, updated_at, (secret IS NOT NULL) AS has_secret"


def list_datasources() -> list[dict]:
    return db.fetchall(f"SELECT {DS_COLS} FROM datasource.datasource ORDER BY id")


def get_datasource(ds_id: int, with_secret: bool = False) -> dict:
    cols = "*" if with_secret else DS_COLS
    return _one(db.fetchone(f"SELECT {cols} FROM datasource.datasource WHERE id=%s", (ds_id,)), "数据源")


def find_datasource(ref: Any) -> dict:
    """按 id 或名称查找（含密文）。"""
    if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
        return get_datasource(int(ref), with_secret=True)
    return _one(db.fetchone("SELECT * FROM datasource.datasource WHERE name=%s", (ref,)), f"数据源 {ref}")


def _validate_ds(data: dict) -> None:
    if not (data.get("name") or "").strip():
        raise Invalid("名称不能为空")
    if data.get("type") not in SUPPORTED_TYPES:
        raise Invalid(f"type 必须是 {SUPPORTED_TYPES} 之一")
    cfg = data.get("config") or {}
    if not isinstance(cfg, dict):
        raise Invalid("config 必须是对象")
    if data["type"] == "api" and not cfg.get("base_url"):
        raise Invalid("API 数据源需要 config.base_url")
    if data["type"] != "api" and not cfg.get("host"):
        raise Invalid("数据库数据源需要 config.host")
    if data["type"] == "oracle":
        oracle_schema(cfg)
    for k in ("password", "token"):
        if k in cfg:
            raise Invalid(f"不要把 {k} 放在 config 中，请使用 secret 字段")
    _validate_source_scene_tables(data)


def _validate_source_scene_tables(data: dict) -> None:
    """数据源配置同时登记可供员工创建个人场景的表和不可放宽的行范围。"""
    config = data.get("config") or {}
    policies = config.get("scenario_tables", [])
    if not isinstance(policies, list) or len(policies) > 500:
        raise Invalid("可建场景的表须为数组，最多 500 项")
    used = set()
    for item in policies:
        if not isinstance(item, dict) or set(item) - {"table_name", "description", "roles", "employees", "row_filters"}:
            raise Invalid("可建场景的表须包含表名、员工或角色，以及可选的固定行范围")
        name = item.get("table_name")
        if not isinstance(name, str) or not name or name != name.strip() or name.casefold() in used or "\x00" in name:
            raise Invalid("可建场景的表名须有效且唯一")
        used.add(name.casefold())
        try:
            table_sql(data["type"], name)
            for key in ("roles", "employees"):
                values = item.get(key, [])
                if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() or v != v.strip()
                                                       or "\x00" in v for v in values):
                    raise Invalid("员工工号和角色须为有效字符串数组")
            if not item.get("roles") and not item.get("employees"):
                raise Invalid(f"{name}：请指定可引用该表的员工或角色")
            if not isinstance(item.get("description", ""), str):
                raise Invalid(f"{name}：表描述须为文本")
            rules = item.get("row_filters", [])
            fields = [{"name": rule.get("column")} for rule in rules if isinstance(rule, dict)] if isinstance(rules, list) else []
            table_filters(rules, name, fields)
        except ConnectorError as exc:
            raise Invalid(str(exc)) from exc


def create_datasource(data: dict) -> dict:
    _validate_ds(data)
    row = db.fetchone(
        "INSERT INTO datasource.datasource(name, type, config, secret, description, enabled) "
        f"VALUES (%s,%s,%s,%s,%s,%s) RETURNING {DS_COLS}",
        (data["name"].strip(), data["type"], db.jsonb(data.get("config") or {}), crypto.encrypt(data.get("secret")),
         data.get("description") or "", data.get("enabled", True)),
    )
    return row


def update_datasource(ds_id: int, data: dict) -> dict:
    cur = get_datasource(ds_id, with_secret=True)
    merged = {**cur, **{k: v for k, v in data.items() if k != "secret"}}
    _validate_ds(merged)
    secret = cur["secret"]
    if data.get("secret"):
        secret = crypto.encrypt(data["secret"])
    elif data.get("clear_secret"):
        secret = None
    row = db.fetchone(
        "UPDATE datasource.datasource SET name=%s, type=%s, config=%s, secret=%s, description=%s, enabled=%s, "
        f"updated_at=now() WHERE id=%s RETURNING {DS_COLS}",
        (merged["name"].strip(), merged["type"], db.jsonb(merged.get("config") or {}), secret,
         merged.get("description") or "", merged.get("enabled", True), ds_id),
    )
    drop_connector(ds_id)
    return row


def delete_datasource(ds_id: int) -> None:
    used = db.fetchone("SELECT count(*) AS n FROM dataset.dataset WHERE datasource_id=%s", (ds_id,))
    if used and used["n"]:
        raise Invalid(f"仍有 {used['n']} 个数据集引用该数据源，无法删除")
    db.execute("DELETE FROM datasource.datasource WHERE id=%s", (ds_id,))
    drop_connector(ds_id)


# ============================ 数据集 ============================
def _dataset_index(ds: dict, tables: list[dict]) -> dict:
    kw_a = tokenize(" ".join([ds["business_scenario"], *ds.get("tags", [])]))
    kw_a += [w for w in [ds["business_scenario"].lower(), *[t.lower() for t in ds.get("tags", [])]] if w not in kw_a]
    kw_b = tokenize(ds.get("business_description") or "")
    kw_c = tokenize(" ".join(f"{t['table_name'].replace('_', ' ')} {t.get('table_description') or ''}" for t in tables))
    text = (
        f"业务场景：{ds['business_scenario']}\n标签：{'、'.join(ds.get('tags') or [])}\n"
        f"业务描述：{ds.get('business_description') or ''}\n数据表：\n"
        + "\n".join(f"- {t['table_name']}：{t.get('table_description') or ''}" for t in tables)
    )
    return {"kw_a": kw_a, "kw_b": kw_b, "kw_c": kw_c, "kw_all": list(dict.fromkeys(kw_a + kw_b + kw_c)), "text": text}


def _embed_or_none(text: str) -> str | None:
    try:
        return to_pgvector(embed_many([text])[0])
    except EmbeddingError as e:
        log.error("向量化失败（该记录仅可被关键词检索命中）: %s", e)
        return None


def reindex_dataset(dataset_id: int) -> None:
    ds = _one(db.fetchone("SELECT * FROM dataset.dataset WHERE id=%s", (dataset_id,)), "数据集")
    tables = db.fetchall("SELECT * FROM dataset.dataset_table WHERE dataset_id=%s ORDER BY sort, id", (dataset_id,))
    add_words([ds["business_scenario"], *ds["tags"]])
    idx = _dataset_index(ds, tables)
    vec = _embed_or_none(idx["text"])
    db.execute(
        "UPDATE dataset.dataset SET kw_a=%s, kw_b=%s, kw_c=%s, kw_all=%s, embedding=%s::vector, embedding_sig=%s "
        "WHERE id=%s",
        (idx["kw_a"], idx["kw_b"], idx["kw_c"], idx["kw_all"], vec, settings.embedding_signature if vec else None,
         dataset_id),
    )


def _contains_pattern(q: str) -> str:
    # 列表搜索按字面匹配，与管理台包含搜索一致。% 和 _ 也可以作为业务文本搜索。
    return "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def list_datasets(q: str | None = None) -> list[dict]:
    where, params = "", ()
    if q and q.strip():
        pattern = _contains_pattern(q)
        where = (" WHERE d.business_scenario ILIKE %s OR d.business_description ILIKE %s "
                 "OR EXISTS (SELECT 1 FROM unnest(d.tags) tag WHERE tag ILIKE %s)")
        params = (pattern, pattern, pattern)
    rows = db.fetchall(
        "SELECT d.id, d.business_scenario, d.business_description, d.datasource_id, d.tags, d.enabled, d.owner_emp_no, "
        "d.created_at, d.updated_at, (d.embedding IS NOT NULL) AS vectorized, s.name AS datasource_name, "
        "s.type AS datasource_type, (SELECT count(*) FROM dataset.dataset_table t WHERE t.dataset_id=d.id) AS table_count "
        "FROM dataset.dataset d LEFT JOIN datasource.datasource s ON s.id=d.datasource_id" + where + " ORDER BY d.id",
        params,
    )
    return rows


def get_dataset(dataset_id: int) -> dict:
    ds = _one(
        db.fetchone(
            "SELECT d.id, d.business_scenario, d.business_description, d.analysis_approach, d.semantic_metrics, d.datasource_id, d.tags, d.enabled, d.owner_emp_no, "
            "s.config AS source_config, s.enabled AS source_enabled, d.created_at, d.updated_at, (d.embedding IS NOT NULL) AS vectorized, s.name AS datasource_name, "
            "s.type AS datasource_type FROM dataset.dataset d LEFT JOIN datasource.datasource s "
            "ON s.id=d.datasource_id WHERE d.id=%s",
            (dataset_id,),
        ),
        "数据集",
    )
    ds["tables"] = db.fetchall(
        "SELECT id, table_name, table_description, sort, row_filters "
        "FROM dataset.dataset_table WHERE dataset_id=%s ORDER BY sort, id",
        (dataset_id,),
    )
    source = {"config": ds.pop("source_config"), "enabled": ds.pop("source_enabled")}
    if ds["owner_emp_no"] is not None:
        catalog = source_scene_tables(source)
        # 场景可追加自己的筛选，数据源固定范围作为独立条件只读附加。
        ds["tables"] = [{**table, "source_row_filters": catalog[table["table_name"]].get("row_filters", [])}
                        for table in ds["tables"] if table["table_name"] in catalog]
    return ds


def _authorize_personal_scene(data: dict, principal: Principal | None) -> None:
    source = get_datasource(data["datasource_id"])
    catalog = source_scene_tables(source, principal)
    tables = {table["table_name"].strip() for table in data["tables"]}
    if not tables:
        raise Invalid("请选择至少一张有权限的数据表或视图")
    if not tables.issubset(catalog):
        raise PermissionDenied("个人场景只能选择数据源已授权开放的表或视图")


def _validate_dataset(data: dict) -> None:
    for field, label in (("business_scenario", "业务场景"), ("business_description", "业务描述")):
        value = data.get(field)
        if not isinstance(value, str) or not value.strip() or "\x00" in value:
            raise Invalid(f"{label}须为有效文本")
    if data.get("analysis_approach") is not None and not isinstance(data["analysis_approach"], str):
        raise Invalid("分析思路须为文本")
    if type(data.get("datasource_id")) is not int or data["datasource_id"] < 1:
        raise Invalid("请选择数据源")
    tags = data.get("tags") or []
    if not isinstance(tags, list) or any(not isinstance(tag, str) or "\x00" in tag for tag in tags):
        raise Invalid("标签须为字符串数组")
    if type(data.get("enabled", True)) is not bool:
        raise Invalid("启用状态须为布尔值")
    tables = data.get("tables", [])
    if not isinstance(tables, list) or any(not isinstance(t, dict) for t in tables):
        raise Invalid("数据表须为对象数组")
    names = [t.get("table_name") for t in tables]
    if any(not isinstance(n, str) or not n.strip() for n in names):
        raise Invalid("数据表名不能为空")
    if len({n.strip() for n in names}) != len(names):
        raise Invalid("数据表名重复")


def _prepare_tables(data: dict, current: dict | None = None) -> list[dict]:
    # 局部更新、改描述或调顺序不依赖数据源在线；只有新增/修改条件或切换数据源时查字段。
    same_source = current is not None and current["datasource_id"] == data["datasource_id"]
    old_rules = {t["table_name"]: t["row_filters"] for t in current["tables"]} if same_source else {}
    conn, tables = None, []
    for t in data.get("tables", []):
        name = t["table_name"].strip()
        rules = t.get("row_filters", [])
        if not isinstance(rules, list) or len(rules) > 20:
            raise Invalid(f"{name}：筛选条件必须是数组，最多 20 个字段")
        # 比较 JSON 的实际类型，避免 Python 把 true 与 1 视为相同而跳过校验。
        if rules and json.dumps(rules, sort_keys=True) != json.dumps(old_rules.get(name), sort_keys=True):
            try:
                conn = conn or get_connector(get_datasource(data["datasource_id"], with_secret=True))
                rules = table_filters(rules, name, conn.columns(name))
            except ConnectorError as e:
                raise Invalid(f"无法保存数据过滤：{e}") from e
        tables.append({**t, "table_name": name, "row_filters": rules})
    return tables


def _save_tables(c, dataset_id: int, tables: list[dict]) -> None:
    c.execute("DELETE FROM dataset.dataset_table WHERE dataset_id=%s", (dataset_id,))
    for i, t in enumerate(tables):
        c.execute(
            "INSERT INTO dataset.dataset_table(dataset_id, table_name, table_description, sort, row_filters) "
            "VALUES (%s,%s,%s,%s,%s)",
            (dataset_id, t["table_name"], t.get("table_description") or "", i, db.jsonb(t["row_filters"])),
        )


def create_dataset(data: dict, principal: Principal | None = None) -> dict:
    from .report_queries import validate_metrics

    _validate_dataset(data)
    owner = principal.emp_no if principal is not None and not principal.is_admin else None
    if owner:
        _authorize_personal_scene(data, principal)
    tables = _prepare_tables(data)
    metrics = validate_metrics(data.get("semantic_metrics", {}), tables)
    with db.conn() as c, c.transaction():
        row = c.execute(
            "INSERT INTO dataset.dataset(business_scenario, business_description, analysis_approach, datasource_id, tags, enabled, semantic_metrics, owner_emp_no) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            (data["business_scenario"].strip(), data["business_description"].strip(),
             (data.get("analysis_approach") or "").strip(), data["datasource_id"],
             [t.strip() for t in data.get("tags") or [] if t.strip()], data.get("enabled", True), db.jsonb(metrics), owner),
        ).fetchone()
        _save_tables(c, row["id"], tables)
    reindex_dataset(row["id"])
    return get_dataset(row["id"])


def update_dataset(dataset_id: int, data: dict, principal: Principal | None = None) -> dict:
    from .report_queries import validate_metrics

    cur = get_dataset(dataset_id)
    _require_scene_manager(cur, principal)
    merged = {**cur, **data}
    _validate_dataset(merged)
    if cur["owner_emp_no"] is not None:
        _authorize_personal_scene(merged, principal)
    tables = _prepare_tables(merged, cur) if "tables" in data or cur["datasource_id"] != merged["datasource_id"] else None
    metrics = validate_metrics(merged["semantic_metrics"], tables if tables is not None else cur["tables"])
    with db.conn() as c, c.transaction():
        c.execute(
            "UPDATE dataset.dataset SET business_scenario=%s, business_description=%s, analysis_approach=%s, datasource_id=%s, tags=%s, "
            "enabled=%s, semantic_metrics=%s, updated_at=now() WHERE id=%s",
            (merged["business_scenario"].strip(), merged["business_description"].strip(),
             (merged["analysis_approach"] or "").strip(), merged["datasource_id"],
             [t.strip() for t in merged.get("tags") or [] if t.strip()], merged.get("enabled", True), db.jsonb(metrics), dataset_id),
        )
        if tables is not None:
            _save_tables(c, dataset_id, tables)
    if cur["business_scenario"] != merged["business_scenario"]:
        db.execute("UPDATE style_rule.style_rule SET business_scenario=%s WHERE business_scenario=%s",
                   (merged["business_scenario"], cur["business_scenario"]))
    reindex_dataset(dataset_id)
    return get_dataset(dataset_id)


def _require_scene_manager(dataset: dict, principal: Principal | None) -> None:
    if principal is not None and not principal.is_admin and dataset["owner_emp_no"] != principal.emp_no:
        raise PermissionDenied("只能管理本人创建的业务场景")


def delete_dataset(dataset_id: int, principal: Principal | None = None) -> None:
    if principal is not None and not principal.is_admin:
        _require_scene_manager(get_dataset(dataset_id), principal)
    db.execute("DELETE FROM dataset.dataset WHERE id=%s", (dataset_id,))


def search_datasets(query: str, top_k: int = 5, dataset_ids: list[int] | None = None) -> dict:
    """dataset_ids 不为 None 时只在这些数据集（有权限的业务场景）内检索。"""
    from .search import hybrid

    if dataset_ids is not None and not dataset_ids:
        return {"query_tokens": tokenize(query), "hits": [], "warnings": []}
    where, params = ("AND t.id = ANY(%(ids)s)", {"ids": dataset_ids}) if dataset_ids is not None else ("", {})
    res = hybrid("dataset", query, top_k, where=where, params=params)
    hits = []
    for h in res["hits"]:
        ds = get_dataset(h["id"])
        hits.append({**h, "dataset": ds})
    return {"query_tokens": res["query_tokens"], "hits": hits, "warnings": res["warnings"]}


# ============================ 样式规则 ============================
# 生效顺序：业务场景规则 ≥ 通用规则 ≥ 内置样式（同一层级内按 priority 升序，后者覆盖前者）
SR_COLS = ("id, name, scope, business_scenario, content, items, theme, priority, enabled, created_at, updated_at, "
           "created_by, (SELECT e.name FROM permission.employee e WHERE e.emp_no = created_by) AS creator_name, "
           "(embedding IS NOT NULL) AS vectorized")


def _clean_theme(theme: dict) -> dict:
    """只保留合法令牌，并规范化取值（规则只存它显式覆盖的设置）。"""
    from . import design

    if not isinstance(theme, dict):
        raise Invalid("theme 必须是对象")
    resolved, warns = design.resolve_overrides(theme)
    bad = [w for w in warns if "不是已知" not in w]
    if bad:
        raise Invalid("；".join(bad))
    return resolved


def _validate_rule(data: dict) -> None:
    if not (data.get("name") or "").strip():
        raise Invalid("规则名称不能为空")
    if data.get("scope") not in ("general", "scenario"):
        raise Invalid("请选择适用范围：通用规则或业务场景规则")
    if data["scope"] == "scenario" and not (data.get("business_scenario") or "").strip():
        raise Invalid("业务场景规则必须选择业务场景")
    if not isinstance(data.get("theme") or {}, dict):
        raise Invalid("theme 必须是对象")


def _rule_payload(data: dict) -> tuple[list, str, dict]:
    from . import design

    raw_items = data.get("items")
    items = design.normalize_items(raw_items)
    # 结构化条目全部退役时也重生成说明，避免回退到含旧要求的 Markdown。
    content = design.items_to_markdown(data["name"].strip(), items) if raw_items else (data.get("content") or "")
    return items, content, _clean_theme(data.get("theme") or {})


def reindex_rule(rule_id: int) -> None:
    r = _one(db.fetchone("SELECT * FROM style_rule.style_rule WHERE id=%s", (rule_id,)), "样式规则")
    kw_a = tokenize(f"{r['name']} {r['business_scenario'] or '通用'}")
    kw_b = tokenize(r["content"])
    kw_c = tokenize(" ".join(map(str, (r["theme"] or {}).keys())))
    text = f"样式规则：{r['name']}\n适用：{r['business_scenario'] or '通用'}\n{r['content']}"
    vec = _embed_or_none(text)
    db.execute(
        "UPDATE style_rule.style_rule SET kw_a=%s, kw_b=%s, kw_c=%s, kw_all=%s, embedding=%s::vector, embedding_sig=%s "
        "WHERE id=%s",
        (kw_a, kw_b, kw_c, list(dict.fromkeys(kw_a + kw_b + kw_c)), vec,
         settings.embedding_signature if vec else None, rule_id),
    )


def list_rules(owner: str | None = None, all_owners: bool = True) -> list[dict]:
    """all_owners=True（管理员）：全部规则；否则：通用规则 + owner 本人的业务场景规则。"""
    if all_owners:
        return db.fetchall(f"SELECT {SR_COLS} FROM style_rule.style_rule "
                           "ORDER BY (scope='scenario'), business_scenario NULLS FIRST, created_by NULLS FIRST, priority, id")
    return db.fetchall(f"SELECT {SR_COLS} FROM style_rule.style_rule WHERE scope='general' OR created_by=%s "
                       "ORDER BY (scope='scenario'), business_scenario NULLS FIRST, priority, id", (owner,))


def get_rule(rule_id: int) -> dict:
    return _one(db.fetchone(f"SELECT {SR_COLS} FROM style_rule.style_rule WHERE id=%s", (rule_id,)), "样式规则")


def create_rule(data: dict) -> dict:
    _validate_rule(data)
    items, content, theme = _rule_payload(data)
    row = db.fetchone(
        "INSERT INTO style_rule.style_rule(name, scope, business_scenario, content, items, theme, priority, enabled, "
        "created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (data["name"].strip(), data["scope"], data.get("business_scenario") if data["scope"] == "scenario" else None,
         content, db.jsonb(items), db.jsonb(theme), int(data.get("priority", 100)), data.get("enabled", True),
         data.get("created_by")),
    )
    reindex_rule(row["id"])
    return get_rule(row["id"])


def update_rule(rule_id: int, data: dict) -> dict:
    from . import design

    current = get_rule(rule_id)
    merged = {**current, **data}
    _validate_rule(merged)
    if "items" in data and "content" not in data:
        merged["content"] = ""
    items, content, theme = _rule_payload(merged)
    # 保存新样式时保留旧分析要求原文；它们只在管理页的历史条目中可见。
    items.extend(it for it in current["items"] if it["category"] in design.RETIRED_ANALYSIS_CATEGORIES)
    db.execute(
        "UPDATE style_rule.style_rule SET name=%s, scope=%s, business_scenario=%s, content=%s, items=%s, theme=%s, "
        "priority=%s, enabled=%s, updated_at=now() WHERE id=%s",
        (merged["name"].strip(), merged["scope"],
         merged.get("business_scenario") if merged["scope"] == "scenario" else None, content, db.jsonb(items),
         db.jsonb(theme), int(merged.get("priority", 100)), merged.get("enabled", True), rule_id),
    )
    reindex_rule(rule_id)
    return get_rule(rule_id)


def delete_rule(rule_id: int) -> None:
    db.execute("DELETE FROM style_rule.style_rule WHERE id=%s", (rule_id,))


def _rule_layers(business_scenario: str | None, exclude_id: int | None = None, draft: dict | None = None,
                 owner: str | None = None) -> tuple[list[dict], list[tuple[str, str, dict]]]:
    """按 内置样式 → 通用规则 → 业务场景规则 的顺序返回生效规则与主题层（可排除正在编辑的规则并放入草稿）。
    业务场景规则只取 owner（报告创建人）本人配置的；owner 为空时只有通用规则。"""
    rows = db.fetchall(
        f"SELECT {SR_COLS} FROM style_rule.style_rule WHERE enabled AND "
        "(scope='general' OR (business_scenario=%s AND created_by=%s)) ORDER BY (scope='scenario'), priority, id",
        (business_scenario, owner),
    )
    rows = [r for r in rows if r["id"] != exclude_id]
    layers = [("general" if r["scope"] == "general" else "scenario", r["name"], r["theme"] or {}) for r in rows]
    if draft is not None:
        level = "general" if draft.get("scope") == "general" else "scenario"
        layer = (level, draft.get("name") or "（编辑中）", draft.get("theme") or {})
        idx = len([x for x in layers if x[0] == "general"]) if level == "general" else len(layers)
        layers.insert(idx, layer)
    return rows, layers


def effective_style(business_scenario: str | None, exclude_id: int | None = None, draft: dict | None = None,
                    owner: str | None = None) -> dict:
    from . import design

    _, layers = _rule_layers(business_scenario, exclude_id, draft, owner)
    value, source = design.resolve_with_sources(layers)
    return {"theme": value, "sources": source, "precedence": design.PRECEDENCE}


def style_rules_for(business_scenario: str | None, query: str | None = None, owner: str | None = None) -> dict:
    """通用规则 + owner 本人的业务场景规则（生效顺序：业务场景 ≥ 通用 ≥ 内置样式）。给出 query 时附带相关度排序。"""
    from . import design

    rows, layers = _rule_layers(business_scenario, owner=owner)
    relevance: dict[int, dict] = {}
    warnings: list[str] = []
    if query:
        from .search import hybrid

        res = hybrid("style_rule", query, top_k=50, where="AND (t.scope='general' OR t.business_scenario=%(sc)s)",
                     params={"sc": business_scenario})
        warnings = res["warnings"]
        relevance = {h["id"]: h for h in res["hits"]}
    theme, sources = design.resolve_with_sources(layers)
    cat_label = {c["key"]: c["label"] for c in design.ITEM_CATEGORIES}
    for r in rows:
        stored_items = r.get("items") or []
        active_items = [it for it in stored_items if it["category"] not in design.RETIRED_ANALYSIS_CATEGORIES]
        if len(active_items) != len(stored_items):
            # 生效接口同时过滤旧条目和由它们生成的说明文本。
            r["content"] = design.items_to_markdown(r["name"], active_items) if active_items else ""
        r["items"] = [{**it, "category_label": cat_label.get(it["category"], "其他展示要求")} for it in active_items]
        if r["id"] in relevance:
            r["relevance"] = relevance[r["id"]]
    return {
        "business_scenario": business_scenario,
        "precedence": design.PRECEDENCE,
        # 事实约束单独返回，不参与可调整的视觉样式优先级，也不写入用户规则表。
        "reporting_rules": design.builtin_reporting_rules(),
        # 指定样式：内置样式 + 通用规则 + 业务场景规则 按优先级合并后的唯一结果，作为对智能体的约束
        "specified": {
            "theme": theme,
            "theme_sources": {k: design.source_text(sources[k]) for k in theme},
            "items": design.merge_items(rows),
        },
        "general_rules": [r for r in rows if r["scope"] == "general"],
        "scenario_rules": [r for r in rows if r["scope"] == "scenario"],
        "warnings": warnings,
    }


# ============================ 报告模板 ============================
TPL_COLS = ("t.id, t.name, t.dataset_id, t.user_requirement, t.analysis_goal, t.business_summary, t.summary, t.version, t.created_at, "
            "t.updated_at, t.created_by, t.template_type, d.business_scenario, "
            "(SELECT e.name FROM permission.employee e WHERE e.emp_no = t.created_by) AS creator_name")
TPL_DETAIL = (f"SELECT {TPL_COLS}, t.markdown FROM report_template.report_template t "
              "LEFT JOIN dataset.dataset d ON d.id=t.dataset_id WHERE t.id=%s")


def list_templates(q: str | None = None, owner: str | None = None) -> list[dict]:
    """owner 不为 None 时只返回该工号创建的模板。"""
    conds, params = [], []
    if q and q.strip():
        conds.append("(t.name ILIKE %s OR t.user_requirement ILIKE %s OR t.business_summary ILIKE %s "
                     "OR t.analysis_goal ILIKE %s OR d.business_scenario ILIKE %s)")
        params += [_contains_pattern(q)] * 5
    if owner is not None:
        conds.append("t.created_by = %s")
        params.append(owner)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    return db.fetchall(
        f"SELECT {TPL_COLS} FROM report_template.report_template t LEFT JOIN dataset.dataset d ON d.id=t.dataset_id "
        f"{where} ORDER BY t.updated_at DESC", params)


def get_template(tpl_id: int) -> dict:
    return _one(db.fetchone(TPL_DETAIL, (tpl_id,)), "报告模板")


def get_template_version(tpl_id: int, version: int) -> dict:
    return _one(db.fetchone(
        "SELECT version, markdown, created_at FROM report_template.report_template_version WHERE template_id=%s AND version=%s",
        (tpl_id, version),
    ), "模板版本")


def template_versions(tpl_id: int) -> list[dict]:
    return db.fetchall(
        "SELECT version, created_at, length(markdown) AS size FROM report_template.report_template_version "
        "WHERE template_id=%s ORDER BY version DESC",
        (tpl_id,),
    )


def save_template(data: dict, tpl_id: int | None = None, *, expected_version: int | None = None) -> dict:
    from .template_parser import parse_template

    md = data.get("markdown")
    if not isinstance(md, str) or not md.strip() or "\x00" in md:
        raise Invalid("请提供有效的 Markdown 正文")
    if data.get("business_summary") is not None and not isinstance(data["business_summary"], str):
        raise Invalid("业务说明须为文本")
    with db.conn() as c, c.transaction():
        cur = _one(c.execute("SELECT * FROM report_template.report_template WHERE id=%s FOR UPDATE", (tpl_id,)).fetchone(),
                   "报告模板") if tpl_id is not None else None
        if expected_version is not None and (not cur or cur["version"] != expected_version):
            raise ReportConflict("报告模板已被修改，请读取最新版本后再提交")
        # 模板类型在创建时确定，所有历史版本沿用同一种读取和使用方式。
        template_type = data.get("template_type", cur["template_type"] if cur else "structured")
        if template_type not in ("structured", "business"):
            raise Invalid("模板类型须为 structured 或 business")
        if cur and template_type != cur["template_type"]:
            raise Invalid("模板类型在创建后不可更改")
        if template_type == "business":
            from .report_spec import ReportSpec, decode_template

            decoded = decode_template(md)
            # 声明式模板保存完整可执行定义。历史业务 Markdown 仍按原文归档。
            title_match = re.search(r"^#[ \t]+([^\n]+)", md, re.M)
            title = title_match[1].strip() if title_match else ""
            summary = {"title": title, "sections": [heading.strip() for heading in re.findall(r"^##[ \t]+(.+)$", md, re.M)]}
            if decoded:
                spec, dataset_id = decoded
                if data.get("dataset_id", cur["dataset_id"] if cur else None) != dataset_id:
                    raise Invalid("声明式模板内容与关联场景必须一致")
                title = spec.title
                if isinstance(spec, ReportSpec):
                    summary = {"title": title, "sections": [part.title for part in spec.sections], "declarative": True}
                else:
                    summary = {"title": title, "modules": list(spec.modules),
                               "declarative": True, "report_mode": "free_html"}
            # 自由版式使用单独的模板语义，历史 Markdown 和既有结构化模板可以继续独立编辑。
            is_free = summary.get("report_mode") == "free_html"
            if cur and is_free != (cur["summary"].get("report_mode") == "free_html"):
                raise Invalid("自由版式与其他模板须分别保存")
            default_goal = ""
        else:
            tpl, errors, _warn = parse_template(md)
            if errors:
                raise Invalid("模板校验失败：\n- " + "\n- ".join(errors))
            title, summary, default_goal = tpl.title, tpl.summary(), tpl.meta.get("analysis_goal") or ""
        name = (data.get("name") or title).strip()
        if not name:
            raise Invalid("模板名称不能为空")
        summary["title"] = title or name
        if cur:
            # 业务说明是管理信息，历史版本只记录 Markdown 正文的变更。
            version = cur["version"] + (1 if cur["markdown"] != md else 0)
            c.execute(
                "UPDATE report_template.report_template SET name=%s, dataset_id=%s, user_requirement=%s, "
                "analysis_goal=%s, business_summary=%s, markdown=%s, summary=%s, version=%s, updated_at=now() WHERE id=%s",
                (name, data.get("dataset_id", cur["dataset_id"]),
                 data.get("user_requirement", cur["user_requirement"]) or "",
                 data.get("analysis_goal", cur["analysis_goal"]) or default_goal,
                 (data.get("business_summary", cur["business_summary"]) or "").strip(),
                 md, db.jsonb(summary), version, tpl_id),
            )
        else:
            version = 1
            tpl_id = c.execute(
                "INSERT INTO report_template.report_template(name, dataset_id, user_requirement, analysis_goal, business_summary, "
                "markdown, summary, created_by, template_type) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (name, data.get("dataset_id"), data.get("user_requirement") or "",
                 data.get("analysis_goal") or default_goal,
                 (data.get("business_summary") or "").strip(), md, db.jsonb(summary),
                 data.get("created_by"), template_type),
            ).fetchone()["id"]
        c.execute(
            "INSERT INTO report_template.report_template_version(template_id, version, markdown) VALUES (%s,%s,%s) "
            "ON CONFLICT DO NOTHING",
            (tpl_id, version, md),
        )
        # 在锁释放前读取此次保存的版本，避免 agent 并行更新时拿到其他版本号。
        return c.execute(TPL_DETAIL, (tpl_id,)).fetchone()


def delete_template(tpl_id: int) -> None:
    db.execute("DELETE FROM report_template.report_template WHERE id=%s", (tpl_id,))


# ============================ 报告 ============================
REPORT_COLS = (
    "r.id, v.name, v.template_id, v.template_version, v.meta, r.created_at, r.created_by, "
    "v.version AS revision, v.base_version, v.created_at AS version_created_at, v.created_by AS version_created_by, "
    "v.change_note, r.revision AS latest_version, length(v.html) AS size"
)
REPORT_SHARE_JOIN = " LEFT JOIN report.report_share s ON s.report_id=r.id AND s.emp_no=%s"
REPORT_SELECTION_JOIN = " LEFT JOIN report.report_selection sel ON sel.report_id=r.id AND sel.emp_no=%s"


def report_links(report_id: int, version: int | None = None) -> dict:
    base = f"{settings.public_base_url}/reports/{report_id}"
    suffix = f"?version={version}" if version is not None else ""
    return {"url": base + suffix, "content_url": f"{base}/content{suffix}", "download_url": f"{base}/download{suffix}"}


def _report_access(principal: Principal, row: dict) -> str | None:
    # 管理员及创建人可管理分享；分享覆盖报告的所有版本。
    if not principal.registered:
        return None
    if principal.is_admin or row["created_by"] == principal.emp_no:
        return "manage"
    return row.get("access_level")


def _report_target(connection, report_id: int) -> tuple[int, int | None]:
    alias = connection.execute(
        "SELECT report_id, version FROM report.report_alias WHERE old_id=%s", (report_id,)
    ).fetchone()
    return (alias["report_id"], alias["version"]) if alias else (report_id, None)


def list_reports(principal: Principal, template_id: int | None = None, q: str | None = None) -> list[dict]:
    if not principal.registered:
        return []
    sql = (f"SELECT {REPORT_COLS}, t.name AS template_name, e.name AS creator_name, s.access_level, "
           "COALESCE(sel.version,r.revision) AS current_version, (sel.version IS NOT NULL) AS version_selected "
           "FROM report.report r" + REPORT_SHARE_JOIN + REPORT_SELECTION_JOIN +
           " JOIN report.report_version v ON v.report_id=r.id AND v.version=COALESCE(sel.version,r.revision) "
           "LEFT JOIN report_template.report_template t ON t.id=v.template_id "
           "LEFT JOIN permission.employee e ON e.emp_no=r.created_by")
    conds, params = [], [principal.emp_no, principal.emp_no]
    if template_id is not None:
        conds.append("v.template_id=%s")
        params.append(template_id)
    if not principal.is_admin:
        conds.append("(r.created_by=%s OR s.emp_no IS NOT NULL)")
        params.append(principal.emp_no)
    if q and q.strip():
        conds.append("(v.name ILIKE %s OR t.name ILIKE %s OR e.name ILIKE %s OR r.created_by ILIKE %s)")
        params += [_contains_pattern(q)] * 4
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    rows = db.fetchall(sql + where + " ORDER BY r.id DESC", params)
    visible = []
    for row in rows:
        try:
            _require_report_data(row, principal)
        except PermissionDenied:
            continue
        row["access_level"] = _report_access(principal, row)
        visible.append(row)
    return visible


def _require_report_data(row: dict, principal: Principal) -> None:
    from .report_spec import validate_scopes

    scopes = row["meta"].get("scopes")
    if scopes:
        validate_scopes(scopes, principal)
    elif not principal.is_admin and row["created_by"] != principal.emp_no:
        raise PermissionDenied("此历史报告缺少数据来源，需按当前权限重新生成后才能共享")


def template_from_report(template_id: int, principal: Principal, report_id: int, version: int | None) -> dict:
    # 只开放与该协作版本绑定的声明式模板；历史 Markdown 可能含业务原始值和未授权 SQL。
    row = get_report(report_id, principal, with_html=False, version=version, with_source=True)
    if row["access_level"] not in ("manage", "edit") or row["template_id"] != template_id or row["meta"].get("source") != "compiled_spec":
        raise PermissionDenied("无权读取该报告关联的模板版本")
    return {**get_template_version(template_id, row["template_version"]), "id": template_id,
            "template_type": "business"}


def get_report(report_id: int, principal: Principal, with_html: bool = True, *,
               version: int | None = None, with_source: bool = False) -> dict:
    cols = REPORT_COLS + (", v.html" if with_html else "")
    with db.conn() as c:
        report_id, legacy_version = _report_target(c, report_id)
        row = _one(c.execute(
            f"SELECT {cols}, s.access_level, COALESCE(sel.version,r.revision) AS current_version, "
            "(sel.version IS NOT NULL) AS version_selected, v.markdown IS NOT NULL AND "
            "(v.source_owner=%s OR %s) AS source_available, "
            "CASE WHEN %s AND (v.source_owner=%s OR %s) THEN v.markdown END AS markdown, "
            "v.dataset_id, v.source_owner "
            f"FROM report.report r{REPORT_SHARE_JOIN}{REPORT_SELECTION_JOIN} "
            "LEFT JOIN report.report_version v ON v.report_id=r.id AND v.version=COALESCE(%s,sel.version,r.revision) "
            "WHERE r.id=%s",
            (principal.emp_no, principal.is_admin, with_source, principal.emp_no, principal.is_admin,
             principal.emp_no, principal.emp_no, version if version is not None else legacy_version, report_id)
        ).fetchone(), "报告")
    row["access_level"] = _report_access(principal, row)
    if row["access_level"] is None:
        raise PermissionDenied("无权查看该报告")
    if row["revision"] is None:
        raise NotFound("报告版本不存在")
    _require_report_data(row, principal)
    if row["access_level"] in ("manage", "edit") and row["meta"].get("source") == "compiled_spec":
        row["source_available"] = True
        if with_source and row["markdown"] is None:
            # 仅在来源通过当前授权复核后开放该版本的声明式定义。
            stored = db.fetchone("SELECT markdown FROM report.report_version WHERE report_id=%s AND version=%s",
                                 (row["id"], row["revision"]))
            row["markdown"] = stored["markdown"] if stored else None
    if row["access_level"] == "view":
        row["source_available"] = False
        row["markdown"] = None
    if not row["source_available"]:
        row["source_owner"] = None
    if not with_source:
        del row["markdown"]
        del row["source_owner"]
    return row


def list_report_versions(report_id: int, principal: Principal) -> dict:
    report = get_report(report_id, principal, with_html=False)
    versions = db.fetchall(
        "SELECT v.version, v.base_version, v.name, v.created_at, v.created_by, v.change_note, v.meta, "
        "e.name AS creator_name FROM report.report_version v LEFT JOIN permission.employee e ON e.emp_no=v.created_by "
        "WHERE v.report_id=%s ORDER BY v.version DESC", (report["id"],))
    report["versions"] = []
    for version in versions:
        try:
            _require_report_data({"meta": version.pop("meta"), "created_by": report["created_by"]}, principal)
        except PermissionDenied:
            continue
        report["versions"].append(version)
    return report


def report_spec_hash(html: str) -> str:
    from hashlib import sha256

    return sha256(html.encode()).hexdigest()


def _report_name(name: str) -> str:
    name = name.strip()
    if not name or "\x00" in name:
        raise Invalid("请输入有效的报告名称")
    return name


def _insert_report_version(c, report_id: int, version: int, base_version: int | None, name: str, html: str,
                           meta: dict, template_id: int | None, template_version: int | None,
                           source: dict, created_by: str | None, change_note: str = "") -> None:
    c.execute(
        "INSERT INTO report.report_version(report_id, version, base_version, name, html, meta, template_id, "
        "template_version, markdown, dataset_id, source_owner, created_by, change_note) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (report_id, version, base_version, name, html, db.jsonb(meta), template_id, template_version,
         source.get("markdown"), source.get("dataset_id"), source.get("source_owner"), created_by, change_note),
    )


def save_report(name: str, html: str, template_id: int | None, template_version: int | None, meta: dict,
                created_by: str | None = None, *, source: dict | None = None) -> dict:
    # 版本库只接收已验收的精确字节，旧格式可读但不能通过另一个入口发布。
    if (not source or meta.get("source") != "compiled_spec" or not meta.get("scopes") or
            meta.get("verification", {}).get("html_sha256") != report_spec_hash(html)):
        raise Invalid("新报告必须提交服务端验收的预览")
    if error := report_document_error(html):
        raise Invalid(error)
    name = _report_name(name)
    meta = {**meta, "html_bytes": len(html.encode()), "single_file": True}
    with db.conn() as c, c.transaction():
        row = c.execute(
            "INSERT INTO report.report(name, template_id, template_version, html, meta, created_by) "
            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id, name, template_id, template_version, meta, created_at, created_by",
            (name, template_id, template_version, html, db.jsonb(meta), created_by),
        ).fetchone()
        _insert_report_version(c, row["id"], 1, None, name, html, meta, template_id, template_version,
                               source or {}, created_by)
    return {**row, "size": len(html), "revision": 1, "latest_version": 1, "current_version": 1,
            "base_version": None, **report_links(row["id"])}


class ReportConflict(RuntimeError):
    pass


def _lock_report(connection, report_id: int) -> dict:
    # 编辑、选择及授权变更锁定同一行。等待期间撤销的权限会在后续查询中生效。
    return _one(connection.execute(
        "SELECT id, created_by, revision FROM report.report WHERE id=%s FOR UPDATE", (report_id,)).fetchone(), "报告")


def _locked_report_access(c, report: dict, principal: Principal) -> str | None:
    if _report_access(principal, report) == "manage":
        return "manage"
    share = c.execute("SELECT access_level FROM report.report_share WHERE report_id=%s AND emp_no=%s",
                      (report["id"], principal.emp_no)).fetchone()
    return _report_access(principal, {**report, **(share or {})})


def _select_report_version(c, report_id: int, emp_no: str, version: int | None) -> None:
    if version is None:
        c.execute("DELETE FROM report.report_selection WHERE report_id=%s AND emp_no=%s", (report_id, emp_no))
        return
    _one(c.execute("SELECT version FROM report.report_version WHERE report_id=%s AND version=%s",
                   (report_id, version)).fetchone(), "报告版本")
    c.execute(
        "INSERT INTO report.report_selection(report_id, emp_no, version) VALUES (%s,%s,%s) "
        "ON CONFLICT (report_id, emp_no) DO UPDATE SET version=EXCLUDED.version", (report_id, emp_no, version),
    )


def select_report_version(report_id: int, principal: Principal, version: int | None) -> dict:
    with db.conn() as c, c.transaction():
        report_id, _ = _report_target(c, report_id)
        report = _lock_report(c, report_id)
        if _locked_report_access(c, report, principal) is None:
            raise PermissionDenied("无权查看该报告")
        selected = _one(c.execute("SELECT meta FROM report.report_version WHERE report_id=%s AND version=%s",
                                  (report_id, version or report["revision"])).fetchone(), "报告版本")
        _require_report_data({**report, **selected}, principal)
        _select_report_version(c, report_id, principal.emp_no, version)
    return get_report(report_id, principal, with_html=False)


def update_report(report_id: int, principal: Principal, name: str, html: str | None, base_version: int | None = None,
                  *, expected_version: int | None = None, change_note: str = "",
                  rendered: tuple[dict, dict] | None = None) -> dict:
    name = _report_name(name)
    change_note = change_note.strip()
    if len(change_note) > 500 or "\x00" in change_note:
        raise Invalid("修改说明最多 500 个字符")
    with db.conn() as c, c.transaction():
        report_id, legacy_version = _report_target(c, report_id)
        row = _lock_report(c, report_id)
        access = _locked_report_access(c, row, principal)
        if access not in ("manage", "edit"):
            raise PermissionDenied("无权编辑该报告")
        if expected_version is not None and expected_version != row["revision"]:
            raise ReportConflict("报告已被更新，请读取修改基准后重新保存")
        if base_version is None and legacy_version is not None:
            base_version = legacy_version
        if base_version is None:
            # 默认基准为该员工当前选择；历史链接仍可直接指定其原始快照。
            selected = c.execute("SELECT version FROM report.report_selection WHERE report_id=%s AND emp_no=%s",
                                 (report_id, principal.emp_no)).fetchone()
            base_version = selected["version"] if selected else row["revision"]
        base = _one(c.execute("SELECT * FROM report.report_version WHERE report_id=%s AND version=%s",
                              (report_id, base_version)).fetchone(), "报告版本")
        _require_report_data({**base, "created_by": row["created_by"]}, principal)
        meta, source = (base["meta"], base) if rendered is None else rendered
        if rendered is None:
            # 管理台仍可更新名称或说明。业务正文变更须经过新编译和验收。
            if html is not None and html not in (base["html"], report_data.for_edit(base["html"])):
                raise Invalid("正文变更请用声明式模板生成已验收的修订预览")
            html = base["html"]
        else:
            if (meta.get("source") != "compiled_spec" or not meta.get("scopes") or
                    meta.get("verification", {}).get("html_sha256") != report_spec_hash(html)):
                raise Invalid("修订内容与验收证据不一致")
            _require_report_data({"meta": meta, "created_by": row["created_by"]}, principal)
            if row["created_by"] != principal.emp_no and row["created_by"]:
                from .auth import principal_for

                _require_report_data({"meta": meta, "created_by": row["created_by"]}, principal_for(row["created_by"]))
        if rendered is not None and (error := report_document_error(html)):
            raise Invalid(error)
        meta = {**meta, "html_bytes": len(html.encode()),
                "single_file": rendered is not None or meta.get("single_file", False)}
        version = row["revision"] + 1
        template_id, template_version = source.get("template_id", base["template_id"]), source["template_version"]
        _insert_report_version(c, report_id, version, base_version, name, html, meta, template_id,
                               template_version, source, principal.emp_no, change_note)
        # 保存后让编辑者跟随最新版本，其他员工原有的选择照常保留。
        _select_report_version(c, report_id, principal.emp_no, None)
        c.execute(
            "UPDATE report.report SET name=%s, html=%s, meta=%s, template_id=%s, template_version=%s, revision=%s "
            "WHERE id=%s",
            (name, html, db.jsonb(meta), template_id, template_version, version, report_id),
        )
    return {**get_report(report_id, principal, with_html=False, version=version), **report_links(report_id, version)}


def _require_report_manager(principal: Principal, row: dict) -> None:
    if _report_access(principal, row) != "manage":
        raise PermissionDenied("只有报告创建人和管理员可以管理分享或删除报告")


def delete_report(report_id: int, principal: Principal) -> None:
    with db.conn() as c, c.transaction():
        _require_report_manager(principal, _lock_report(c, report_id))
        c.execute("DELETE FROM report.report WHERE id=%s", (report_id,))


def list_report_shares(report_id: int, principal: Principal) -> list[dict]:
    report = get_report(report_id, principal, with_html=False)
    _require_report_manager(principal, report)
    return db.fetchall(
        "SELECT s.emp_no, COALESCE(e.name,'') AS name, COALESCE(e.enabled,false) AS enabled, "
        "(e.emp_no IS NOT NULL) AS registered, s.access_level FROM report.report_share s "
        "LEFT JOIN permission.employee e ON e.emp_no=s.emp_no WHERE s.report_id=%s ORDER BY s.emp_no", (report["id"],))


def search_report_recipients(report_id: int, principal: Principal, q: str) -> list[dict]:
    report = get_report(report_id, principal, with_html=False)
    _require_report_manager(principal, report)
    if "\x00" in q:
        raise Invalid("请输入有效的姓名或工号")
    # 仅提供分享所需的目录字段，工号精确匹配优先，其余结果可继续缩小搜索范围。
    pattern = _contains_pattern(q)
    return db.fetchall(
        "SELECT emp_no, name FROM permission.employee WHERE enabled AND emp_no IS DISTINCT FROM %s "
        "AND (emp_no ILIKE %s OR name ILIKE %s) "
        "ORDER BY (emp_no = %s) DESC, name, emp_no LIMIT 20",
        (report["created_by"], pattern, pattern, q.strip()))


def set_report_share(report_id: int, principal: Principal, emp_no: str, access_level: str) -> dict:
    emp_no = emp_no.strip()
    if not emp_no or emp_no in (".", "..") or re.search(r"[\x00-\x1f\x7f/\\]", emp_no):
        raise Invalid("请输入有效的员工工号")
    with db.conn() as c, c.transaction():
        report = _lock_report(c, report_id)
        _require_report_manager(principal, report)
        if emp_no == report["created_by"]:
            raise Invalid("报告创建人已拥有管理权限")
        employee = c.execute("SELECT emp_no, name, enabled, roles, is_admin FROM permission.employee WHERE emp_no=%s FOR SHARE",
                             (emp_no,)).fetchone()
        if not employee or not employee["enabled"]:
            raise Invalid("接收人需先登记并启用，才能核实报告数据权限")
        current = c.execute("SELECT meta FROM report.report_version WHERE report_id=%s AND version=%s",
                            (report_id, report["revision"])).fetchone()
        if not current or not current["meta"].get("scopes"):
            raise Invalid("报告缺少可验证来源，请通过声明式模板重新生成后分享")
        _require_report_data({**report, **current},
                             Principal(emp_no, is_admin=employee["is_admin"], roles=employee["roles"]))
        c.execute(
            "INSERT INTO report.report_share(report_id, emp_no, access_level) VALUES (%s,%s,%s) "
            "ON CONFLICT (report_id, emp_no) DO UPDATE SET access_level=EXCLUDED.access_level",
            (report_id, emp_no, access_level))
        return {"emp_no": emp_no, "name": employee["name"] if employee else "", "enabled": bool(employee),
                "registered": bool(employee), "access_level": access_level}


def revoke_report_share(report_id: int, principal: Principal, emp_no: str) -> None:
    with db.conn() as c, c.transaction():
        _require_report_manager(principal, _lock_report(c, report_id))
        c.execute("DELETE FROM report.report_share WHERE report_id=%s AND emp_no=%s", (report_id, emp_no.strip()))


# ============================ 其他 ============================
def reindex_all(only_stale: bool = False) -> dict:
    cond = " WHERE embedding_sig IS DISTINCT FROM %s" if only_stale else ""
    params = (settings.embedding_signature,) if only_stale else None
    ds_ids = [r["id"] for r in db.fetchall("SELECT id FROM dataset.dataset" + cond, params)]
    rule_ids = [r["id"] for r in db.fetchall("SELECT id FROM style_rule.style_rule" + cond, params)]
    for i in ds_ids:
        reindex_dataset(i)
    for i in rule_ids:
        reindex_rule(i)
    failed = db.fetchone(
        "SELECT (SELECT count(*) FROM dataset.dataset WHERE embedding IS NULL) + "
        "(SELECT count(*) FROM style_rule.style_rule WHERE embedding IS NULL) AS n"
    )["n"]
    if not failed:
        db.set_setting("embedding_signature", settings.embedding_signature)
    return {"datasets": len(ds_ids), "style_rules": len(rule_ids), "not_vectorized": failed}


def load_custom_words() -> None:
    rows = db.fetchall("SELECT business_scenario, tags FROM dataset.dataset")
    words = []
    for r in rows:
        words.append(r["business_scenario"])
        words.extend(r["tags"] or [])
    add_words(words)


def stats() -> dict:
    return db.fetchone(
        "SELECT (SELECT count(*) FROM datasource.datasource) AS datasources, "
        "(SELECT count(*) FROM dataset.dataset) AS datasets, "
        "(SELECT count(*) FROM style_rule.style_rule) AS style_rules, "
        "(SELECT count(*) FROM report_template.report_template) AS templates, "
        "(SELECT count(*) FROM report.report) AS reports, "
        "(SELECT count(*) FROM permission.employee) AS employees"
    )


# ============================ 权限：员工 / 授权 / 令牌 ============================
def list_employees() -> list[dict]:
    return db.fetchall(
        "SELECT e.*, (SELECT count(*) FROM permission.table_grant g WHERE g.subject_type='employee' AND g.subject=e.emp_no) "
        "AS direct_grants FROM permission.employee e ORDER BY e.emp_no")


def save_employee(data: dict, emp_no: str | None = None) -> dict:
    no = (emp_no or data.get("emp_no") or "").strip()
    if not no:
        raise Invalid("工号不能为空")
    # 权限和启用状态只接受 JSON 布尔值，避免把字符串 "false" 当成真值。
    for flag in ("is_admin", "enabled"):
        if flag in data and not isinstance(data[flag], bool):
            raise Invalid(f"{flag} 必须为布尔值（true 或 false）")
    name, department = data.get("name") or "", data.get("department") or ""
    roles = [r.strip() for r in data.get("roles") or [] if r and r.strip()]
    if emp_no:
        # 只改系统身份时保留数据角色和启用状态；显式的 false、空数组仍会写入。
        return _one(db.fetchone(
            "UPDATE permission.employee SET name=COALESCE(%s, name), department=COALESCE(%s, department), "
            "roles=COALESCE(%s, roles), is_admin=COALESCE(%s, is_admin), enabled=COALESCE(%s, enabled), "
            "updated_at=now() WHERE emp_no=%s RETURNING *",
            (name if "name" in data else None, department if "department" in data else None,
             roles if "roles" in data else None, data.get("is_admin"), data.get("enabled"), no)), "员工")
    if db.fetchone("SELECT 1 AS x FROM permission.employee WHERE emp_no=%s", (no,)):
        raise Invalid(f"工号 {no} 已存在")
    return db.fetchone("INSERT INTO permission.employee(emp_no, name, department, roles, is_admin, enabled) "
                       "VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
                       (no, name, department, roles, data.get("is_admin", False), data.get("enabled", True)))


def delete_employee(emp_no: str) -> None:
    # 删除员工时撤销数据直授和报告授权，浏览器会话由外键一并清理。
    with db.conn() as c, c.transaction():
        c.execute("DELETE FROM permission.table_grant WHERE subject_type='employee' AND subject=%s", (emp_no,))
        deleted = c.execute("DELETE FROM permission.employee WHERE emp_no=%s RETURNING emp_no", (emp_no,)).fetchone()
        if deleted:
            c.execute("DELETE FROM report.report_share WHERE emp_no=%s", (emp_no,))


def list_grants() -> list[dict]:
    return db.fetchall(
        "SELECT g.*, d.business_scenario, e.name AS employee_name FROM permission.table_grant g "
        "JOIN dataset.dataset d ON d.id=g.dataset_id "
        "LEFT JOIN permission.employee e ON g.subject_type='employee' AND e.emp_no=g.subject "
        "ORDER BY g.subject_type, g.subject, d.business_scenario, g.table_name")


def add_grants(data: dict) -> dict:
    st = data.get("subject_type")
    subject = (data.get("subject") or "").strip()
    if st not in ("employee", "role") or not subject:
        raise Invalid("请指定授权对象（员工工号或角色编码）")
    ds = get_dataset(int(data.get("dataset_id") or 0))
    tables = data.get("tables")
    # 空表单不能意外变成全表授权。全表范围由调用方显式提交 ["*"]。
    if not isinstance(tables, list) or not tables or not all(isinstance(table, str) for table in tables):
        raise Invalid("请指定要授权的数据表；全部表请显式提交 [\"*\"]")
    valid = {t["table_name"] for t in ds["tables"]}
    bad = [t for t in tables if t != "*" and t not in valid]
    if bad:
        raise Invalid(f"数据集「{ds['business_scenario']}」中不存在表：{bad}")
    if st == "employee" and not db.fetchone("SELECT 1 AS x FROM permission.employee WHERE emp_no=%s", (subject,)):
        raise Invalid(f"工号 {subject} 未登记，请先在员工中添加")
    chosen = ["*"] if "*" in tables else tables
    rows = db.fetchall(
        "INSERT INTO permission.table_grant(subject_type, subject, dataset_id, table_name) "
        "SELECT %s,%s,%s,table_name FROM unnest(%s::text[]) AS names(table_name) "
        "ON CONFLICT DO NOTHING RETURNING id", (st, subject, ds["id"], chosen))
    return {"added": len(rows)}


def delete_grant(grant_id: int) -> None:
    db.execute("DELETE FROM permission.table_grant WHERE id=%s", (grant_id,))


__all__ = ["NotFound", "Invalid", "ConnectorError"]
