"""SmartReportGen MCP 服务（Streamable HTTP，路径 /mcp）。

认证：每个请求通过 Authorization: Bearer 携带海尔 IAM access token；经 IAM 核实的工号用于查询权限表，
      决定可检索的业务场景与可访问的数据表。数据访问一律默认拒绝。
报告生成流程：授权场景检索 → Markdown 指南与快照 → HTML 分块拼装 → Chromium 校验入库。
"""
from __future__ import annotations

import contextvars
import logging
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from hashlib import sha256
from typing import Any, Literal

import uvicorn
from fastapi.encoders import jsonable_encoder
from pydantic import Field
from typing import Annotated
from mcp.server.fastmcp import Context, FastMCP
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from psycopg.errors import UniqueViolation

from . import auth, db, design, repo, report_browser, report_data, report_draft, report_pipeline, report_queries, services
from .auth import AuthError, Principal
from .config import settings
from .connectors import ConnectorError
from .permission import Access, PermissionDenied, access_for, source_scene_tables
from .scenario_filters import effective_filters
from .report_document import skill_dir
from .telemetry import measure, observe_tool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("smartreportgen.mcp")

SKILL_DIR = skill_dir()
SKILL_FILE = SKILL_DIR / "SKILL.md"
EXAMPLE_FILE = SKILL_DIR / "references" / "example-report.html"


def _skill_body() -> str:
    """SKILL.md 去掉 frontmatter 的正文。"""
    text = SKILL_FILE.read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:].lstrip()
    return text

INSTRUCTIONS = """SmartReportGen 智能报告 MCP。用 get_skill_guide 获取从报告分流到验收保存的主流程。
先区分新建与修订。修订用报告链接或 list_reports 定位，多个匹配候选先请用户确认。get_report 返回的 revision 用于 base_version，latest_version 用于 expected_version。
新建用 search_business_scenarios 匹配已授权场景，复用适用场景。缺少适用场景时，可用 list_scene_datasources 查看开放数据源与固定范围并配合 save_business_scenario 协助建场景，或给出 scenario_management_url 引导用户配置。
已知场景后调用 prepare_report_context 获取分析思路、表画像、Markdown 指南摘要和生效样式。
报告模板是生成报告的必备产物：新建报告前必须先通过 save_report_template 保存模板（或确定完全复用的已有模板）并在创建草稿/保存报告时绑定 template_id 和 template_version；修改报告正文时必须同步更新对应的报告模板（save_report_template）并传入更新后的模板版本。
读取匹配的指南，用 run_query(snapshot=True) 取得授权快照，创建 HTML 草稿并分轮填写章节。复杂报告可在 create_html_draft 绑定数据块、模板、配置筛选和预置有序章节槽位后并行拼装，按 diagnostics.section_ids 分派修复（详见 get_skill_guide(reference="parallel-subagents")）。
默认用 save_report 或 revise_report 提交 draft_id，服务端按需运行离线 Chromium。先审阅时可调用 verify_html_draft。
采用海尔 IAM，场景及事实来源受当前员工授权约束。"""

mcp = FastMCP("smartreportgen", instructions=INSTRUCTIONS, stateless_http=True, json_response=True)


def _tool():
    """同步业务函数在线程池运行，避免慢查询阻塞整个 MCP 事件循环。"""
    def register(fn):
        @wraps(fn)
        def guarded(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except _EXPECTED as exc:
                return _err(exc)
            except Exception as exc:
                # 业务失败统一返回可观测结果，内部异常类型入日志，避免记录业务内容和凭证。
                log.error("工具 %s 遇到内部异常：%s", fn.__name__, type(exc).__name__)
                return {"ok": False, "error_type": "internal_error", "error": "工具执行失败，请按调用标识查询服务日志"}

        observed = observe_tool(guarded)

        @wraps(fn)
        async def dispatch(*args, **kwargs):
            # FastMCP 直接调用同步函数；仅标记为同步工具无法自动获得线程池隔离。
            return await run_in_threadpool(observed, *args, **kwargs)

        mcp.tool()(dispatch)
        # 保留同步入口供应用内部和单元测试调用；对外注册的是上面的异步入口。
        return observed
    return register


_EXPECTED = (repo.NotFound, repo.Invalid, ConnectorError, RuntimeError, ValueError,
             PermissionDenied, AuthError)


def _ok(obj: Any) -> Any:
    return jsonable_encoder(obj)


def _err(e: Exception) -> dict:
    out = {"ok": False, "error": str(e)}
    if isinstance(e, PermissionDenied):
        out["error_type"] = "permission_denied"
    if isinstance(e, repo.ReportConflict):
        out["error_type"] = "conflict"
    if isinstance(e, report_browser.ReportVerifyError):
        out.update(error_type="verify_failed", failed_stage=e.stage, diagnostics=e.diagnostics)
    return out


# ------------------------------------------------------------ 身份与权限
def _principal(ctx: Context) -> Principal:
    req = getattr(ctx.request_context, "request", None)
    if req is None:
        raise AuthError("无法获取请求上下文")
    p = req.scope.get("state", {}).get("principal")
    # 正常 HTTP 入口复用中间件已核实的身份，直接调用工具时也使用同一 Bearer 校验。
    return p if p is not None else auth.verify_bearer(req.headers.get("authorization"))


def _access(ctx: Context) -> Access:
    p = _principal(ctx)
    if not p.registered:
        raise PermissionDenied(f"工号 {p.emp_no} 未在权限表中登记或已停用，请联系管理员开通")
    return access_for(p)


def _require_dataset(acc: Access, dataset_id: int) -> None:
    if not acc.can_dataset(dataset_id):
        raise PermissionDenied(f"{acc.principal.label} 无权访问该业务场景（dataset_id={dataset_id}）")


def _owner(acc: Access) -> str | None:
    """返回非管理员的工号，用于模板查询同时匹配创建人和场景授权。"""
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
        "semantic_metrics": {key: metric for key, metric in ds.get("semantic_metrics", {}).items()
                             if acc.can_table(ds["id"], metric["table"])},
        "tables": [{"table_name": t["table_name"], "table_description": t["table_description"],
                    "row_filters": effective_filters(t)} for t in tables],
    }
    if hidden:
        out["unauthorized_table_count"] = hidden
    return out


