"""声明式报告预览、认证交付与版本证据。"""
from __future__ import annotations

from hashlib import sha256
import json
from uuid import UUID

from . import repo, report_browser, report_data, report_queries, report_spec
from .config import settings
from .permission import PermissionDenied, access_for
from .telemetry import measure


def _hash(text: str) -> str:
    return sha256(text.encode()).hexdigest()


def capture(spec: report_spec.ReportDefinition, dataset_id, principal, parameters: dict, sample_rows=5):
    dataset = repo.get_dataset(dataset_id)
    access = access_for(principal)
    if not access.can_dataset(dataset_id) or not dataset["enabled"]:
        raise PermissionDenied("无权读取该业务场景")
    source = repo.get_datasource(dataset["datasource_id"], with_secret=True)
    return report_queries.capture(dataset_id, dataset, source,
                                  None if access.all else access.tables.get(dataset_id, set()),
                                  principal.emp_no, spec.queries, parameters, settings.query_max_rows, sample_rows)


def preview(spec, dataset_id, principal, *, bindings=None, parameters=None, template=None,
            base=None, requested_report_id=None):
    if bindings is not None and parameters is not None:
        raise repo.Invalid("快照绑定和取数参数只能提供一种")
    if bindings is not None:
        snapshot_source = "provided"
        with measure("report.snapshot.load"):
            snapshots = {key: report_data.read_snapshot(principal.emp_no, ref) for key, ref in bindings.items()}
    elif parameters is None and base and base["meta"].get("snapshot_origins"):
        # 纯展示修订沿用已存版本的数据。读取同一个基准后仍要重新校验当前权限与新绑定。
        snapshot_source = "reused_base"
        with measure("report.snapshot.reuse"):
            stored = repo.get_report(base["id"], principal, version=base["revision"])
            old_origins = stored["meta"]["snapshot_origins"]
            if _hash(stored["html"]) != stored["meta"]["verification"]["html_sha256"]:
                raise repo.Invalid("基准报告正文与验收证据不一致")
            blocks = report_data._DataBlocks(stored["html"]).blocks
            block = next((item for item in blocks if item.fields.get("id") == "srg-data"), None)
            if not block:
                raise repo.Invalid("基准版本缺少可复用的事实")
            data = json.loads(block.data)["data"]
            snapshots = {key: {**data[key], "origin": old_origins[key]} for key in report_spec._get_fields(spec)
                         if key in data and key in old_origins}
    else:
        if not spec.queries:
            raise repo.Invalid("该模板未保存取数计划，请提交有来源证明的快照")
        snapshot_source = "captured"
        with measure("report.snapshot.capture"):
            refs = capture(spec, dataset_id, principal, parameters or {}, sample_rows=0)
            snapshots = {key: report_data.read_snapshot(principal.emp_no, item["snapshot_id"]) for key, item in refs.items()}
    if spec.queries:
        for key, plan in spec.queries.items():
            snap_origin = snapshots.get(key, {}).get("origin", {})
            snap_hash = snap_origin.get("plan_sha256")
            if not snap_hash:
                raise repo.Invalid(
                    f"快照 {key} 来自 run_query 探查而非声明式取数计划；"
                    "请改用 query_report_data 生成快照，或在预览时省略 bindings 直接传 parameters"
                )
            if snap_hash != report_queries.plan_hash(plan):
                raise repo.Invalid(f"快照 {key} 的取数计划与模板版本不一致")
    dataset = repo.get_dataset(dataset_id)
    theme = repo.style_rules_for(dataset["business_scenario"], owner=principal.emp_no)["specified"]["theme"]
    with measure("spec.compile"):
        source, page, scopes = report_spec.compile_report(spec, snapshots, dataset_id, theme)
    report_spec.validate_scopes(scopes, principal)
    with measure("spec.browser"):
        evidence = report_browser.verify(page, spec, snapshots, dataset_id)
    evidence.update({"source_sha256": _hash(source), "html_sha256": _hash(page), "static_audit": "passed",
                     "snapshot_source": snapshot_source,
                     "template_mode": "structured" if isinstance(spec, report_spec.ReportSpec) else "free_html"})
    meta = {"source": "compiled_spec", "scopes": scopes, "verification": evidence,
            "template_id": template["id"] if template else None,
            "template_version": template["version"] if template else None,
            "spec_json": report_spec.encode_template(spec, dataset_id), "dataset_id": dataset_id,
            "snapshot_origins": {key: value["origin"] for key, value in snapshots.items()}}
    ref = report_data.save_preview(principal.emp_no, page, source_html=source, proof=meta,
                                   report_id=base["id"] if base else None, requested_report_id=requested_report_id,
                                   base_version=base["revision"] if base else None)
    url = f"{settings.public_base_url}/reports/preview/{ref}"
    return {"preview_id": str(ref), "url": url, "download_url": f"{url}?download=true",
            "expires_in_seconds": report_data.TTL_SECONDS, "html_bytes": len(page.encode()), "verification": evidence,
            "warnings": evidence.get("warnings", []),
            "template_id": meta["template_id"], "template_version": meta["template_version"], "report_id": requested_report_id,
            **{k: evidence[k] for k in ("source_sha256", "html_sha256")}}


