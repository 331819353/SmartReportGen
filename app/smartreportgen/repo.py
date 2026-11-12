"""各类元数据的增删改查与检索索引维护。"""
from __future__ import annotations

import logging
from typing import Any

from . import crypto, db
from .config import settings
from .connectors import SUPPORTED_TYPES, ConnectorError, drop_connector
from .embedding import EmbeddingError, embed_many, to_pgvector
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
    for k in ("password", "token"):
        if k in cfg:
            raise Invalid(f"不要把 {k} 放在 config 中，请使用 secret 字段")


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


def list_datasets() -> list[dict]:
    rows = db.fetchall(
        "SELECT d.id, d.business_scenario, d.business_description, d.datasource_id, d.tags, d.enabled, "
        "d.created_at, d.updated_at, (d.embedding IS NOT NULL) AS vectorized, s.name AS datasource_name, "
        "s.type AS datasource_type, (SELECT count(*) FROM dataset.dataset_table t WHERE t.dataset_id=d.id) AS table_count "
        "FROM dataset.dataset d LEFT JOIN datasource.datasource s ON s.id=d.datasource_id ORDER BY d.id"
    )
    return rows


def get_dataset(dataset_id: int) -> dict:
    ds = _one(
        db.fetchone(
            "SELECT d.id, d.business_scenario, d.business_description, d.datasource_id, d.tags, d.enabled, "
            "d.created_at, d.updated_at, (d.embedding IS NOT NULL) AS vectorized, s.name AS datasource_name, "
            "s.type AS datasource_type FROM dataset.dataset d LEFT JOIN datasource.datasource s "
            "ON s.id=d.datasource_id WHERE d.id=%s",
            (dataset_id,),
        ),
        "数据集",
    )
    ds["tables"] = db.fetchall(
        "SELECT id, table_name, table_description, sort FROM dataset.dataset_table WHERE dataset_id=%s ORDER BY sort, id",
        (dataset_id,),
    )
    return ds


def _validate_dataset(data: dict) -> None:
    if not (data.get("business_scenario") or "").strip():
        raise Invalid("业务场景不能为空")
    if not (data.get("business_description") or "").strip():
        raise Invalid("业务描述不能为空（混合检索依赖它）")
    if not data.get("datasource_id"):
        raise Invalid("请选择数据源")
    names = [t.get("table_name", "").strip() for t in data.get("tables") or []]
    if any(not n for n in names):
        raise Invalid("数据表名不能为空")
    if len(set(names)) != len(names):
        raise Invalid("数据表名重复")


def _save_tables(c, dataset_id: int, tables: list[dict]) -> None:
    c.execute("DELETE FROM dataset.dataset_table WHERE dataset_id=%s", (dataset_id,))
    for i, t in enumerate(tables):
        c.execute(
            "INSERT INTO dataset.dataset_table(dataset_id, table_name, table_description, sort) VALUES (%s,%s,%s,%s)",
            (dataset_id, t["table_name"].strip(), t.get("table_description") or "", i),
        )


def create_dataset(data: dict) -> dict:
    _validate_dataset(data)
    with db.conn() as c, c.transaction():
        row = c.execute(
            "INSERT INTO dataset.dataset(business_scenario, business_description, datasource_id, tags, enabled) "
            "VALUES (%s,%s,%s,%s,%s) RETURNING id",
            (data["business_scenario"].strip(), data["business_description"].strip(), data["datasource_id"],
             [t.strip() for t in data.get("tags") or [] if t.strip()], data.get("enabled", True)),
        ).fetchone()
        _save_tables(c, row["id"], data.get("tables") or [])
    reindex_dataset(row["id"])
    return get_dataset(row["id"])


