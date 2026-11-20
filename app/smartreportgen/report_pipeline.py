"""展开、校验、暂存和发布离线 HTML 报告。"""
from __future__ import annotations

import logging
from hashlib import sha256
from time import perf_counter
from uuid import UUID

from . import repo, report_browser, report_data, report_document, report_draft, report_queries
from .config import settings
from .permission import PermissionDenied, access_for
from .report_browser import ReportVerifyError
from .telemetry import failure_stage, measure

log = logging.getLogger(__name__)


def _hash(text: str) -> str:
    return sha256(text.encode()).hexdigest()


def _scopes(source: str, principal, dataset_id: int | None, base: dict | None, *, inherit: bool) -> tuple[list[dict], int]:
    scopes = {}
    bindings = [block for block in report_data._DataBlocks(source).blocks if block.kind == "snapshot"]
    reuse = inherit or not bindings
    for block in bindings:
        ref = block.fields[report_data.MARKER]
        block_id = block.fields["id"]
        if ref == "base":
            if base is None:
                raise repo.Invalid(f"数据块 {block_id} 引用了不存在的基准版本")
            reuse = True
        elif ref != "embedded":
            payload = report_data.read_snapshot(principal.emp_no, ref)
            origin = payload.get("origin") or {}
            scope = origin.get("scope")
            if not scope:
                raise PermissionDenied(f"数据块 {block_id} 缺少来源授权，请重新取得完整快照")
            if origin.get("source_complete") is False:
                raise repo.Invalid(f"数据块 {block_id} 的接口数据仅供探查，请重新取得来源确认完整的快照")
            scopes[report_data._json(scope)] = scope
        else:
            # 内联事实可能由基准 HTML 复制而来，始终继承基准权限指纹。
            reuse = True
    if reuse and base:
        for scope in base["meta"].get("scopes") or []:
            scopes[report_data._json(scope)] = scope
    if dataset_id is None:
        dataset_id = next(iter(scopes.values()))["dataset_id"] if scopes else None
    if scopes and dataset_id not in {scope["dataset_id"] for scope in scopes.values()}:
        raise repo.Invalid("报告的业务场景与已授权的快照来源不一致")
    if not scopes:
        if dataset_id is None:
            raise repo.Invalid("请指定业务场景或绑定有来源权限的快照")
        if base and not base["meta"].get("scopes") and reuse:
            raise PermissionDenied("基准报告缺少数据授权凭证，请重取数据后重建正文")
        # 无快照报告使用该员工在场景内当时可见的表指纹，便于将来撤权后复核。
        access = access_for(principal)
        if not access.can_dataset(dataset_id):
            raise PermissionDenied("无权访问报告关联的业务场景")
        dataset = repo.get_dataset(dataset_id)
        source_row = repo.get_datasource(dataset["datasource_id"])
        tables = [t["table_name"] for t in dataset["tables"] if access.can_table(dataset_id, t["table_name"])]
        scope = report_queries.scope_for(dataset_id, dataset, source_row, tables)
        scopes[report_data._json(scope)] = scope
    report_queries.validate_scopes(list(scopes.values()), principal)
    return list(scopes.values()), dataset_id


