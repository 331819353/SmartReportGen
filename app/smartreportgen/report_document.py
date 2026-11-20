"""检查 Agent 提交的单文件报告及静态资源引用。"""
from __future__ import annotations

import json
import re
from functools import cache
from hashlib import sha256
from html import escape
from html.parser import HTMLParser
from pathlib import Path

CSS_REFERENCE = re.compile(r"url\(\s*([\"']?)(.*?)\1\s*\)|@import\s*([\"'])(.*?)\3", re.I | re.S)
CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)
SCRIPT_TYPES = {"", "module", "text/javascript", "application/javascript", "text/ecmascript", "application/ecmascript"}
RESOURCE_ATTRIBUTES = {
    "script": ("src", "href", "xlink:href"), "img": ("src",), "input": ("src",),
    "audio": ("src",), "video": ("src", "poster"), "source": ("src",), "track": ("src",),
    "embed": ("src",), "object": ("data",), "iframe": ("src",),
    "image": ("href", "xlink:href"), "use": ("href", "xlink:href"), "feimage": ("href", "xlink:href"),
}
LEGACY_ECHARTS = re.compile(r'<script src="(?:[^"<>]*/)?static/vendor/echarts\.min\.js"></script>')


def skill_dir() -> Path:
    """定位唯一技能目录：镜像内优先 docs/skill，本地开发/测试回退仓库 skills/smart-report。"""
    packaged = Path(__file__).with_name("docs") / "skill"
    return packaged if (packaged / "SKILL.md").is_file() else Path(__file__).resolve().parents[2] / "skills" / "smart-report"


@cache
def echarts_js() -> str:
    """复用内置图表脚本，避免报告展开与合并重复读取同一个大文件。"""
    return (Path(__file__).with_name("static") / "vendor" / "echarts.min.js").read_text(encoding="utf-8")


@cache
def formatters_js() -> str:
    return (skill_dir() / "assets" / "report-formatters.js").read_text(encoding="utf-8")


def inline_legacy_echarts(html: str) -> str:
    """内联旧结构化报告引用的内置图表库。新报告已经内联时直接返回原文。"""
    return LEGACY_ECHARTS.sub(lambda _: f"<script>{echarts_js()}</script>", html)


