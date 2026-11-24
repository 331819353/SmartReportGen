"""按员工隔离的模块化 HTML 草稿和离线文档组装。"""
from __future__ import annotations

import fcntl
import json
import logging
import re
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields
from hashlib import sha256
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from time import perf_counter, time
from typing import Literal
from uuid import UUID, uuid4

from . import repo, report_data, report_document
from .config import settings
from .permission import PermissionDenied, access_for
from .telemetry import measure

log = logging.getLogger(__name__)
MARKER = "<!--SRG_SECTIONS-->"
SHARED_SECTION = "__shared__"
DEFAULT_SHELL = f'<div class="srg-page"><main id="srg-main">{MARKER}</main></div>'
BASE_CSS = """*{box-sizing:border-box}
body{margin:0;background:var(--color-bg,#f5f7fa);color:var(--color-text,#182636);font-family:system-ui,sans-serif}
.srg-page{max-width:1440px;margin:auto;padding:clamp(12px,3vw,36px)}
[data-srg-section]{margin-block:0 24px}
img{max-width:100%}
"""

SRG_BRIDGE_JS = """(() => {
  const cache = new Map();
  const charts = new Set();
  function raw(blockId) {
    if (!cache.has(blockId)) {
      const block = document.getElementById(blockId);
      if (!block) throw new Error('找不到数据块：' + blockId);
      cache.set(blockId, JSON.parse(block.textContent));
    }
    return cache.get(blockId);
  }
  // 原始行保留数据库精度，转换为 Number 只用在绘图等需要数值坐标的地方。
  function num(value) {
    const input = value?.$decimal ?? value?.$integer ?? value;
    return input == null || input === '' ? NaN : Number(input);
  }
  function rows(blockId) {
    const {columns, rows} = raw(blockId);
    return rows.map(row => Object.fromEntries(columns.map((column, i) => [column, row[i]])));
  }
  function initChart(domOrId, option) {
    const dom = typeof domOrId === 'string' ? document.getElementById(domOrId) : domOrId;
    if (!dom) throw new Error('找不到图表容器：' + domOrId);
    const chart = echarts.getInstanceByDom(dom) || echarts.init(dom);
    charts.add(chart);
    chart.setOption(option);
    return chart;
  }
  function section(sectionId) {
    // 章节脚本可以在自己的容器内用 data 属性定位节点，避免创建全页 id。
    const root = document.getElementById('srg-sec-' + sectionId);
    if (!root) throw new Error('找不到章节容器：' + sectionId);
    return root;
  }
  addEventListener('resize', () => {
    for (const chart of charts) {
      if (chart.isDisposed()) charts.delete(chart);
      else chart.resize();
    }
  });
  window.SRG = {raw, rows, num, charts, initChart, section};
  window.__srgErrors = [];
})();"""

IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}")
SOURCE_FIELDS = ("title", "dataset_id", "template_id", "template_version", "snapshots", "style_id",
                 "use_echarts", "head_css", "shell_html", "shared_js", "sections", "legacy_full_html")


@dataclass
class DraftSection:
    section_id: str
    title: str = ""
    html: str = ""
    css: str = ""
    js: str = ""
    updated_at: float = field(default_factory=time)


@dataclass
class HtmlDraft:
    draft_id: str
    owner: str
    title: str
    dataset_id: int | None = None
    template_id: int | None = None
    template_version: int | None = None
    report_id: int | None = None
    base_version: int | None = None
    snapshots: dict[str, str] = field(default_factory=dict)
    style_id: str | None = None
    use_echarts: bool = True
    head_css: str = ""
    shell_html: str = ""
    shared_js: str = ""
    sections: list[DraftSection] = field(default_factory=list)
    legacy_full_html: str = ""
    revision: int = 1
    status: Literal["assembling", "verifying", "verified", "verify_failed", "saved"] = "assembling"
    last_preview_id: str | None = None
    last_verified_revision: int | None = None
    last_verification: dict | None = None
    last_error: dict | None = None
    events: list[dict] = field(default_factory=list)
    saved_report_id: int | None = None
    saved_version: int | None = None

    def to_source_json(self) -> str:
        data = asdict(self)
        return report_data._json({"smartreportgen_html_draft": {key: data[key] for key in SOURCE_FIELDS}})

    def status_dict(self, *, include_code: bool = False, section_id: str | None = None,
                    section_ids: list[str] | None = None) -> dict:
        if section_id is not None and section_ids is not None:
            raise repo.Invalid("请使用 section_id 或 section_ids 之一指定章节")
        if section_ids is not None and not isinstance(section_ids, (list, tuple)):
            raise repo.Invalid("section_ids 须为章节标识列表")
        if section_id is not None:
            requested: list[str] | None = [section_id]
        elif section_ids is not None:
            requested = section_ids
        else:
            requested = None
        if requested is not None:
            existing = {section.section_id for section in self.sections}
            for sid in requested:
                if not isinstance(sid, str):
                    raise repo.Invalid("章节标识必须为字符串")
                if sid != SHARED_SECTION and sid not in existing:
                    raise repo.NotFound(f"草稿中没有该章节：{sid}")
        shared_fields = ("head_css", "shell_html", "shared_js", "legacy_full_html")
        # 局部读稿只构造需要的章节结果，避免为每次并发读取复制其他章节的源码结构。
        data = {item.name: getattr(self, item.name) for item in fields(self)
                if item.name not in ("owner", "sections", *shared_fields)}
        target_ids = set(requested) if requested is not None else None
        data["sections"] = [
            {"section_id": section.section_id, "title": section.title, "updated_at": section.updated_at,
             **{f"{key}_chars": len(getattr(section, key)) for key in ("html", "css", "js")},
             **({key: getattr(section, key) for key in ("html", "css", "js")} if include_code else {})}
            for section in self.sections if target_ids is None or section.section_id in target_ids
        ]
        if include_code and (target_ids is None or SHARED_SECTION in target_ids):
            data.update({key: getattr(self, key) for key in shared_fields})
        return data


