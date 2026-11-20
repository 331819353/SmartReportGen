"""保存在生成阶段使用的私有 JSON 快照，并将引用展开为离线 HTML。"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass
from hashlib import sha256
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from threading import Lock
from typing import Callable
from uuid import UUID, uuid4

from . import report_document
from .config import settings

TTL_SECONDS = 24 * 60 * 60
MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
PREVIEW_BUDGET = 8 * 1024
MAX_PREVIEW_BUDGET = 64 * 1024
MARKER = "data-srg-snapshot"
VENDOR_MARKER = "data-srg-vendor"
ASSET_MARKER = "data-srg-asset"
STYLE_MARKER = "data-srg-style"
MARKERS = (MARKER, VENDOR_MARKER, ASSET_MARKER, STYLE_MARKER)
_FILE_PATTERN = re.compile(r"[0-9a-f]{64}-[0-9a-f]{32}\.(?:json|html|css|meta\.json)|\.srg-[\w-]+")
log = logging.getLogger(__name__)
_cleanup_lock = Lock()
_next_cleanup = 0.0


class ReportDataError(ValueError):
    pass


def _json(data) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _path(owner: str, ref: UUID, suffix: str) -> Path:
    # 访问者的工号参与寻址。即使知道别人的引用 ID，也只能在本人工号范围内查找。
    return Path(settings.report_work_dir) / f"{sha256(owner.encode()).hexdigest()}-{ref.hex}.{suffix}"


def _cleanup(directory: Path) -> None:
    global _next_cleanup
    with _cleanup_lock:
        if time.monotonic() < _next_cleanup:
            return
        _next_cleanup = time.monotonic() + 600
    # 读取时单独判断有效期，清理只负责回收存储空间。临时文件也纳入清理以处理进程异常退出。
    cutoff = time.time() - TTL_SECONDS
    for path in directory.iterdir():
        if not _FILE_PATTERN.fullmatch(path.name):
            continue
        try:
            if path.stat().st_mtime <= cutoff:
                path.unlink(missing_ok=True)
        except FileNotFoundError:
            pass
        except OSError:
            log.warning("无法清理已过期的报告工作文件", exc_info=True)


def _write_ref(owner: str, ref: UUID, suffix: str, content: str) -> None:
    path = _path(owner, ref, suffix)
    temp = None
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _cleanup(path.parent)
        # 私有临时文件先写完整，再原子发布。并发预览与保存只能看到完整快照。
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".srg-", delete=False,
                                         mode="w", encoding="utf-8") as file:
            temp = Path(file.name)
            file.write(content)
        temp.replace(path)
    except OSError as exc:
        if temp:
            temp.unlink(missing_ok=True)
        log.exception("无法保存报告工作文件")
        raise ReportDataError("无法保存报告工作文件，请检查服务端存储") from exc


def _write(owner: str, suffix: str, content: str) -> UUID:
    ref = uuid4()
    _write_ref(owner, ref, suffix, content)
    return ref


def _read(owner: str, ref: UUID, suffix: str) -> str | None:
    try:
        with _path(owner, ref, suffix).open(encoding="utf-8") as file:
            if time.time() - os.fstat(file.fileno()).st_mtime < TTL_SECONDS:
                return file.read()
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.exception("无法读取报告工作文件")
        raise ReportDataError("无法读取报告工作文件，请检查服务端存储") from exc
    return None


def save_query(owner: str, columns: list[str], rows: list[list], sample_rows: int, *, origin: dict | None = None) -> dict:
    """保存全部结果；只返回有字节上限的列名与前几行完整样本。"""
    payload = _json({"columns": columns, "rows": rows, **({"origin": origin} if origin else {})})
    if len(payload.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise ReportDataError("查询结果超过单个快照的 32 MiB 上限，请缩小查询范围、减少字段或分批查询")
    ref = _write(owner, "json", payload)
    # 兼容原有小样本 8 KiB 预算；请求较多证据行（最多 200 行）时按需放宽到 64 KiB。
    budget = PREVIEW_BUDGET if sample_rows <= 20 else min(MAX_PREVIEW_BUDGET, max(PREVIEW_BUDGET, sample_rows * 1024))
    visible_columns, sample = [], []
    for column in columns:
        budget -= len(_json(column).encode("utf-8")) + 1
        if budget < 0:
            break
        visible_columns.append(column)
    # 返回完整的前缀样本，遇到超长行即停止，避免截断字段值后改变其含义。
    if len(visible_columns) == len(columns):
        for row in rows[:sample_rows]:
            budget -= len(_json(row).encode("utf-8")) + 1
            if budget < 0:
                break
            sample.append(row)
    return {"snapshot_id": str(ref), "columns": visible_columns, "column_count": len(columns),
            "sample_rows": sample, "sample_limited": (len(visible_columns) < len(columns)
                                                       or len(sample) < min(sample_rows, len(rows))),
            "expires_in_seconds": TTL_SECONDS}


def read_snapshot(owner: str, snapshot_id: str) -> dict:
    try:
        payload = _read(owner, UUID(snapshot_id), "json")
    except (TypeError, ValueError, AttributeError) as exc:
        raise ReportDataError("快照 ID 无效") from exc
    if payload is None:
        raise ReportDataError("快照不可用、无权限或已过期，请重新取数")
    return json.loads(payload)


def save_style(owner: str, css: str) -> str:
    return str(_write(owner, "css", css))


def save_preview(owner: str, html: str, *, source_html: str | None = None,
                 report_id: int | None = None, requested_report_id: int | None = None,
                 base_version: int | None = None, proof: dict | None = None) -> UUID:
    ref = _write(owner, "html", html)
    if source_html is not None:
        meta = {"html": source_html, "report_id": report_id,
                "requested_report_id": requested_report_id, "base_version": base_version, "proof": proof}
        _write_ref(owner, ref, "meta.json", _json(meta))
    return ref


def read_preview(owner: str, ref: UUID) -> str | None:
    return _read(owner, ref, "html")


def read_preview_source(owner: str, preview_id: str) -> dict:
    try:
        ref = UUID(str(preview_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ReportDataError("无效的预览 ID") from exc
    raw = _read(owner, ref, "meta.json")
    if raw is None:
        raise ReportDataError("预览源码不可用或已过期，请重新预览")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ReportDataError("预览源码已损坏，请重新预览") from exc
    if not isinstance(data, dict) or not isinstance(data.get("html"), str):
        raise ReportDataError("预览源码格式无效，请重新预览")
    return data


@dataclass
class _DataBlock:
    tag: str
    kind: str
    marker: str
    fields: dict[str, str | None]
    start: int
    end: int
    data: str


class _DataBlocks(HTMLParser):
    # 这些标签内的示例代码和纯文本不应被当作数据引用。
    CDATA_CONTENT_ELEMENTS = (*HTMLParser.CDATA_CONTENT_ELEMENTS, "title", "textarea", "xmp", "iframe",
                              "noembed", "noframes", "noscript")

    def __init__(self, html: str, *, validate_bindings: bool = False) -> None:
        super().__init__()
        self.html = html
        self.lines = [0, *(m.end() for m in re.finditer("\n", html))]
        self.blocks: list[_DataBlock] = []
        self.snapshot_ids: set[str] = set()
        self.has_asset = False
        self.validate_bindings = validate_bindings
        self.live_ids: set[str] = set()
        self.reused_ids: set[str] = set()
        self.template_depth = 0
        self.pending: tuple[str, str, str, dict[str, str | None], int, int] | None = None
        try:
            self.feed(html)
            self.close()
        except AssertionError as exc:
            raise ReportDataError("HTML 包含无法解析的标记声明") from exc
        if self.pending:
            tag = self.pending[0]
            msg = "数据引用缺少 </script> 结束标签" if self.pending[1] == "snapshot" else f"引用缺少 </{tag}> 结束标签"
            raise ReportDataError(msg)
        if validate_bindings:
            for block in self.blocks:
                block_id = block.fields.get("id")
                if block_id and block_id in self.reused_ids:
                    if block.kind == "snapshot":
                        raise ReportDataError(f"数据块 id 与页面元素重复：{block_id}。请改用唯一 id，并同步更新脚本中的引用")
                    raise ReportDataError(f"引用块 id 与页面元素重复：{block_id}。请改用唯一 id")

    def _offset(self) -> int:
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "template":
            self.template_depth += 1
        if self.validate_bindings and not self.template_depth:
            # 浏览器遇到重复属性时使用第一个；template 的内容没有进入报告主 DOM。
            element_id = next((value for key, value in attrs if key == "id"), None)
            if element_id:
                if element_id in self.live_ids:
                    self.reused_ids.add(element_id)
                self.live_ids.add(element_id)
        if tag not in ("script", "style"):
            return
        fields = dict(attrs)
        markers = [m for m in MARKERS if m in fields]
        if not markers:
            return
        if len(markers) > 1:
            raise ReportDataError("单个标签不能同时使用多种 data-srg 展开属性")
        marker = markers[0]
        if tag == "style" and marker != STYLE_MARKER:
            raise ReportDataError(f"{marker} 只能用于 <script> 标签")
        if tag == "script" and marker == STYLE_MARKER:
            raise ReportDataError("data-srg-style 只能用于 <style> 标签")

        dup_attr = len(fields) != len(attrs)
        has_external = any(key in fields for key in ("src", "href", "xlink:href"))
        if marker == MARKER:
            kind = "snapshot"
            block_id = fields.get("id")
            if (not block_id or not fields.get(MARKER) or dup_attr or
                    (fields.get("type") or "").strip().lower() != "application/json" or has_external):
                raise ReportDataError("数据引用必须为带 id 和快照引用的内联 application/json 脚本标签")
            if block_id in self.snapshot_ids:
                raise ReportDataError(f"数据块 id 重复：{block_id}")
            if self.validate_bindings and self.template_depth:
                raise ReportDataError(f"数据块位于 template 内，页面脚本无法直接读取：{block_id}")
            self.snapshot_ids.add(block_id)
        elif marker == VENDOR_MARKER:
            kind = "vendor"
            script_type = (fields.get("type") or "").split(";", 1)[0].strip().lower()
            if not fields.get(VENDOR_MARKER) or dup_attr or has_external or script_type not in (
                    "", "text/javascript", "application/javascript"):
                raise ReportDataError("内置运行时引用必须为内联 JavaScript <script> 标签")
            if self.validate_bindings and self.template_depth:
                raise ReportDataError("内置运行时引用不能位于 template 内")
        elif marker == ASSET_MARKER:
            kind = "asset"
            script_type = (fields.get("type") or "").split(";", 1)[0].strip().lower()
            if (fields.get("id") != "srg-formatters" or not fields.get(ASSET_MARKER) or dup_attr or has_external or
                    script_type not in ("", "text/javascript", "application/javascript")):
                raise ReportDataError('格式化模块引用必须设置 id="srg-formatters" 且为内联 JavaScript <script> 标签')
            if self.has_asset:
                raise ReportDataError("格式化模块引用重复：srg-formatters")
            if self.validate_bindings and self.template_depth:
                raise ReportDataError("格式化模块引用不能位于 template 内")
            self.has_asset = True
        else:
            kind = "style"
            if not fields.get(STYLE_MARKER) or dup_attr or has_external:
                raise ReportDataError("内嵌样式引用必须为带 data-srg-style 的 <style> 标签")
            if self.validate_bindings and self.template_depth:
                raise ReportDataError("内嵌样式引用不能位于 template 内")

        start = self._offset()
        self.pending = (tag, kind, marker, fields, start, start + len(self.get_starttag_text()))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        fields = dict(attrs)
        if tag in ("script", "style") and any(m in fields for m in MARKERS):
            if MARKER in fields:
                raise ReportDataError("数据引用必须使用成对的 <script> 标签")
            raise ReportDataError(f"引用必须使用成对的 <{tag}> 标签")
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag == "template" and self.template_depth:
            self.template_depth -= 1
        if self.pending and tag == self.pending[0]:
            block_tag, kind, marker, fields, start, data_start = self.pending
            data_end = self._offset()
            end = self.html.index(">", data_end) + 1
            self.blocks.append(_DataBlock(block_tag, kind, marker, fields, start, end, self.html[data_start:data_end]))
            self.pending = None

    def replace(self, replacement: Callable[[_DataBlock], tuple[str, str] | None]) -> str:
        parts, offset = [], 0
        for block in self.blocks:
            updated = replacement(block)
            if updated is None:
                continue
            ref, data = updated
            attributes = " ".join(f'{key}="{escape(value or "", quote=True)}"'
                                  for key, value in {**block.fields, block.marker: ref}.items())
            parts.extend((self.html[offset:block.start], f"<{block.tag} {attributes}>{data}</{block.tag}>"))
            offset = block.end
        return "".join([*parts, self.html[offset:]]) if parts else self.html


def _escape_style_text(css: str) -> str:
    return re.sub(r"</style", lambda m: "<\\/" + m.group(0)[2:], css, flags=re.I)


def embed(html: str, owner: str, base_html: str = "") -> str:
    """展开本人的快照、样式及内置运行时，或复用有权编辑的基准版本中的数据/样式块。"""
    lower = html.lower()
    if not any(marker in lower for marker in MARKERS):
        return html
    base_blocks: list[_DataBlock] | None = None

    def get_base_blocks() -> list[_DataBlock]:
        nonlocal base_blocks
        if base_blocks is None:
            base_blocks = _DataBlocks(base_html).blocks
        return base_blocks

    def resolve(block: _DataBlock) -> tuple[str, str] | None:
        ref = block.fields[block.marker] or ""
        data = block.data
        if ref == "embedded" and data.strip():
            return None

        if block.kind == "snapshot":
            block_id = block.fields["id"] or ""
            if data.strip():
                raise ReportDataError(f"待展开的数据块应为空：{block_id}")
            if ref == "base":
                base = next((b for b in get_base_blocks() if b.kind == "snapshot" and b.fields.get("id") == block_id), None)
                if not base or base.fields[MARKER] != "embedded" or not base.data.strip():
                    raise ReportDataError(f"基准版本没有数据块：{block_id}")
                return "embedded", base.data
            try:
                snapshot_id = UUID(ref)
            except ValueError as exc:
                raise ReportDataError(f"无效的数据快照 ID：{block_id}") from exc
            payload = _read(owner, snapshot_id, "json")
            if payload is None:
                raise ReportDataError(f"数据快照不可用或已过期，请重新取数：{block_id}")
            # JSON 仍须符合 HTML 脚本标签的解析规则，防止数据中的 </script> 关闭标签。
            body = json.loads(payload)
            return "embedded", _json({"columns": body["columns"], "rows": body["rows"]}).replace("<", "\\u003c")

        if block.kind == "vendor":
            if data.strip():
                raise ReportDataError("待展开的内置运行时引用应为空")
            if ref != "echarts":
                raise ReportDataError(f"不支持的内置运行时：{ref}")
            return "embedded", report_document.echarts_js()

        if block.kind == "asset":
            if data.strip():
                raise ReportDataError("待展开的格式化模块引用应为空")
            if ref != "report-formatters.js":
                raise ReportDataError(f"不支持的技能资产：{ref}")
            return "embedded", report_document.formatters_js()

        # block.kind == "style"
        if data.strip():
            raise ReportDataError("待展开的内嵌样式引用应为空")
        if ref == "base":
            base_styles = [b for b in get_base_blocks()
                           if b.kind == "style" and b.fields.get(STYLE_MARKER) == "embedded" and b.data.strip()]
            style_id_attr = block.fields.get("id")
            if style_id_attr:
                matched = [b for b in base_styles if b.fields.get("id") == style_id_attr]
                if len(matched) != 1:
                    raise ReportDataError(f"基准版本没有样式块：{style_id_attr}")
                return "embedded", matched[0].data
            if len(base_styles) != 1:
                raise ReportDataError("基准版本没有可唯一复用的内嵌样式块")
            return "embedded", base_styles[0].data
        try:
            style_uuid = UUID(ref)
        except ValueError as exc:
            raise ReportDataError(f"无效的样式引用 ID：{ref}") from exc
        css = _read(owner, style_uuid, "css")
        if css is None:
            raise ReportDataError(f"样式引用不可用或已过期，请重新获取：{ref}")
        return "embedded", _escape_style_text(css)

    # 只校验即将交付的正文。仍允许读取旧基准的数据，供修订时修正冲突。
    return _DataBlocks(html, validate_bindings=True).replace(resolve)


def for_edit(html: str) -> str:
    """省略已内联的大块数据与内置运行时/样式，返回能在同一基准版本上预览和修订的 HTML。"""
    lower = html.lower()
    if not any(marker in lower for marker in MARKERS):
        return html
    collapsed_ref = {
        "snapshot": "base",
        "vendor": "echarts",
        "asset": "report-formatters.js",
        "style": "base",
    }

    def fold(block: _DataBlock) -> tuple[str, str] | None:
        if block.fields[block.marker] == "embedded":
            return collapsed_ref[block.kind], ""
        return None

    return _DataBlocks(html).replace(fold)


def deflate_static(html: str) -> tuple[str, dict[str, str]]:
    """仅折叠可无损还原的内置脚本，并返回要按内容哈希存储的原始字节。"""
    lower = html.lower()
    if VENDOR_MARKER not in lower and ASSET_MARKER not in lower:
        return html, {}
    parser = _DataBlocks(html)
    echarts = report_document.echarts_js()
    formatters = report_document.formatters_js()
    assets = {}

    def fold(block: _DataBlock) -> tuple[str, str] | None:
        if block.fields[block.marker] != "embedded":
            return None
        if block.kind == "vendor":
            expected = echarts
        elif block.kind == "asset":
            expected = formatters
        else:
            return None
        if block.data != expected:
            return None
        attributes = " ".join(
            f'{key}="{escape(value or "", quote=True)}"' for key, value in block.fields.items()
        )
        if html[block.start:block.end] != f"<{block.tag} {attributes}>{block.data}</{block.tag}>":
            return None
        digest = sha256(expected.encode()).hexdigest()
        assets[digest] = expected
        return f"sha256:{digest}", ""

    return parser.replace(fold), assets


def inflate_static(html: str, load_assets: Callable[[set[str]], dict[str, str]]) -> str:
    """按该报告引用的脚本版本恢复 HTML，兼容升级前保存的固定名称占位符。"""
    lower = html.lower()
    if not any(ref in lower for ref in (f'{VENDOR_MARKER}="echarts"', f'{ASSET_MARKER}="report-formatters.js"',
                                        f'{VENDOR_MARKER}="sha256:', f'{ASSET_MARKER}="sha256:')):
        return html
    parser = _DataBlocks(html)
    digests = {ref[7:] for block in parser.blocks if block.kind in ("vendor", "asset") and not block.data.strip()
               if (ref := block.fields[block.marker]) and re.fullmatch(r"sha256:[a-f0-9]{64}", ref)}
    assets = load_assets(digests) if digests else {}
    if any(digest not in assets or sha256(assets[digest].encode()).hexdigest() != digest for digest in digests):
        raise ReportDataError("报告引用的离线脚本缺失或内容校验失败")

    def expand(block: _DataBlock) -> tuple[str, str] | None:
        ref = block.fields[block.marker] or ""
        if block.data.strip():
            return None
        if block.kind in ("vendor", "asset") and ref.startswith("sha256:"):
            if ref[7:] not in assets:
                raise ReportDataError("报告引用的离线脚本标识无效")
            return "embedded", assets[ref[7:]]
        if block.kind == "vendor" and ref == "echarts":
            return "embedded", report_document.echarts_js()
        if block.kind == "asset" and ref == "report-formatters.js":
            return "embedded", report_document.formatters_js()
        return None

    return parser.replace(expand)