class _ReportDocument(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.doctype = False
        self.elements: set[str] = set()
        self.inline_style = False
        self.inline_script = False
        self.references: set[str] = set()
        self._block = ""

    def handle_decl(self, decl: str) -> None:
        self.doctype |= decl.lower() == "doctype html"

    def _resource(self, value: str, location: str, *, script: bool = False) -> None:
        # data URI 和文档内锚点不需要随报告另行分发文件。
        value = value.strip().lower()
        if value and (script or not value.startswith(("data:", "#"))):
            self.references.add(location)

    def _css(self, data: str) -> None:
        for match in CSS_REFERENCE.finditer(CSS_COMMENT.sub("", data)):
            self._resource(match[2] if match[2] is not None else match[4], "CSS url()/@import")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.elements.add(tag)
        attrs = {key: value or "" for key, value in attrs}
        if tag == "style":
            self._block = "style"
        elif tag == "script":
            # 内嵌 JSON 只是数据，不能替代可执行的联动脚本。
            if attrs.get("type", "").split(";", 1)[0].strip().lower() in SCRIPT_TYPES and "src" not in attrs:
                self._block = "script"
            if "src" in attrs and not attrs["src"].strip():
                self.references.add("script[src]")
        for key in RESOURCE_ATTRIBUTES.get(tag, ()):
            self._resource(attrs.get(key, ""), f"{tag}[{key}]", script=tag == "script")
        if tag == "link" and set(attrs.get("rel", "").lower().split()) & {"stylesheet", "preload", "modulepreload", "icon"}:
            self._resource(attrs.get("href", ""), "link[href]")
        if "style" in attrs:
            self._css(attrs["style"])
        if tag in ("img", "source"):
            # srcset 的 URL 可以含逗号，例如 data URI；描述符与下一个候选之间用逗号分隔。
            candidates = attrs.get("srcset", "").lstrip(" ,\t\r\n\f")
            while candidates:
                parts = candidates.split(maxsplit=1)
                url = parts[0]
                self._resource(url.rstrip(","), f"{tag}[srcset]")
                remainder = parts[1] if len(parts) == 2 else ""
                candidates = (remainder if url.endswith(",") else remainder.partition(",")[2]).lstrip(" ,\t\r\n\f")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("style", "script"):
            self._block = ""

    def handle_data(self, data: str) -> None:
        if self._block == "style":
            self.inline_style |= bool(data.strip())
            self._css(data)
        elif self._block == "script":
            self.inline_script |= bool(data.strip())


def report_document_error(html: str, *, legacy: bool = False) -> str | None:
    """检查单文件结构与静态依赖；联动、着色和脚本发出的请求须在浏览器验证。"""
    if "\x00" in html:
        return "HTML 内容包含无效字符"
    document = _ReportDocument()
    try:
        document.feed(html)
        document.close()
    except AssertionError:
        # HTMLParser 会对非法的标记声明抛出断言，转换成可修正的输入错误。
        return "HTML 包含无法解析的标记声明"
    if "html" not in document.elements or (not legacy and (
        not document.doctype or not {"head", "body"} <= document.elements
    )):
        return "请提交包含 <!doctype html>、<html>、<head> 和 <body> 的完整 HTML"
    if document.references:
        return "请将报告依赖内联到单文件 HTML，发现外部资源引用：" + "、".join(sorted(document.references))
    # 旧版允许保存静态 HTML。合并时仍检查外部依赖，保持旧报告可用。
    if not legacy and (not document.inline_style or not document.inline_script):
        return "请在同一个 HTML 文件中内联 <style> 样式和可执行的 <script> 脚本"
    return None


# 拼接时保留原始报告头与正文，收起目录等外围元素，供原样式及数据脚本继续使用。
_MERGE_CONTENT_STYLE = """
[data-srg-merge-hidden] { display: none !important; }
html, body, [data-srg-merge-shell], [data-srg-merge-main] {
  height: auto !important;
  min-height: 0 !important;
  max-height: none !important;
  overflow: visible !important;
}
[data-srg-merge-shell] { display: block !important; position: static !important; }
.rf-layout { padding-block: 12px !important; }
.rf-filter-sticky { position: relative !important; }
"""

_MERGE_SCRIPT = """
const documents = JSON.parse(document.getElementById('documents').textContent);
const sharedEchartsEl = document.getElementById('shared-echarts');
const sharedEcharts = sharedEchartsEl ? JSON.parse(sharedEchartsEl.textContent) : {};
const contentStyle = document.getElementById('content-style').textContent;
const frames = new Map();
const reportFooter = '.rf-footer, .report-footer, .page-footer';
const reportHeading = 'header, [role="banner"], .rf-header, .report-header, .page-header, .report-cover, ' +
  'h1, [role="heading"][aria-level="1"]';
const pageChrome = 'footer, nav, [role="contentinfo"], ' + reportFooter;
const contentsNav = '.rf-toc, #toc, .toc, #table-of-contents, .table-of-contents, ' +
  '[aria-label*="目录"], [aria-label*="table of contents" i]';
const filterSelector = '.rf-filterbar, #filters, .filters, .filter-bar, [data-report-filters], ' +
  '[aria-label="报告筛选"], form:has(select)';
const hidden = 'data-srg-merge-hidden';

function prepareReport(source) {
  // 先在不执行脚本的文档中处理布局，图表初始化时即得到正文宽度。
  const doc = new DOMParser().parseFromString(source, 'text/html');
  for (const el of doc.querySelectorAll('script[data-srg-vendor^="merge:"]')) {
    const key = el.getAttribute('data-srg-vendor').slice(6);
    if (Object.hasOwn(sharedEcharts, key)) {
      el.setAttribute('data-srg-vendor', 'embedded');
      el.textContent = sharedEcharts[key];
    }
  }
  const body = doc.body;
  const main = body.querySelector('main:not([hidden]), [role="main"]:not([hidden])') ||
    body.querySelector('.rf-main, .report-content, #report-content, .main-content, #main-content');
  const filters = [...body.querySelectorAll(filterSelector)];
  const chrome = [...body.querySelectorAll(pageChrome)];
  for (const el of body.querySelectorAll(contentsNav)) el.setAttribute(hidden, '');
  for (const el of chrome) {
    if (main && el.contains(main)) continue;
    if (!el.matches(reportFooter)) {
      // 保留章节标题和脚注。正文内由锚点组成的导航是目录，标签页控件仍保留。
      if (el.matches('nav') && main?.contains(el)) {
        const links = [...el.querySelectorAll('a[href]')];
        if (!links.length || el.querySelector('[role="tab"]') ||
            links.some(link => !link.getAttribute('href').startsWith('#'))) continue;
      }
      if (!el.matches('nav') && el.closest('article, section')) continue;
    }
    el.setAttribute(hidden, '');
  }

  body.setAttribute('data-srg-merge-shell', '');
  if (main && !main.closest('template, svg')) {
    main.setAttribute('data-srg-merge-main', '');
    // 有些报告头在正文外层。保留标题或报告头所在的分支及其原有布局。
    const headingBranches = new Set();
    for (const heading of body.querySelectorAll(reportHeading)) {
      if (heading.closest('[hidden], [' + hidden + ']')) continue;
      for (let node = heading; node !== body; node = node.parentElement) headingBranches.add(node);
    }
    // 逐层展开正文外围的布局，消除侧栏隐藏后遗留的网格列及固定高度。
    for (let node = main; node !== body; node = node.parentElement) {
      const shell = node.parentElement;
      shell.setAttribute('data-srg-merge-shell', '');
      for (const sibling of shell.children) {
        if (sibling !== node && !headingBranches.has(sibling)) sibling.setAttribute(hidden, '');
      }
    }
  }

  const insertionPoint = main?.firstChild;
  for (const filter of filters) {
    if (filter.parentElement.closest(filterSelector) || filter.closest('[hidden], nav, footer')) continue;
    const container = filter.closest('[' + hidden + ']');
    if (!container) continue;
    // 报告头里的筛选保留在原位。仅将随外围布局隐藏的全局筛选移到正文前。
    if (main) main.insertBefore(filter, insertionPoint);
    else container.before(filter);
    filter.removeAttribute(hidden);
    if (filter.classList.contains('rf-filter-in-header')) {
      filter.classList.replace('rf-filter-in-header', 'rf-filter-sticky');
    }
  }

  const style = doc.createElement('style');
  style.textContent = contentStyle;
  const script = doc.createElement('script');
  script.textContent = '(' + observeHeight.toString() + ')()';
  // 在原报告的脚本执行前注册高度监听。原文中的业务脚本仅在沙箱内执行。
  doc.head.prepend(script);
  doc.head.append(style);
  return '<!doctype html>' + doc.documentElement.outerHTML;
}

function observeHeight() {
  document.addEventListener('DOMContentLoaded', () => {
    let pending = false;
    let previous = 0;
    function measure() {
      if (pending) return;
      pending = true;
      requestAnimationFrame(() => {
        pending = false;
        const root = document.documentElement;
        // 根节点的 scrollHeight 至少等于 iframe 视口高度。用正文几何尺寸允许筛选后缩短。
        const height = Math.ceil(Math.max(1, root.getBoundingClientRect().height,
          document.body.getBoundingClientRect().top + window.scrollY + document.body.scrollHeight));
        if (height !== previous) {
          previous = height;
          parent.postMessage({ type: 'srg-report-height', height }, '*');
        }
      });
    }
    const observer = new ResizeObserver(measure);
    observer.observe(document.documentElement);
    observer.observe(document.body);
    // 下拉面板等绝对定位内容可以改变溢出高度，但不会触发 ResizeObserver。
    const mutations = new MutationObserver(measure);
    mutations.observe(document.body, { attributes: true, childList: true, subtree: true });
    window.addEventListener('load', measure, { once: true });
    measure();
  });
}

window.addEventListener('message', event => {
  // 只接受当前文件创建的子页消息，且只调整发出消息的那一页。
  const frame = frames.get(event.source);
  const data = event.data;
  if (frame && data?.type === 'srg-report-height' && Number.isSafeInteger(data.height) && data.height > 0) {
    frame.style.height = data.height + 'px';
  }
});
const stack = document.getElementById('report-stack');
for (const report of documents) {
  const frame = document.createElement('iframe');
  frame.title = report.name;
  frame.setAttribute('sandbox', 'allow-scripts');
  frame.referrerPolicy = 'no-referrer';
  frame.srcdoc = prepareReport(report.html);
  stack.append(frame);
  frames.set(frame.contentWindow, frame);
}
"""


def merge_report_documents(name: str, reports: list[dict]) -> str:
    """按选定顺序纵向拼接各份报告的标题和正文，各报告在沙箱中独立运行。"""
    from .report_data import VENDOR_MARKER, _DataBlocks

    echarts_code = echarts_js()
    legacy_tag = f'<script>{echarts_code}</script>'
    shared_echarts: dict[str, str] = {}
    items = []

    def fold_echarts(block):
        if block.kind == "vendor" and block.fields[VENDOR_MARKER] == "embedded" and block.data:
            # 各报告的 ECharts 版本可能不同。只合并字节一致的脚本，并在各自沙箱内恢复。
            digest = sha256(block.data.encode()).hexdigest()
            shared_echarts[digest] = block.data
            return f"merge:{digest}", ""
        return None

    for r in reports:
        body = r["html"]
        if legacy_tag in body:
            body = body.replace(legacy_tag, f'<script {VENDOR_MARKER}="embedded">{echarts_code}</script>')
        if VENDOR_MARKER in body.lower():
            body = _DataBlocks(body).replace(fold_echarts)
        items.append({"name": r["name"], "html": body})
    # 转义小于号，防止正文里的 </script> 提前终止 JSON 并在外层页面执行。
    documents = json.dumps(items, ensure_ascii=False).replace("<", "\\u003c")
    shared_block = (
        f'<script id="shared-echarts" type="application/json">{json.dumps(shared_echarts).replace("<", "\\u003c")}</script>'
        if shared_echarts else ""
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval'; style-src 'unsafe-inline' data:; img-src data: blob:; font-src data:; media-src data: blob:; frame-src about: data: blob:; base-uri 'none'; form-action 'none'">
<meta name="referrer" content="no-referrer">
<title>{escape(name)}</title>
<style>
  body {{ margin: 0; }}
  #report-stack > iframe {{ display: block; width: 100%; height: 1px; border: 0; }}
</style>
</head>
<body>
<main id="report-stack" aria-label="合并报告正文"></main>
<script id="content-style" type="text/plain">{_MERGE_CONTENT_STYLE}</script>
{shared_block}
<script id="documents" type="application/json">{documents}</script>
<script>{_MERGE_SCRIPT}</script>
</body>
</html>"""
