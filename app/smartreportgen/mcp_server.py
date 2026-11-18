"""SmartReportGen MCP 服务（Streamable HTTP，路径 /mcp）。

认证：每个请求携带海尔 IAM access token；经 IAM 核实的工号用于查询权限表（permission schema），
      决定可检索的业务场景与可访问的数据表。数据访问一律默认拒绝。
工具按智能报告生成流程组织：
  a. search_business_scenarios → get_dataset_tables
  b. describe_tables / describe_table / run_query
  d. get_style_rules
  d 与 e 之间. save_report_template → get_report_template
  e. agent 按已保存的业务模板开发 HTML → preview_report_html → save_report
  f. get_report / revise_report
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

import uvicorn
from fastapi.encoders import jsonable_encoder
from mcp.server.fastmcp import Context, FastMCP
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from . import auth, db, design, mcp_oauth, repo, report_data, services
from .auth import AuthError, Principal
from .config import settings
from .connectors import ConnectorError
from .permission import Access, PermissionDenied, access_for
from .renderer import RenderError, validate as validate_md
from .report_document import inline_legacy_echarts, report_document_error
from .template_parser import TemplateError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("smartreportgen.mcp")

SKILL_DIR = Path(__file__).with_name("docs") / "skill"
SKILL_FILE = SKILL_DIR / "SKILL.md"
SPEC_FILE = SKILL_DIR / "references" / "template-spec.md"
EXAMPLE_FILE = SKILL_DIR / "references" / "example-report.html"


def _skill_body() -> str:
    """SKILL.md 去掉 frontmatter 的正文。"""
    text = SKILL_FILE.read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:].lstrip()
    return text

INSTRUCTIONS = """SmartReportGen 智能报告 MCP（使用海尔 IAM access token；数据按员工工号做表级权限控制）。
若你所在的 agent 没有加载 smart-report 技能（Skill），请先调用 get_skill_guide 获取完整的生成流程与方法，再按流程执行。推荐流程：
1) search_business_scenarios(用户需求) 在你有权限的业务场景中混合检索 → get_dataset_tables(dataset_id) 获取数据表和场景的分析思路；
2) 多表场景先用 describe_tables 列出所有授权表，逐张用 describe_table 和单表事实查询核实可用指标、粒度、关联键及覆盖范围，记录每张表的指标或无法使用的原因；
3) 将场景的 analysis_approach 与用户问题逐项映射到指标、来源表、查询和支撑数值。对每个小节用 run_query 直取完整口径的正式指标，以 tables_used 核对来源并定位具体事实行；明细样本仅供核对字段。实际、目标、完成率、同比、差额、贡献、总计与预测必须直取，禁止在 SQL、前端或 LLM 中补算，缺失显示 —。跨表仅在验证粒度后关联或透视原值；
4) get_style_rules(业务场景) 获取 reporting_rules（独立的事实与离线交付约束）及视觉样式和展示要求 specified（业务场景 ≥ 通用 ≥ 内置 合并）。样式调整不能豁免 reporting_rules；
5) 将逐表指标、分析项与证据的映射、缺口、章节、筛选和展示要求写入业务 Markdown 模板。用 save_report_template(template_type="business", ...) 保存，并用 get_report_template 读取指定版本。
6) 用 run_query(snapshot=true) 保存各组报告数据，与同口径的直接事实对账。Agent 在 HTML 中内联 CSS、JavaScript、所用图表运行时与页面资源，并用独立的 application/json 数据块引用各 snapshot_id。用 preview_report_html 展开数据，在浏览器逐小节核对默认及代表性筛选下的结论和源端总计，再用 save_report 提交相同 HTML 并关联模板版本；服务端内联快照数据，下载仍为离线单文件。打开、浏览与筛选不得触发取数、生成或保存。
修改已有报告时用 get_report(include_html=true, omit_snapshot_data=true) 读取选中的版本，显式指定的 version 优先。预览和 revise_report 均传入读取的 revision 作为 base_version，可复用基准版本的数据块。
遇到“无权访问”时不要尝试绕过，应告知用户需要申请哪些表的权限。"""

mcp = FastMCP("smartreportgen", instructions=INSTRUCTIONS, stateless_http=True, json_response=True)

_EXPECTED = (repo.NotFound, repo.Invalid, ConnectorError, RenderError, TemplateError, RuntimeError, ValueError,
             PermissionDenied, AuthError)


def _ok(obj: Any) -> Any:
    return jsonable_encoder(obj)


def _err(e: Exception) -> dict:
    out = {"ok": False, "error": str(e)}
    if isinstance(e, PermissionDenied):
        out["error_type"] = "permission_denied"
    if isinstance(e, repo.ReportConflict):
        out["error_type"] = "conflict"
    return out


# ------------------------------------------------------------ 身份与权限
def _principal(ctx: Context) -> Principal:
    req = getattr(ctx.request_context, "request", None)
    if req is None:
        raise AuthError("无法获取请求上下文")
    p = req.scope.get("state", {}).get("principal")
    return p if p is not None else auth.verify_bearer(
        req.headers.get("authorization"), req.headers.get("x-iam-access-token") or req.headers.get("access-token"))


def _access(ctx: Context) -> Access:
    p = _principal(ctx)
    if not p.registered:
        raise PermissionDenied(f"工号 {p.emp_no} 未在权限表中登记或已停用，请联系管理员开通")
    return access_for(p)


def _require_dataset(acc: Access, dataset_id: int) -> None:
    if not acc.can_dataset(dataset_id):
        raise PermissionDenied(f"{acc.principal.label} 无权访问该业务场景（dataset_id={dataset_id}）")


def _guard(acc: Access):
    def g(dataset: dict | None) -> set[str] | None:
        if dataset:
            _require_dataset(acc, dataset["id"])
            return None if acc.all else acc.tables.get(dataset["id"], set())
        if not acc.all:
            raise PermissionDenied("请为查询关联有权限的业务场景，并使用该场景的数据源")
        return None
    return g


def _owner(acc: Access) -> str | None:
    """非管理员只能看到/修改自己创建的模板与报告。"""
    return None if acc.all else (acc.principal.emp_no or "")


def _check_owner(acc: Access, created_by: str | None, what: str) -> None:
    if not acc.all and created_by != acc.principal.emp_no:
        raise PermissionDenied(f"无权操作他人创建的{what}")


def _dataset_brief(ds: dict, acc: Access) -> dict:
    tables = [t for t in ds.get("tables", []) if acc.can_table(ds["id"], t["table_name"])]
    hidden = len(ds.get("tables", [])) - len(tables)
    out = {
        "dataset_id": ds["id"], "business_scenario": ds["business_scenario"],
        "business_description": ds["business_description"], "analysis_approach": ds["analysis_approach"],
        "tags": ds.get("tags") or [],
        "datasource": {"id": ds.get("datasource_id"), "name": ds.get("datasource_name"), "type": ds.get("datasource_type")},
        "tables": [{"table_name": t["table_name"], "table_description": t["table_description"],
                    "row_filters": t["row_filters"]} for t in tables],
    }
    if hidden:
        out["unauthorized_table_count"] = hidden
    return out


# ============================== 身份 ==============================
@mcp.tool()
def whoami(ctx: Context) -> dict:
    """返回当前令牌对应的员工身份，以及有权限的业务场景与数据表。"""
    try:
        p = _principal(ctx)
        from .permission import describe
        return _ok({"ok": True, "emp_no": p.emp_no, "name": p.name, "is_admin": p.is_admin, "via": p.via,
                    "registered": p.registered, "roles": p.roles,
                    "access": describe(access_for(p)) if p.registered else {"all": False, "datasets": []}})
    except _EXPECTED as e:
        return _err(e)


# ============================== a. 场景检索 ==============================
@mcp.tool()
def search_business_scenarios(query: str, ctx: Context, top_k: int = 3) -> dict:
    """根据用户需求，在【当前员工有权限】的业务场景中做混合检索（向量 + 关键词，RRF 融合），
    返回最匹配的业务场景、分析思路及其中有权限的数据表。query 直接传入用户的原始需求描述效果最好。"""
    try:
        acc = _access(ctx)
        res = repo.search_datasets(query, max(1, min(top_k, 10)), dataset_ids=acc.dataset_ids())
    except _EXPECTED as e:
        return _err(e)
    hits = []
    for h in res["hits"]:
        hits.append({
            **_dataset_brief(h["dataset"], acc),
            "scores": {k: h.get(k) for k in ("rrf", "relevance", "vector_score", "vector_rank", "keyword_score",
                                             "keyword_rank")},
            "matched_terms": h.get("matched_terms", []),
        })
    tip = ""
    if not hits:
        tip = ("没有命中你有权限的业务场景：可换用业务术语重新检索；若确需该数据，请联系管理员申请权限。"
               if not acc.all else "没有命中任何业务场景：请换用业务术语重新检索，或在管理台维护数据集。")
    elif hits[0]["scores"]["relevance"] < 0.25:
        tip = "最高相关度偏低，命中可能不准确，请核对业务描述，必要时向用户确认业务场景。" + (
            "也可能是你没有所需业务场景的权限（检索只覆盖已授权的场景），如需请联系管理员申请。" if not acc.all else "")
    elif len(hits) >= 2 and hits[1]["scores"]["relevance"] >= 0.8 * hits[0]["scores"]["relevance"]:
        tip = "前两名相关度接近，请结合业务描述判断，必要时向用户确认业务场景。"
    return _ok({"ok": True, "query_tokens": res["query_tokens"], "hits": hits, "warnings": res["warnings"], "tip": tip})


@mcp.tool()
def get_dataset_tables(dataset_id: int, ctx: Context) -> dict:
    """获取业务场景的描述、分析思路、已授权的表或视图、各表取值范围及数据源信息。"""
    try:
        acc = _access(ctx)
        _require_dataset(acc, dataset_id)
        return _ok({"ok": True, **_dataset_brief(repo.get_dataset(dataset_id), acc)})
    except _EXPECTED as e:
        return _err(e)


@mcp.tool()
def list_business_scenarios(ctx: Context) -> dict:
    """列出当前员工有权限的业务场景（数据集）概要。"""
    try:
        acc = _access(ctx)
    except _EXPECTED as e:
        return _err(e)
    return _ok({"ok": True, "datasets": [
        {"dataset_id": d["id"], "business_scenario": d["business_scenario"], "datasource": d["datasource_name"],
         "datasource_type": d["datasource_type"],
         "table_count": d["table_count"] if acc.all else len(acc.tables.get(d["id"], ()))}
        for d in repo.list_datasets() if d["enabled"] and acc.can_dataset(d["id"])]})


# ============================== b. 数据探查 ==============================
@mcp.tool()
def describe_tables(dataset_id: int, ctx: Context, table_names: list[str] | None = None,
                    offset: int = 0, limit: int = 10) -> dict:
    """批量查看一个业务场景中已授权的表或视图的字段。默认分页列出所有授权表的结构，不返回数据行。
    可传 table_names 指定一组表。按 next_offset 取下一页，使用 describe_table 查看具体表的样本和画像。"""
    try:
        acc = _access(ctx)
        _require_dataset(acc, dataset_id)
        ds = repo.get_dataset(dataset_id)
        scope = {table["table_name"]: table for table in ds["tables"]}
        names = [name for name in scope if acc.can_table(dataset_id, name)]
        if table_names is not None:
            names = list(dict.fromkeys(table_names))
            for name in names:
                if name not in scope or not acc.can_table(dataset_id, name):
                    raise PermissionDenied(f"数据表或视图不在授权的场景范围内：{name}")
        offset = max(0, offset)
        stop = offset + max(1, min(limit, 20))
        tables = []
        conn = services.get_connector(services.datasource_for_dataset(ds))
        for name in names[offset:stop]:
            item = {"table_name": name, "table_description": scope[name]["table_description"]}
            try:
                item["columns"] = conn.columns(name)
            except ConnectorError as exc:
                # 单表元数据失败时保留错误和其他表的结构，便于定位尚未覆盖的分析。
                item["columns_error"] = str(exc)
            tables.append(item)
        return _ok({"ok": True, "tables": tables, "total": len(names),
                    "next_offset": stop if stop < len(names) else None})
    except _EXPECTED as e:
        return _err(e)


@mcp.tool()
def describe_table(dataset_id: int, table_name: str, ctx: Context, sample_rows: int = 5) -> dict:
    """查看【有权限】的数据表结构与数据情况：字段名/类型/注释、总行数、样本行、列画像（类型、空值率、去重数、最值、高频取值）。"""
    try:
        acc = _access(ctx)
        _require_dataset(acc, dataset_id)
        ds = repo.get_dataset(dataset_id)
        if table_name not in {t["table_name"] for t in ds["tables"]}:
            raise repo.Invalid(f"表 {table_name} 不属于业务场景「{ds['business_scenario']}」")
        if not acc.can_table(dataset_id, table_name):
            raise PermissionDenied(f"{acc.principal.label} 无权访问数据表：{table_name}")
        ds_row = services.datasource_for_dataset(ds)
        allowed = None if acc.all else acc.tables.get(dataset_id, set())
        return _ok({"ok": True, **services.describe_table(ds_row, table_name, max(0, min(sample_rows, 50)),
                                                          dataset=ds, allowed=allowed)})
    except _EXPECTED as e:
        return _err(e)


@mcp.tool()
def run_query(dataset_id: int, sql: str, ctx: Context, limit: int | None = None,
              snapshot: bool = False, sample_rows: int = 5) -> dict:
    """在业务场景上执行只读 SQL（单条 SELECT/WITH），只能引用该场景中当前员工有权限的表或视图。
    场景的逐表取值条件由服务端在 JOIN、聚合前自动应用。
    SQL 方言与数据源类型一致（API 使用 DuckDB）。返回实际引用的 tables_used，列名统一转小写。
    普通模式默认返回至多 200 行，可在 SQL 中先按完整业务口径聚合后读取少量证据行。
    snapshot=true 将完整 SQL 结果保存为 JSON，由报告使用 snapshot_id 引用。
    MCP 仅返回行数和少量明细样本（sample_rows 0–20），样本用于检查字段。
    truncated=true 表示超过返回行数上限，未创建快照；请调整聚合或分批查询。
    truncated=false 只说明返回上限未截断 SQL 结果，业务覆盖仍需检查查询口径、SQL 自带的行数限制和数据源覆盖。"""
    try:
        acc = _access(ctx)
        _require_dataset(acc, dataset_id)
        ds = repo.get_dataset(dataset_id)
        ds_row = services.datasource_for_dataset(ds)
        allowed = None if acc.all else acc.tables.get(dataset_id, set())
        result = services.run_query(ds_row, sql, limit, dataset=ds, allowed=allowed, snapshot=snapshot)
        if snapshot:
            rows = result.pop("rows")
            if result["truncated"]:
                # 截断结果不能以快照交付，防止联动指标把不完整的数据当成全量。
                del result["columns"]
                result["warning"] = "查询结果超过行数上限，未保存快照。请缩小范围、调整聚合粒度或分批查询。"
            else:
                result.update(report_data.save_query(acc.principal.emp_no, result["columns"], rows,
                                                     max(0, min(sample_rows, 20))))
        return _ok({"ok": True, **result})
    except _EXPECTED as e:
        return _err(e)


@mcp.tool()
def get_skill_guide(include_example: bool = False,
                    reference: Literal["fact-contract", "green-energy-september-facts"] | None = None) -> str:
    """返回 smart-report 技能全文：报告生成的完整流程（a-f）、分析与设计方法、权限处理与最终回复格式。
    适用于没有本地技能机制的 agent；开始生成报告前调用一次。include_example=true 时附带可运行的 HTML 示例。
    reference 可按需附带 fact-contract（事实取数细则）或 green-energy-september-facts（附件的绿能九月场景参考，未经本次验证）。"""
    text = _skill_body()
    if reference is not None:
        # 仅允许已维护的参考名，不能把客户端参数当作任意文件路径读取。
        files = {"fact-contract": "fact-contract.md", "green-energy-september-facts": "green-energy-september-facts.md"}
        if reference not in files:
            raise ValueError("不支持的技能参考文档")
        text += "\n\n---\n\n" + (SKILL_DIR / "references" / files[reference]).read_text(encoding="utf-8")
    if include_example:
        text += "\n\n## 附：筛选联动与结论动态着色的 HTML 示例\n\n```html\n" + EXAMPLE_FILE.read_text(encoding="utf-8") + "```\n"
    return text


@mcp.prompt(name="smart_report", description="按 SmartReportGen 流程，根据需求生成交互式 HTML 分析报告")
def smart_report_prompt(requirement: str) -> str:
    return (_skill_body() + "\n\n---\n\n## 本次需求\n\n" + requirement +
            "\n\n请严格按以上流程，使用 smartreportgen MCP 工具完成报告并按“最终回复格式”答复。")


@mcp.tool()
def get_template_spec() -> str:
    """返回服务端结构化报告模板的 Markdown 语法。维护结构化模板时使用。直接编写 HTML 报告时参考 get_skill_guide。"""
    return SPEC_FILE.read_text(encoding="utf-8")


# ============================== d. 样式规则 ==============================
@mcp.tool()
def get_style_rules(business_scenario: str, ctx: Context, query: str = "") -> dict:
    """检索该业务场景的指定样式：你本人为该场景配置的样式 ≥ 管理员配置的通用规则 ≥ 内置经营报告主题，合并为一份 specified，
    作为展示约束交给你：specified.theme 为视觉样式取值（渲染时自动生效），specified.items 为好坏判定、涨跌颜色、数字格式、
    文案等展示要求。章节、图表类型和筛选条件由用户需求、场景分析思路及数据证据决定。
    reporting_rules 为独立的事实直取、缺失不补算及离线单文件约束，不参与视觉样式覆盖。
    builtin_style 提供可选用的内联 CSS 和组件说明。视觉样式生效顺序：你的决定 ≥ 业务场景规则 ≥ 通用规则 ≥ 内置样式。
    将适用的样式和展示要求落实到生成的 HTML 中。"""
    try:
        p = _principal(ctx)
        res = repo.style_rules_for(business_scenario, query or None, owner=p.emp_no)
    except _EXPECTED as e:
        return _err(e)
    rules = [{"id": r["id"], "name": r["name"], "scope": r["scope"],
              **({"relevance": r["relevance"]} if r.get("relevance") else {})}
             for r in res["general_rules"] + res["scenario_rules"]]
    return _ok({
        "ok": True,
        "business_scenario": res["business_scenario"],
        "precedence": res["precedence"],
        "reporting_rules": res["reporting_rules"],
        "specified": res["specified"],
        # 自编 HTML 可以直接复用内置组件样式；指定主题和展示要求仍通过原有字段返回。
        "builtin_style": design.builtin_style(res["specified"]["theme"]),
        "rules": rules,
        "note": ("reporting_rules 独立约束事实取数与离线交付，不能因样式调整豁免。"
                 "specified 合并了业务场景、通用及内置样式。直接编写 HTML 时，请将适用的 theme 和 items 落实到 CSS、图表配置与脚本。"
                 "采用内置样式时，可以内联 builtin_style.css 并参考组件说明；同类卡片复用 CSS 与图表配置。"
                 "items 各类别内排在前面的优先，请落实到健康度、升降色、数字格式与文案。"
                 "章节、图表类型和筛选条件由用户需求、场景分析思路及数据证据决定。确有理由时可调整展示要求并在回复中说明。"
                 "使用服务端结构化模板渲染时，theme 会自动应用。"),
        "warnings": res["warnings"],
    })


# ============================== e/f. 模板与报告 ==============================
@mcp.tool()
def validate_report_template(markdown: str, ctx: Context, dataset_id: int | None = None, execute: bool = True) -> dict:
    """校验旧结构化模板的语法、图表参数、度量表达式。execute=true 时按权限执行查询并检查字段引用。
    新报告的业务 Markdown 与单文件 HTML 使用 get_skill_guide 中的编写和浏览器验收流程。"""
    try:
        acc = _access(ctx)
        if dataset_id:
            _require_dataset(acc, dataset_id)
        return _ok(validate_md(markdown, dataset_id, execute, guard=_guard(acc), owner=acc.principal.emp_no))
    except _EXPECTED as e:
        return _err(e)


@mcp.tool()
def save_report_template(name: str, markdown: str, ctx: Context, dataset_id: int | None = None,
                         user_requirement: str = "", analysis_goal: str = "", template_id: int | None = None,
                         business_summary: str | None = None,
                         template_type: Literal["business", "structured"] | None = None) -> dict:
    """保存报告模板。新报告的业务 Markdown 请传 template_type='business'，原文可用普通标题、段落和表格。
    旧结构化模板传 structured 或沿用旧客户端默认值。传 template_id 更新既有模板，类型沿用原值，正文变化时增加版本。
    business_summary 是管理台显示的简短用途说明，完整业务文档放在 markdown。"""
    try:
        acc = _access(ctx)
        if dataset_id:
            _require_dataset(acc, dataset_id)
        data = {"name": name, "markdown": markdown, "dataset_id": dataset_id,
                "user_requirement": user_requirement, "analysis_goal": analysis_goal}
        if template_id:  # 更新时只覆盖调用方实际提供的字段
            _check_owner(acc, repo.get_template(template_id).get("created_by"), "报告模板")
            data = {k: v for k, v in data.items() if k == "markdown" or v not in (None, "")}
        else:
            data["created_by"] = acc.principal.emp_no
        # 省略时兼容旧客户端，显式传空字符串可以清除说明。
        if business_summary is not None:
            data["business_summary"] = business_summary
        if template_type is not None:
            data["template_type"] = template_type
        t = repo.save_template(data, template_id)
    except _EXPECTED as e:
        return _err(e)
    return _ok({"ok": True, "template_id": t["id"], "version": t["version"], "name": t["name"],
                "template_type": t["template_type"], "summary": t["summary"]})


@mcp.tool()
def get_report_template(template_id: int, ctx: Context, version: int | None = None) -> dict:
    """读取有权限的报告模板及 Markdown 全文。指定 version 时读取对应正文，用于编写或复现该版报告。"""
    try:
        acc = _access(ctx)
        t = repo.get_template(template_id)
        _check_owner(acc, t.get("created_by"), "报告模板")
        if version is not None:
            # 历史版本只记录正文，当前模板的摘要和其他可变元数据不代表历史版本。
            t = {**{key: t[key] for key in ("id", "template_type")}, **repo.get_template_version(template_id, version)}
        return _ok({"ok": True, **t})
    except _EXPECTED as e:
        return _err(e)


@mcp.tool()
def list_report_templates(ctx: Context, keyword: str = "") -> dict:
    """列出本人创建的报告模板（可按名称/需求/业务场景关键字过滤），便于复用历史模板。"""
    try:
        acc = _access(ctx)
    except _EXPECTED as e:
        return _err(e)
    rows = repo.list_templates(keyword or None, owner=_owner(acc))
    return _ok({"ok": True, "templates": [
        {"template_id": r["id"], "name": r["name"], "business_scenario": r["business_scenario"], "version": r["version"],
         "template_type": r["template_type"],
         "user_requirement": r["user_requirement"], "updated_at": r["updated_at"], "summary": r["summary"]} for r in rows[:50]]})


@mcp.tool()
def render_report_html(template_id: int, ctx: Context, report_name: str = "") -> dict:
    """基于本人创建的报告模板执行查询（按权限校验每条 SQL）并生成 HTML 报告页面，同时存储到报告库。
    用于结构化模板渲染。直接编写的完整 HTML 请使用 save_report 保存。"""
    try:
        acc = _access(ctx)
        tpl = repo.get_template(template_id)
        _check_owner(acc, tpl.get("created_by"), "报告模板")
        r = services.render_template_to_report(tpl, report_name, guard=_guard(acc),
                                               created_by=acc.principal.emp_no)
    except _EXPECTED as e:
        return _err(e)
    return _ok({"ok": True, "report_id": r["id"], "name": r["name"], "version": r["revision"],
                **repo.report_links(r["id"], r["revision"]), "html_bytes": r["meta"]["html_bytes"],
                "warnings": r["warnings"], "queries": r["meta"].get("queries")})


@mcp.tool()
def preview_report_html(html: str, ctx: Context, report_id: int | None = None,
                        base_version: int | None = None) -> dict:
    """展开引用并校验单文件 HTML，返回仅本人可访问的预览及下载链接，响应不包含正文或全量数据。
    预览修订时传 report_id 和读取到的 base_version，以复用该版本的嵌入数据。"""
    try:
        principal = _access(ctx).principal
        if base_version is not None and report_id is None:
            raise repo.Invalid("指定基准版本时须同时指定报告 ID")
        base = None
        if report_id is not None:
            base = repo.get_report(report_id, principal, version=base_version)
            if base["access_level"] not in ("manage", "edit"):
                raise PermissionDenied("无权编辑该报告")
        page = report_data.embed(html, principal.emp_no, base["html"] if base else "")
        if base and not base["meta"].get("single_file"):
            # 与保存修订版一致，旧版内置 ECharts 引用也可在预览中离线运行。
            page = inline_legacy_echarts(page)
        if error := report_document_error(page):
            raise repo.Invalid(error)
        ref = report_data.save_preview(principal.emp_no, page)
        url = f"{settings.public_base_url}/reports/preview/{ref}"
    except _EXPECTED as e:
        return _err(e)
    return _ok({"ok": True, "url": url, "download_url": f"{url}?download=true",
                "html_bytes": len(page.encode("utf-8")), "expires_in_seconds": report_data.TTL_SECONDS})


@mcp.tool()
def save_report(name: str, html: str, ctx: Context, template_id: int | None = None,
                template_version: int | None = None) -> dict:
    """创建独立报告，提交经预览验证的 HTML，服务端在保存前将快照引用展开为离线单文件。
    引用格式：<script type="application/json" id="sales" data-srg-snapshot="快照ID"></script>。
    传 template_id 和 template_version 关联开发时使用的模板版本。修改现有报告时使用 revise_report。"""
    try:
        acc = _access(ctx)
        if template_version is not None and template_id is None:
            raise repo.Invalid("指定模板版本时须同时指定模板 ID")
        ver = template_version
        if template_id is not None:
            t = repo.get_template(template_id)
            _check_owner(acc, t.get("created_by"), "报告模板")
            if ver is None:
                ver = t["version"]
            elif ver != t["version"]:
                repo.get_template_version(template_id, ver)
        r = repo.save_report(name, html, template_id, ver, {"source": "mcp.save_report"},
                             created_by=acc.principal.emp_no)
    except _EXPECTED as e:
        return _err(e)
    return _ok({"ok": True, "report_id": r["id"], "version": r["revision"],
                "template_id": template_id, "template_version": ver,
                **repo.report_links(r["id"], r["revision"]), "html_bytes": r["meta"]["html_bytes"]})


@mcp.tool()
def list_reports(ctx: Context, keyword: str = "", template_id: int | None = None) -> dict:
    """查找可访问的报告，每份报告返回一项，含当前版本及最新版本号。"""
    try:
        rows = repo.list_reports(_access(ctx).principal, template_id, keyword)
    except _EXPECTED as e:
        return _err(e)
    return _ok({"ok": True, "reports": [
        {"report_id": r["id"], "name": r["name"], "current_version": r["current_version"],
         "latest_version": r["latest_version"], "access_level": r["access_level"],
         "template_id": r["template_id"], "template_name": r["template_name"], "created_by": r["created_by"],
         **repo.report_links(r["id"])} for r in rows[:50]
    ], "total": len(rows)})


@mcp.tool()
def get_report(report_id: int, ctx: Context, include_html: bool = False, version: int | None = None,
               include_source: bool = False, omit_snapshot_data: bool = False) -> dict:
    """读取可访问的报告。显式 version 优先，省略时读取本账号的当前选择或最新版。
    include_html 返回该版本的 HTML；include_source 返回有权限读取的版本 Markdown 源码。
    同时传 omit_snapshot_data=true 时，快照数据替换为可复用的基准引用，避免把它们读入模型。"""
    try:
        r = repo.get_report(report_id, _access(ctx).principal, with_html=include_html,
                            version=version, with_source=include_source)
        if include_html and omit_snapshot_data:
            r["html"] = report_data.for_edit(r["html"])
    except _EXPECTED as e:
        return _err(e)
    return _ok({"ok": True, **r, **repo.report_links(r["id"], r["revision"])})


@mcp.tool()
def list_report_versions(report_id: int, ctx: Context) -> dict:
    """列出报告版本、基准版本、修改人、修改说明及当前选择。"""
    try:
        return _ok({"ok": True, **repo.list_report_versions(report_id, _access(ctx).principal)})
    except _EXPECTED as e:
        return _err(e)


@mcp.tool()
def select_report_version(report_id: int, ctx: Context, version: int | None = None) -> dict:
    """确认本账号后续查看和修改使用的版本。version 留空恢复跟随最新版本。"""
    try:
        r = repo.select_report_version(report_id, _access(ctx).principal, version)
    except _EXPECTED as e:
        return _err(e)
    return _ok({"ok": True, **r, **repo.report_links(r["id"], r["revision"])})


@mcp.tool()
def revise_report(report_id: int, name: str, ctx: Context, base_version: int | None = None,
                  html: str | None = None, markdown: str | None = None, change_note: str = "",
                  expected_version: int | None = None) -> dict:
    """从指定基准保存同一报告的新版本。基准省略时使用当前选择或最新版。
    读取基准后传回其 revision 作为 base_version。HTML 可包含新快照引用和基准数据块引用。
    markdown 仅供旧结构化报告重渲染。"""
    try:
        acc = _access(ctx)
        r = services.revise_report(report_id, acc.principal, name, base_version=base_version,
                                   html=html, markdown=markdown, change_note=change_note,
                                   expected_version=expected_version, guard=_guard(acc))
    except _EXPECTED as e:
        return _err(e)
    return _ok({"ok": True, "report_id": r["id"], **r})


# ============================== 启动 ==============================
class TokenAuth:
    """纯 ASGI 中间件：/mcp 请求必须携带有效令牌，解析出的身份放入 scope["state"]["principal"]。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/mcp"):
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            # 只记录 Authorization 请求头是否存在，避免把 IAM 令牌写入日志。
            log.info("MCP authorization_present=%s", "authorization" in headers)
            try:
                principal = await run_in_threadpool(auth.verify_bearer, headers.get("authorization"),
                                                    headers.get("x-iam-access-token") or headers.get("access-token"))
            except auth.AuthUnavailable as e:
                await JSONResponse({"error": "iam_unavailable", "message": str(e)}, status_code=503)(scope, receive, send)
                return
            except AuthError as e:
                challenge = 'Bearer realm="smartreportgen"'
                if mcp_oauth.enabled():
                    challenge += f', resource_metadata="{mcp_oauth.resource_metadata_url()}"'
                resp = JSONResponse({"error": "unauthorized", "message": str(e)}, status_code=401,
                                    headers={"WWW-Authenticate": challenge})
                await resp(scope, receive, send)
                return
            scope.setdefault("state", {})["principal"] = principal
        await self.app(scope, receive, send)


def build_app():
    mcp.settings.host = "0.0.0.0"
    mcp.settings.port = settings.mcp_port
    try:  # 新版 SDK 默认开启 DNS rebinding 防护，仅允许 localhost Host；容器内需放开（认证由令牌负责）
        from mcp.server.transport_security import TransportSecuritySettings

        mcp.settings.transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    except ImportError:
        pass
    app = mcp.streamable_http_app()

    async def health(_req):
        return JSONResponse({"ok": True})

    app.add_route("/healthz", health)
    # 标准 OAuth 发现从 MCP 的公网地址开始，登录与换票走管理 API 的地址。
    for path in {
        mcp_oauth.resource_metadata_path(),
        mcp_oauth.resource_metadata_local_path(),
        "/.well-known/oauth-protected-resource",
    }:
        app.add_route(path, mcp_oauth.resource_metadata)
    app.add_middleware(TokenAuth)
    return app


def main() -> None:
    db.init_db()
    repo.load_custom_words()
    uvicorn.run(build_app(), host="0.0.0.0", port=settings.mcp_port, log_level="info")


if __name__ == "__main__":
    main()
