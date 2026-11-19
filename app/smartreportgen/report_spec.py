"""声明式报告的字段、快照绑定和离线编译。"""
from __future__ import annotations

from decimal import Decimal
from functools import cache
from hashlib import sha256
from html import escape
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import re
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from . import design, report_data, report_document
from .permission import PermissionDenied, access_for
from .report_queries import FieldName, Key, QueryPlan, Scalar, StrictModel, scope_for

ASSET = Path(__file__).with_name("static") / "report" / "compiled.js"
TOKEN = re.compile(r"\{\{([a-z][a-z0-9_]{0,47})(\.state)?\}\}")
BARE_NUMBER = re.compile(r"\d|[零〇一二两三四五六七八九十百千万亿]+(?:元|吨|件|家|人|台|个|成|倍|%|％|个百分点)")
DECIMAL = Annotated[str, Field(pattern=r"^[+-]?\d+(?:\.\d+)?$", max_length=100)]
Text = Annotated[str, Field(min_length=1, max_length=300)]
HEX = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]
State = Literal["up", "down", "neutral", "met", "miss", "warning"]
CHART_TYPES = ("bar", "line", "area", "pie", "donut", "combo", "waterfall")
RADIAL_TYPES = ("pie", "donut")


def resolve_report_theme(values: dict) -> dict:
    # 单份报告使用与规则编辑器一致的令牌；主题只能影响展示，不提供可执行内容。
    if any(design.canonical_key(key) not in design.BUILTIN_TOKENS for key in values):
        raise ValueError("报告 theme 只接受内置样式令牌，详见 get_report_spec")
    if any(type(values[key]) is not bool for key in values if design.canonical_key(key) in design.BOOLS):
        raise ValueError("目录、筛选吸顶、斑马纹选项须使用 JSON 布尔值")
    try:
        resolved, notes = design.resolve_overrides(values)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("报告 theme 含无效的字号、尺寸或其他令牌取值") from exc
    if notes:
        raise ValueError("；".join(notes))
    state_keys = {"color-trend-up", "color-trend-down", "color-status-positive", "color-status-negative",
                  "color-status-warning", "color-text-secondary"}
    if any(not re.fullmatch(r"#[0-9a-fA-F]{6}", resolved[key]) for key in resolved.keys() & state_keys):
        raise ValueError("表达状态的颜色和次级文本色需填写六位十六进制色值")
    if any(len(resolved[key]) > 20 for key in resolved.keys() & design.PALETTE_TOKENS.keys()):
        raise ValueError("每个图表色板最多配置 20 个颜色")
    return resolved


class Format(StrictModel):
    kind: Literal["number", "rate", "delta", "rate_delta", "text"] = "number"
    scale: Literal["decimal", "percent", "number"] = "number"
    digits: int = Field(default=2, ge=0, le=6)
    unit: str = Field(default="", max_length=24, pattern=r"^[^%％]+$|^$")
    divisor: int = Field(default=1, ge=1, le=10**12)
    signed: bool = False
    signal: Literal["trend", "attainment"] | None = None
    direction: Literal["higher", "lower"] = "higher"
    threshold: DECIMAL | None = None
    warning_threshold: DECIMAL | None = None

    @model_validator(mode="after")
    def validate_scale(self):
        if (self.kind in ("rate", "rate_delta")) != (self.scale != "number"):
            raise ValueError("比率必须声明 decimal 或 percent，其余格式使用 number")
        if self.kind in ("rate", "rate_delta", "text") and (self.unit or self.divisor != 1):
            raise ValueError("比率及文本字段不能设置数字单位或换算")
        if self.signal == "attainment" and self.threshold is None:
            raise ValueError("达标判定必须提供原始尺度的阈值")
        if self.threshold is not None and self.signal != "attainment":
            raise ValueError("阈值仅适用于达标判定")
        if self.warning_threshold is not None:
            if self.threshold is None or (Decimal(self.warning_threshold) - Decimal(self.threshold)) * (
                    1 if self.direction == "higher" else -1) >= 0:
                raise ValueError("预警阈值必须处于达标线的未达标一侧")
        return self


class Source(StrictModel):
    query: Key
    field: FieldName
    by: dict[Key, FieldName] = Field(default_factory=dict, max_length=8)
    where: dict[FieldName, Scalar] = Field(default_factory=dict, max_length=8)


