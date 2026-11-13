"""报告模板 → HTML。查询在服务端执行，结果作为数据包嵌入页面；筛选联动、聚合与图表在浏览器端完成。"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from markdown_it import MarkdownIt

from . import design, repo
from .config import settings
from .connectors import ConnectorError, get_connector
from .template_parser import (
    DEFAULT_HEIGHT, DEFAULT_SPAN, METRIC_TOKEN_RE, Template, check_against_data, parse_template,
)

STATIC = Path(__file__).with_name("static")
_md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False}).enable("table").enable("strikethrough")

class RenderError(ValueError):
    pass


def _scenario_of(tpl: Template, dataset_id: int | None) -> str | None:
    if tpl.meta.get("scenario"):
        return str(tpl.meta["scenario"])
    ds_id = dataset_id or tpl.meta.get("dataset_id")
    if ds_id:
        try:
            return repo.get_dataset(int(ds_id))["business_scenario"]
        except repo.NotFound:
            return None
    return None


def resolve_theme(tpl: Template, dataset_id: int | None = None, owner: str | None = None) -> tuple[dict, list[str]]:
    """生效顺序：智能体决定（模板 theme）≥ 业务场景规则 ≥ 通用规则 ≥ 内置样式。
    规则合并出的指定样式是约束，智能体可以按自己的判断在模板中覆盖。返回 (渲染用完整取值, 提示)。"""
    rules = repo.style_rules_for(_scenario_of(tpl, dataset_id), owner=owner)
    specified = rules["specified"]["theme"]
    chosen, warns = design.resolve_overrides(tpl.theme)
    sources = rules["specified"]["theme_sources"]
    overridden = sorted(k for k in chosen if k in specified and chosen[k] != specified[k])
    if overridden:
        warns.append("提示：模板 theme 覆盖了指定样式 " + "、".join(f"{k}（原为{sources.get(k, '')}）" for k in overridden)
                     + "，按智能体的决定展示；请确认这是有意为之")
    theme = {**specified, **chosen}
    return design.full(theme), warns


# ------------------------------------------------------------ 数据
def _default_datasource(tpl: Template, dataset_id: int | None) -> dict | None:
    ref = tpl.meta.get("datasource")
    if ref:
        return repo.find_datasource(ref)
    ds_id = dataset_id or tpl.meta.get("dataset_id")
    if not ds_id and tpl.meta.get("scenario"):
        from . import db

        row = db.fetchone("SELECT id FROM dataset.dataset WHERE business_scenario=%s", (tpl.meta["scenario"],))
        ds_id = row["id"] if row else None
    if ds_id:
        d = repo.get_dataset(int(ds_id))
        if d.get("datasource_id"):
            return repo.get_datasource(d["datasource_id"], with_secret=True)
    return None


def run_queries(tpl: Template, dataset_id: int | None = None, max_rows: int | None = None,
                guard: Callable[[dict, str], None] | None = None) -> tuple[dict, dict, list[str]]:
    """返回 (data{qid:{columns,rows}}, stats{qid:{...}}, errors)。guard(ds_row, sql) 用于数据权限校验，不通过时抛异常。"""
    max_rows = max_rows or settings.query_max_rows
    data: dict[str, dict] = {}
    stats: dict[str, dict] = {}
    errors: list[str] = []
    default_ds = None
    default_err = None
    try:
        default_ds = _default_datasource(tpl, dataset_id)
    except repo.NotFound as e:
        default_err = str(e)
    for qid, q in tpl.queries.items():
        t0 = time.time()
        try:
            ds = repo.find_datasource(q["datasource"]) if q.get("datasource") else default_ds
            if not ds:
                raise ConnectorError(default_err or "未指定数据源：请在 front matter 设置 datasource，或关联数据集")
            if guard:
                guard(ds, q["sql"])
            res = get_connector(ds).query(q["sql"], max_rows)
        except (ConnectorError, repo.NotFound, RuntimeError, PermissionError) as e:
            errors.append(f"查询 {qid} 执行失败：{e}")
            continue
        data[qid] = {"columns": res["columns"], "rows": res["rows"]}
        stats[qid] = {"datasource": ds["name"], "rows": len(res["rows"]), "columns": res["columns"],
                      "truncated": res["truncated"], "ms": int((time.time() - t0) * 1000)}
        if res["truncated"]:
            errors.append(f"查询 {qid} 结果超过 {max_rows} 行已截断——请在 SQL 中先聚合到图表与筛选所需的粒度")
    return data, stats, errors


# ------------------------------------------------------------ 布局
def _layout(charts: list[dict]) -> list[tuple[dict, int, int]]:
    """12 栅格自动排布：未指定 span 的图表按类型给默认宽度，逐行装箱后把剩余空间平均分给自动项。
    内部使用 60 列网格，以便 5 个/行等非整除布局。返回 [(chart, span60, height)]."""
    items = []
    for c in charts:
        auto = c.get("span") is None
        items.append({"c": c, "auto": auto, "span": float(c["span"] if not auto else DEFAULT_SPAN.get(c["type"], 6))})
    # 参考样式的紧凑指标卡可五等分；六张按三列两行。窄屏由 CSS 重排，显式 span 优先。
    i = 0
    while i < len(items):
        j = i
        while j < len(items) and items[j]["auto"] and items[j]["c"]["type"] == "kpi":
            j += 1
        if j - i in (5, 6):
            for it in items[i:j]:
                it["span"] = 12 / 5 if j - i == 5 else 4.0
        i = max(j, i + 1)
    rows: list[list[dict]] = []
    cur: list[dict] = []
    used = 0
    for it in items:
        if cur and used + it["span"] > 12 + 1e-6:
            rows.append(cur)
            cur, used = [], 0
        cur.append(it)
        used += it["span"]
    if cur:
        rows.append(cur)
    out = []
    for row in rows:
        total = sum(i["span"] for i in row)
        autos = [i for i in row if i["auto"]]
        if autos and total < 12 - 1e-6:
            if len(autos) == len(row):
                for i in row:
                    i["u"] = 60 // len(row) if 60 % len(row) == 0 else int(i["span"] * 60 // total)
            else:
                spare = int(round((12 - total) * 5))
                for i in row:
                    i["u"] = int(round(i["span"] * 5)) + (spare // len(autos) if i["auto"] else 0)
        else:
            for i in row:
                i["u"] = int(round(i["span"] * 5))
        heights = []
        for i in row:
            c = i["c"]
            h = c.get("height") or DEFAULT_HEIGHT.get(c["type"]) or 300
            heights.append(h)
        # 同一行非 KPI 图表统一高度，保证卡片对齐
        non_kpi = [h for i, h in zip(row, heights) if i["c"]["type"] != "kpi" and not i["c"].get("height")]
        row_h = max(non_kpi) if non_kpi else None
        for i, h in zip(row, heights):
            if row_h and i["c"]["type"] != "kpi" and not i["c"].get("height"):
                h = row_h
            out.append((i["c"], i["u"], h))
    return out


_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_CODE = re.compile(r"(`+)(.+?)\1")


def _cjk_bold(text: str) -> str:
    """CommonMark 规定 ** 紧邻中文标点时不构成强调（如 **“规模”**：），中文报告里极常见。
    先把 **…** 换成私有区占位符，渲染后再还原为 <strong>（跳过行内代码）。"""
    parts = _CODE.split(text)
    out = []
    i = 0
    while i < len(parts):
        out.append(_BOLD.sub("\ue000\\1\ue001", parts[i]))
        if i + 2 < len(parts):
            out.append(parts[i + 1] + parts[i + 2] + parts[i + 1])
        i += 3
    return "".join(out)


def _md_html(text: str, tpl: Template) -> str:
    out = _md.render(_cjk_bold(text)).replace("\ue000", "<strong>").replace("\ue001", "</strong>")

    def rep(m: re.Match) -> str:
        mid, fmt = m.group(1), m.group(2) or ""
        if mid not in tpl.measures:
            return m.group(0)
        return f'<span class="rf-metric" data-m="{mid}" data-fmt="{html.escape(fmt)}">…</span>'

    return METRIC_TOKEN_RE.sub(rep, out)


def _chart_unit(c: dict, tpl: Template) -> str:
    if c.get("unit"):
        return str(c["unit"])
    if c["type"] in ("kpi", "gauge", "bullet") or not c["y"]:
        return ""
    ms = [tpl.measures[y["key"]] for y in c["y"] if y["key"] in tpl.measures]
    pairs = {(m.get("format", "auto"), m.get("unit", "")) for m in ms}
    if len(pairs) != 1:
        return ""
    fmt, unit = pairs.pop()
    return {"wan": "万" + unit, "yi": "亿" + unit, "percent": "%", "percent0": "%", "pp": "百分点",
            "money": "元"}.get(fmt, unit)


def _chart_html(c: dict, span: int, height: int, tpl: Template) -> str:
    title = c["title"]
    if not title and c["type"] in ("kpi", "gauge", "bullet") and c["y"]:
        title = tpl.measures[c["y"][0]["key"]]["name"]
    unit = _chart_unit(c, tpl)
    note = f'<div class="rf-card-note">{html.escape(c["note"])}</div>' if c["note"] else ""
    head = (f'<div class="rf-card-head"><div class="rf-card-title" title="{html.escape(title)}">{html.escape(title)}</div>'
            + (f'<span class="rf-unit">{html.escape(unit)}</span>' if unit else "")
            + '<span class="rf-spacer"></span><span class="rf-status" hidden></span>'
            '<span class="rf-link-hint" hidden title="点击图形可联动筛选">⇄ 可联动</span></div>') if title else ""
    cls = f"rf-card rf-chart rf-type-{c['type']}" + (" rf-narrow" if span < 30 else "")
    body_h = "" if c["type"] == "kpi" else f' style="height:{height}px"'
    return (f'<div class="{cls}" data-chart="{c["id"]}" style="--span:{span}">{head}'
            f'<div class="rf-chart-body"{body_h}></div>{note}</div>')


def _items_html(items: list[dict], tpl: Template, section_id: str | None = None) -> str:
    parts: list[str] = []
    group: list[dict] = []

    def flush() -> None:
        if group:
            charts = [tpl.charts[g["spec"]["_id"]] for g in group]
            cells = "".join(_chart_html(c, s, h, tpl) for c, s, h in _layout(charts))
            grid = "rf-grid rf-grid-kpi" if all(c["type"] == "kpi" for c in charts) else "rf-grid"
            parts.append(f'<div class="{grid}">{cells}</div>')
            group.clear()

    for index, it in enumerate(items, 1):
        if it["kind"] == "chart":
            if it["spec"].get("_id") in tpl.charts:
                group.append(it)
            continue
        flush()
        if it["kind"] == "text":
            parts.append(f'<div class="rf-text">{_md_html(it["md"], tpl)}</div>')
        elif it["kind"] == "finding":
            # 子结论锚点使用章节与条目位置，避免重名标题生成重复 ID。
            anchor = f' id="{section_id}-finding-{index}"' if section_id else ""
            parts.append(
                f'<div class="rf-finding"{anchor}><h3 class="rf-finding-title">{html.escape(it["title"])}</h3>'
                f'{_items_html(it["items"], tpl)}</div>'
            )
    flush()
    return "".join(parts)


def _body_html(tpl: Template) -> tuple[str, str]:
    secs = []
    toc = []
    for i, s in enumerate(tpl.sections, 1):
        sid = f"sec-{i}"
        summary = ""
        if s["summary"]:
            summary = f'<div class="rf-summary"><div class="rf-summary-body">{_md_html(s["summary"], tpl)}</div></div>'
        title = html.escape(s["title"])
        secs.append(
            f'<section class="rf-section" id="{sid}"><h2 class="rf-section-title">{title}</h2>'
            f'{summary}{_items_html(s["items"], tpl, sid)}</section>'
        )
        children = "".join(
            f'<a href="#{sid}-finding-{index}">{html.escape(it["title"])}</a>'
            for index, it in enumerate(s["items"], 1) if it["kind"] == "finding"
        )
        if children:
            children = f'<div class="rf-toc-children">{children}</div>'
        toc.append(f'<div class="rf-toc-group"><a class="rf-toc-parent" href="#{sid}">{title}</a>{children}</div>')
    intro = _items_html(tpl.intro, tpl) if tpl.intro else ""
    return (f'<div class="rf-intro">{intro}</div>' if intro else "") + "".join(secs), "".join(toc)


def _json_script(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/").replace("<!--", "<\\!--")


def build_html(tpl: Template, data: dict, theme: dict, generated_at: str, echarts_src: str | None = None,
               inline_echarts: bool = False) -> str:
    body, toc = _body_html(tpl)
    meta = tpl.meta
    measures = {k: {kk: m[kk] for kk in ("query", "name", "format", "aggs", "js", "unit") if kk in m}
                for k, m in tpl.measures.items()}
    charts = {k: {kk: v for kk, v in c.items() if kk not in ("span", "height")} for k, c in tpl.charts.items()}
    payload = {
        "meta": {"title": tpl.title, "subtitle": meta.get("subtitle") or "", "scenario": meta.get("scenario") or "",
                 "generated_at": generated_at},
        "theme": design.runtime_tokens(theme),
        "filters": tpl.filters, "presets": tpl.presets, "measures": measures, "charts": charts, "data": data,
    }
    css = (STATIC / "report" / "report.css").read_text(encoding="utf-8")
    js = (STATIC / "report" / "runtime.js").read_text(encoding="utf-8")
    if inline_echarts:
        ech = "<script>" + (STATIC / "vendor" / "echarts.min.js").read_text(encoding="utf-8") + "</script>"
    else:
        ech = f'<script src="{html.escape(echarts_src or "/static/vendor/echarts.min.js")}"></script>'
    sub = html.escape(meta.get("subtitle") or "")
    goal = html.escape(str(meta.get("analysis_goal") or ""))
    scenario = html.escape(str(meta.get("scenario") or ""))
    header_cls = "rf-header rf-header-banner" if theme["header_style"] == "banner" else "rf-header rf-header-plain"
    toc_html = f'<nav class="rf-toc" aria-label="报告目录"><div class="rf-toc-card"><div class="rf-toc-title">目录</div>{toc}</div></nav>' if theme["toc"] and toc else ""
    # 筛选沿用同一套联动控件。默认放在封面内，选择吸顶时移到封面下方。
    sticky = theme["filter_sticky"]
    filter_cls = "rf-filter-sticky" if sticky else "rf-filter-in-header"
    filters = f'<div class="rf-filterbar {filter_cls}" id="rf-filterbar" role="region" aria-label="报告筛选"><div class="rf-filterbar-inner"></div></div>'
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="{html.escape(settings.app_name)}">
<title>{html.escape(tpl.title)}</title>
<style>{design.css_vars(theme)}
{css}
{theme['custom_css']}</style>
</head>
<body class="{'rf-zebra' if theme['table_zebra'] else ''}">
<div class="rf-wrap rf-layout{' rf-has-toc' if toc_html else ''}">
  {toc_html}
  <main class="rf-main">
    <header class="{header_cls}">
      <h1 class="rf-title">{html.escape(tpl.title)}</h1>
      {f'<p class="rf-subtitle">{sub}</p>' if sub else ''}
      {f'<p class="rf-goal"><b>分析目标</b>{goal}</p>' if goal else ''}
      {f'<div class="rf-chips"><span class="rf-chip">业务场景 · {scenario}</span></div>' if scenario else ''}
      {filters if not sticky else ''}
    </header>
    {filters if sticky else ''}
    {body}
  </main>
</div>
<footer class="rf-footer"><div class="rf-wrap">由 {html.escape(settings.app_name)} 生成 · {html.escape(generated_at)}</div></footer>
{ech}
<script id="rf-data" type="application/json">{_json_script(payload)}</script>
<script>{js}</script>
</body>
</html>"""