def _read(owner: str, ref: UUID) -> HtmlDraft | None:
    raw = report_data._read(owner, ref, "draft.json")
    if raw is None:
        return None
    data = json.loads(raw)
    data["sections"] = [DraftSection(**section) for section in data["sections"]]
    return HtmlDraft(**data)


def load_draft(owner: str, draft_id: str) -> HtmlDraft:
    try:
        ref = UUID(draft_id)
    except (ValueError, AttributeError, TypeError) as exc:
        raise repo.Invalid("无效的草稿 ID") from exc
    draft = _read(owner, ref)
    if draft is None:
        raise repo.NotFound("草稿不存在或已过期")
    return draft


@contextmanager
def lock_draft(owner: str, draft_id: str):
    # 固定数量的分片锁跨进程生效。锁文件不删除，避免等待者和新请求锁住不同 inode。
    directory = Path(settings.report_work_dir)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    stripe = int(sha256((owner + str(UUID(draft_id))).encode()).hexdigest()[:2], 16)
    with (directory / f"draft-lock-{stripe:02x}").open("ab") as lock:
        with measure("draft.acquire_lock"):
            fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def record(draft: HtmlDraft, action: str, started: float, section_id: str | None = None) -> None:
    draft.events = [*draft.events[-29:], {"ts": time(), "action": action, "section_id": section_id,
                     "revision": draft.revision, "elapsed_ms": round((perf_counter() - started) * 1000, 3),
                     "status": draft.status}]
    report_data._write_ref(draft.owner, UUID(draft.draft_id), "draft.json", report_data._json(asdict(draft)))


def get_draft_status(draft_id: str, owner: str, *, include_code: bool = False, section_id: str | None = None,
                     section_ids: list[str] | None = None) -> dict:
    return load_draft(owner, draft_id).status_dict(
        include_code=include_code, section_id=section_id, section_ids=section_ids,
    )


def list_drafts(owner: str, is_admin: bool = False) -> list[dict]:
    # 管理台只列进度和诊断，不返回草稿源码。精确工号始终参与草稿读写寻址。
    directory = Path(settings.report_work_dir)
    prefix = "*" if is_admin else sha256(owner.encode()).hexdigest()
    cutoff = time() - report_data.TTL_SECONDS
    drafts = []
    for path in directory.glob(f"{prefix}-draft-*.json"):
        try:
            if path.stat().st_mtime <= cutoff:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        data["sections"] = [DraftSection(**section) for section in data["sections"]]
        draft = HtmlDraft(**data)
        status = draft.status_dict()
        if is_admin:
            status["owner"] = draft.owner
        drafts.append(status)
    return sorted(drafts, key=lambda draft: draft["events"][-1]["ts"], reverse=True)


def _check_shell(shell: str) -> None:
    if shell and shell.count(MARKER) != 1:
        raise repo.Invalid(f"页面骨架必须包含一个 {MARKER} 占位符")