def _verify(principal, *, draft=None, html=None, dataset_id=None, report_id=None, base_version=None,
            template_id=None, template_version=None) -> dict:
    started = perf_counter()
    if draft:
        draft.status, draft.last_error = "verifying", None
        report_draft.record(draft, "verify_start", started)
    try:
        with measure("draft.load_source"):
            base = report_draft.edit_base(principal, report_id, base_version) if report_id is not None else None
            source = report_draft.assemble_source_html(draft) if draft else html
            if not source:
                raise repo.Invalid("请提交草稿 ID 或完整 HTML")
            if base:
                dataset_id = dataset_id or base.get("dataset_id") or base["meta"].get("dataset_id")
                if template_id is None:
                    template_id, template_version = base.get("template_id"), base.get("template_version")
        with measure("report.scope"):
            scopes, dataset_id = _scopes(source, principal, dataset_id, base, inherit=draft is None and base is not None)
        legacy = draft.legacy_full_html if draft and not draft.shell_html else ""
        with measure("html.embed_and_check"):
            if draft:
                # 先保留快照标签与页面元素冲突的章节归因。历史整页扩展只允许沿用已有的重名。
                duplicates = report_draft.duplicate_dom_ids(source)
                if duplicates and legacy:
                    inherited = {item["id"]: item["count"] for item in report_draft.duplicate_dom_ids(legacy)}
                    duplicates = [item for item in duplicates if item["count"] > inherited.get(item["id"], 1)]
                if duplicates:
                    raise report_browser.duplicate_id_error(duplicates, "html.embed_and_check")
            page = report_data.embed(source, principal.emp_no, base["html"] if base else "")
            if base and not base["meta"].get("single_file"):
                page = report_document.inline_legacy_echarts(page)
            if error := report_document.report_document_error(page, structural_only=True):
                raise ReportVerifyError(error, "html.embed_and_check", {"message": error})
            page = report_document.enforce_offline_policy(page)
        with measure("chromium.verify"):
            expected = [section.section_id for section in draft.sections] if draft else None
            evidence = report_browser.verify_html(page, expected_sections=expected, allow_shared_duplicate_ids=bool(legacy))
        with measure("preview.save"):
            evidence.update(source_sha256=_hash(source), html_sha256=_hash(page))
            proof = {"source": "assembled_html", "scopes": scopes, "verification": evidence,
                     "dataset_id": dataset_id, "template_id": template_id, "template_version": template_version,
                     "draft_json": draft.to_source_json() if draft else None,
                     "draft_id": draft.draft_id if draft else None, "draft_revision": draft.revision if draft else None}
            ref = str(report_data.save_preview(
                principal.emp_no, page, source_html=source, proof=proof,
                report_id=base["id"] if base else None, requested_report_id=report_id,
                base_version=base["revision"] if base else None,
            ))
            if draft:
                draft.status = "verified"
                draft.last_preview_id, draft.last_verified_revision = ref, draft.revision
                draft.last_verification = evidence
                report_draft.record(draft, "verify_passed", started)
    except Exception as exc:
        if draft:
            draft.status = "verify_failed"
            draft.last_error = {"error": str(exc), "failed_stage": getattr(exc, "stage", failure_stage() or "draft.verify"),
                                "diagnostics": getattr(exc, "diagnostics", {})}
            try:
                report_draft.record(draft, "verify_failed", started)
            except report_data.ReportDataError:
                log.exception("无法记录草稿校验失败状态")
        raise
    url = f"{settings.public_base_url}/reports/preview/{ref}"
    return {"preview_id": ref, "url": url, "download_url": f"{url}?download=true",
            "expires_in_seconds": report_data.TTL_SECONDS, "html_bytes": len(page.encode()),
            "verification": evidence, "warnings": evidence.get("warnings", []),
            "template_id": template_id, "template_version": template_version, "report_id": report_id,
            "draft_id": proof["draft_id"], "revision": proof["draft_revision"],
            "source_sha256": evidence["source_sha256"], "html_sha256": evidence["html_sha256"]}


def _verify_draft(principal, draft, *, dataset_id=None, report_id=None, base_version=None,
                  template_id=None, template_version=None) -> dict:
    _check_base_match(principal, draft.report_id, draft.report_id, draft.base_version, report_id, base_version)
    if dataset_id is not None and draft.dataset_id is not None and dataset_id != draft.dataset_id:
        raise repo.Invalid("草稿与指定业务场景不一致")
    return _verify(principal, draft=draft, dataset_id=draft.dataset_id or dataset_id,
                   report_id=draft.report_id, base_version=draft.base_version,
                   template_id=template_id if template_id is not None else draft.template_id,
                   template_version=template_version if template_version is not None else draft.template_version)


def verify_and_stage(principal, *, draft_id=None, html=None, dataset_id=None, report_id=None,
                     base_version=None, template_id=None, template_version=None) -> dict:
    if (draft_id is None) == (html is None):
        raise repo.Invalid("请提供草稿 ID 或完整 HTML，两者只能选一")
    if base_version is not None and report_id is None:
        raise repo.Invalid("指定基准版本时须提供报告 ID")
    if draft_id is not None:
        draft = report_draft.load_draft(principal.emp_no, draft_id)
        with report_draft.lock_draft(principal.emp_no, draft.draft_id):
            return _verify_draft(principal, report_draft.load_draft(principal.emp_no, draft.draft_id),
                                 dataset_id=dataset_id, report_id=report_id, base_version=base_version,
                                 template_id=template_id, template_version=template_version)
    return _verify(principal, html=html, dataset_id=dataset_id, report_id=report_id, base_version=base_version,
                   template_id=template_id, template_version=template_version)


def load_preview(principal, ref: str, *, certified=False) -> tuple[dict, str]:
    meta = report_data.read_preview_source(principal.emp_no, ref)
    proof = meta.get("proof")
    if certified and (not proof or proof.get("source") != "assembled_html"):
        raise repo.Invalid("请提交通过当前 Chromium 流程校验的预览")
    if meta["report_id"] is not None:
        report_draft.edit_base(principal, meta["report_id"], meta["base_version"], with_html=False)
    page = report_data.read_preview(principal.emp_no, UUID(ref))
    if page is None:
        raise repo.NotFound("预览已过期，请重新生成")
    if proof:
        evidence = proof["verification"]
        if (evidence.get("status") != "passed" or _hash(page) != evidence.get("html_sha256")
                or _hash(meta["html"]) != evidence.get("source_sha256")):
            raise repo.Invalid("预览证据与报告内容不一致，请重新生成")
        if not proof.get("scopes"):
            raise PermissionDenied("预览缺少可验证的数据权限")
        report_queries.validate_scopes(proof["scopes"], principal)
    return meta, page


