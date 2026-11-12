"""样式令牌。令牌键名即 CSS 变量名（去掉前缀 --）。

- 内置样式（BUILTIN_TOKENS）只有企业规范中的颜色：页面与卡片、文字、健康度、升降色、图表色板。
- 其余样式项（字号、间距、圆角、对齐等）没有内置值。只有内置样式与规则中设置过的项会提供给智能体。
- 展示取值：智能体在模板 theme 中配置了的项按其配置；未配置的项按 业务场景规则 ≥ 通用规则 ≥ 内置样式。
- _BASE 仅是渲染兜底（智能体也没有决定时避免页面错乱），不属于内置样式，也不会提供给智能体。
样式项按报告页面的组成部分分为 12 类（TOKEN_GROUPS）。
"""
from __future__ import annotations

import re
from typing import Any

COLOR_TOKENS = {
    "color-brand": "#2168F3",              # 标题前标记、多项对比图、判不了好坏的卡
    "color-bg-page": "#EEF4FE",            # 报告页背景
    "color-text-primary": "#17396D",       # 标题、主值、读数值
    "color-text-secondary": "#5F7DA8",     # 单位、标签、坐标轴、注释
    "color-text-inverse": "#FFFFFF",       # 深色底上的文字
    "color-bg-card": "#FFFFFF",            # 卡片画布
    "color-border-card": "#DCE7F8",        # 卡片边框、分隔线
    "color-status-positive": "#16A866",    # 达标、向好
    "color-status-warning": "#F2B22E",     # 临近、关注
    "color-status-negative": "#F0585B",    # 未达、承压
    "color-trend-up": "#16A866",           # 同比、环比上升
    "color-trend-down": "#F0585B",         # 同比、环比下降
}
AUTO_COLOR_TOKENS = {  # 空值 = 自动（由主题色派生）
    "color-header-start": "",              # 头部横幅渐变起始色（默认主题色）
    "color-header-end": "",                # 头部横幅渐变结束色（默认主题色与主文字色混合）
    "color-table-head-bg": "",             # 表头底色（默认卡片底色）
}
PALETTE_TOKENS = {
    "chart-palette-categorical": ["#2168F3", "#4EA5F8", "#1A44A5", "#92C3FA", "#3F84EE", "#C4DCFB"],
    "chart-palette-sequential": ["#EEF5FE", "#D6E6FC", "#B4D3FA", "#85B8F6", "#4C90EE", "#2168F3"],
    "chart-palette-diverging": ["#1A44A5", "#EEF4FE", "#4EA5F8"],
}
FONT_FAMILY = "'Inter', 'Noto Sans SC', -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif"
FONT_TOKENS = {  # 字号按设备固定，最小 12px
    "rre-page-font-title": {"size": 28, "weight": 700},   # 报告大标题
    "rre-page-font-section": {"size": 20, "weight": 700}, # 章节标题
    "rre-card-font-title": {"size": 16, "weight": 600},   # 卡片标题、子结论标题
    "rre-card-font-unit": {"size": 12, "weight": 500},    # 标题右侧单位标签
    "rre-card-font-hero": {"size": 36, "weight": 700},    # 指标卡、仪表盘、子弹图主值
    "rre-card-font-affix": {"size": 16, "weight": 500},   # 紧跟主值的 %、万元
    "rre-card-font-read": {"size": 16, "weight": 600},    # 辅助指标值、环心值、合计
    "rre-card-font-label": {"size": 12, "weight": 400},   # 辅助指标名、坐标轴、图例、表头
    "rre-card-font-table": {"size": 13, "weight": 400},   # 单元格、榜单、漏斗阶段名
    "rre-card-font-prose": {"size": 14, "weight": 400},   # 正文
}
SIZE_TOKENS = {  # px；不随卡片宽度变化，只有图形区随卡片伸缩
    "rre-page-pad": 24, "rre-section-gap": 40, "rre-toc-width": 168, "rre-header-pad": 32, "rre-control-height": 32,
    "rre-card-pad": 20, "rre-card-gap": 20, "rre-card-border-width": 1, "rre-card-title-line": 22, "rre-card-head-gap": 12,
    "rre-card-fact-line": 22, "rre-card-fact-gap": 6, "rre-status-bar": 4, "rre-card-row-height": 36,
    "rre-card-legend-gap": 16, "rre-card-axis-gap": 8, "rre-card-axis-title-gap": 4, "rre-card-bar-max": 24,
    "radius-card": 16, "rre-radius-panel": 18, "rre-radius-tile": 999, "rre-radius-chip": 999,
    "rre-radius-control": 999, "rre-radius-pill": 999, "rre-data-radius": 12,
}
NUMBER_TOKENS = {  # 小数：(默认, 最小, 最大)
    "rre-prose-line-height": (1.65, 1.2, 2.4),
    "rre-chart-line-width": (2.4, 1.0, 6.0),
}
PERCENT_TOKENS = {  # 0~40%：派生色的混合比例
    "rre-summary-tint": 6,                 # 结论框底色中主题色的比例
    "rre-summary-border-tint": 22,         # 结论框边框中主题色的比例
}
OTHER_TOKENS = {
    "font-family": FONT_FAMILY,
    "shadow-card": "0 8px 28px rgba(33, 104, 243, .07)",
}
LAYOUT_OPTIONS = {  # 非 CSS 变量的页面选项
    "header_style": "banner",   # banner：主题色横幅 + 反白文字；plain：白色卡片
    "toc": True,
    "max_width": 1360,
    "filter_sticky": True,      # 滚动时筛选栏吸顶
    "chart_grid": "dashed",     # 图表网格线：dashed / solid / none
    "table_zebra": False,       # 表格斑马纹
    # 对齐（水平）；控件、按钮、标签内的文字始终垂直居中
    "header_align": "left",     # 报告头部文字
    "filter_align": "left",     # 筛选控件中的显示值
    "section_align": "left",    # 章节标题与本节结论
    "card_title_align": "left", # 卡片标题
    "kpi_align": "left",        # 指标卡主数值与辅助数值
    "table_text_align": "left", # 表格文字列
    "table_num_align": "right", # 表格数值列
    "custom_css": "",
}

