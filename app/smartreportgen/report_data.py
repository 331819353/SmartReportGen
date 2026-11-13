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

from .config import settings

TTL_SECONDS = 24 * 60 * 60
MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
PREVIEW_BUDGET = 8 * 1024
MARKER = "data-srg-snapshot"
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
        if not re.fullmatch(r"[0-9a-f]{64}-[0-9a-f]{32}\.(json|html)|\.srg-[\w-]+", path.name):
            continue
        try:
            if path.stat().st_mtime <= cutoff:
                path.unlink(missing_ok=True)
        except FileNotFoundError:
            pass
        except OSError:
            log.warning("无法清理已过期的报告工作文件", exc_info=True)


def _write(owner: str, suffix: str, content: str) -> UUID:
    ref = uuid4()
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


def save_query(owner: str, columns: list[str], rows: list[list], sample_rows: int) -> dict:
    """保存全部结果；只返回有字节上限的列名与前几行完整样本。"""
    payload = _json({"columns": columns, "rows": rows})
    if len(payload.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise ReportDataError("查询结果超过单个快照的 32 MiB 上限，请缩小查询范围、减少字段或分批查询")
    ref = _write(owner, "json", payload)
    budget = PREVIEW_BUDGET
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


def save_preview(owner: str, html: str) -> UUID:
    return _write(owner, "html", html)


def read_preview(owner: str, ref: UUID) -> str | None:
    return _read(owner, ref, "html")


@dataclass
class _DataBlock:
    fields: dict[str, str | None]
    start: int
    end: int
    data: str


class _DataBlocks(HTMLParser):
    # 这些标签内的示例代码和纯文本不应被当作数据引用。
    CDATA_CONTENT_ELEMENTS = (*HTMLParser.CDATA_CONTENT_ELEMENTS, "title", "textarea", "xmp", "iframe",
                              "noembed", "noframes", "noscript")

    def __init__(self, html: str) -> None:
        super().__init__()
        self.html = html
        self.lines = [0, *(m.end() for m in re.finditer("\n", html))]
        self.blocks: dict[str, _DataBlock] = {}
        self.pending: tuple[str, dict[str, str | None], int, int] | None = None
        try:
            self.feed(html)
            self.close()
        except AssertionError as exc:
            raise ReportDataError("HTML 包含无法解析的标记声明") from exc
        if self.pending:
            raise ReportDataError("数据引用缺少 </script> 结束标签")

    def _offset(self) -> int:
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        fields = dict(attrs)
        if MARKER not in fields:
            return
        block_id = fields.get("id")
        if (not block_id or not fields.get(MARKER) or len(fields) != len(attrs) or
                (fields.get("type") or "").strip().lower() != "application/json" or
                any(key in fields for key in ("src", "href", "xlink:href"))):
            raise ReportDataError("数据引用必须为带 id 和快照引用的内联 application/json 脚本标签")
        if block_id in self.blocks:
            raise ReportDataError(f"数据块 id 重复：{block_id}")
        start = self._offset()
        self.pending = (block_id, fields, start, start + len(self.get_starttag_text()))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script" and MARKER in dict(attrs):
            raise ReportDataError("数据引用必须使用成对的 <script> 标签")

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.pending:
            block_id, fields, start, data_start = self.pending
            data_end = self._offset()
            end = self.html.index(">", data_end) + 1
            self.blocks[block_id] = _DataBlock(fields, start, end, self.html[data_start:data_end])
            self.pending = None

    def replace(self, replacement: Callable[[str, str, str], tuple[str, str] | None]) -> str:
        parts, offset = [], 0
        for block_id, block in self.blocks.items():
            updated = replacement(block_id, block.fields[MARKER] or "", block.data)
            if updated is None:
                continue
            ref, data = updated
            attributes = " ".join(f'{key}="{escape(value or "", quote=True)}"'
                                  for key, value in {**block.fields, MARKER: ref}.items())
            parts.extend((self.html[offset:block.start], f"<script {attributes}>{data}</script>"))
            offset = block.end
        return "".join([*parts, self.html[offset:]]) if parts else self.html


def embed(html: str, owner: str, base_html: str = "") -> str:
    """展开本人的快照，或复用有权编辑的基准版本中的数据块。"""
    if MARKER not in html.lower():
        return html
    base_blocks = None

    def resolve(block_id: str, ref: str, data: str) -> tuple[str, str] | None:
        nonlocal base_blocks
        if ref == "embedded" and data.strip():
            return None
        if data.strip():
            raise ReportDataError(f"待展开的数据块应为空：{block_id}")
        if ref == "base":
            if base_blocks is None:
                base_blocks = _DataBlocks(base_html).blocks
            base = base_blocks.get(block_id)
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
        return "embedded", payload.replace("<", "\\u003c")

    return _DataBlocks(html).replace(resolve)


def for_edit(html: str) -> str:
    """省略已内联的大块数据，返回能在同一基准版本上预览和修订的 HTML。"""
    if MARKER not in html.lower():
        return html
    return _DataBlocks(html).replace(lambda _id, ref, _data: ("base", "") if ref == "embedded" else None)