class Metric(StrictModel):
    sources: list[Source] = Field(min_length=1, max_length=16)
    format: Format = Field(default_factory=Format)


class Filter(StrictModel):
    id: Key
    label: Text
    query: Key
    field: FieldName
    default: Scalar = None


class Column(StrictModel):
    field: FieldName
    label: Text
    format: Format = Field(default_factory=Format)
    series: Literal["bar", "line"] | None = None
    axis: Literal["primary", "secondary"] = "primary"


class ChartStyle(StrictModel):
    orientation: Literal["vertical", "horizontal"] = "vertical"
    stacked: bool = False
    legend: Literal["auto", "top", "bottom", "right", "hidden"] = "auto"
    labels: bool = False
    smooth: bool = False
    show_points: bool = True
    grid: Literal["solid", "dashed", "none"] | None = None
    palette: list[HEX] = Field(default_factory=list, max_length=20)
    inner_radius: int | None = Field(default=None, ge=0, le=85)
    outer_radius: int = Field(default=70, ge=20, le=95)


class WaterfallStyle(StrictModel):
    role_field: FieldName | None = None
    opening: Text = "期初"
    closing: Text = "期末"
    change: Text | None = None
    total_color: HEX = "#6B7280"
    labels: bool = True

    @model_validator(mode="after")
    def validate_roles(self):
        roles = (self.opening, self.closing) + ((self.change,) if self.change is not None else ())
        if len(set(roles)) != len(roles):
            raise ValueError("瀑布图的期初、变动及期末标记必须区分")
        if (self.role_field is None) != (self.change is None):
            raise ValueError("瀑布图 role_field 与 change 必须一起配置")
        return self


class Card(StrictModel):
    title: Text
    type: Literal["kpi", "table", "bar", "line", "area", "pie", "donut", "combo", "waterfall"]
    metric: Key | None = None
    query: Key | None = None
    by: dict[Key, FieldName] = Field(default_factory=dict, max_length=8)
    columns: list[Column] = Field(default_factory=list, max_length=12)
    limit: int = Field(default=50, ge=1, le=200)
    span: int | None = Field(default=None, ge=1, le=12)
    height: int | None = Field(default=None, ge=160, le=960)
    chart: ChartStyle | None = None
    waterfall: WaterfallStyle | None = None

    @model_validator(mode="after")
    def validate_card(self):
        if self.waterfall is not None and self.type != "waterfall":
            raise ValueError("waterfall 配置只适用于瀑布图")
        if self.type == "kpi":
            if not self.metric or self.query or self.columns or self.by:
                raise ValueError("指标卡只引用一个 metric")
        elif self.metric or not self.query or not self.columns:
            raise ValueError("图表、表格须声明 query 和 columns")
        if self.type not in CHART_TYPES:
            if self.chart is not None or any(c.series or c.axis != "primary" for c in self.columns):
                raise ValueError("chart、series 与 axis 只适用于图表卡片")
            return self
        radial = self.type in RADIAL_TYPES
        x, *series = self.columns
        if (not series or x.format.kind != "text" or x.series or x.axis != "primary"
                or any(c.format.kind == "text" for c in series) or (radial and len(series) != 1)):
            raise ValueError("图表首列为类别，其余为数值；饼图和环图只允许一列数值")
        if self.type == "waterfall":
            if len(series) != 1 or series[0].format.kind not in ("number", "rate"):
                raise ValueError("瀑布图只接受一列已核实的同口径数值，如期初、期末和增减额")
            if self.chart is not None:
                raise ValueError("瀑布图用 waterfall 配置总计颜色、标签与事实角色")
            self.waterfall = self.waterfall or WaterfallStyle()
        if self.type == "combo":
            if {c.series for c in series} != {"bar", "line"}:
                raise ValueError("组合图须分别指定至少一列柱形和一列折线")
        elif any(c.series or c.axis != "primary" for c in series):
            raise ValueError("单列 series 与次轴仅用于柱线组合图")
        unit_by_axis = {}
        for c in series:
            signature = (c.format.scale != "number", c.format.divisor, c.format.unit)
            if unit_by_axis.setdefault(c.axis, signature) != signature:
                raise ValueError("同一坐标轴的度量必须使用相同展示单位")
        if "primary" not in unit_by_axis:
            raise ValueError("图表必须至少绑定一个主轴序列")
        if self.chart:
            if (self.chart.stacked or self.chart.orientation == "horizontal") and self.type != "bar":
                raise ValueError("横向和堆叠选项仅用于柱形图")
            if self.chart.smooth and self.type not in ("line", "area", "combo"):
                raise ValueError("平滑曲线仅用于含折线的图表")
            if self.chart.inner_radius is not None and not radial:
                raise ValueError("内环半径仅用于饼图与环图")
            if radial and self.chart.outer_radius <= (45 if self.chart.inner_radius is None else self.chart.inner_radius):
                raise ValueError("外环半径必须大于内环半径")
        return self