def _check_base_match(principal, actual_id, requested_id, actual_version, report_id, base_version):
    if report_id is not None and report_id not in (actual_id, requested_id):
        # 迁移前的报告链接可作为修订入口，以数据库当前实际 ID 确认是否为同一报告。
        if actual_id is None or report_draft.edit_base(principal, report_id, actual_version, with_html=False)["id"] != actual_id:
            raise repo.Invalid("正文与指定报告不一致")
    if base_version is not None and base_version != actual_version:
        raise repo.Invalid("正文与指定基准版本不一致")


def persist_report(name, principal, *, draft_id=None, preview_id=None, html=None, dataset_id=None, template_id=None,
                   template_version=None, report_id=None, base_version=None, expected_version=None, change_note="") -> dict:
    repo._report_name(name)
    if sum(item is not None for item in (draft_id, preview_id, html)) != 1:
        raise repo.Invalid("请从 draft_id、preview_id、html 中提供一种正文输入")
    if draft_id is not None:
        draft = report_draft.load_draft(principal.emp_no, draft_id)
        with report_draft.lock_draft(principal.emp_no, draft.draft_id):
            draft = report_draft.load_draft(principal.emp_no, draft.draft_id)
            if (draft.report_id is None) != (report_id is None):
                raise repo.Invalid("请按草稿绑定的报告选择新建或修订入口")
            _check_base_match(principal, draft.report_id, draft.report_id, draft.base_version, report_id, base_version)
            staged = None
            if draft.last_verified_revision == draft.revision and draft.last_preview_id:
                with measure("preview.load"):
                    try:
                        staged = load_preview(principal, draft.last_preview_id, certified=True)
                    except (report_data.ReportDataError, repo.NotFound):
                        # 临时预览失效时才重跑 Chromium。撤权、证据异常等错误直接返回。
                        pass
            if staged is None:
                preview_id = _verify_draft(principal, draft, dataset_id=dataset_id, report_id=report_id,
                                           base_version=base_version)["preview_id"]
                with measure("preview.load"):
                    staged = load_preview(principal, preview_id, certified=True)
            return _persist(name, principal, staged, report_id, base_version, expected_version, change_note,
                            dataset_id, template_id, template_version, draft)
    if html is not None:
        preview_id = verify_and_stage(principal, html=html, dataset_id=dataset_id, report_id=report_id,
                                      base_version=base_version, template_id=template_id,
                                      template_version=template_version)["preview_id"]
    with measure("preview.load"):
        staged = load_preview(principal, preview_id, certified=True)
    proof = staged[0]["proof"]
    if proof.get("draft_id"):
        with report_draft.lock_draft(principal.emp_no, proof["draft_id"]):
            try:
                draft = report_draft.load_draft(principal.emp_no, proof["draft_id"])
            except repo.NotFound:
                draft = None  # 独立验收的预览仍可在自己的有效期内提交。
            with measure("draft.check_revision"):
                if draft and draft.revision != proof["draft_revision"]:
                    raise repo.ReportConflict("草稿在预览后已修改，请重新校验当前版本")
            return _persist(name, principal, staged, report_id, base_version, expected_version, change_note,
                            dataset_id, template_id, template_version, draft)
    return _persist(name, principal, staged, report_id, base_version, expected_version, change_note,
                    dataset_id, template_id, template_version, None)


def _persist(name, principal, staged, report_id, base_version, expected_version, change_note,
             dataset_id, template_id, template_version, draft):
    started = perf_counter()
    src, page = staged
    proof = src["proof"]
    if (src["report_id"] is None) != (report_id is None):
        raise repo.Invalid("请按预览绑定的报告选择新建或修订入口")
    _check_base_match(principal, src["report_id"], src["requested_report_id"], src["base_version"], report_id, base_version)
    if dataset_id is not None and dataset_id != proof["dataset_id"]:
        raise repo.Invalid("指定业务场景与预览不一致")
    if template_id is None or template_id == proof["template_id"]:
        template_id = proof["template_id"]
        if template_version is None:
            template_version = proof["template_version"]
    with measure("report.persist"):
        source = {"markdown": proof["draft_json"], "dataset_id": proof["dataset_id"],
                  "source_owner": principal.emp_no, "template_id": template_id, "template_version": template_version}
        meta = {k: proof[k] for k in ("source", "scopes", "verification", "dataset_id")}
        if report_id is None:
            result = repo.save_report(name, page, template_id, template_version, meta, principal.emp_no, source=source)
        else:
            result = repo.update_report(src["report_id"], principal, name, page, src["base_version"],
                                        change_note=change_note, expected_version=expected_version, rendered=(meta, source))
    if draft:
        draft.status = "saved"
        draft.saved_report_id, draft.saved_version = result["id"], result["revision"]
        try:
            report_draft.record(draft, "save", started)
        except report_data.ReportDataError:
            # 数据库事务已完成，进度文件写入失败不应把入库结果报告为失败。
            log.exception("报告已入库，草稿状态写入失败")
    return result
