"""报告模板（markdown）解析与静态校验。语法说明见 .claude/skills/smart-report/references/template-spec.md。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml

CHART_TYPES = {
    "kpi", "bar", "hbar", "stacked_bar", "line", "area", "stacked_area", "combo", "pie", "donut",
    "funnel", "treemap", "scatter", "heatmap", "gauge", "table", "bullet",
}
TYPE_ALIASES = {"horizontal_bar": "hbar", "column": "bar", "ring": "donut", "stack_bar": "stacked_bar",
                "stack_area": "stacked_area", "card": "kpi", "metric": "kpi"}
FILTER_TYPES = {"select", "multi", "range", "date_range"}
FORMATS_RE = re.compile(r"^(auto|int|wan|yi|money|percent|percent0|pp|float1|float2|raw|0(\.0+)?%?)$")
SORTS = {"value_desc", "value_asc", "x_asc", "x_desc", "none"}
ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
FIELD_RE = re.compile(r"^[^\W\d][\w]*$", re.U)
AGG_RE = re.compile(r"\b(sum|avg|count|count_distinct|min|max)\s*\(\s*(distinct\s+)?(\*|[^\W\d][\w]*)\s*\)", re.I | re.U)
METRIC_TOKEN_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\|\s*([\w.%]+)\s*)?\}\}")
DEFAULT_SPAN = {"kpi": 3, "gauge": 4, "pie": 4, "donut": 4, "funnel": 4, "treemap": 6, "table": 12, "heatmap": 12,
                "combo": 12, "line": 6, "area": 6, "stacked_area": 6, "bar": 6, "hbar": 6, "stacked_bar": 6,
                "scatter": 6, "bullet": 6}
DEFAULT_HEIGHT = {"kpi": 118, "gauge": 240, "table": 360, "heatmap": 360, "bullet": 240}
TRENDS = {True: "up", "up": "up", "down": "down", "none": "none"}


class TemplateError(ValueError):
    pass


def compile_expr(expr: str) -> dict:
    """把 'sum(profit)/sum(amount)' 编译为 {aggs: [[op, field]], js: 'a[0]/a[1]'}。"""
    if not isinstance(expr, str) or not expr.strip():
        raise TemplateError("度量表达式为空")
    aggs: list[list[str]] = []

    def rep(m: re.Match) -> str:
        op = m.group(1).lower()
        if m.group(2):
            if op != "count":
                raise TemplateError(f"只有 count 支持 distinct：{expr}")
            op = "count_distinct"
        fld = m.group(3).lower()
        if fld == "*" and op != "count":
            raise TemplateError(f"{op}(*) 不合法：{expr}")
        aggs.append([op, fld])
        return f"a[{len(aggs) - 1}]"

    js = AGG_RE.sub(rep, expr.strip())
    rest = re.sub(r"a\[\d+\]", "", js)
    if not re.fullmatch(r"[\s\d.+\-*/()]*", rest):
        raise TemplateError(f"度量表达式只能包含聚合函数(sum/avg/count/count_distinct/min/max)、数字和 + - * / ( )：{expr}")
    if not aggs:
        raise TemplateError(f"度量表达式必须包含聚合函数：{expr}")
    return {"aggs": aggs, "js": js}


@dataclass
class Template:
    meta: dict
    queries: dict[str, dict]
    measures: dict[str, dict]
    filters: list[dict]
    presets: list[dict]
    charts: dict[str, dict]
    intro: list[dict]
    sections: list[dict]
    theme: dict
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return str(self.meta.get("title") or "")

    def summary(self) -> dict:
        return {
            "title": self.title,
            "sections": [s["title"] for s in self.sections],
            "chart_count": len(self.charts),
            "filter_count": len(self.filters),
            "query_count": len(self.queries),
            "chart_types": sorted({c["type"] for c in self.charts.values()}),
        }


def split_front_matter(md: str) -> tuple[dict, str]:
    md = md.lstrip("﻿")
    m = re.match(r"^\s*---[ \t]*\n(.*?)\n---[ \t]*(\n|$)(.*)$", md, re.S)
    if not m:
        return {}, md
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise TemplateError(f"front matter YAML 解析失败：{e}") from e
    if not isinstance(meta, dict):
        raise TemplateError("front matter 必须是 YAML 对象")
    return meta, m.group(3)


# ---------------------------------------------------------------- body
def _parse_body(body: str, errors: list[str]) -> tuple[list[dict], list[dict], list[dict], str | None]:
    lines = body.split("\n")
    intro: list[dict] = []
    sections: list[dict] = []
    raw_charts: list[dict] = []
    h1: str | None = None
    cur_sec: dict | None = None
    cur_find: dict | None = None
    buf: list[str] = []

    def target() -> list[dict]:
        if cur_find is not None:
            return cur_find["items"]
        if cur_sec is not None:
            return cur_sec["items"]
        return intro

    def flush() -> None:
        text = "\n".join(buf).strip("\n")
        if text.strip():
            target().append({"kind": "text", "md": text})
        buf.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        fm = re.match(r"^\s*(`{3,}|~{3,})\s*([\w一-鿿-]*)\s*$", line)
        if fm:
            fence, info = fm.group(1), fm.group(2).lower()
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith(fence):
                j += 1
            block = "\n".join(lines[i + 1 : j])
            if info in ("chart", "图表"):
                flush()
                line_no = i + 1
                try:
                    spec = yaml.safe_load(block) or {}
                    if not isinstance(spec, dict):
                        raise TemplateError("图表块必须是 YAML 对象")
                except (yaml.YAMLError, TemplateError) as e:
                    errors.append(f"第 {line_no} 行图表块 YAML 无效：{e}")
                    spec = None
                if spec is not None:
                    spec["_line"] = line_no
                    raw_charts.append(spec)
                    target().append({"kind": "chart", "spec": spec})
            else:
                buf.extend(lines[i : j + 1])
            i = j + 1
            continue
        hm = re.match(r"^(#{1,3})\s+(.+?)\s*#*\s*$", line)
        if hm:
            level, title = len(hm.group(1)), hm.group(2).strip()
            flush()
            if level == 1 and not sections and h1 is None:
                h1 = title
            elif level <= 2:
                cur_sec = {"title": title, "summary": None, "items": []}
                sections.append(cur_sec)
                cur_find = None
            else:
                if cur_sec is None:
                    cur_sec = {"title": "", "summary": None, "items": []}
                    sections.append(cur_sec)
                cur_find = {"kind": "finding", "title": title, "items": []}
                cur_sec["items"].append(cur_find)
            i += 1
            continue
        buf.append(line)
        i += 1
    flush()

    # 每节第一个引用块（位于第一个子结论之前）作为本节汇总结论
    for sec in sections:
        for idx, it in enumerate(sec["items"]):
            if it["kind"] == "finding":
                break
            if it["kind"] != "text":
                continue
            ls = it["md"].split("\n")
            start = next((k for k, s in enumerate(ls) if s.lstrip().startswith(">")), None)
            if start is None:
                continue
            end = start
            while end < len(ls) and ls[end].lstrip().startswith(">"):
                end += 1
            sec["summary"] = "\n".join(re.sub(r"^\s*>\s?", "", s) for s in ls[start:end]).strip()
            rest = "\n".join(ls[:start] + ls[end:]).strip()
            if rest:
                it["md"] = rest
            else:
                sec["items"].pop(idx)
            break
    return intro, sections, raw_charts, h1


# ---------------------------------------------------------------- 规范化
def _measure_ref(ref: Any, chart_query: str | None, tpl_measures: dict, anon: dict, where: str,
                 errors: list[str]) -> str | None:
    """返回度量 key；内联表达式注册为匿名度量。"""
    over: dict = {}
    if isinstance(ref, dict):
        over = {k: ref[k] for k in ("name", "format", "as", "axis", "color") if k in ref}
        if "measure" in ref:
            ref = ref["measure"]
        elif "expr" in ref:
            ref = {"expr": ref["expr"]}
        elif "field" in ref:
            ref = {"expr": f"{ref.get('agg', 'sum')}({ref['field']})"}
        else:
            errors.append(f"{where}：度量需提供 measure / expr / field")
            return None
    if isinstance(ref, str) and ref in tpl_measures:
        base = tpl_measures[ref]
        if not over:
            return ref
        key = f"{ref}__{len(anon)}"
        anon[key] = {**base, **{k: v for k, v in over.items() if k in ("name", "format")}, "_display": over}
        return key
    expr = ref["expr"] if isinstance(ref, dict) else ref
    if not isinstance(expr, str):
        errors.append(f"{where}：无法识别的度量 {ref!r}")
        return None
    try:
        comp = compile_expr(expr)
    except TemplateError as e:
        if isinstance(ref, str) and ID_RE.match(ref):
            errors.append(f"{where}：未定义的度量 {ref!r}")
        else:
            errors.append(f"{where}：{e}")
        return None
    if not chart_query:
        errors.append(f"{where}：内联度量 {expr!r} 需要图表指定 query")
        return None
    key = f"_m{len(anon) + 1}"
    anon[key] = {"query": chart_query, "name": over.get("name") or expr, "format": over.get("format", "auto"),
                 "expr": expr, **comp, "_display": over}
    return key


def _as_list(v: Any) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _norm_chart(spec: dict, idx: int, tpl: Template, anon: dict) -> dict | None:
    errors = tpl.errors
    line = spec.pop("_line", "?")
    ctype = str(spec.get("type") or "").strip().lower()
    ctype = TYPE_ALIASES.get(ctype, ctype)
    cid = str(spec.get("id") or f"chart{idx + 1}")
    where = f"图表 {cid}（第 {line} 行）"
    if ctype not in CHART_TYPES:
        errors.append(f"{where}：type={spec.get('type')!r} 不支持，可选 {sorted(CHART_TYPES)}")
        return None
    if not ID_RE.match(cid):
        errors.append(f"{where}：id 只能包含字母数字下划线")
        return None
    if cid in tpl.charts:
        errors.append(f"{where}：id 重复")
        return None

    y_refs = _as_list(spec.get("y") if "y" in spec else spec.get("measures", spec.get("measure")))
    query = spec.get("query")
    if not query:
        for r in y_refs:
            name = r.get("measure") if isinstance(r, dict) else r
            if isinstance(name, str) and name in tpl.measures:
                query = tpl.measures[name]["query"]
                break
    if not query and len(tpl.queries) == 1:
        query = next(iter(tpl.queries))
    if query and query not in tpl.queries:
        errors.append(f"{where}：query={query!r} 未在 front matter queries 中定义")
        return None

    ys = []
    for r in y_refs:
        k = _measure_ref(r, query, tpl.measures, anon, where, errors)
        if k:
            m = tpl.measures.get(k) or anon.get(k)
            if m["query"] != query:
                errors.append(f"{where}：度量 {k} 属于查询 {m['query']}，与图表查询 {query} 不一致")
                continue
            disp = m.get("_display") or {}
            ys.append({"key": k, "as": disp.get("as"), "axis": disp.get("axis"), "color": disp.get("color")})
    sub = []
    for r in _as_list(spec.get("sub")):
        k = _measure_ref(r, query, tpl.measures, anon, where, errors)
        if k:
            tv = r.get("trend") if isinstance(r, dict) else None
            if tv not in (None, False) and tv not in TRENDS:
                errors.append(f"{where}：sub.trend 可选 up（越大越好）/ down（越小越好）/ none（不着色）")
                tv = None
            sub.append({"key": k, "trend": TRENDS.get(tv) if tv not in (None, False) else None})

    status = None
    st = spec.get("status")
    if st is not None:
        if not isinstance(st, dict) or not isinstance(st.get("good"), (int, float)):
            errors.append(f"{where}：status 需为 {{measure?, direction: up|down, good: 数值, warn?: 数值}}")
        elif ctype not in ("kpi", "gauge", "bullet"):
            errors.append(f"{where}：只有 kpi / gauge / bullet 可以判定健康度（status）")
        else:
            direction = st.get("direction", "up")
            warn = st.get("warn")
            if direction not in ("up", "down"):
                errors.append(f"{where}：status.direction 可选 up / down")
            elif warn is not None and not isinstance(warn, (int, float)):
                errors.append(f"{where}：status.warn 必须是数值")
            elif warn is not None and ((direction == "up" and warn > st["good"]) or (direction == "down" and warn < st["good"])):
                errors.append(f"{where}：status.warn 应介于“承压”与 good 之间（direction={direction}）")
            else:
                mkey = None
                if st.get("measure") is not None:
                    mkey = _measure_ref(st["measure"], query, tpl.measures, anon, where, errors)
                labels = st.get("labels") if isinstance(st.get("labels"), dict) else {}
                status = {"measure": mkey, "direction": direction, "good": st["good"], "warn": warn,
                          "labels": {k: str(v) for k, v in labels.items() if k in ("positive", "warning", "negative")}}

    def fld(name: str) -> str | None:
        v = spec.get(name)
        if v is None:
            return None
        v = str(v).strip().lower()
        if not FIELD_RE.match(v):
            errors.append(f"{where}：{name}={v!r} 不是合法字段名")
            return None
        return v

    out: dict[str, Any] = {
        "id": cid, "type": ctype, "query": query, "title": str(spec.get("title") or ""),
        "note": str(spec.get("note") or ""), "x": fld("x"), "series": fld("series"), "group": fld("group"),
        "y": ys, "sub": sub, "dims": [str(d).lower() for d in _as_list(spec.get("dims"))],
        "columns": [str(d).lower() for d in _as_list(spec.get("columns"))],
        "sort": spec.get("sort"), "top": spec.get("top"), "others": bool(spec.get("others", ctype in ("pie", "donut"))),
        "stack": bool(spec.get("stack", ctype in ("stacked_bar", "stacked_area"))),
        "labels": spec.get("labels"), "smooth": bool(spec.get("smooth", False)), "max": spec.get("max"),
        "series_limit": int(spec.get("series_limit", 8)), "bar_in_cell": bool(spec.get("bar_in_cell", ctype == "table")),
        "page_size": int(spec.get("page_size", 0) or 0), "legend": spec.get("legend"),
        "span": spec.get("span"), "height": spec.get("height"), "label": str(spec.get("label") or ""),
        "x_label": str(spec.get("x_label") or ""), "y_label": str(spec.get("y_label") or ""),
        "zoom": bool(spec.get("zoom", False)),
        "headers": {str(k).lower(): str(v) for k, v in (spec.get("headers") or {}).items()}
        if isinstance(spec.get("headers"), dict) else {},
        "status": status, "unit": str(spec.get("unit") or ""),
    }
    if out["sort"] is not None and out["sort"] not in SORTS:
        errors.append(f"{where}：sort 可选 {sorted(SORTS)}")
    if out["top"] is not None and not (isinstance(out["top"], int) and out["top"] > 0):
        errors.append(f"{where}：top 必须为正整数")
    if out["span"] is not None and not (isinstance(out["span"], int) and 1 <= out["span"] <= 12):
        errors.append(f"{where}：span 必须是 1-12 的整数")
    if out["height"] is not None and not (isinstance(out["height"], int) and 60 <= out["height"] <= 1200):
        errors.append(f"{where}：height 必须是 60-1200 的整数（像素）")
    if not query and ctype != "table":
        errors.append(f"{where}：缺少 query")

    need_y = {"kpi": 1, "gauge": 1, "pie": 1, "donut": 1, "funnel": 1, "treemap": 1, "heatmap": 1, "scatter": 2,
              "bullet": 2}
    if ctype in need_y and len(ys) < need_y[ctype]:
        errors.append(f"{where}：{ctype} 至少需要 {need_y[ctype]} 个度量(y)")
    if ctype in ("bar", "hbar", "stacked_bar", "line", "area", "stacked_area", "combo", "pie", "donut", "funnel",
                 "treemap", "heatmap") and not out["x"]:
        errors.append(f"{where}：{ctype} 需要 x（分类/时间字段）")
    if ctype in ("bar", "hbar", "stacked_bar", "line", "area", "stacked_area", "combo") and not ys:
        errors.append(f"{where}：{ctype} 至少需要一个度量(y)")
    if ctype == "heatmap" and not out["series"]:
        errors.append(f"{where}：heatmap 需要 series 作为纵轴分类字段")
    if ctype == "scatter" and not out["group"]:
        errors.append(f"{where}：scatter 需要 group（每个点代表的维度字段）")
    if ctype == "table" and not (ys or out["columns"]):
        errors.append(f"{where}：table 需要 dims+y（聚合表）或 columns（明细表）")
    if ctype == "table" and not query:
        errors.append(f"{where}：缺少 query")
    if out["series"] and len(ys) > 1 and ctype not in ("heatmap",):
        errors.append(f"{where}：使用 series 拆分时只能有一个度量")
    return out


def _norm_filters(meta: dict, tpl: Template) -> None:
    raw = meta.get("filters") or []
    if not isinstance(raw, list):
        tpl.errors.append("filters 必须是列表")
        return
    seen = set()
    for i, f in enumerate(raw):
        where = f"筛选器 #{i + 1}"
        if not isinstance(f, dict):
            tpl.errors.append(f"{where}：必须是对象")
            continue
        fid = str(f.get("id") or f.get("field") or "")
        ftype = str(f.get("type") or "multi").lower()
        fieldname = str(f.get("field") or fid).lower()
        if not ID_RE.match(fid) or fid in seen:
            tpl.errors.append(f"{where}：id 缺失/非法/重复")
            continue
        if ftype not in FILTER_TYPES:
            tpl.errors.append(f"{where}：type 可选 {sorted(FILTER_TYPES)}")
            continue
        targets = f.get("targets") or {}
        if not isinstance(targets, dict) or any(q not in tpl.queries for q in targets):
            tpl.errors.append(f"{where}：targets 必须是 {{查询id: 字段}}，且查询已定义")
            targets = {}
        seen.add(fid)
        tpl.filters.append({
            "id": fid, "label": str(f.get("label") or fid), "type": ftype, "field": fieldname,
            "targets": {k: str(v).lower() for k, v in targets.items()},
            "exclude": [q for q in _as_list(f.get("exclude")) if q in tpl.queries],
            "default": f.get("default"), "options": f.get("options"),
            "options_query": f.get("options_query"), "cascade": bool(f.get("cascade", True)),
            "placeholder": str(f.get("placeholder") or "全部"),
        })
    for p in meta.get("presets") or []:
        if isinstance(p, dict) and p.get("name") and isinstance(p.get("values"), dict):
            bad = [k for k in p["values"] if k not in seen]
            if bad:
                tpl.errors.append(f"预设 {p['name']}：引用了未定义的筛选器 {bad}")
                continue
            tpl.presets.append({"name": str(p["name"]), "values": p["values"]})
        else:
            tpl.errors.append("presets 每项需要 name 与 values")


def parse_template(md: str) -> tuple[Template, list[str], list[str]]:
    errors: list[str] = []
    try:
        meta, body = split_front_matter(md or "")
    except TemplateError as e:
        meta, body = {}, md or ""
        errors.append(str(e))
    tpl = Template(meta=meta, queries={}, measures={}, filters=[], presets=[], charts={}, intro=[], sections=[],
                   theme=meta.get("theme") if isinstance(meta.get("theme"), dict) else {}, errors=errors)

    # queries & measures
    rawq = meta.get("queries") or {}
    if not isinstance(rawq, dict) or not rawq:
        errors.append("front matter 必须定义 queries（至少一个查询）")
        rawq = {}
    for qid, q in rawq.items():
        if not ID_RE.match(str(qid)):
            errors.append(f"查询 id {qid!r} 只能包含字母数字下划线")
            continue
        if isinstance(q, str):
            q = {"sql": q}
        if not isinstance(q, dict) or not str(q.get("sql") or "").strip():
            errors.append(f"查询 {qid}：缺少 sql")
            continue
        tpl.queries[qid] = {"id": qid, "sql": str(q["sql"]).strip(), "datasource": q.get("datasource"),
                            "label": str(q.get("label") or qid)}
        for mid, m in (q.get("measures") or {}).items():
            if not ID_RE.match(str(mid)):
                errors.append(f"度量 id {mid!r} 非法")
                continue
            if mid in tpl.measures:
                errors.append(f"度量 {mid} 重复定义")
                continue
            if isinstance(m, str):
                m = {"expr": m}
            try:
                comp = compile_expr(m.get("expr") or (f"{m.get('agg', 'sum')}({m['field']})" if m.get("field") else ""))
            except TemplateError as e:
                errors.append(f"度量 {mid}：{e}")
                continue
            fmt = str(m.get("format") or "auto")
            if not FORMATS_RE.match(fmt):
                errors.append(f"度量 {mid}：format={fmt!r} 不支持")
            tpl.measures[mid] = {"query": qid, "name": str(m.get("name") or mid), "format": fmt,
                                 "expr": m.get("expr"), "unit": str(m.get("unit") or ""), **comp}

    _norm_filters(meta, tpl)

    intro, sections, raw_charts, h1 = _parse_body(body, errors)
    if h1 and not tpl.meta.get("title"):
        tpl.meta["title"] = h1
    if not tpl.meta.get("title"):
        errors.append("缺少报告标题（front matter title 或一级标题）")
    if not sections:
        errors.append("至少需要一个章节（## 二级标题）")
    tpl.intro, tpl.sections = intro, sections

    anon: dict[str, dict] = {}
    for idx, spec in enumerate(raw_charts):
        norm = _norm_chart(spec, idx, tpl, anon)
        if norm:
            tpl.charts[norm["id"]] = norm
            spec["_id"] = norm["id"]
    for k, m in anon.items():
        m.pop("_display", None)
        tpl.measures[k] = m

    # 汇总结论与图表由报告结构和样式规则决定；这里只检查文本中的度量引用。
    def scan(items: list[dict]) -> None:
        for it in items:
            if it["kind"] == "text":
                for m in METRIC_TOKEN_RE.finditer(it["md"]):
                    if m.group(1) not in tpl.measures:
                        tpl.warnings.append(f"文本中的 {{{{{m.group(1)}}}}} 未定义为度量，将原样显示")
            elif it["kind"] == "finding":
                scan(it["items"])

    scan(intro)
    for sec in sections:
        scan(sec["items"])
        if sec["summary"]:
            scan([{"kind": "text", "md": sec["summary"]}])
    return tpl, tpl.errors, tpl.warnings


def check_against_data(tpl: Template, columns: dict[str, list[str]]) -> list[str]:
    """用查询结果列校验字段引用。columns: {query_id: [列名]}"""
    errs: list[str] = []
    for mid, m in tpl.measures.items():
        cols = columns.get(m["query"])
        if cols is None:
            continue
        for op, f in m["aggs"]:
            if f != "*" and f not in cols:
                errs.append(f"度量 {mid if not mid.startswith('_m') else m.get('expr')}：字段 {f} 不在查询 {m['query']} 的结果列 {cols} 中")
    for c in tpl.charts.values():
        cols = columns.get(c["query"])
        if cols is None:
            continue
        for k in ("x", "series", "group"):
            if c.get(k) and c[k] not in cols:
                errs.append(f"图表 {c['id']}：{k}={c[k]} 不在查询 {c['query']} 的结果列 {cols} 中")
        for d in c["dims"] + c["columns"]:
            if d not in cols:
                errs.append(f"图表 {c['id']}：字段 {d} 不在查询 {c['query']} 的结果列中")
    for f in tpl.filters:
        hit = [q for q, cols in columns.items()
               if q not in f["exclude"] and (f["targets"].get(q) or f["field"]) in cols]
        if not hit:
            errs.append(f"筛选器 {f['id']}：没有任何查询包含字段 {f['field']}，筛选不会生效")
        for q, col in f["targets"].items():
            if q in columns and col not in columns[q]:
                errs.append(f"筛选器 {f['id']}：targets.{q}={col} 不在该查询结果列中")
    return errs