def render(markdown: str, dataset_id: int | None = None, inline_echarts: bool = False,
           guard: Callable[[dict, str], None] | None = None, owner: str | None = None) -> tuple[str, dict]:
    t0 = time.time()
    tpl, errors, warnings = parse_template(markdown)
    if errors:
        raise RenderError("模板校验失败：\n- " + "\n- ".join(errors))
    data, stats, qerrors = run_queries(tpl, dataset_id, guard=guard)
    hard = [e for e in qerrors if "执行失败" in e]
    if hard:
        raise RenderError("\n".join(hard))
    ferr = check_against_data(tpl, {k: v["columns"] for k, v in data.items()})
    if ferr:
        raise RenderError("字段校验失败：\n- " + "\n- ".join(ferr))
    theme, twarn = resolve_theme(tpl, dataset_id, owner)
    generated_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    out = build_html(tpl, data, theme, generated_at, inline_echarts=inline_echarts)
    meta = {"queries": stats, "warnings": warnings + twarn + qerrors, "summary": tpl.summary(),
            "render_ms": int((time.time() - t0) * 1000), "html_bytes": len(out.encode())}
    return out, meta


def validate(markdown: str, dataset_id: int | None = None, execute: bool = True,
             guard: Callable[[dict, str], None] | None = None, owner: str | None = None) -> dict:
    tpl, errors, warnings = parse_template(markdown)
    res: dict[str, Any] = {"ok": False, "errors": list(errors), "warnings": list(warnings), "summary": tpl.summary()}
    if errors or not execute:
        res["ok"] = not errors
        return res
    data, stats, qerrors = run_queries(tpl, dataset_id, guard=guard)
    res["errors"] += [e for e in qerrors if "执行失败" in e]
    res["warnings"] += [e for e in qerrors if "执行失败" not in e]
    res["errors"] += check_against_data(tpl, {k: v["columns"] for k, v in data.items()})
    _, twarn = resolve_theme(tpl, dataset_id, owner)
    res["warnings"] += twarn
    res["queries"] = {
        q: {**stats[q], "sample": data[q]["rows"][:5]} for q in stats
    }
    fvals = {}
    for f in tpl.filters:
        vals: set = set()
        for q, d in data.items():
            col = f["targets"].get(q) or f["field"]
            if col in d["columns"] and q not in f["exclude"]:
                i = d["columns"].index(col)
                vals.update(str(r[i]) for r in d["rows"] if r[i] is not None)
        fvals[f["id"]] = {"distinct": len(vals), "examples": sorted(vals)[:12]}
    res["filters"] = fvals
    total_rows = sum(s["rows"] for s in stats.values())
    if total_rows > 20000:
        res["warnings"].append(f"嵌入数据共 {total_rows} 行，页面可能偏大，建议在 SQL 中进一步聚合")
    res["ok"] = not res["errors"]
    return res


def standalone(html_text: str) -> str:
    """把外链的 echarts 替换为内联脚本，得到可离线打开的单文件 HTML。"""
    ech = (STATIC / "vendor" / "echarts.min.js").read_text(encoding="utf-8")
    return re.sub(r'<script src="[^"]*echarts\.min\.js"></script>', lambda _m: f"<script>{ech}</script>", html_text, count=1)