# ============================== 身份 ==============================
@_tool()
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


# ============================== 1. 场景检索 ==============================
@_tool()
def search_business_scenarios(query: str, ctx: Context, top_k: int = 3,
                              include_context: bool = False) -> dict:
    """根据用户需求，在【当前员工有权限】的业务场景中做混合检索（向量 + 关键词，RRF 融合），
    返回最匹配的业务场景、分析思路及已授权表，并给出管理台业务场景入口。query 宜使用用户的原始需求。
    include_context 可在首个结果相关且与下一名有足够差距时一并返回其模板设计上下文。"""
    try:
        acc = _access(ctx)
        requested = max(1, min(top_k, 10))
        # 即使只返回一名也要用第二名判断歧义，避免自动加载误匹配的表画像。
        res = repo.search_datasets(query, max(2, requested) if include_context else requested, dataset_ids=acc.dataset_ids())
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
        tip = ("没有命中你有权限的业务场景。可换用业务术语检索，或从已授权的数据源构建场景。缺少来源授权时请联系管理员。"
               if not acc.all else "没有命中任何业务场景：请换用业务术语重新检索，或在管理台维护数据集。")
    elif hits[0]["scores"]["relevance"] < 0.25:
        tip = "最高相关度偏低，命中可能不准确，请核对业务描述，必要时向用户确认业务场景。" + (
            "也可能是你没有所需业务场景的权限（检索只覆盖已授权的场景），如需请联系管理员申请。" if not acc.all else "")
    elif len(hits) >= 2 and hits[1]["scores"]["relevance"] >= 0.8 * hits[0]["scores"]["relevance"]:
        tip = "前两名相关度接近，请结合业务描述判断，必要时向用户确认业务场景。"
    # 管理台和 MCP 可以使用不同的公网地址，配置场景应跳到管理台的实际部署子路径。
    result = {"ok": True, "query_tokens": res["query_tokens"], "hits": hits[:requested], "warnings": res["warnings"], "tip": tip,
              "scenario_management_url": f"{settings.public_base_url}/#datasets"}
    if include_context and hits and not tip:
        result["context"] = prepare_report_context.__wrapped__(hits[0]["dataset_id"], ctx, query)
    return _ok(result)


@_tool()
def get_dataset_tables(dataset_id: int, ctx: Context) -> dict:
    """获取业务场景的描述、分析思路、已授权的表或视图、各表取值范围及数据源信息。"""
    try:
        acc = _access(ctx)
        _require_dataset(acc, dataset_id)
        return _ok({"ok": True, **_dataset_brief(repo.get_dataset(dataset_id), acc)})
    except _EXPECTED as e:
        return _err(e)


@_tool()
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


@_tool()
def list_scene_datasources(ctx: Context) -> dict:
    """列出当前员工可用于构建业务场景的数据源及开放表清单（含固定行范围）。"""
    try:
        acc = _access(ctx)
        items = []
        for row in repo.list_datasources():
            if not row.get("enabled", True):
                continue
            catalog = source_scene_tables(row) if acc.all else source_scene_tables(row, acc.principal)
            if not acc.all and not catalog:
                continue
            tables = [{"table_name": name, "description": entry.get("description", ""),
                       "row_filters": entry.get("row_filters", [])} for name, entry in catalog.items()]
            items.append({"datasource_id": row["id"], "name": row["name"], "type": row["type"],
                          "description": row.get("description", ""), "tables": tables, "all_tables": acc.all})
        return _ok({"ok": True, "datasources": items, "scenario_management_url": f"{settings.public_base_url}/#datasets"})
    except _EXPECTED as e:
        return _err(e)