# 内置样式：只有企业规范中的颜色
BUILTIN_TOKENS: dict[str, Any] = {**COLOR_TOKENS, **PALETTE_TOKENS}
DEFAULT_TOKENS = BUILTIN_TOKENS  # 兼容旧名称
# 非内置样式项的渲染兜底（不是内置样式，不提供给智能体）
_BASE: dict[str, Any] = {**AUTO_COLOR_TOKENS, **FONT_TOKENS, **SIZE_TOKENS, **{k: v[0] for k, v in NUMBER_TOKENS.items()},
                         **PERCENT_TOKENS, **OTHER_TOKENS, **LAYOUT_OPTIONS}
KNOWN_KEYS = set(BUILTIN_TOKENS) | set(_BASE)

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


def resolve(*layers: dict | None) -> tuple[dict, list[str]]:
    """内置样式 + 按顺序叠加的样式层，返回 (已设置的样式项, 告警)。未设置的非内置项不在结果中。"""
    out: dict[str, Any] = dict(BUILTIN_TOKENS)
    warns: list[str] = []
    for layer in layers:
        for k, v in (layer or {}).items():
            k = str(k).lstrip("-")
            k = LEGACY_ALIASES.get(k, k)
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
    """渲染用的完整取值：未设置的非内置项使用兜底值。"""
    return {**_BASE, **values}


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
    he = t["color-header-end"] or "color-mix(in srgb,var(--color-brand) 62%,var(--color-text-primary))"
    parts.append(f"--rf-header-bg:linear-gradient(120deg,{hs} 0%,{he} 100%);")
    parts.append(f"--rf-table-head-bg:{t['color-table-head-bg'] or 'var(--color-bg-card)'};")
    parts.append(f"--font-family:{t['font-family']};--shadow-card:{t['shadow-card']};--rf-max:{int(t['max_width'])}px;")
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