class Section(StrictModel):
    title: Text
    narrative: str = Field(default="", max_length=1800)
    cards: list[Card] = Field(default_factory=list, max_length=18)


class ReportDefinition(StrictModel):
    version: Literal[1] = 1
    title: Text
    # 快照 ID 属于某次运行，模板只保存查询计划和稳定引用名。
    queries: dict[Key, QueryPlan] = Field(default_factory=dict, max_length=16)
    filters: list[Filter] = Field(default_factory=list, max_length=8)
    metrics: dict[Key, Metric] = Field(default_factory=dict, max_length=100)
    rule_sources: list[Text] = Field(default_factory=lambda: ["内置展示规则"], min_length=1, max_length=30)
    rate_symbols: Literal["semantic", "percent-only"] = "semantic"
    colors: dict[State, HEX] = Field(default_factory=dict)
    theme: dict[str, Any] = Field(default_factory=dict, max_length=100)

    def validate_bindings(self, used_metrics: set[str], *, allow_text: bool = False):
        self.theme = resolve_report_theme(self.theme)
        filters = {f.id for f in self.filters}
        if len(filters) != len(self.filters):
            raise ValueError("筛选器 ID 重复")
        for key, metric in self.metrics.items():
            variants = {frozenset(s.by) for s in metric.sources}
            if ((not allow_text and metric.format.kind == "text") or len(variants) != len(metric.sources)
                    or any(v - filters for v in variants)):
                raise ValueError(f"指标 {key} 包含重复、未知筛选映射或不受支持的文本格式")
        aliases = set()
        for _, card in self.card_outputs():
            if set(card.by) - filters:
                raise ValueError(f"卡片「{card.title}」含未知筛选器")
            aliases.add(card.query)
        if used_metrics - self.metrics.keys():
            raise ValueError(f"页面引用未定义指标 {sorted(used_metrics - self.metrics.keys())}")
        if self.metrics.keys() - used_metrics:
            raise ValueError(f"指标未用于任何展示 {sorted(self.metrics.keys() - used_metrics)}")
        aliases |= {s.query for key in used_metrics for s in self.metrics[key].sources}
        if not aliases:
            raise ValueError("至少需要一项绑定快照的定量展示")
        aliases |= {f.query for f in self.filters}
        if self.queries and aliases != set(self.queries):
            raise ValueError("查询计划名称须与页面实际引用完全一致")


class ReportSpec(ReportDefinition):
    sections: list[Section] = Field(min_length=1, max_length=12)

    def card_outputs(self):
        for index, section in enumerate(self.sections, 1):
            for number, card in enumerate(section.cards):
                if card.query is not None:
                    yield f"card-{index}-{number}", card

    @model_validator(mode="after")
    def validate_layout(self):
        used_metrics = set()
        for section in self.sections:
            plain = TOKEN.sub("", section.narrative)
            if "{{" in plain or "}}" in plain or BARE_NUMBER.search(plain):
                raise ValueError(f"章节「{section.title}」的定量正文须通过 {{{{metric}}}} 绑定")
            used_metrics.update(match[1] for match in TOKEN.finditer(section.narrative))
            used_metrics.update(card.metric for card in section.cards if card.metric is not None)
        self.validate_bindings(used_metrics)
        return self