@_tool()
def save_business_scenario(
    business_scenario: str,
    business_description: str,
    datasource_id: int,
    tables: list[dict[str, Any]],
    ctx: Context,
    dataset_id: int | None = None,
    analysis_approach: str = "",
    tags: list[str] | None = None,
    semantic_metrics: dict[str, Any] | None = None,
) -> dict:
    """按当前员工数据源授权新建或更新个人业务场景（含分析思路、逐表筛选和官方指标）。"""
    try:
        acc = _access(ctx)
        payload: dict[str, Any] = {
            "business_scenario": business_scenario,
            "business_description": business_description,
            "datasource_id": datasource_id,
            "tables": tables,
            "enabled": True,
        }
        if dataset_id is None:
            payload["analysis_approach"] = analysis_approach
            payload["tags"] = tags or []
            payload["semantic_metrics"] = semantic_metrics or {}
        else:
            if analysis_approach:
                payload["analysis_approach"] = analysis_approach
            if tags is not None:
                payload["tags"] = tags
            if semantic_metrics is not None:
                payload["semantic_metrics"] = semantic_metrics
        try:
            saved = (repo.update_dataset(dataset_id, payload, acc.principal) if dataset_id is not None
                     else repo.create_dataset(payload, acc.principal))
        except UniqueViolation as exc:
            raise repo.Invalid("业务场景名称已存在，请更换名称或传入 dataset_id 更新") from exc
        refreshed = access_for(acc.principal)
        return _ok({"ok": True, **_dataset_brief(saved, refreshed)})
    except _EXPECTED as e:
        return _err(e)


# ============================== 2. 数据探查 ==============================
def _describe_tables_page(acc: Access, dataset_id: int, *, ds: dict | None = None,
                          table_names: list[str] | None = None, offset: int = 0, limit: int = 10,
                          include_profile: bool = False, sample_rows: int = 5,
                          profile_rows: int | None = None, refresh_schema: bool = False) -> dict:
    ds = ds or repo.get_dataset(dataset_id)
    scope = {table["table_name"]: table for table in ds["tables"]}
    names = [name for name in scope if acc.can_table(dataset_id, name)]
    if table_names is not None:
        names = list(dict.fromkeys(table_names))
        for name in names:
            if name not in scope or not acc.can_table(dataset_id, name):
                raise PermissionDenied(f"数据表或视图不在授权的场景范围内：{name}")
    offset = max(0, offset)
    stop = offset + max(1, min(limit, 20))
    ds_row = services.datasource_for_dataset(ds)
    conn = services.get_connector(ds_row)
    allowed = None if acc.all else acc.tables.get(dataset_id, set())
    sample_rows = max(0, min(sample_rows, 50))
    profile_rows = max(0, min(profile_rows if profile_rows is not None else (100 if sample_rows else 0), 2000))

    def _inspect_table(name: str) -> dict:
        item = {"table_name": name, "table_description": scope[name]["table_description"]}
        try:
            if include_profile:
                item.update(services.describe_table(ds_row, name, sample_rows, profile_rows,
                                                    dataset=ds, allowed=allowed, include_count=False,
                                                    include_extrema=False, refresh_schema=refresh_schema))
            else:
                item["columns"] = conn.cached_columns(name, refresh=refresh_schema)
        except ConnectorError as exc:
            # 单表元数据失败时保留错误和其他表的结构，便于定位尚未覆盖的分析。
            item["profile_error" if include_profile else "columns_error"] = str(exc)
        return item

    page_names = names[offset:stop]
    if len(page_names) > 1:
        with ThreadPoolExecutor(max_workers=min(4, len(page_names))) as pool:
            futures = [pool.submit(contextvars.copy_context().run, _inspect_table, name) for name in page_names]
            tables = [future.result() for future in futures]
    else:
        tables = [_inspect_table(name) for name in page_names]
    return {"tables": tables, "total": len(names), "next_offset": stop if stop < len(names) else None}


@_tool()
def describe_tables(dataset_id: int, ctx: Context, table_names: list[str] | None = None,
                    offset: int = 0, limit: int = 10, include_profile: bool = False,
                    sample_rows: int = 5, profile_rows: int | None = None, refresh_schema: bool = False) -> dict:
    """批量查看授权表字段；include_profile=true 一并返回各表的轻量样本画像，省去逐表工具往返。
    轻量画像默认读取最多 100 行，不查总行数和全范围最值；不能当作业务覆盖证据。
    sample_rows=0 且未指定 profile_rows 时不读取样本。按 next_offset 翻完授权表；单表完整统计用 describe_table(mode='full')。
    数据库字段结构短期缓存，可用 refresh_schema=true 刷新；权限每次重新校验。"""
    try:
        acc = _access(ctx)
        _require_dataset(acc, dataset_id)
        return _ok({"ok": True, **_describe_tables_page(
            acc, dataset_id, table_names=table_names, offset=offset, limit=limit,
            include_profile=include_profile, sample_rows=sample_rows, profile_rows=profile_rows,
            refresh_schema=refresh_schema)})
    except _EXPECTED as e:
        return _err(e)


