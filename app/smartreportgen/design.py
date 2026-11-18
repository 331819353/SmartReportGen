"""样式令牌。令牌键名即 CSS 变量名（去掉前缀 --）。

- 内置样式（BUILTIN_TOKENS）取自经营报告参考样式，覆盖配色、排版、页面布局与控件。
- 展示取值：智能体在模板 theme 中配置了的项按其配置；未配置的项按 业务场景规则 ≥ 通用规则 ≥ 内置样式。
  自定义 CSS 按需提供，内置样式只使用结构化令牌和通用报告组件。
样式项按报告页面的组成部分分为 12 类（TOKEN_GROUPS）。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

COLOR_TOKENS = {
    "color-brand": "#3B82F6",              # 选中态、结论强调线
    "color-bg-page": "#F5F7FA",            # 报告页背景
    "color-heading": "#1E3A8A",            # 深蓝章节标题、主数值与表头
    "color-text-primary": "#1F2937",       # 正文与表格读数
    "color-text-secondary": "#64748B",     # 单位、标签、坐标轴、注释
    "color-text-inverse": "#FFFFFF",       # 深色底上的文字
    "color-bg-card": "#FFFFFF",            # 卡片画布
    "color-bg-summary": "#F8FAFC",         # 结论框和辅助读数的浅灰底
    "color-border-card": "#E5E7EB",        # 卡片边框、分隔线
    "color-status-positive": "#10B981",    # 达标、向好
    "color-status-warning": "#F59E0B",     # 临近、关注
    "color-status-negative": "#EF4444",    # 未达、承压
    "color-trend-up": "#047857",           # 浅绿底上的涨跌标签
    "color-trend-down": "#B91C1C",         # 浅红底上的涨跌标签
}
AUTO_COLOR_TOKENS = {  # 支持清空，便于覆盖默认配色
    "color-header-start": "#0B2641",
    "color-header-middle": "#174A7C",      # 留空时使用两段端点色
    "color-header-end": "#0F766E",
    "color-table-head-bg": "#F6F9FD",
}
PALETTE_TOKENS = {
    "chart-palette-categorical": ["#2F75B5", "#F28E2B", "#1B9E77", "#7F7F7F", "#3B82F6", "#14B8A6", "#CBD5E1"],
    "chart-palette-sequential": ["#EFF6FF", "#DBEAFE", "#BFDBFE", "#93C5FD", "#3B82F6", "#1E3A8A"],
    "chart-palette-diverging": ["#D73027", "#F8FAFC", "#1B9E77"],
}
FONT_FAMILY = "'Microsoft YaHei UI', 'Microsoft YaHei', 'PingFang SC', Arial, sans-serif"
FONT_TOKENS = {  # 字号按设备固定，最小 12px
    "rre-page-font-title": {"size": 25, "weight": 700},   # 报告大标题
    "rre-page-font-section": {"size": 16, "weight": 800}, # 章节标题
    "rre-card-font-title": {"size": 14, "weight": 700},   # 图表标题、子结论标题
    "rre-card-font-unit": {"size": 12, "weight": 700},    # 标题右侧单位标签
    "rre-card-font-hero": {"size": 19, "weight": 800},    # 指标卡、仪表盘、子弹图主值
    "rre-card-font-affix": {"size": 12, "weight": 600},   # 紧跟主值的 %、万元
    "rre-card-font-read": {"size": 12, "weight": 700},    # 辅助指标值、环心值、合计
    "rre-card-font-label": {"size": 12, "weight": 400},   # 辅助指标名、坐标轴、图例、表头
    "rre-card-font-table": {"size": 13, "weight": 400},   # 单元格、榜单、漏斗阶段名
    "rre-card-font-prose": {"size": 14, "weight": 400},   # 正文
}
SIZE_TOKENS = {  # px；不随卡片宽度变化，只有图形区随卡片伸缩
    "rre-page-pad": 14, "rre-section-gap": 14, "rre-section-pad": 16, "rre-toc-width": 188, "rre-toc-gap": 16,
    "rre-header-pad": 24, "rre-control-height": 36,
    "rre-card-pad": 14, "rre-card-gap": 12, "rre-card-border-width": 1, "rre-card-title-line": 20, "rre-card-head-gap": 8,
    "rre-kpi-pad": 10, "rre-kpi-gap": 8, "rre-kpi-min-height": 96,
    "rre-card-fact-line": 18, "rre-card-fact-gap": 3, "rre-status-bar": 2, "rre-card-row-height": 34,
    "rre-card-legend-gap": 16, "rre-card-axis-gap": 8, "rre-card-axis-title-gap": 4, "rre-card-bar-max": 24,
    "radius-card": 8, "rre-radius-panel": 16, "rre-radius-section": 10, "rre-radius-tile": 999, "rre-radius-chip": 999,
    "rre-radius-control": 999, "rre-radius-pill": 999, "rre-data-radius": 2,
}
NUMBER_TOKENS = {  # 小数：(默认, 最小, 最大)
    "rre-prose-line-height": (1.55, 1.2, 2.4),
    "rre-chart-line-width": (2.4, 1.0, 6.0),
}
PERCENT_TOKENS = {  # 0~40%：派生色的混合比例
    "rre-summary-tint": 0,                 # 结论框底色中主题色的比例
    "rre-summary-border-tint": 0,          # 结论框边框中主题色的比例
}
OTHER_TOKENS = {
    "font-family": FONT_FAMILY,
    "shadow-card": "none",
    "shadow-section": "0 4px 18px rgba(15,23,42,.06)",
    "shadow-header": "0 12px 28px rgba(11,38,65,.18)",
}
LAYOUT_OPTIONS = {  # 非 CSS 变量的页面选项
    "header_style": "banner",   # banner：主题色横幅 + 反白文字；plain：白色卡片
    "toc": True,
    "max_width": 1036,         # 封面与正文宽度；加上目录 188px 和列间距 16px 后为 1240px
    "filter_sticky": False,     # 默认筛选位于封面；开启后移到封面下方并吸顶
    "chart_grid": "solid",      # 图表网格线：dashed / solid / none
    "table_zebra": True,        # 表格斑马纹
    # 对齐（水平）；控件、按钮、标签内的文字始终垂直居中
    "header_align": "left",     # 报告头部文字
    "filter_align": "left",     # 筛选控件中的显示值
    "section_align": "left",    # 章节标题与本节结论
    "card_title_align": "left", # 卡片标题
    "kpi_align": "left",        # 指标卡主数值与辅助数值
    "table_text_align": "left", # 表格文字列
    "table_num_align": "right", # 表格数值列
}

# 内置视觉样式直接参与优先级合并，管理台和 MCP 都能看到来源。
BUILTIN_TOKENS: dict[str, Any] = {
    **COLOR_TOKENS, **AUTO_COLOR_TOKENS, **PALETTE_TOKENS, **FONT_TOKENS, **SIZE_TOKENS,
    **{k: v[0] for k, v in NUMBER_TOKENS.items()}, **PERCENT_TOKENS, **OTHER_TOKENS, **LAYOUT_OPTIONS,
}
DEFAULT_TOKENS = BUILTIN_TOKENS  # 兼容旧名称
KNOWN_KEYS = set(BUILTIN_TOKENS) | {"custom_css"}

# 旧版主题键 → 令牌
LEGACY_ALIASES = {
    "primary": "color-brand", "accent": "color-brand", "palette": "chart-palette-categorical",
    "background": "color-bg-page", "surface": "color-bg-card", "text": "color-text-primary",
    "muted": "color-text-secondary", "border": "color-border-card", "up_color": "color-trend-up",
    "down_color": "color-trend-down", "font_family": "font-family", "radius": "radius-card", "gap": "rre-card-gap",
    "shadow": "shadow-card", "header_text": "color-text-inverse",
}
CHOICES = {"header_style": ("banner", "plain"), "chart_grid": ("dashed", "solid", "none"),
           "header_align": ("left", "center"), "filter_align": ("left", "center"), "section_align": ("left", "center"),
           "card_title_align": ("left", "center"), "kpi_align": ("left", "center"),
           "table_text_align": ("left", "center"), "table_num_align": ("right", "center")}
BOOLS = ("toc", "filter_sticky", "table_zebra")

_SAFE = re.compile(r"^[#\w\s,.'\"()%+\-/]*$")
_COLOR = re.compile(r"^(#[0-9a-fA-F]{3,8}|rgba?\([\d\s.,%]+\)|hsla?\([\d\s.,%deg]+\)|[a-zA-Z]+)$")


def canonical_key(k: Any) -> str:
    k = str(k).lstrip("-")
    return LEGACY_ALIASES.get(k, k)


def resolve_overrides(layer: dict | None) -> tuple[dict, list[str]]:
    """只返回该层显式指定且有效的取值，避免非法覆盖退回内置值并盖掉上一级。"""
    out: dict[str, Any] = {}
    warns: list[str] = []
    for raw_key, v in (layer or {}).items():
        k = canonical_key(raw_key)
        if k in ("header_bg", "summary_bg", "number_font", "chart_label_size"):
            continue  # 旧版选项，已由令牌体系取代
        if k not in KNOWN_KEYS:
            warns.append(f"theme.{k} 不是已知样式项，已忽略")
            continue
        ok, val, msg = _validate(k, v)
        if ok:
            out[k] = val
        else:
            warns.append(f"theme.{k}：{msg}，已忽略")
    return out, warns


def resolve(*layers: dict | None) -> tuple[dict, list[str]]:
    """从内置样式开始依次叠加各层的有效取值。"""
    out = dict(BUILTIN_TOKENS)
    warns = []
    for layer in layers:
        values, notes = resolve_overrides(layer)
        out.update(values)
        warns.extend(notes)
    return out, warns


def _validate(k: str, v: Any) -> tuple[bool, Any, str]:
    if k == "custom_css":
        return True, str(v).replace("</", "<\\/"), ""
    if k in PALETTE_TOKENS:
        if isinstance(v, list) and len(v) >= 2 and all(isinstance(c, str) and _COLOR.match(c.strip()) for c in v):
            return True, [c.strip() for c in v], ""
        return False, None, "必须是至少 2 个颜色的数组"
    if k in FONT_TOKENS:
        if isinstance(v, str):  # 允许 "16px 600"
            m = re.match(r"^\s*(\d+)(?:px)?\s*[·/ ]\s*(\d{3})\s*$", v)
            if not m:
                return False, None, "格式应为 {size, weight} 或 '16px 600'"
            v = {"size": int(m.group(1)), "weight": int(m.group(2))}
        if not isinstance(v, dict) or "size" not in v:
            return False, None, "格式应为 {size, weight}"
        size, weight = int(v["size"]), int(v.get("weight", 400))
        if size < 12 or size > 72:
            return False, None, "字号需在 12~72px 之间"
        if weight not in (300, 400, 500, 600, 700, 800):
            return False, None, "字重只能是 300~800 的整百"
        return True, {"size": size, "weight": weight}, ""
    if k in SIZE_TOKENS or k == "max_width":
        try:
            n = int(float(str(v).replace("px", "")))
        except ValueError:
            return False, None, "必须是像素整数"
        return (True, n, "") if 0 <= n <= 4000 else (False, None, "超出合理范围")
    if k in NUMBER_TOKENS:
        _, lo, hi = NUMBER_TOKENS[k]
        try:
            n = round(float(v), 2)
        except (TypeError, ValueError):
            return False, None, "必须是数字"
        return (True, n, "") if lo <= n <= hi else (False, None, f"需在 {lo}~{hi} 之间")
    if k in PERCENT_TOKENS:
        try:
            n = int(float(str(v).replace("%", "")))
        except ValueError:
            return False, None, "必须是百分比整数"
        return (True, n, "") if 0 <= n <= 40 else (False, None, "需在 0~40% 之间")
    if k in BOOLS:
        return True, bool(v), ""
    if k in CHOICES:
        return (True, v, "") if v in CHOICES[k] else (False, None, f"可选 {'/'.join(CHOICES[k])}")
    if k in COLOR_TOKENS or k in AUTO_COLOR_TOKENS:
        v = str(v or "").strip()
        if not v and k in AUTO_COLOR_TOKENS:
            return True, "", ""
        return (True, v, "") if _COLOR.match(v) else (False, None, "不是合法颜色")
    if isinstance(v, str) and _SAFE.match(v):
        return True, v, ""
    return False, None, "包含不安全字符"


def full(values: dict) -> dict:
    """渲染用的完整取值，补全尚未设置的样式和可选 CSS。"""
    return {**BUILTIN_TOKENS, "custom_css": "", **values}


_JUSTIFY = {"left": "flex-start", "center": "center", "right": "flex-end"}


def css_vars(t: dict) -> str:
    t = full(t)
    parts = []
    for k in COLOR_TOKENS:
        parts.append(f"--{k}:{t[k]};")
    for k in PALETTE_TOKENS:
        for i, c in enumerate(t[k], 1):
            parts.append(f"--{k}-{i}:{c};")
    for k in FONT_TOKENS:
        parts.append(f"--{k}-size:{t[k]['size']}px;--{k}-weight:{t[k]['weight']};")
    for k in SIZE_TOKENS:
        parts.append(f"--{k}:{t[k]}px;")
    for k in NUMBER_TOKENS:
        parts.append(f"--{k}:{t[k]};")
    for k in PERCENT_TOKENS:
        parts.append(f"--{k}:{t[k]}%;")
    # 自动色：留空时由主题色派生
    hs = t["color-header-start"] or "var(--color-brand)"
    hm = t["color-header-middle"]
    he = t["color-header-end"] or "color-mix(in srgb,var(--color-brand) 62%,var(--color-text-primary))"
    middle_stop = f"{hm} 64%," if hm else ""
    parts.append(f"--rf-header-bg:linear-gradient(120deg,{hs} 0%,{middle_stop}{he} 100%);")
    parts.append(f"--rf-table-head-bg:{t['color-table-head-bg'] or 'var(--color-bg-card)'};")
    parts.append(f"--font-family:{t['font-family']};--shadow-card:{t['shadow-card']};"
                 f"--shadow-section:{t['shadow-section']};--shadow-header:{t['shadow-header']};"
                 f"--rf-max:{int(t['max_width'])}px;")
    for k in ("header_align", "filter_align", "section_align", "card_title_align", "kpi_align", "table_text_align",
              "table_num_align"):
        name = k.replace("_align", "").replace("_", "-")
        parts.append(f"--rf-{name}-align:{t[k]};--rf-{name}-justify:{_JUSTIFY[t[k]]};")
    centered = lambda k: t[k] == "center"
    parts.append(f"--rf-header-goal-margin:{'14px auto 0' if centered('header_align') else '14px 0 0'};"
                 f"--rf-card-title-spacer:{0 if centered('card_title_align') else 1};"
                 f"--rf-kpi-fact-justify:{'center' if centered('kpi_align') else 'space-between'};")
    return ":root{" + "".join(parts) + "}"


def runtime_tokens(t: dict) -> dict:
    """传给浏览器端运行时（ECharts 配置）使用的令牌（已含兜底值）。"""
    t = full(t)
    return {k: t[k] for k in KNOWN_KEYS if k != "custom_css"}


def toc_min_width(t: dict) -> int:
    """宽屏使用双列；目录或页边距调大时，等正文仍有可读宽度再切换。"""
    min_report_width = min(t["max_width"], 640)
    columns_width = min_report_width + t["rre-toc-width"] + t["rre-toc-gap"]
    return max(981, columns_width + 2 * t["rre-page-pad"])


def report_css(theme: dict) -> str:
    """为服务端渲染和 Agent 自编 HTML 生成同一份内联样式。"""
    theme = full(theme)
    path = Path(__file__).with_name("static") / "report" / "report.css"
    # 容器查询无法读取 CSS 变量，按合并后的主题计算目录断点。
    css = path.read_text(encoding="utf-8").replace("__RF_TOC_MIN_WIDTH__", f"{toc_min_width(theme)}px")
    return "\n".join((css_vars(theme), css, theme["custom_css"]))


# ============================================================ 业务人员可读的元数据（样式规则编辑器使用）
PRECEDENCE = ("智能体决定 ≥ 业务场景规则 ≥ 通用规则 ≥ 内置样式：后三者合并为一份“指定样式”（视觉样式与展示要求），"
              "同一项以优先级高者为准，作为约束交给智能体；智能体可以按自己的判断调整，没有指定的由智能体自由决定")
# 内置版式集中维护在样式层，样式编辑器和 Agent 读取相同的说明。
BUILTIN_PAGE_LAYOUT = ("宽屏下目录与报告两列整体居中，目录随滚动保持可见，封面和正文在右侧同列且等宽。"
                       "空间不足时，目录放在封面下方、正文之前，长目录在卡片内滚动。")
BUILTIN_CARD_LAYOUT = "同一行的卡片等高并铺满可用宽度，单张卡片占满所在行。"
BUILTIN_CARD_STYLE = ("同一报告内同类卡片跨章节共用组件样式。表格统一表头、行高、隔行底色和列对齐。"
                      "同类图表统一图形、坐标轴、网格、图例、标签和配色规则。")

# 事实与交付规则独立于视觉继承链，不能被场景配色、展示条目或 Agent 的样式调整覆盖。
# 仅提炼附件的通用要求；固定组织、账期和历史验证声明留在按需读取的场景参考中。
BUILTIN_REPORTING_RULES = (
    ("direct_facts", "业务指标直取",
     "实际、目标、同期、完成率、同比、环比、差额、贡献、总计、小计、整体比率和预测，"
     "必须来自已确认事实表或正式视图的直接字段或指标行。SQL、Python、前端、图表和 LLM 均不得补算；"
     "不能用 SUM、AVG、业务算术或自建计算视图代替正式指标。"),
    ("missing_facts", "缺失与异常",
     "缺失显示 — 或暂无数据，区分空字段、无记录、无权限、字段待确认和重复记录。真实零和负号保留；"
     "不得空值转零、取绝对值、改符号或以旧批次补缺。重复事实须核实业务唯一键与版本，不能随意取首条或求和消重。"),
    ("scope", "粒度与覆盖",
     "先确认组织、期间、MTD/YTD、区域、指标及粒度。总计与明细、父子节点分别直取，不能相加；"
     "不能累计 MTD 充当 YTD，不能混用总体、国内、海外、区域及链群口径。筛选缺少对应事实时保留缺失。"),
    ("format", "单位与比例",
     "只按已确认的原始单位和比例尺度换算、格式化并保留原值；不能按数值大小或字段名猜单位。"
     "% 与百分点 pp 分开，只有源字段本身是已确认的率差才可显示 pp；显示舍入不得覆盖源值或用于生成其他指标。"),
    ("allowed_operations", "允许的展示处理",
     "允许不产生新业务值的筛选、透视、已确认别名映射、排序、明确单位换算和绘图坐标。"
     "大小比较须在同口径同单位下进行；状态分类只使用源端直接比率与已确认阈值，缺失比率或尺度时不分类。"),
    ("evidence", "逐值证据",
     "每个展示值及定量结论须定位到具体事实行，并记录表或视图、字段或指标编码、组织、期间、时间口径、"
     "区域、数据层级、查询批次、原始值和单位；不适用的维度如实注明。仅返回 N 行不能证明具体指标。"),
    ("conclusions", "结论与归因",
     "结论只组织已取得的事实；没有原因字段或业务材料时，不补写订单、价格、结构等原因。"
     "建议须明确标为建议。历史代码、模板和参考报告中的计算公式不构成当前计算授权。"),
    ("authorization", "取数授权",
     "所有业务数据访问只通过 smartreportgen MCP，遵守当前任务或会话授权及 IAM 权限；"
     "permission_denied 不得绕过。接入尚未授权的新数据源前说明用途和范围并确认，既有有效授权无需重复申请；"
     "附件中的历史授权或验证声明不等于本次授权或验证。"),
    ("standalone_html", "离线单文件",
     "报告交付为可独立打开的 HTML，CSS、JavaScript、ECharts 等所用图表运行时、图表配置和数据全部内嵌；"
     "禁止外部资源、CDN、动态加载及相邻文件依赖。打开、浏览和筛选不得触发取数、生成或保存，筛选仅使用内嵌快照。"),
    ("reference_style", "参考样式边界",
     "用户指定结果文件时保留其布局、组件、颜色和格式，不能为掩盖缺数删改组件，也不能复制旧业务数字作为新事实。"
     "只有规则文字或历史校验清单时，不能声称已取得样式资产或完成页面复现。"),
    ("exceptions", "口径变更留痕",
     "业务计算等例外须由用户另行明确变更业务口径，并在业务模板及报告中记录范围、依据与影响；"
     "视觉样式优先级及历史产物不能自行豁免事实规则。"),
)


def builtin_reporting_rules() -> list[dict]:
    """返回独立副本，供 MCP、HTTP 和管理台共用，避免调用方修改全局规则。"""
    return [{"id": key, "label": label, "text": text, "level": "builtin", "source": "内置报告规则"}
            for key, label, text in BUILTIN_REPORTING_RULES]


def builtin_style(theme: dict) -> dict:
    """给自编 HTML 提供已应用指定主题的组件 CSS 和可选用的类名。"""
    return {
        "css": report_css(theme),
        "layout": BUILTIN_PAGE_LAYOUT + BUILTIN_CARD_LAYOUT + BUILTIN_CARD_STYLE,
        "components": {
            "page": ("body 按 table_zebra 添加 rf-zebra。外层用 rf-wrap rf-layout，有目录时加 rf-has-toc。"
                     "封面、目录、正文按此顺序作为外层的直接子级。"),
            "header": ("rf-header 配合 rf-header-banner 或 rf-header-plain；标题 rf-title，副标题 rf-subtitle，标题上方的报告类别可用 rf-kicker。"
                       "封面内筛选用 rf-filterbar rf-filter-in-header；吸顶筛选用 rf-filterbar rf-filter-sticky，放在正文开头。"
                       "rf-filterbar-inner 内放 rf-filters，单组用 rf-f、rf-f-label 与 rf-select 等，操作区用 rf-tools 和 rf-btn。"),
            "toc": "rf-toc > rf-toc-card，含 rf-toc-title 与 rf-toc-group；分组链接用 rf-toc-parent，子链接包在 rf-toc-children。",
            "section": "rf-main 内放 rf-section；用 rf-section-title、rf-summary 和 rf-finding-title 排版章节标题、结论及子标题。",
            "cards": ("rf-grid 内放 rf-card，卡片内用 rf-card-head、rf-card-title 和 rf-chart-body。用 --span 指定占比（60 为整行）。"
                      "图表卡另加 rf-chart。纯指标行另加 rf-grid-kpi，指标卡用 rf-type-kpi 和 rf-hero；辅助数值用 rf-facts > rf-fact。"
                      "窄屏允许两列的指标卡加 rf-narrow。"),
            "table": "rf-table-wrap 内放 rf-table；数值列的表头和单元格用 num，外层表格卡用 rf-card rf-type-table。",
            "metrics": "动态结论用 rf-metric，结论与表格数值可用 data-state=positive 或 negative 着色，持平或无数据时清除状态。",
        },
    }

# 按报告页面的组成部分分类；每个令牌只属于一类
TOKEN_GROUPS: list[dict] = [
    {"key": "theme", "label": "主题", "desc": "整份报告的主色调、页面底色、文字颜色与字体", "tokens": [
        {"key": "color-brand", "label": "主题色", "type": "color", "desc": "选中态、结论强调线与默认图形强调色"},
        {"key": "color-bg-page", "label": "页面底色", "type": "color", "desc": "整个报告页面的背景"},
        {"key": "color-heading", "label": "标题强调色", "type": "color", "desc": "章节、目录、指标主数值和表头"},
        {"key": "color-text-primary", "label": "主文字", "type": "color", "desc": "正文、图表标题、读数"},
        {"key": "color-text-secondary", "label": "次要文字", "type": "color", "desc": "单位、标签、坐标轴、注释"},
        {"key": "color-text-inverse", "label": "深色底上的文字", "type": "color", "desc": "例如头部横幅、选中按钮上的文字"},
        {"key": "font-family", "label": "字体", "type": "text", "desc": "按顺序优先使用"},
    ]},
    {"key": "typography", "label": "排版", "desc": "各处文字的字号与字重；字号固定、最小 12px，放不下时截断而不缩小", "tokens": [
        {"key": "rre-page-font-title", "label": "报告大标题", "type": "font", "sample": "经营分析报告"},
        {"key": "rre-page-font-section", "label": "章节标题", "type": "font", "sample": "一、经营概览"},
        {"key": "rre-card-font-title", "label": "卡片标题 / 子结论标题", "type": "font", "sample": "营业收入"},
        {"key": "rre-card-font-unit", "label": "单位标签", "type": "font", "sample": "万元"},
        {"key": "rre-card-font-hero", "label": "主数值", "type": "font", "sample": "128.46"},
        {"key": "rre-card-font-affix", "label": "主数值后缀", "type": "font", "sample": "万元"},
        {"key": "rre-card-font-read", "label": "辅助数值", "type": "font", "sample": "110.27"},
        {"key": "rre-card-font-label", "label": "标签、坐标轴、图例", "type": "font", "sample": "同比 · 华东"},
        {"key": "rre-card-font-table", "label": "表格文字", "type": "font", "sample": "华东 4,210"},
        {"key": "rre-card-font-prose", "label": "正文", "type": "font", "sample": "本月收入高于目标。"},
        {"key": "rre-prose-line-height", "label": "正文行距", "type": "number", "step": 0.05, "min": 1.2, "max": 2.4, "desc": "行高是字号的倍数"},
    ]},
    {"key": "layout", "label": "页面布局", "desc": BUILTIN_PAGE_LAYOUT + BUILTIN_CARD_LAYOUT, "tokens": [
        {"key": "max_width", "label": "标题与正文最大宽度", "type": "px"},
        {"key": "rre-page-pad", "label": "页面左右边距", "type": "px"},
        {"key": "rre-section-gap", "label": "章节之间的间距", "type": "px"},
        {"key": "rre-card-gap", "label": "卡片之间的间距", "type": "px"},
        {"key": "toc", "label": "显示目录", "type": "bool"},
        {"key": "rre-toc-width", "label": "侧栏目录宽度", "type": "px"},
        {"key": "rre-toc-gap", "label": "目录与正文间距", "type": "px"},
    ]},
    {"key": "header", "label": "报告头部", "desc": "报告最上方的标题区域", "tokens": [
        {"key": "header_style", "label": "头部样式", "type": "choice",
         "options": [{"value": "banner", "label": "主题色横幅"}, {"value": "plain", "label": "白色简洁"}]},
        {"key": "color-header-start", "label": "横幅渐变起始色", "type": "color_auto", "desc": "留空则跟随主题色"},
        {"key": "color-header-middle", "label": "横幅渐变过渡色", "type": "color_auto", "desc": "留空则只使用起始色与结束色"},
        {"key": "color-header-end", "label": "横幅渐变结束色", "type": "color_auto", "desc": "留空则由主题色自动加深"},
        {"key": "rre-header-pad", "label": "头部内边距", "type": "px"},
        {"key": "rre-radius-panel", "label": "头部圆角", "type": "px"},
        {"key": "shadow-header", "label": "头部投影", "type": "text"},
        {"key": "header_align", "label": "头部文字对齐", "type": "choice", "options": [{"value": "left", "label": "左对齐"}, {"value": "center", "label": "居中"}]},
    ]},
    {"key": "filter", "label": "筛选栏", "desc": "报告顶部的全局筛选与快捷场景按钮", "tokens": [
        {"key": "filter_sticky", "label": "滚动时吸顶", "type": "bool", "desc": "开启后放在封面下方，关闭时放在封面内"},
        {"key": "rre-control-height", "label": "筛选控件高度", "type": "px"},
        {"key": "rre-radius-control", "label": "筛选控件圆角", "type": "px", "desc": "999 为胶囊形"},
        {"key": "rre-radius-pill", "label": "按钮圆角", "type": "px", "desc": "快捷场景、重置按钮"},
        {"key": "filter_align", "label": "筛选值对齐", "type": "choice", "desc": "下拉框、多选框中显示的值；文字始终垂直居中",
         "options": [{"value": "left", "label": "左对齐"}, {"value": "center", "label": "居中"}]},
    ]},
    {"key": "section", "label": "章节与结论", "desc": "章节面板和节首结论", "tokens": [
        {"key": "rre-section-pad", "label": "章节内边距", "type": "px"},
        {"key": "rre-radius-section", "label": "章节与目录圆角", "type": "px"},
        {"key": "shadow-section", "label": "章节与目录投影", "type": "text"},
        {"key": "color-bg-summary", "label": "结论框底色", "type": "color"},
        {"key": "rre-summary-tint", "label": "结论框底色深浅", "type": "percent", "desc": "主题色在底色中的比例，越大越深"},
        {"key": "rre-summary-border-tint", "label": "结论框边框深浅", "type": "percent"},
        {"key": "section_align", "label": "章节标题与结论对齐", "type": "choice", "options": [{"value": "left", "label": "左对齐"}, {"value": "center", "label": "居中"}]},
    ]},
    {"key": "card", "label": "卡片", "desc": "每个图表、指标外面的白色卡片", "tokens": [
        {"key": "color-bg-card", "label": "卡片底色", "type": "color"},
        {"key": "color-border-card", "label": "卡片描边与分隔线", "type": "color"},
        {"key": "rre-card-border-width", "label": "描边粗细", "type": "px"},
        {"key": "radius-card", "label": "卡片圆角", "type": "px"},
        {"key": "shadow-card", "label": "卡片投影", "type": "text", "desc": "CSS 投影写法，如 0 8px 28px rgba(33,104,243,.07)"},
        {"key": "rre-card-pad", "label": "卡片内边距", "type": "px"},
        {"key": "rre-card-title-line", "label": "标题行高", "type": "px"},
        {"key": "rre-card-head-gap", "label": "标题到正文的间距", "type": "px"},
        {"key": "rre-radius-chip", "label": "单位与状态标签圆角", "type": "px"},
        {"key": "card_title_align", "label": "卡片标题对齐", "type": "choice", "options": [{"value": "left", "label": "左对齐"}, {"value": "center", "label": "居中"}]},
    ]},
    {"key": "kpi", "label": "指标卡", "desc": "主数值、辅助数值行与好坏判定色条", "tokens": [
        {"key": "rre-kpi-pad", "label": "指标卡内边距", "type": "px"},
        {"key": "rre-kpi-gap", "label": "指标卡之间的间距", "type": "px"},
        {"key": "rre-kpi-min-height", "label": "指标卡最小高度", "type": "px"},
        {"key": "rre-card-fact-line", "label": "辅助数值行高", "type": "px"},
        {"key": "rre-card-fact-gap", "label": "辅助数值行距", "type": "px"},
        {"key": "rre-radius-tile", "label": "辅助数值底块圆角", "type": "px"},
        {"key": "rre-status-bar", "label": "好坏判定色条粗细", "type": "px", "desc": "卡片顶部的绿 / 黄 / 红色条"},
        {"key": "kpi_align", "label": "指标卡内容对齐", "type": "choice", "desc": "主数值与辅助数值", "options": [{"value": "left", "label": "左对齐"}, {"value": "center", "label": "居中"}]},
    ]},
    {"key": "data", "label": "数据配色", "desc": "图形、好坏判定与涨跌的颜色", "tokens": [
        {"key": "chart-palette-categorical", "label": "分类配色", "type": "palette", "desc": "多个系列、多个类别时按顺序取色"},
        {"key": "chart-palette-sequential", "label": "深浅色阶", "type": "palette", "desc": "热力图等表示强弱，由浅到深"},
        {"key": "chart-palette-diverging", "label": "正负色阶", "type": "palette", "desc": "有正有负的数据：负值 → 中间 → 正值"},
        {"key": "color-status-positive", "label": "向好 / 达标", "type": "color"},
        {"key": "color-status-warning", "label": "关注 / 临近", "type": "color"},
        {"key": "color-status-negative", "label": "承压 / 未达", "type": "color"},
        {"key": "color-trend-up", "label": "上升", "type": "color", "desc": "中国习惯“红涨绿跌”可将上升、下降互换"},
        {"key": "color-trend-down", "label": "下降", "type": "color"},
    ]},
    {"key": "chart", "label": "图表", "desc": "同类图表共用图形、坐标轴、网格与图例样式", "tokens": [
        {"key": "rre-card-bar-max", "label": "柱子最大宽度", "type": "px"},
        {"key": "rre-data-radius", "label": "柱子圆角", "type": "px"},
        {"key": "rre-chart-line-width", "label": "折线粗细", "type": "number", "step": 0.2, "min": 1, "max": 6},
        {"key": "chart_grid", "label": "网格线", "type": "choice",
         "options": [{"value": "dashed", "label": "虚线"}, {"value": "solid", "label": "实线"}, {"value": "none", "label": "不显示"}]},
        {"key": "rre-card-legend-gap", "label": "图例间距", "type": "px"},
        {"key": "rre-card-axis-gap", "label": "刻度文字到轴线", "type": "px"},
        {"key": "rre-card-axis-title-gap", "label": "轴标题到刻度", "type": "px"},
    ]},
    {"key": "table", "label": "表格", "desc": "明细表、汇总表与排行表共用表头、行高、隔行底色和列对齐", "tokens": [
        {"key": "rre-card-row-height", "label": "行高", "type": "px"},
        {"key": "color-table-head-bg", "label": "表头底色", "type": "color_auto", "desc": "留空则与卡片底色一致"},
        {"key": "table_zebra", "label": "斑马纹（隔行底色）", "type": "bool"},
        {"key": "table_text_align", "label": "文字列对齐", "type": "choice", "options": [{"value": "left", "label": "左对齐"}, {"value": "center", "label": "居中"}]},
        {"key": "table_num_align", "label": "数值列对齐", "type": "choice", "options": [{"value": "right", "label": "右对齐"}, {"value": "center", "label": "居中"}]},
    ]},
    {"key": "advanced", "label": "高级", "desc": "仅供技术人员使用", "tokens": [
        {"key": "custom_css", "label": "自定义 CSS", "type": "code"},
    ]},
]

# 旧条目保留原类别供管理台查阅。章节、选图和筛选由分析阶段决定。
RETIRED_ANALYSIS_CATEGORIES = {"structure": "版式结构", "chart": "图表选择", "filter": "必备筛选"}

ITEM_CATEGORIES: list[dict] = [
    {"key": "status", "label": "好坏判定", "hint": "哪些指标判断好坏、标准是什么（只用于指标卡、仪表盘、子弹图）",
     "examples": ["目标达成率 ≥100% 判为达标，≥95% 判为关注，其余判为承压", "满意度 ≥4.5 分为达标，≥4.2 分为关注"]},
    {"key": "trend", "label": "涨跌颜色", "hint": "哪些指标越大越好、哪些越小越好",
     "examples": ["销售额、毛利等指标上升为好", "成本、投诉率、处理时长等指标下降为好", "库存量变化不判断好坏，不着色"]},
    {"key": "number", "label": "数字格式", "hint": "金额、比例等数字怎么显示",
     "examples": ["金额以万元显示，保留 2 位小数", "比率显示为百分比，保留 1 位小数", "处理时长单位为小时"]},
    {"key": "copy", "label": "文案要求", "hint": "结论、说明文字的写法",
     "examples": ["提到同比时注明对比期间", "业务缩写首次出现时写出全称"]},
    {"key": "other", "label": "其他展示要求", "hint": "补充其他显示格式或文案规范", "examples": []},
]
_CAT_LABEL = {c["key"]: c["label"] for c in ITEM_CATEGORIES}
for _g in TOKEN_GROUPS:
    for _t in _g["tokens"]:
        _t["builtin"] = _t["key"] in BUILTIN_TOKENS


def normalize_items(items) -> list[dict]:
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        text = str(it.get("text") or "").strip()
        cat = str(it.get("category") or "")
        # 兼容旧客户端提交的类别，避免退役条目落入“其他”后重新生效。
        if cat in RETIRED_ANALYSIS_CATEGORIES:
            continue
        cat = cat if cat in _CAT_LABEL else "other"
        if text:
            out.append({"category": cat, "text": text[:500]})
    return out


def merge_items(rules: list[dict]) -> list[dict]:
    """把各条规则的条目合并为一份指定规则：按类别分组，组内业务场景规则在前、通用规则在后（冲突时以前者为准），
    相同文字只保留优先级最高的一条。rules 需按 通用 → 场景 的顺序传入。"""
    ordered = [r for r in rules if r.get("scope") == "scenario"][::-1] + [r for r in rules if r.get("scope") != "scenario"][::-1]
    seen: set[tuple[str, str]] = set()
    by_cat: dict[str, list[dict]] = {}
    for r in ordered:
        level = "scenario" if r.get("scope") == "scenario" else "general"
        src = ("业务场景规则" if level == "scenario" else "通用规则") + f"「{r.get('name') or ''}」"
        for it in r.get("items") or []:
            key = (it["category"], re.sub(r"\s+", "", it["text"]))
            if key in seen:
                continue
            seen.add(key)
            by_cat.setdefault(it["category"], []).append(
                {"category": it["category"], "category_label": _CAT_LABEL.get(it["category"], "其他"),
                 "text": it["text"], "level": level, "source": src})
    return [it for c in ITEM_CATEGORIES for it in by_cat.get(c["key"], [])]


def source_text(src: dict) -> str:
    return {"builtin": "内置样式", "general": f"通用规则「{src.get('rule')}」",
            "scenario": f"业务场景规则「{src.get('rule')}」"}.get(src.get("level"), "")


def items_to_markdown(name: str, items: list[dict]) -> str:
    """把结构化规则条目生成给模型阅读的 markdown（按类别分组）。"""
    lines = [f"## {name}"]
    for c in ITEM_CATEGORIES:
        group = [it["text"] for it in items if it["category"] == c["key"]]
        if group:
            lines.append(f"### {c['label']}")
            lines += [f"- {t}" for t in group]
    return "\n".join(lines)


def legacy_items(content: str) -> list[dict]:
    """旧版 markdown 规则 → 条目（列表项各成一条，归入“其他”，供人工再分类）。"""
    items = []
    for line in (content or "").splitlines():
        m = re.match(r"^\s*(?:[-*]|\d+[.、])\s+(.+)$", line)
        if m:
            items.append({"category": "other", "text": re.sub(r"\*\*|`", "", m.group(1)).strip()})
    return items


def theme_diff(theme: dict, base: dict) -> dict:
    """只保留与继承值不同的设置（规则只存自己覆盖的部分）。"""
    overrides, _ = resolve_overrides(theme)
    return {k: v for k, v in overrides.items() if v != base.get(k)}


def resolve_with_sources(layers: list[tuple[str, str, dict]]) -> tuple[dict, dict]:
    """layers: [(level, rule_name, theme)]，按 通用 → 场景 顺序。返回 (已设置的样式项, 每项来源)；
    来源 level：builtin（内置样式）/ general / scenario / unset（未设置，由智能体决定）。"""
    value, _ = resolve()
    source = {k: {"level": "builtin", "rule": "内置样式"} for k in value}
    for k in KNOWN_KEYS - value.keys():
        source[k] = {"level": "unset", "rule": "未设置（由智能体决定）"}
    for level, rule_name, theme in layers:
        overrides, _ = resolve_overrides(theme)
        value.update(overrides)
        for key in overrides:
            source[key] = {"level": level, "rule": rule_name}
    return value, source