def encode_template(spec: ReportDefinition, dataset_id: int) -> str:
    # 版本固定完整模板和场景，两种模板均沿用已有的模板版本存储。
    key = "smartreportgen_spec" if isinstance(spec, ReportSpec) else "smartreportgen_free_html"
    return json.dumps({key: spec.model_dump(mode="json"), "dataset_id": dataset_id},
                      ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def decode_template(markdown: str) -> tuple[ReportDefinition, int] | None:
    try:
        data = json.loads(markdown)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    if {"smartreportgen_spec", "smartreportgen_free_html"} <= data.keys():
        raise ValueError("报告模板只能保存一种定义")
    if "smartreportgen_free_html" in data:
        from .report_free import FreeReport

        model, key = FreeReport, "smartreportgen_free_html"
    elif "smartreportgen_spec" in data:
        model, key = ReportSpec, "smartreportgen_spec"
    else:
        return None
    if type(data.get("dataset_id")) is not int or data["dataset_id"] < 1:
        raise ValueError("声明式模板必须锁定业务场景")
    return model.model_validate(data[key]), data["dataset_id"]


def validate_scopes(scopes: list[dict], principal) -> None:
    """交付前验证当前表授权以及当时的场景行范围，无法证明包含时拒绝交付。"""
    from . import repo

    acc = access_for(principal)
    datasets, sources = {}, {}
    for recorded in scopes:
        ds_id = recorded["dataset_id"]
        if not principal.registered or not all(acc.can_table(ds_id, t) for t in recorded["tables"]):
            raise PermissionDenied("当前账号无权访问该报告版本包含的数据表")
        try:
            if ds_id not in datasets:
                datasets[ds_id] = repo.get_dataset(ds_id)
            dataset = datasets[ds_id]
            ds_source = dataset.get("datasource_id")
            if ds_source != recorded["source_id"] or not dataset["enabled"]:
                raise PermissionDenied("报告来源已变化，请按当前数据权限重新取数")
            if ds_source not in sources:
                sources[ds_source] = repo.get_datasource(ds_source)
            source = sources[ds_source]
            if not source["enabled"] or scope_for(ds_id, dataset, source, recorded["tables"]) != recorded:
                raise PermissionDenied("报告数据源或行范围已变化，请重新取数")
        except repo.NotFound as exc:
            raise PermissionDenied("报告来源已删除，无法核实该版本的数据权限") from exc


def _get_fields(spec: ReportDefinition) -> dict[str, set[str]]:
    required: dict[str, set[str]] = {}
    for item in spec.filters:
        required.setdefault(item.query, set()).add(item.field)
    for metric in spec.metrics.values():
        for source in metric.sources:
            required.setdefault(source.query, set()).update({source.field, *source.by.values(), *source.where})
    for _, card in spec.card_outputs():
        selection = required.setdefault(card.query, set())
        selection.update({*card.by.values(), *(c.field for c in card.columns)})
        if card.waterfall and card.waterfall.role_field:
            selection.add(card.waterfall.role_field)
    return required


def _project(spec: ReportDefinition, snapshots: dict[str, dict], dataset_id: int) -> tuple[dict, list[dict]]:
    fields = _get_fields(spec)
    if set(fields) != set(snapshots):
        raise ValueError("快照别名须与页面引用完全一致")
    origins = {key: value.get("origin", {}) for key, value in snapshots.items()}
    scopes = {json.dumps(origin["scope"], sort_keys=True): origin["scope"] for origin in origins.values()
              if origin.get("scope")}
    if not all(origin.get("scope", {}).get("dataset_id") == dataset_id for origin in origins.values()):
        raise ValueError("快照缺少当前场景的来源证据或接口完整合同")
    if len(origins) > 1:
        batches = {origin.get("batch") for origin in origins.values()}
        if len(batches) != 1 or None in batches:
            raise ValueError("多个快照必须来自同一次一致性取数计划")
    for binding, fmt in [(source, metric.format) for metric in spec.metrics.values() for source in metric.sources] + [
        (Source(query=c.query, field=column.field), column.format)
        for _, c in spec.card_outputs() for column in c.columns
    ]:
        official = origins[binding.query].get("scales", {}).get(binding.field)
        if official is not None and (fmt.scale != official or fmt.kind == "text"):
            raise ValueError(f"字段 {binding.field} 的展示尺度与官方指标配置不一致")
    output = {}
    for name, selection in fields.items():
        payload = snapshots[name]
        cols = payload["columns"]
        missing = selection - set(cols)
        if missing or any(cols.count(x) != 1 for x in selection):
            raise ValueError(f"快照 {name} 的字段缺失或不唯一 {sorted(missing)}")
        order = sorted(selection)
        indexes = [cols.index(c) for c in order]
        # 只有展示和筛选实际使用的字段会出现在离线报告中。
        output[name] = {"columns": order, "rows": [[row[i] for i in indexes] for row in payload["rows"]]}
    return output, list(scopes.values())


def _script_json(data) -> str:
    return json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).replace("<", "\\u003c")