def update_dataset(dataset_id: int, data: dict) -> dict:
    cur = get_dataset(dataset_id)
    merged = {**cur, **data}
    _validate_dataset(merged)
    with db.conn() as c, c.transaction():
        c.execute(
            "UPDATE dataset.dataset SET business_scenario=%s, business_description=%s, datasource_id=%s, tags=%s, "
            "enabled=%s, updated_at=now() WHERE id=%s",
            (merged["business_scenario"].strip(), merged["business_description"].strip(), merged["datasource_id"],
             [t.strip() for t in merged.get("tags") or [] if t.strip()], merged.get("enabled", True), dataset_id),
        )
        if "tables" in data:
            _save_tables(c, dataset_id, data["tables"])
    if cur["business_scenario"] != merged["business_scenario"]:
        db.execute("UPDATE style_rule.style_rule SET business_scenario=%s WHERE business_scenario=%s",
                   (merged["business_scenario"], cur["business_scenario"]))
    reindex_dataset(dataset_id)
    return get_dataset(dataset_id)


def delete_dataset(dataset_id: int) -> None:
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
# 生效顺序：业务场景规则 ≥ 通用规则 ≥ 内置默认（同一层级内按 priority 升序，后者覆盖前者）
SR_COLS = ("id, name, scope, business_scenario, content, items, theme, priority, enabled, created_at, updated_at, "
           "created_by, (SELECT e.name FROM permission.employee e WHERE e.emp_no = created_by) AS creator_name, "
           "(embedding IS NOT NULL) AS vectorized")