@_tool()
def prepare_report_context(dataset_id: int, ctx: Context, query: str = "",
                           sample_rows: int = 5, profile_rows: int = 100) -> dict:
    """一步加载业务场景（分析思路、授权表、官方指标）、前 10 张表轻量画像、生效样式（含 style_id）与 Markdown 指南。"""
    try:
        acc = _access(ctx)
        _require_dataset(acc, dataset_id)
        ds = repo.get_dataset(dataset_id)
        tables_page = _describe_tables_page(
            acc, dataset_id, ds=ds, offset=0, limit=10, include_profile=True,
            sample_rows=sample_rows, profile_rows=profile_rows,
        )
        style = _resolve_style(acc.principal, ds["business_scenario"], query=query, include_css=False)
        templates = repo.list_templates(owner=_owner(acc), dataset_id=dataset_id, allowed_datasets=acc.dataset_ids())
        return _ok({
            "ok": True,
            "scenario": _dataset_brief(ds, acc),
            "tables": tables_page["tables"],
            "total_tables": tables_page["total"],
            "next_offset": tables_page["next_offset"],
            "style": style,
            "templates": [_template_brief(row) for row in templates[:50]],
            "html_assembly_hint": "先确定或通过 save_report_template 保存关联本业务场景的 Markdown 报告模板（必备产物），"
                                  "再用 create_html_draft 绑定模板（template_id、template_version）、数据块与 snapshot_id"
                                  "（可传 sections 预置有序章节槽位），再分轮或并行编写章节。浏览器端通过 window.SRG.rows(blockId) 访问授权快照。",
        })
    except _EXPECTED as e:
        return _err(e)


@_tool()
def describe_table(dataset_id: int, table_name: str, ctx: Context, sample_rows: int = 5,
                   mode: Literal["light", "full"] = "light", profile_rows: int | None = None,
                   refresh_schema: bool = False) -> dict:
    """查看授权表字段、样本及画像。默认 light 读取最多 100 行，跳过总行数和全范围最值。
    full 恢复完整探查（默认 2,000 行画像、COUNT 和数值/日期最值），仅在需要这些技术统计时使用。
    light 下 sample_rows=0 且未指定 profile_rows 时仅取元数据；profile_rows 可指定 0–2,000。
    row_count_status/extrema_status 明确统计是否执行；轻量结果及样本不能证明全量业务覆盖。"""
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
        sample_rows = max(0, min(sample_rows, 50))
        if mode not in ("light", "full"):
            raise repo.Invalid("探查模式必须为 light 或 full")
        default_rows = (2000 if mode == "full" else 100) if sample_rows else 0
        rows = profile_rows if profile_rows is not None else default_rows
        return _ok({"ok": True, **services.describe_table(ds_row, table_name, sample_rows,
                                                          max(0, min(rows, 2000)), dataset=ds, allowed=allowed,
                                                          include_count=mode == "full", include_extrema=mode == "full",
                                                          refresh_schema=refresh_schema)})
    except _EXPECTED as e:
        return _err(e)