@cache
def audit_module():
    path = report_document.skill_dir() / "assets" / "audit_report.py"
    spec = spec_from_file_location("srg_report_audit", path)
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def compile_report(spec: ReportDefinition, snapshots: dict[str, dict], dataset_id: int, theme_values: dict) -> tuple[str, str, list[dict]]:
    data, scopes = _project(spec, snapshots, dataset_id)
    theme = design.full({**theme_values, **spec.theme})
    colors = {"up": theme["color-trend-up"], "down": theme["color-trend-down"],
              "met": theme["color-status-positive"], "miss": theme["color-status-negative"],
              "warning": theme["color-status-warning"], "neutral": theme["color-text-secondary"], **spec.colors}
    rules = {"version": 2, "colors": colors, "rule_sources": spec.rule_sources, "rate_symbols": spec.rate_symbols}
    controls = _controls(spec, theme)
    if isinstance(spec, ReportSpec):
        body, layout_css = _structured_layout(spec, theme, controls), ""
    else:
        body, layout_css, _ = spec.render_layout(controls)
    return _document(spec, data, scopes, theme, rules, body, layout_css)


def _controls(spec, theme):
    filters = "".join(f'<label>{escape(f.label)} <select id="filter-{f.id}"></select></label>' for f in spec.filters)
    sticky = 'rf-filter-sticky' if theme["filter_sticky"] else ''
    return (f'<div class="srg-controls {sticky}" id="srg-controls">{filters}'
            '<button id="srg-reset" type="button">重置</button></div>' if filters else "")


def _structured_layout(spec, theme, controls):
    # 结构化模板继续使用现有标题、目录和卡片网格。
    sections, toc = [], []
    for index, section in enumerate(spec.sections, 1):
        title = escape(section.title)
        pieces, start = [], 0
        for match in TOKEN.finditer(section.narrative):
            mode = ' data-metric-state="true"' if match[2] else ''
            pieces.extend((escape(section.narrative[start:match.start()]),
                           f'<strong class="rf-metric" data-metric="{match[1]}"{mode}>—</strong>'))
            start = match.end()
        pieces.append(escape(section.narrative[start:]))
        intro = f'<p class="rf-summary">{"".join(pieces)}</p>' if section.narrative else ''
        cards = []
        for number, card in enumerate(section.cards):
            metric = f' data-metric="{card.metric}"' if card.metric else ""
            span = card.span * 5 if card.span is not None else (20 if card.metric else 30)
            height = f";--srg-card-height:{card.height}px" if card.height else ""
            cards.append(f'<article class="rf-card rf-type-{card.type}" style="--span:{span}{height}">'
                         f'<h3 class="rf-card-title">{escape(card.title)}</h3>'
                         f'<div class="srg-card-output{" rf-metric" if card.metric else ""}" id="card-{index}-{number}"{metric}>—</div></article>')
        sections.append(f'<section class="rf-section" id="sec-{index}"><h2 class="rf-section-title">{title}</h2>{intro}'
                        f'<div class="rf-grid">{"".join(cards)}</div></section>')
        toc.append(f'<a class="rf-toc-parent" href="#sec-{index}">{title}</a>')
    sticky = theme["filter_sticky"]
    return f"""<div class="rf-wrap rf-layout{' rf-has-toc' if theme['toc'] else ''}">
<header class="rf-header rf-header-{theme['header_style']}"><h1 class="rf-title">{escape(spec.title)}</h1>{controls if not sticky else ''}</header>
{f'<nav class="rf-toc" aria-label="报告目录"><div class="rf-toc-card">{"".join(toc)}</div></nav>' if theme['toc'] else ''}
<main class="rf-main">{controls if sticky else ''}{"".join(sections)}</main></div>"""