# ============================================================ 业务人员可读的元数据（样式规则编辑器使用）
PRECEDENCE = ("智能体决定 ≥ 业务场景规则 ≥ 通用规则 ≥ 内置样式（仅颜色）：后三者合并为一份“指定样式”（视觉样式与生成要求），"
              "同一项以优先级高者为准，作为约束交给智能体；智能体可以按自己的判断调整，没有指定的由智能体自由决定")

# 按报告页面的组成部分分类；每个令牌只属于一类
TOKEN_GROUPS: list[dict] = [
    {"key": "theme", "label": "主题", "desc": "整份报告的主色调、页面底色、文字颜色与字体", "tokens": [
        {"key": "color-brand", "label": "主题色", "type": "color", "desc": "章节标记、结论标签、多项对比图、无法判断好坏的卡片"},
        {"key": "color-bg-page", "label": "页面底色", "type": "color", "desc": "整个报告页面的背景"},
        {"key": "color-text-primary", "label": "主文字", "type": "color", "desc": "标题、主数值、正文"},
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
    {"key": "layout", "label": "页面布局", "desc": "页面宽度、边距、章节间距与目录", "tokens": [
        {"key": "max_width", "label": "页面最大宽度", "type": "px"},
        {"key": "rre-page-pad", "label": "页面左右边距", "type": "px"},
        {"key": "rre-section-gap", "label": "章节之间的间距", "type": "px"},
        {"key": "rre-card-gap", "label": "卡片之间的间距", "type": "px"},
        {"key": "toc", "label": "显示左侧目录", "type": "bool"},
        {"key": "rre-toc-width", "label": "目录宽度", "type": "px"},
    ]},
    {"key": "header", "label": "报告头部", "desc": "报告最上方的标题区域", "tokens": [
        {"key": "header_style", "label": "头部样式", "type": "choice",
         "options": [{"value": "banner", "label": "主题色横幅"}, {"value": "plain", "label": "白色简洁"}]},
        {"key": "color-header-start", "label": "横幅渐变起始色", "type": "color_auto", "desc": "留空则跟随主题色"},
        {"key": "color-header-end", "label": "横幅渐变结束色", "type": "color_auto", "desc": "留空则由主题色自动加深"},
        {"key": "rre-header-pad", "label": "头部内边距", "type": "px"},
        {"key": "rre-radius-panel", "label": "头部与结论框圆角", "type": "px"},
        {"key": "header_align", "label": "头部文字对齐", "type": "choice", "options": [{"value": "left", "label": "左对齐"}, {"value": "center", "label": "居中"}]},
    ]},
    {"key": "filter", "label": "筛选栏", "desc": "报告顶部的全局筛选与快捷场景按钮", "tokens": [
        {"key": "filter_sticky", "label": "滚动时吸顶", "type": "bool"},
        {"key": "rre-control-height", "label": "筛选控件高度", "type": "px"},
        {"key": "rre-radius-control", "label": "筛选控件圆角", "type": "px", "desc": "999 为胶囊形"},
        {"key": "rre-radius-pill", "label": "按钮圆角", "type": "px", "desc": "快捷场景、重置按钮"},
        {"key": "filter_align", "label": "筛选值对齐", "type": "choice", "desc": "下拉框、多选框中显示的值；文字始终垂直居中",
         "options": [{"value": "left", "label": "左对齐"}, {"value": "center", "label": "居中"}]},
    ]},
    {"key": "section", "label": "章节与结论", "desc": "每节开头的“本节结论”框", "tokens": [
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
    {"key": "chart", "label": "图表", "desc": "柱、线、坐标轴、网格与图例", "tokens": [
        {"key": "rre-card-bar-max", "label": "柱子最大宽度", "type": "px"},
        {"key": "rre-data-radius", "label": "柱子圆角", "type": "px"},
        {"key": "rre-chart-line-width", "label": "折线粗细", "type": "number", "step": 0.2, "min": 1, "max": 6},
        {"key": "chart_grid", "label": "网格线", "type": "choice",
         "options": [{"value": "dashed", "label": "虚线"}, {"value": "solid", "label": "实线"}, {"value": "none", "label": "不显示"}]},
        {"key": "rre-card-legend-gap", "label": "图例间距", "type": "px"},
        {"key": "rre-card-axis-gap", "label": "刻度文字到轴线", "type": "px"},
        {"key": "rre-card-axis-title-gap", "label": "轴标题到刻度", "type": "px"},
    ]},
    {"key": "table", "label": "表格", "desc": "明细表与排行表", "tokens": [
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

ITEM_CATEGORIES: list[dict] = [
    {"key": "structure", "label": "版式结构", "hint": "报告应该有哪些章节、先放什么后放什么",
     "examples": ["报告首节为“经营概览”，放销售额、毛利额、毛利率、订单数 4 个指标卡，并附同比",
                  "最后一节为“结论与行动建议”，列 3 到 5 条可执行建议"]},
    {"key": "chart", "label": "图表选择", "hint": "某类分析用什么图表",
     "examples": ["区域排名使用横向条形图，只显示前 10 名", "渠道构成使用环形图，类别不超过 6 个", "目标达成用仪表盘加分区域子弹图"]},
    {"key": "filter", "label": "必备筛选", "hint": "报告顶部必须提供哪些筛选条件",
     "examples": ["必须包含月份区间、大区、省份筛选，省份随大区联动", "提供“本季度”“近 12 个月”两个快捷选项"]},
    {"key": "status", "label": "好坏判定", "hint": "哪些指标判断好坏、标准是什么（只用于指标卡、仪表盘、子弹图）",
     "examples": ["目标达成率 ≥100% 判为达标，≥95% 判为关注，其余判为承压", "满意度 ≥4.5 分为达标，≥4.2 分为关注"]},
    {"key": "trend", "label": "涨跌颜色", "hint": "哪些指标越大越好、哪些越小越好",
     "examples": ["销售额、毛利等指标上升为好", "成本、投诉率、处理时长等指标下降为好", "库存量变化不判断好坏，不着色"]},
    {"key": "number", "label": "数字格式", "hint": "金额、比例等数字怎么显示",
     "examples": ["金额以万元显示，保留 2 位小数", "比率显示为百分比，保留 1 位小数", "处理时长单位为小时"]},
    {"key": "copy", "label": "文案要求", "hint": "结论、说明文字的写法",
     "examples": ["每节结论先说结果再说原因，必须带具体数字和对比", "每张图表下用一句话说明它如何支撑结论"]},
    {"key": "other", "label": "其他", "hint": "以上都不适用的要求", "examples": []},
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
        cat = it.get("category") if it.get("category") in _CAT_LABEL else "other"
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
    resolved, _ = resolve(base, theme)
    keys = {LEGACY_ALIASES.get(str(k).lstrip("-"), str(k).lstrip("-")) for k in (theme or {})}
    return {k: resolved[k] for k in keys if k in resolved and resolved[k] != base.get(k)}


def resolve_with_sources(layers: list[tuple[str, str, dict]]) -> tuple[dict, dict]:
    """layers: [(level, rule_name, theme)]，按 通用 → 场景 顺序。返回 (已设置的样式项, 每项来源)；
    来源 level：builtin（内置样式）/ general / scenario / unset（未设置，由智能体决定）。"""
    value, _ = resolve()
    source = {k: {"level": "builtin", "rule": "内置样式"} for k in value}
    for k in _BASE:
        source[k] = {"level": "unset", "rule": "未设置（由智能体决定）"}
    for level, rule_name, theme in layers:
        resolved, _ = resolve(theme)
        for k in (theme or {}):
            kk = LEGACY_ALIASES.get(str(k).lstrip("-"), str(k).lstrip("-"))
            if kk in resolved:
                value[kk] = resolved[kk]
                source[kk] = {"level": level, "rule": rule_name}
    return value, source