def _clean_theme(theme: dict) -> dict:
    """只保留合法令牌，并规范化取值（规则只存它显式覆盖的设置）。"""
    from . import design

    if not isinstance(theme, dict):
        raise Invalid("theme 必须是对象")
    resolved, warns = design.resolve(theme)
    bad = [w for w in warns if "不是已知令牌" not in w]
    if bad:
        raise Invalid("；".join(bad))
    keys = {design.LEGACY_ALIASES.get(str(k).lstrip("-"), str(k).lstrip("-")) for k in theme}
    return {k: resolved[k] for k in keys if k in resolved}


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

    items = design.normalize_items(data.get("items"))
    content = design.items_to_markdown(data["name"].strip(), items) if items else (data.get("content") or "")
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
    merged = {**get_rule(rule_id), **data}
    _validate_rule(merged)
    items, content, theme = _rule_payload(merged)
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
    """按 内置默认 → 通用规则 → 业务场景规则 的顺序返回生效规则与主题层（可排除正在编辑的规则并放入草稿）。
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
    """通用规则 + owner 本人的业务场景规则（生效顺序：业务场景 ≥ 通用 ≥ 内置默认）。给出 query 时附带相关度排序。"""
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
    merged_theme, sources = design.resolve_with_sources(layers)
    cat_label = {c["key"]: c["label"] for c in design.ITEM_CATEGORIES}
    for r in rows:
        r["items"] = [{**it, "category_label": cat_label.get(it["category"], "其他")} for it in r.get("items") or []]
        if r["id"] in relevance:
            r["relevance"] = relevance[r["id"]]
    return {
        "business_scenario": business_scenario,
        "general_rules": [r for r in rows if r["scope"] == "general"],
        "scenario_rules": [r for r in rows if r["scope"] == "scenario"],
        "merged_theme": merged_theme,
        "overridden_by": {k: v for k, v in sources.items() if v["level"] != "default"},
        "precedence": design.PRECEDENCE,
        "merge_order": "内置默认 → 通用规则 → 业务场景规则（后者覆盖前者；规则条目冲突时以业务场景规则为准）",
        "warnings": warnings,
    }


# ============================ 报告模板 ============================
TPL_COLS = ("t.id, t.name, t.dataset_id, t.user_requirement, t.analysis_goal, t.summary, t.version, t.created_at, "
            "t.updated_at, t.created_by, d.business_scenario, "
            "(SELECT e.name FROM permission.employee e WHERE e.emp_no = t.created_by) AS creator_name")


def list_templates(q: str | None = None, owner: str | None = None) -> list[dict]:
    """owner 不为 None 时只返回该工号创建的模板。"""
    conds, params = [], []
    if q:
        conds.append("(t.name ILIKE %s OR t.user_requirement ILIKE %s OR d.business_scenario ILIKE %s)")
        params += [f"%{q}%"] * 3
    if owner is not None:
        conds.append("t.created_by = %s")
        params.append(owner)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    return db.fetchall(
        f"SELECT {TPL_COLS} FROM report_template.report_template t LEFT JOIN dataset.dataset d ON d.id=t.dataset_id "
        f"{where} ORDER BY t.updated_at DESC", params)


def get_template(tpl_id: int) -> dict:
    return _one(
        db.fetchone(
            f"SELECT {TPL_COLS}, t.markdown FROM report_template.report_template t "
            "LEFT JOIN dataset.dataset d ON d.id=t.dataset_id WHERE t.id=%s",
            (tpl_id,),
        ),
        "报告模板",
    )


def template_versions(tpl_id: int) -> list[dict]:
    return db.fetchall(
        "SELECT version, created_at, length(markdown) AS size FROM report_template.report_template_version "
        "WHERE template_id=%s ORDER BY version DESC",
        (tpl_id,),
    )


def save_template(data: dict, tpl_id: int | None = None) -> dict:
    from .template_parser import parse_template

    md = data.get("markdown") or ""
    tpl, errors, _warn = parse_template(md)
    if errors:
        raise Invalid("模板校验失败：\n- " + "\n- ".join(errors))
    name = (data.get("name") or tpl.title or "").strip()
    if not name:
        raise Invalid("模板名称不能为空")
    summary = tpl.summary()
    with db.conn() as c, c.transaction():
        if tpl_id:
            cur = c.execute("SELECT * FROM report_template.report_template WHERE id=%s FOR UPDATE", (tpl_id,)).fetchone()
            if not cur:
                raise NotFound("报告模板不存在")
            version = cur["version"] + (1 if cur["markdown"] != md else 0)
            c.execute(
                "UPDATE report_template.report_template SET name=%s, dataset_id=%s, user_requirement=%s, "
                "analysis_goal=%s, markdown=%s, summary=%s, version=%s, updated_at=now() WHERE id=%s",
                (name, data.get("dataset_id", cur["dataset_id"]),
                 data.get("user_requirement", cur["user_requirement"]) or "",
                 data.get("analysis_goal", cur["analysis_goal"]) or tpl.meta.get("analysis_goal") or "",
                 md, db.jsonb(summary), version, tpl_id),
            )
        else:
            version = 1
            tpl_id = c.execute(
                "INSERT INTO report_template.report_template(name, dataset_id, user_requirement, analysis_goal, markdown, "
                "summary, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (name, data.get("dataset_id"), data.get("user_requirement") or "",
                 data.get("analysis_goal") or tpl.meta.get("analysis_goal") or "", md, db.jsonb(summary),
                 data.get("created_by")),
            ).fetchone()["id"]
        c.execute(
            "INSERT INTO report_template.report_template_version(template_id, version, markdown) VALUES (%s,%s,%s) "
            "ON CONFLICT DO NOTHING",
            (tpl_id, version, md),
        )
    return get_template(tpl_id)


def delete_template(tpl_id: int) -> None:
    db.execute("DELETE FROM report_template.report_template WHERE id=%s", (tpl_id,))


# ============================ 报告 ============================
def list_reports(template_id: int | None = None, owner: str | None = None) -> list[dict]:
    sql = ("SELECT r.id, r.name, r.template_id, r.template_version, r.meta, r.created_at, length(r.html) AS size, "
           "r.created_by, t.name AS template_name, "
           "(SELECT e.name FROM permission.employee e WHERE e.emp_no = r.created_by) AS creator_name "
           "FROM report.report r LEFT JOIN report_template.report_template t ON t.id=r.template_id")
    conds, params = [], []
    if template_id:
        conds.append("r.template_id=%s")
        params.append(template_id)
    if owner is not None:
        conds.append("r.created_by=%s")
        params.append(owner)
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    return db.fetchall(sql + where + " ORDER BY r.id DESC", params)


def get_report(report_id: int, with_html: bool = True) -> dict:
    cols = "*" if with_html else "id, name, template_id, template_version, meta, created_at, created_by, length(html) AS size"
    return _one(db.fetchone(f"SELECT {cols} FROM report.report WHERE id=%s", (report_id,)), "报告")


def save_report(name: str, html: str, template_id: int | None, template_version: int | None, meta: dict,
                created_by: str | None = None) -> dict:
    if not html or "<html" not in html[:2000].lower():
        raise Invalid("html 内容无效")
    row = db.fetchone(
        "INSERT INTO report.report(name, template_id, template_version, html, meta, created_by) "
        "VALUES (%s,%s,%s,%s,%s,%s) "
        "RETURNING id, name, template_id, template_version, meta, created_at, created_by, length(html) AS size",
        (name, template_id, template_version, html, db.jsonb(meta), created_by),
    )
    row["url"] = f"{settings.public_base_url}/reports/{row['id']}"
    return row


def delete_report(report_id: int) -> None:
    db.execute("DELETE FROM report.report WHERE id=%s", (report_id,))


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
    roles = [r.strip() for r in data.get("roles") or [] if r and r.strip()]
    if emp_no:
        _one(db.fetchone("SELECT 1 AS x FROM permission.employee WHERE emp_no=%s", (no,)), "员工")
        db.execute("UPDATE permission.employee SET name=%s, department=%s, roles=%s, is_admin=%s, enabled=%s, "
                   "updated_at=now() WHERE emp_no=%s",
                   (data.get("name") or "", data.get("department") or "", roles, bool(data.get("is_admin")),
                    data.get("enabled", True), no))
    else:
        if db.fetchone("SELECT 1 AS x FROM permission.employee WHERE emp_no=%s", (no,)):
            raise Invalid(f"工号 {no} 已存在")
        db.execute("INSERT INTO permission.employee(emp_no, name, department, roles, is_admin, enabled) "
                   "VALUES (%s,%s,%s,%s,%s,%s)",
                   (no, data.get("name") or "", data.get("department") or "", roles, bool(data.get("is_admin")),
                    data.get("enabled", True)))
    return db.fetchone("SELECT * FROM permission.employee WHERE emp_no=%s", (no,))


def delete_employee(emp_no: str) -> None:
    db.execute("DELETE FROM permission.table_grant WHERE subject_type='employee' AND subject=%s", (emp_no,))
    db.execute("UPDATE permission.access_token SET revoked=true WHERE emp_no=%s", (emp_no,))
    db.execute("DELETE FROM permission.employee WHERE emp_no=%s", (emp_no,))


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
    tables = data.get("tables") or ["*"]
    valid = {t["table_name"] for t in ds["tables"]}
    bad = [t for t in tables if t != "*" and t not in valid]
    if bad:
        raise Invalid(f"数据集「{ds['business_scenario']}」中不存在表：{bad}")
    if st == "employee" and not db.fetchone("SELECT 1 AS x FROM permission.employee WHERE emp_no=%s", (subject,)):
        raise Invalid(f"工号 {subject} 未登记，请先在员工中添加")
    n = 0
    for t in ("*",) if "*" in tables else tables:
        row = db.fetchone(
            "INSERT INTO permission.table_grant(subject_type, subject, dataset_id, table_name) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT DO NOTHING RETURNING id", (st, subject, ds["id"], t))
        n += 1 if row else 0
    return {"added": n}


def delete_grant(grant_id: int) -> None:
    db.execute("DELETE FROM permission.table_grant WHERE id=%s", (grant_id,))


def list_tokens(emp_no: str | None = None) -> list[dict]:
    sql = ("SELECT t.jti, t.emp_no, e.name, t.note, t.expires_at, t.revoked, t.created_at, t.last_used_at, "
           "(t.expires_at < now()) AS expired FROM permission.access_token t "
           "LEFT JOIN permission.employee e ON e.emp_no=t.emp_no")
    if emp_no:
        return db.fetchall(sql + " WHERE t.emp_no=%s ORDER BY t.created_at DESC", (emp_no,))
    return db.fetchall(sql + " ORDER BY t.created_at DESC")


def revoke_token(jti: str) -> None:
    db.execute("UPDATE permission.access_token SET revoked=true WHERE jti=%s", (jti,))


__all__ = ["NotFound", "Invalid", "ConnectorError"]