def load_preview(principal, ref: str, *, certified=False) -> tuple[dict, str]:
    meta = report_data.read_preview_source(principal.emp_no, ref)
    proof = meta.get("proof")
    if certified and not proof:
        raise repo.Invalid("此预览没有服务端验收证据，请用声明式模板预览")
    if meta["report_id"] is not None:
        report = repo.get_report(meta["report_id"], principal, with_html=False,
                                 version=meta["base_version"])
        if report["access_level"] not in ("edit", "manage"):
            raise PermissionDenied("无权使用该报告的修订预览")
    page = report_data.read_preview(principal.emp_no, UUID(ref))
    if page is None:
        raise repo.NotFound("预览已过期，请重新生成")
    if proof:
        evidence = proof["verification"]
        if (evidence.get("status") != "passed" or evidence.get("static_audit") != "passed"
                or _hash(page) != evidence.get("html_sha256") or _hash(meta["html"]) != evidence.get("source_sha256")):
            raise repo.Invalid("预览证据与报告内容不一致，请重新生成")
        report_spec.validate_scopes(proof["scopes"], principal)
    return meta, page


def persist(name, principal, ref, *, template_id=None, template_version=None, report_id=None,
            base_version=None, expected_version=None, change_note=""):
    src, page = load_preview(principal, ref, certified=True)
    proof = src["proof"]
    if template_id is not None and proof["template_id"] != template_id or (
            template_version is not None and proof["template_version"] != template_version):
        raise repo.Invalid("模板与已验收的预览不一致")
    if proof["template_id"] is not None:
        tpl = repo.get_template_version(proof["template_id"], proof["template_version"])
        # 新增展示选项时，旧模板按当前默认值归一化后比对，避免旧版本被误判为篡改。
        original = report_spec.decode_template(tpl["markdown"])
        staged = report_spec.decode_template(proof["spec_json"])
        if original is None or original != staged:
            raise repo.Invalid("预览与模板版本的声明式内容不一致")
    source = {"markdown": proof["spec_json"], "dataset_id": proof["dataset_id"], "source_owner": principal.emp_no,
              "template_id": proof["template_id"], "template_version": proof["template_version"]}
    meta = {k: proof[k] for k in ("source", "scopes", "verification", "snapshot_origins")}
    if report_id is None:
        if src["report_id"] is not None:
            raise repo.Invalid("此预览对应现有报告，请保存为修订版本")
        return repo.save_report(name, page, proof["template_id"], proof["template_version"], meta,
                                principal.emp_no, source=source)
    if src["report_id"] is None or report_id not in {src["report_id"], src["requested_report_id"]} or (
            base_version is not None and base_version != src["base_version"]):
        raise repo.Invalid("预览与报告或基准版本不一致")
    return repo.update_report(src["report_id"], principal, name, page, src["base_version"],
                              change_note=change_note, expected_version=expected_version, rendered=(meta, source))