def _bind_snapshots(draft: HtmlDraft, snapshots: dict[str, str]) -> None:
    for block_id, ref in snapshots.items():
        if not IDENTIFIER.fullmatch(block_id) or block_id in {"srg-main", "srg-theme", "srg-formatters"}:
            raise repo.Invalid(f"无效的数据块标识：{block_id}")
        if ref == "base":
            if draft.report_id is None:
                raise repo.Invalid(f"新建草稿不能复用基准数据块：{block_id}")
        else:
            try:
                UUID(ref)
            except (ValueError, AttributeError, TypeError) as exc:
                raise repo.Invalid(f"无效的快照 ID：{block_id}") from exc
    draft.snapshots.update(snapshots)


def edit_base(principal, report_id: int, base_version: int | None, *, with_html: bool = True,
              with_source: bool = False) -> dict:
    base = repo.get_report(report_id, principal, version=base_version, with_html=with_html, with_source=with_source)
    if base["access_level"] not in ("edit", "manage"):
        raise PermissionDenied("无权编辑该报告")
    return base


def create_draft(title: str, principal, *, dataset_id: int | None = None, snapshots: dict[str, str] | None = None,
                 head_css: str = "", shell_html: str = "", shared_js: str = "", style_id: str | None = None,
                 use_echarts: bool = True, template_id: int | None = None, template_version: int | None = None,
                 report_id: int | None = None, base_version: int | None = None,
                 sections: list[dict] | None = None) -> dict:
    started = perf_counter()
    if base_version is not None and report_id is None:
        raise repo.Invalid("指定基准版本时须提供报告 ID")
    source = {}
    restored = {}
    if report_id is not None:
        base = edit_base(principal, report_id, base_version, with_source=True)
        report_id, base_version = base["id"], base["revision"]
        if base["meta"].get("source") == "assembled_html" and base.get("markdown"):
            source = json.loads(base["markdown"])["smartreportgen_html_draft"]
            restored = {block_id: "base" for block_id in source.get("snapshots", {})}
        else:
            # 历史版本的成品仍可原样保留，追加章节时复用其内联数据与运行时。
            base_html = report_document.inline_legacy_echarts(base["html"])
            parsed = report_data._DataBlocks(base_html)
            restored = {b.fields["id"]: "base" for b in parsed.blocks if b.kind == "snapshot"}
            restored.update({key: "base" for key in report_data.legacy_queries(parsed)})
            source["legacy_full_html"] = parsed.replace(lambda b: ("base", "") if b.kind == "snapshot" else None)
        if source.get("style_id"):
            source["style_id"] = "base"
        source.update(dataset_id=base.get("dataset_id") or base["meta"].get("dataset_id"),
                      template_id=base.get("template_id"), template_version=base.get("template_version"))
    resolved_tpl_id, resolved_tpl_ver = repo.resolve_template_pair(
        template_id, template_version,
        default_id=source.get("template_id"), default_version=source.get("template_version"),
    )
    resolved_dataset_id = dataset_id if dataset_id is not None else source.get("dataset_id")
    if report_id is None:
        tpl = repo.require_report_template(
            principal, resolved_tpl_id, resolved_tpl_ver, dataset_id=resolved_dataset_id,
        )
        if resolved_dataset_id is None and tpl.get("dataset_id") is not None:
            resolved_dataset_id = tpl["dataset_id"]
    elif template_id is not None or template_version is not None:
        repo.require_report_template(
            principal, resolved_tpl_id, resolved_tpl_ver, dataset_id=resolved_dataset_id,
        )
    draft = HtmlDraft(
        draft_id=str(uuid4()), owner=principal.emp_no, title=title.strip() or source.get("title", ""),
        dataset_id=resolved_dataset_id,
        template_id=resolved_tpl_id,
        template_version=resolved_tpl_ver,
        report_id=report_id, base_version=base_version, style_id=style_id or source.get("style_id"),
        snapshots=restored, use_echarts=use_echarts,
        head_css=head_css or source.get("head_css", ""), shell_html=shell_html or source.get("shell_html", ""),
        shared_js=shared_js or source.get("shared_js", ""),
        sections=[DraftSection(**section) for section in source.get("sections", [])],
        legacy_full_html=source.get("legacy_full_html", ""),
    )
    if not draft.title or "\x00" in draft.title:
        raise repo.Invalid("草稿标题不能为空或包含无效字符")
    if draft.dataset_id is not None and (not access_for(principal).can_dataset(draft.dataset_id)
                                         or not repo.get_dataset(draft.dataset_id)["enabled"]):
        raise PermissionDenied("无权访问草稿关联的业务场景")
    _check_shell(draft.shell_html)
    # 历史 HTML 的数据块 ID 可能早于当前命名约定。保留原 ID，仅对新绑定应用命名检查。
    _bind_snapshots(draft, snapshots or {})
    if sections is not None and not isinstance(sections, (list, tuple)):
        raise repo.Invalid("sections 须为章节对象列表")
    # 复用更新规则。修订时按列表顺序应用到已恢复章节，所有槽位准备好后才发布草稿。
    for patch in sections or ():
        _patch_section(draft, patch)
    record(draft, "create", started)
    return draft.status_dict()