def _document(spec, data, scopes, theme, rules, body, layout_css):
    # 离线文件只执行内置脚本，业务数据通过 JSON 进入运行时。
    css = report_data._escape_style_text(design.report_css(theme))
    runtime = ASSET.read_text(encoding="utf-8")
    outputs = list(spec.card_outputs())
    charts = any(card.type in CHART_TYPES for _, card in outputs)
    vendor = '<script data-srg-vendor="echarts"></script>' if charts else ''
    style = """<style>
    .srg-controls{display:flex;flex-wrap:wrap;gap:1rem;align-items:center;margin-top:12px}
    .srg-controls label{display:flex;align-items:center;flex-wrap:wrap;gap:.4rem;max-width:100%;min-width:0}
    .srg-controls :is(select,button){max-width:100%;min-height:max(36px,var(--rre-control-height));padding:5px 10px;border:1px solid var(--color-border-card);border-radius:var(--rre-radius-control);background:var(--color-bg-card);color:var(--color-text-primary);font:inherit}
    .srg-controls select{min-width:80px;text-align:var(--rf-filter-align)}
    .srg-controls :is(select,button):focus-visible{outline:2px solid var(--color-brand);outline-offset:2px}
    .srg-controls.rf-filter-sticky{margin-top:0}
    .srg-card-output{min-width:0;margin-top:10px}
    .srg-card-output.rf-metric{font-size:var(--rre-card-font-hero-size);font-weight:var(--rre-card-font-hero-weight);text-align:var(--rf-kpi-align);min-height:var(--srg-card-height,0);white-space:normal;overflow-wrap:anywhere}
    .srg-chart{height:var(--srg-card-height,280px);min-width:0}
    .srg-table{overflow:auto;max-height:var(--srg-card-height,none)}
    .srg-card-output [data-empty]{color:var(--srg-neutral)}
    .srg-free{min-width:0;overflow-wrap:anywhere}
    .srg-badge{display:inline-block;padding:.15em .45em;border:1px solid currentColor;border-radius:.5em}
    </style>"""
    block = '<script type="application/json" id="srg-data" data-srg-snapshot="compiled"></script>'
    author_style = f"<style>{layout_css}</style>" if layout_css else ""
    doc = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:">
<title>{escape(spec.title)}</title><style data-srg-style="embedded">{css}</style>{style}{author_style}</head>
<body class="{'rf-zebra' if theme['table_zebra'] else ''}">{body}
<script type="application/json" id="srg-display-contract">{_script_json(rules)}</script>
<script id="srg-formatters" data-srg-asset="report-formatters.js"></script>{vendor}
{block}<script>{runtime}</script></body></html>"""
    # 先审计源文档，再审计最终离线字节；运行时白名单只接受服务端固定文件的摘要。
    runtime_hash = sha256(runtime.strip().encode()).hexdigest()
    audit = audit_module()
    errors = audit.audit_html(doc, runtime_hash=runtime_hash)
    view = {
        "filters": [item.model_dump(mode="json") for item in spec.filters],
        "metrics": {key: item.model_dump(mode="json") for key, item in spec.metrics.items()},
        "outputs": {key: card.model_dump(mode="json", exclude={"option"}) for key, card in outputs},
    }
    for key, card in outputs:
        if option := getattr(card, "option", None):
            view["outputs"][key]["option"] = option.model_dump(mode="json", exclude_none=True)
    # 画布读不到非 CSS 选项，发送已合并的图表配置和分类色板。
    view["chart_defaults"] = {key: theme[key] for key in ("chart_grid", "chart-palette-categorical")}
    payload = _script_json({"spec": view, "data": data})
    if len(payload.encode()) > report_data.MAX_SNAPSHOT_BYTES:
        raise ValueError("报告实际展示数据超过 32 MiB，请缩小范围或使用官方聚合")
    page = report_data.embed(doc.replace(block, block.replace('="compiled"></script>', f'="embedded">{payload}</script>', 1)), "")
    errors.extend(audit.audit_html(page, expanded=True, runtime_hash=runtime_hash))
    if error := report_document.report_document_error(page):
        errors.append(error)
    if errors:
        raise ValueError("报告展示门禁失败：\n" + "\n".join(errors))
    return doc, page, scopes