@_tool()
def run_query(dataset_id: int, sql: str, ctx: Context, limit: int | None = None,
              snapshot: bool = False, sample_rows: int = 5) -> dict:
    """在业务场景上执行只读 SQL（单条 SELECT/WITH），只能引用该场景中当前员工有权限的表或视图。
    场景的逐表取值条件由服务端在 JOIN、聚合前自动应用。
    SQL 方言与数据源类型一致（API 使用 DuckDB）。返回实际引用的 tables_used，列名统一转小写。
    普通模式默认返回至多 200 行；支持按正式业务口径进行只读聚合、算术、排序及去重。
    snapshot=true 将完整 SQL 结果保存为 JSON，由报告使用 snapshot_id 引用。
    MCP 仅返回行数和少量明细样本（sample_rows 0–200），≤200 行的正式查询可一次同时取得完整证据与快照。
    truncated=true 表示超过返回行数上限，未创建快照；请缩小到所需正式指标或完整分批查询。
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
                result["warning"] = "查询结果超过行数上限，未保存快照。请缩小到所需正式指标或按完整口径分批查询。"
            else:
                with measure("snapshot.write"):
                    # 来源是否完整和权限是两项证据，探查快照也要记录经校验的表与行范围。
                    origin = {"scope": report_queries.scope_for(dataset_id, ds, ds_row, result["tables_used"]),
                              "consistency": "single_statement", "scales": {},
                              "source_complete": result.get("source_complete", True)}
                    result.update(report_data.save_query(acc.principal.emp_no, result["columns"], rows,
                                                         max(0, min(sample_rows, 200)), origin=origin))
                if result.get("source_complete") is False:
                    result["warning"] = "API 未确认全量，该快照仅供探查，正式报告须取得来源确认完整的快照。"
        return _ok({"ok": True, **result})
    except _EXPECTED as e:
        return _err(e)


@_tool()
def get_skill_guide(include_example: bool = False,
                    reference: Literal["fact-contract", "display-contract", "html-assembly", "parallel-subagents"] | None = None,
                    asset: Literal["report-formatters.js"] | None = None) -> str:
    """返回 smart-report 技能原文，可附加取数、展示、HTML 拼装或子智能体并行编排说明。"""
    if asset is not None:
        if asset != "report-formatters.js" or reference is not None or include_example:
            raise ValueError("资产仅支持 report-formatters.js，须单独读取")
        return (SKILL_DIR / "assets" / asset).read_text(encoding="utf-8")
    text = _skill_body()
    if reference is not None:
        if reference not in {"fact-contract", "display-contract", "html-assembly", "parallel-subagents"}:
            raise ValueError("不支持的技能参考文档")
        text += "\n\n---\n\n" + (SKILL_DIR / "references" / f"{reference}.md").read_text(encoding="utf-8")
    if include_example:
        text += "\n\n## 附：筛选联动 HTML 示例\n\n```html\n" + EXAMPLE_FILE.read_text(encoding="utf-8") + "\n```\n"
    return text


@mcp.prompt(name="smart_report", description="按 SmartReportGen 流程，根据需求生成交互式 HTML 分析报告")
def smart_report_prompt(requirement: str) -> str:
    return (_skill_body() + "\n\n---\n\n## 本次需求\n\n" + requirement +
            "\n\n请严格按以上流程，使用 smartreportgen MCP 工具完成报告并交付验收通过的版本。")


def _resolve_style(principal: Principal, business_scenario: str, query: str = "", include_css: bool = True) -> dict:
    res = repo.style_rules_for(business_scenario, query or None, owner=principal.emp_no)
    builtin = design.builtin_style(res["specified"]["theme"])
    if include_css:
        try:
            builtin["style_id"] = report_data.save_style(principal.emp_no, builtin["css"])
        except report_data.ReportDataError:
            pass
    else:
        css = builtin.pop("css")
        builtin["style_id"] = report_data.save_style(principal.emp_no, css)
    rules = [{"id": r["id"], "name": r["name"], "scope": r["scope"],
              **({"relevance": r["relevance"]} if r.get("relevance") else {})}
             for r in res["general_rules"] + res["scenario_rules"]]
    return {
        "ok": True,
        "business_scenario": res["business_scenario"],
        "precedence": res["precedence"],
        "reporting_rules": res["reporting_rules"],
        "specified": res["specified"],
        # 自编 HTML 可以直接复用内置组件样式；指定主题和展示要求仍通过原有字段返回。
        "builtin_style": builtin,
        "rules": rules,
        "note": ("reporting_rules 提供跨场景的事实、展示和交付规则。事实与交付要求独立生效，展示默认值按 precedence 应用。"
                 "specified 合并了业务场景、通用及内置样式。直接编写 HTML 时，请将适用的 theme 和 items 落实到 CSS、图表配置与脚本。"
                 "采用内置样式时，可以内联 builtin_style.css 并参考组件说明；同类卡片复用 CSS 与图表配置。"
                 "items 各类别内排在前面的优先，请落实到健康度、升降色、数字格式与文案。"
                 "当前用户明确展示要求优先，低层许可不能豁免高层禁止；不得以智能体判断放宽数字格式、单位、口径、缺失处理和状态色。"
                 "章节、图表类型、筛选维度及未指定的装饰样式由用户需求和数据证据决定。"
                 "草稿引用 builtin_style.style_id 后自动内联主题。交付前复核展示，再提交 Chromium 校验。"),
        "warnings": res["warnings"],
    }


@_tool()
def get_style_rules(business_scenario: str, ctx: Context, query: str = "", include_css: bool = True) -> dict:
    """检索该业务场景的指定样式：你本人为该场景配置的样式 ≥ 管理员配置的通用规则 ≥ 内置经营报告主题，合并为一份 specified，
    作为展示约束交给你：specified.theme 为视觉样式取值（渲染时自动生效），specified.items 为好坏判定、涨跌颜色、数字格式、
    文案等展示要求。章节、图表类型和筛选条件由用户需求、场景分析思路及数据证据决定。
    reporting_rules 提供跨场景的事实、展示和交付规则。事实与交付要求独立生效，展示默认值按下方优先级应用。
    builtin_style 提供 style_id（可通过 <style data-srg-style="style_id"></style> 由服务端展开）及组件说明；
    传 include_css=false 可省略 22KB 内置 CSS 原文以减少上下文占用。
    展示顺序：当前用户明确要求 > 业务场景规则 > 通用规则 > 内置默认值。
    低优先级条目及智能体判断不能豁免高优先级规则；仅章节、图表、筛选维度及未指定装饰样式可裁量。
    将生效要求落实到全部展示面，按技能门禁通过后保存。"""
    try:
        p = _principal(ctx)
        return _ok(_resolve_style(p, business_scenario, query=query, include_css=include_css))
    except _EXPECTED as e:
        return _err(e)


# ============================== 5–7. 模板与报告 ==============================
@_tool()
def query_report_data(dataset_id: int, queries: dict[report_queries.Key, report_queries.QueryPlan], ctx: Context,
                      parameters: dict[report_queries.Key, report_queries.ParamValue] | None = None,
                      sample_rows: Annotated[int, Field(ge=0, le=200)] = 5) -> dict:
    """同一批次取数，可信结果可用于多快照报告。只允许投影字段或调用场景登记的官方指标。"""
    try:
        acc = _access(ctx)
        _require_dataset(acc, dataset_id)
        if not 1 <= len(queries) <= 16:
            raise repo.Invalid("每一批取数包含 1 至 16 条计划")
        dataset = repo.get_dataset(dataset_id)
        source = services.datasource_for_dataset(dataset)
        if not dataset.get("enabled", True):
            raise PermissionDenied("该业务场景已停用")
        results = report_queries.capture(dataset_id, dataset, source,
                     None if acc.all else acc.tables.get(dataset_id, set()), acc.principal.emp_no,
                     queries, parameters or {}, settings.query_max_rows, sample_rows)
        return _ok({"ok": True, "queries": results})
    except _EXPECTED as e:
        return _err(e)


@_tool()
def get_preview_file(preview_id: str, ctx: Context, offset: Annotated[int, Field(ge=0)] = 0,
                     chars: Annotated[int, Field(ge=1, le=24000)] = 12000) -> dict:
    """通过已登录的 MCP 读取验收预览，按字符分页，无需向 Shell 或浏览器传 IAM 令牌。"""
    try:
        _, page = report_pipeline.load_preview(_access(ctx).principal, preview_id, certified=True)
        end = min(offset + chars, len(page))
        return _ok({"ok": True, "html": page[offset:end], "offset": offset,
                    "next_offset": end if end < len(page) else None, "total_chars": len(page),
                    "html_sha256": sha256(page.encode()).hexdigest()})
    except _EXPECTED as e:
        return _err(e)


def _template_brief(row: dict) -> dict:
    return {"template_id": row["id"], "name": row["name"], "version": row["version"],
            "dataset_id": row["dataset_id"], "business_scenario": row["business_scenario"],
            "business_summary": row["business_summary"], "sections": row["summary"].get("sections", []),
            "summary": row["summary"], "user_requirement": row["user_requirement"], "updated_at": row["updated_at"]}


@_tool()
def list_report_templates(ctx: Context, keyword: str = "", dataset_id: int | None = None) -> dict:
    """按关键词或业务场景检索自己创建或已授权场景的 Markdown 报告模板指南。"""
    acc = _access(ctx)
    rows = repo.list_templates(keyword or None, owner=_owner(acc), dataset_id=dataset_id,
                               allowed_datasets=acc.dataset_ids())
    return _ok({"ok": True, "templates": [_template_brief(row) for row in rows[:50]]})


@_tool()
def get_report_template(template_id: int, ctx: Context, version: int | None = None) -> dict:
    """读取用于编写 HTML 报告的完整指南。可指定历史版本获取当时的 Markdown 正文。"""
    acc = _access(ctx)
    tpl = repo.get_template(template_id)
    repo.require_template_access(tpl, acc)
    if version is not None:
        tpl = {**tpl, **repo.get_template_version(template_id, version)}
        tpl["summary"] = repo._template_summary(tpl["markdown"], tpl["name"])
    return _ok({"ok": True, **tpl, "template_id": tpl["id"]})


@_tool()
def save_report_template(name: str, markdown: str, ctx: Context, dataset_id: int | None = None,
                         user_requirement: str = "", analysis_goal: str = "", business_summary: str | None = None,
                         template_id: int | None = None, expected_version: int | None = None) -> dict:
    """新建或更新 Markdown 报告指南，记录可复用的取数、指标、章节、图表、联动及布局要求。
    报告模板是生成和修订报告的必备产物：新建时请传入 dataset_id 关联业务场景，更新时可传 expected_version 检查并行修改。"""
    acc = _access(ctx)
    data = {"name": name, "markdown": markdown, "user_requirement": user_requirement, "analysis_goal": analysis_goal}
    if dataset_id is not None:
        _require_dataset(acc, dataset_id)
        data["dataset_id"] = dataset_id
    if template_id is not None:
        current = repo.get_template(template_id)
        _check_owner(acc, current["created_by"], "报告模板")
        # 未传场景时沿用原关联；操作人的场景权限仍须有效。
        if dataset_id is None and current["dataset_id"] is not None:
            _require_dataset(acc, current["dataset_id"])
        data = {k: v for k, v in data.items() if k in ("markdown", "dataset_id") or v != ""}
    else:
        data["created_by"] = acc.principal.emp_no
    if business_summary is not None:
        data["business_summary"] = business_summary
    with measure("template.save"):
        tpl = repo.save_template(data, template_id, expected_version=expected_version)
    return _ok({"ok": True, **_template_brief(tpl), "template_type": "business"})


@_tool()
def create_html_draft(title: str, ctx: Context, dataset_id: int | None = None, snapshots: dict[str, str] | None = None,
                      head_css: str = "", shell_html: str = "", shared_js: str = "", style_id: str | None = None,
                      use_echarts: bool = True, template_id: int | None = None, template_version: int | None = None,
                      report_id: int | None = None, base_version: int | None = None,
                      sections: list[dict[str, Any]] | None = None) -> dict:
    """创建多轮或并行拼装的 HTML 草稿。新建报告须绑定已保存的 template_id 和 template_version；
    可传 sections 预置有序章节槽位，提供报告和基准版本时复用已保存的源码与快照。"""
    with measure("draft.create"):
        result = report_draft.create_draft(
            title, _access(ctx).principal, dataset_id=dataset_id, snapshots=snapshots, head_css=head_css,
            shell_html=shell_html, shared_js=shared_js, style_id=style_id, use_echarts=use_echarts,
            template_id=template_id, template_version=template_version, report_id=report_id, base_version=base_version,
            sections=sections,
        )
    return _ok({"ok": True, **result})


@_tool()
def upsert_html_section(draft_id: str, ctx: Context, section_id: str | None = None, html: str = "", title: str = "",
                        css: str = "", js: str = "", after_section_id: str | None = None, delete: bool = False,
                        sections: list[dict[str, Any]] | None = None, snapshots: dict[str, str] | None = None,
                        head_css: str | None = None, shell_html: str | None = None, shared_js: str | None = None,
                        template_id: int | None = None, template_version: int | None = None) -> dict:
    """增改、移动或删除章节，可批量应用。批量元素支持 index 和显式空字符串清除字段，也可更新草稿绑定的模板版本。"""
    acc = _access(ctx)
    owner = acc.principal.emp_no
    if sections is not None and section_id is not None:
        raise repo.Invalid("请使用 section_id 更新单个章节，或使用 sections 批量更新")
    if section_id is not None:
        patch = {"section_id": section_id, "delete": delete}
        patch.update({key: value for key, value in (("html", html), ("title", title), ("css", css), ("js", js))
                      if value})
        if after_section_id is not None:
            patch["after_section_id"] = after_section_id
        sections = [patch]
    elif any((html, title, css, js, delete, after_section_id)):
        raise repo.Invalid("单个章节的修改须提供 section_id")
    with measure("draft.upsert"):
        result = report_draft.upsert_sections(draft_id, owner, sections or [], snapshots=snapshots, head_css=head_css,
                                              shell_html=shell_html, shared_js=shared_js,
                                              template_id=template_id, template_version=template_version,
                                              principal=acc.principal)
    return _ok({"ok": True, **result})


@_tool()
def get_html_draft(draft_id: str, ctx: Context, include_code: bool = False, section_id: str | None = None,
                   section_ids: list[str] | None = None) -> dict:
    """查询草稿进度、最近校验和失败诊断。必要时读取指定章节（支持 section_ids 批量或 '__shared__' 全局源码）或全页草稿源码。"""
    with measure("draft.read"):
        result = report_draft.get_draft_status(draft_id, _access(ctx).principal.emp_no,
                                               include_code=include_code, section_id=section_id,
                                               section_ids=section_ids)
    return _ok({"ok": True, **result})


@_tool()
def verify_html_draft(ctx: Context, draft_id: str | None = None, html: str | None = None,
                      dataset_id: int | None = None, report_id: int | None = None, base_version: int | None = None,
                      template_id: int | None = None, template_version: int | None = None) -> dict:
    """展开报告，使用离线 Chromium 校验，并返回可审阅和入库的预览。"""
    result = report_pipeline.verify_and_stage(_access(ctx).principal, draft_id=draft_id, html=html,
                                               dataset_id=dataset_id, report_id=report_id, base_version=base_version,
                                               template_id=template_id, template_version=template_version)
    return _ok({"ok": True, **result})


@_tool()
def preview_report_html(html: str, ctx: Context, report_id: int | None = None,
                        base_version: int | None = None, dataset_id: int | None = None,
                        template_id: int | None = None, template_version: int | None = None) -> dict:
    """完整 HTML 的兼容预览入口，返回通过 Chromium 校验的预览。"""
    result = report_pipeline.verify_and_stage(_access(ctx).principal, html=html, dataset_id=dataset_id,
                                               report_id=report_id, base_version=base_version,
                                               template_id=template_id, template_version=template_version)
    return _ok({"ok": True, **result})


def _report_delivery(row: dict) -> dict:
    verification = row["meta"].get("verification") or {}
    return {"ok": True, "report_id": row["id"], "version": row["revision"],
            "template_id": row["template_id"], "template_version": row["template_version"],
            **repo.report_links(row["id"], row["revision"]), "html_bytes": row["meta"].get("html_bytes"),
            "verification": verification, "warnings": verification.get("warnings", [])}


@_tool()
def save_report(name: str, ctx: Context, draft_id: str | None = None, preview_id: str | None = None,
                html: str | None = None, dataset_id: int | None = None, template_id: int | None = None,
                template_version: int | None = None) -> dict:
    """保存草稿、通过校验的预览或完整 HTML。必须绑定已保存的 template_id 和 template_version（可由草稿/预览继承）。
    草稿和 HTML 按需自动完成离线 Chromium 校验。"""
    row = report_pipeline.persist_report(name, _access(ctx).principal, draft_id=draft_id, preview_id=preview_id,
                                         html=html, dataset_id=dataset_id, template_id=template_id,
                                         template_version=template_version)
    return _ok(_report_delivery(row))



@_tool()
def list_reports(ctx: Context, keyword: str = "", template_id: int | None = None) -> dict:
    """查找可访问的报告，每份报告返回一项，含当前版本及最新版本号。"""
    try:
        rows = repo.list_reports(_access(ctx).principal, template_id, keyword)
    except _EXPECTED as e:
        return _err(e)
    return _ok({"ok": True, "reports": [
        {"report_id": r["id"], "name": r["name"], "current_version": r["current_version"],
         "latest_version": r["latest_version"], "access_level": r["access_level"],
         "template_id": r["template_id"], "template_version": r["template_version"],
         "template_name": r["template_name"], "template_latest_version": r["template_latest_version"],
         "created_by": r["created_by"], **repo.report_links(r["id"])} for r in rows[:50]
    ], "total": len(rows)})


@_tool()
def get_report(report_id: int, ctx: Context, include_html: bool = False, version: int | None = None,
               include_source: bool = False, omit_snapshot_data: bool = False) -> dict:
    """读取可访问的报告。显式 version 优先，省略时读取本账号的当前选择或最新版。
    include_html 返回该版本的 HTML；include_source 返回有权限读取的版本分块源码或历史源码。
    同时传 omit_snapshot_data=true 时，快照数据替换为可复用的基准引用，避免把它们读入模型。"""
    try:
        r = repo.get_report(report_id, _access(ctx).principal, with_html=include_html,
                            version=version, with_source=include_source)
        if include_html and omit_snapshot_data:
            r["html"] = report_data.for_edit(r["html"])
    except _EXPECTED as e:
        return _err(e)
    return _ok({"ok": True, **r, **repo.report_links(r["id"], r["revision"])})


@_tool()
def list_report_versions(report_id: int, ctx: Context) -> dict:
    """列出报告版本、基准版本、修改人、修改说明及当前选择。"""
    try:
        return _ok({"ok": True, **repo.list_report_versions(report_id, _access(ctx).principal)})
    except _EXPECTED as e:
        return _err(e)


@_tool()
def select_report_version(report_id: int, ctx: Context, version: int | None = None) -> dict:
    """确认本账号后续查看和修改使用的版本。version 留空恢复跟随最新版本。"""
    try:
        r = repo.select_report_version(report_id, _access(ctx).principal, version)
    except _EXPECTED as e:
        return _err(e)
    return _ok({"ok": True, **r, **repo.report_links(r["id"], r["revision"])})


@_tool()
def revise_report(report_id: int, name: str, ctx: Context, base_version: int | None = None,
                  draft_id: str | None = None, preview_id: str | None = None, html: str | None = None,
                  template_id: int | None = None, template_version: int | None = None,
                  change_note: str = "", expected_version: int | None = None) -> dict:
    """追加报告版本。正文支持草稿、已校验预览或完整 HTML，修改正文须同步更新报告模板并绑定新版 template_id/template_version。
    只改名称或说明时省略正文。"""
    row = services.revise_report(report_id, _access(ctx).principal, name, base_version=base_version,
                                 draft_id=draft_id, preview_id=preview_id, html=html,
                                 template_id=template_id, template_version=template_version,
                                 change_note=change_note, expected_version=expected_version)
    return _ok({**row, **_report_delivery(row)})


# ============================== 启动 ==============================
class TokenAuth:
    """MCP 仅接受 IAM Bearer 令牌，核实后的员工身份放入请求 state 供工具复用。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/mcp"):
            authorization = Headers(scope=scope).get("authorization")
            # 只记录 Authorization 请求头是否存在，避免把 IAM 令牌写入日志。
            log.info("MCP authorization_present=%s", authorization is not None)
            try:
                principal = await run_in_threadpool(auth.verify_bearer, authorization)
            except auth.AuthUnavailable as e:
                await JSONResponse({"error": "iam_unavailable", "message": str(e)}, status_code=503)(scope, receive, send)
                return
            except AuthError as e:
                resp = JSONResponse({"error": "unauthorized", "message": str(e)}, status_code=401,
                                    headers={"WWW-Authenticate": 'Bearer realm="smartreportgen"'})
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
    app.add_middleware(TokenAuth)
    return app


def main() -> None:
    db.init_db()
    repo.migrate_report_versions()
    repo.load_custom_words()
    try:
        uvicorn.run(build_app(), host="0.0.0.0", port=settings.mcp_port, log_level="info")
    finally:
        report_browser.close_pool()


if __name__ == "__main__":
    main()