def _patch_section(draft: HtmlDraft, patch: dict) -> None:
    if not isinstance(patch, dict):
        raise repo.Invalid("章节列表元素必须为对象")
    section_id = patch.get("section_id")
    if not isinstance(section_id, str) or not IDENTIFIER.fullmatch(section_id):
        raise repo.Invalid("章节标识必须以字母或下划线开头，只能包含字母、数字、下划线和连字符")
    if extra := patch.keys() - {"section_id", "html", "title", "css", "js", "delete", "after_section_id", "index"}:
        raise repo.Invalid("未知章节字段：" + "、".join(sorted(extra)))
    if "delete" in patch and type(patch["delete"]) is not bool:
        raise repo.Invalid("delete 须为布尔值")
    existing_index = next((i for i, section in enumerate(draft.sections) if section.section_id == section_id), None)
    # 保留历史同名章节的编辑能力，避免新增章节与共享源码的诊断标识混淆。
    if section_id == SHARED_SECTION and existing_index is None:
        raise repo.Invalid(f"章节标识 {SHARED_SECTION} 保留给页面骨架与共享脚本")
    if patch.get("delete"):
        if existing_index is None:
            raise repo.NotFound(f"草稿中没有章节：{section_id}")
        draft.sections.pop(existing_index)
        return
    section = draft.sections.pop(existing_index) if existing_index is not None else DraftSection(section_id)
    for key in ("html", "title", "css", "js"):
        if key in patch:
            if not isinstance(patch[key], str):
                raise repo.Invalid(f"章节 {section_id} 的 {key} 必须为文本")
            setattr(section, key, patch[key])
    after = patch.get("after_section_id")
    index = patch.get("index")
    if after is not None:
        if index is not None:
            raise repo.Invalid("请使用 after_section_id 或 index 指定位置")
        index = next((i + 1 for i, item in enumerate(draft.sections) if item.section_id == after), None)
        if index is None:
            raise repo.NotFound(f"找不到排序参照章节：{after}")
    if index is None:
        index = len(draft.sections) if existing_index is None else existing_index
    if type(index) is not int or not 0 <= index <= len(draft.sections):
        raise repo.Invalid("章节索引超出范围")
    section.updated_at = time()
    draft.sections.insert(index, section)


def upsert_sections(draft_id: str, owner: str, sections_patch: list[dict], *, snapshots: dict | None = None,
                    head_css: str | None = None, shell_html: str | None = None, shared_js: str | None = None,
                    title: str | None = None, template_id: int | None = None,
                    template_version: int | None = None, principal=None) -> dict:
    started = perf_counter()
    draft_id = load_draft(owner, draft_id).draft_id
    with lock_draft(owner, draft_id):
        draft = load_draft(owner, draft_id)
        if template_id is not None or template_version is not None:
            tid, tver = repo.resolve_template_pair(
                template_id, template_version,
                default_id=draft.template_id, default_version=draft.template_version,
            )
            repo.require_report_template(principal, tid, tver, dataset_id=draft.dataset_id)
            draft.template_id, draft.template_version = tid, tver
        # 批次全部通过后才写入。改动使旧预览失效，历史诊断仍可在草稿中定位。
        for patch in sections_patch:
            _patch_section(draft, patch)
        for key, value in (("head_css", head_css), ("shell_html", shell_html), ("shared_js", shared_js), ("title", title)):
            if value is not None:
                setattr(draft, key, value)
        if not draft.title.strip() or "\x00" in draft.title:
            raise repo.Invalid("草稿标题不能为空或包含无效字符")
        _check_shell(draft.shell_html)
        _bind_snapshots(draft, snapshots or {})
        draft.status = "assembling"
        draft.revision += 1
        record(draft, "upsert", started, sections_patch[0]["section_id"] if len(sections_patch) == 1 else None)
    return draft.status_dict()


def _script(code: str, section: str) -> str:
    section_json = json.dumps(section)
    # 在各自的错误边界内解析脚本，使语法错误也能关联章节。转义尖括号保护 HTML 脚本边界。
    source = json.dumps(f"{code}\n//# sourceURL=srg-section-{section}.js").replace("<", "\\u003c")
    body = (
        f"(() => {{ try {{\n  new Function({source})();\n}} catch (error) {{\n"
        f"  window.__srgErrors.push({{section: {section_json}, message: String(error), stack: error?.stack}});\n"
        f"  console.error('[SRG {section}]', error);\n"
        f"}} }})();"
    )
    return f'<script data-srg-for="{section}">{body}</script>'


def assemble_source_html(draft: HtmlDraft) -> str:
    _check_shell(draft.shell_html)
    sections = "\n".join(
        f'<section id="srg-sec-{sec.section_id}" data-srg-section="{sec.section_id}">{sec.html}</section>'
        for sec in draft.sections
    )
    legacy = draft.legacy_full_html if not draft.shell_html else ""
    legacy_ids = set()
    has_vendor = False
    if legacy:
        # 基准中已经存在的快照沿用原位置，确保旧脚本初始化时即可读取。
        parsed = report_data._DataBlocks(legacy)
        legacy_ids = {block.fields["id"] for block in parsed.blocks if block.kind == "snapshot"}
        has_vendor = any(block.kind == "vendor" for block in parsed.blocks) or legacy.count(report_document.echarts_js()) > 0
        legacy = parsed.replace(lambda block: (draft.snapshots[block.fields["id"]], "")
                                if block.kind == "snapshot" and block.fields["id"] in draft.snapshots else None)
    bindings = "\n".join(
        f'<script id="{escape(block_id, quote=True)}" type="application/json" data-srg-snapshot="{ref}"></script>'
        for block_id, ref in draft.snapshots.items() if block_id not in legacy_ids
    )
    vendor = '<script data-srg-vendor="echarts"></script>' if draft.use_echarts and not has_vendor else ""
    shared = _script(draft.shared_js, SHARED_SECTION) if draft.shared_js else ""
    scripts = "\n".join(_script(sec.js, sec.section_id) for sec in draft.sections if sec.js)
    runtime = f"{bindings}\n{vendor}\n<script>{SRG_BRIDGE_JS}</script>\n{shared}\n{scripts}"
    style = f'<style id="srg-theme" data-srg-style="{escape(draft.style_id, quote=True)}"></style>' if draft.style_id else ""
    css = "\n".join(f'<style data-srg-for="{sec.section_id}">/* [section:{sec.section_id}] */\n'
                    f'{report_data._escape_style_text(sec.css)}</style>'
                    for sec in draft.sections if sec.css)
    styles = f"{style}<style>{'' if legacy else BASE_CSS}{report_data._escape_style_text(draft.head_css)}</style>\n{css}"
    if legacy:
        return report_document.extend_legacy_document(legacy, styles=styles, sections=sections, runtime=runtime)
    shell = (draft.shell_html or DEFAULT_SHELL).replace(MARKER, sections)
    return report_document.enforce_offline_policy(
        f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{escape(draft.title)}</title>{styles}</head><body>{shell}\n{runtime}</body></html>'
    )


class _DuplicateIdInspector(HTMLParser):
    """统计主文档中重复的元素 id，并归因到所在章节或共享骨架。"""

    CDATA_CONTENT_ELEMENTS = (*HTMLParser.CDATA_CONTENT_ELEMENTS, "title", "textarea", "xmp", "iframe",
                              "noembed", "noframes", "noscript")

    def __init__(self) -> None:
        super().__init__()
        self.template_depth = 0
        self.sections: list[str | None] = []
        self.counts: dict[str, int] = {}
        self.owners: dict[str, list[str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self.template_depth:
            if tag == "section":
                self.sections.append(next((value for key, value in attrs if key == "data-srg-section"), None))
            # 浏览器保留首个同名属性。template 节点自身在主文档中，内部节点则不参与。
            element_id = next((value for key, value in attrs if key == "id"), None)
            if element_id:
                owner = next((item for item in reversed(self.sections) if item), None) or SHARED_SECTION
                self.counts[element_id] = self.counts.get(element_id, 0) + 1
                section_list = self.owners.setdefault(element_id, [])
                if owner not in section_list:
                    section_list.append(owner)
        if tag == "template":
            self.template_depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # HTML 的非空元素不会因尾部斜杠关闭。章节和 template 的作用域仍等待结束标签。
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag == "template" and self.template_depth:
            self.template_depth -= 1
        elif tag == "section" and not self.template_depth and self.sections:
            self.sections.pop()


def duplicate_dom_ids(html: str) -> list[dict]:
    """返回主文档内重复出现的元素 id 及涉及的章节列表。"""
    inspector = _DuplicateIdInspector()
    inspector.feed(html)
    inspector.close()
    return [
        {"id": element_id, "count": count, "section_ids": inspector.owners[element_id]}
        for element_id, count in inspector.counts.items()
        if count > 1
    ]
