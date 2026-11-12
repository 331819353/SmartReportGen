"""设计令牌（通用视觉规范）。报告页面只从令牌取值，派生色用 color-mix 从令牌混出。

令牌键名即 CSS 变量名（去掉前缀 --）。主题解析顺序：
  本模块默认值 → 通用样式规则 → 业务场景样式规则（按 priority）→ 模板 theme
"""
from __future__ import annotations

import re
from typing import Any

COLOR_TOKENS = {
    "color-bg-page": "#EEF4FE",            # 报告页背景
    "color-bg-card": "#FFFFFF",            # 卡片画布
    "color-border-card": "#DCE7F8",        # 卡片边框、分隔线
    "color-brand": "#2168F3",              # 标题前标记、多项对比图、判不了好坏的卡
    "color-text-primary": "#17396D",       # 标题、主值、读数值
    "color-text-secondary": "#5F7DA8",     # 单位、标签、坐标轴、注释
    "color-text-inverse": "#FFFFFF",       # 深色底上的文字
    "color-status-positive": "#16A866",    # 达标、向好
    "color-status-warning": "#F2B22E",     # 临近、关注
    "color-status-negative": "#F0585B",    # 未达、承压
    "color-trend-up": "#16A866",           # 同比、环比上升
    "color-trend-down": "#F0585B",         # 同比、环比下降
}
PALETTE_TOKENS = {
    "chart-palette-categorical": ["#2168F3", "#4EA5F8", "#1A44A5", "#92C3FA", "#3F84EE", "#C4DCFB"],
    "chart-palette-sequential": ["#EEF5FE", "#D6E6FC", "#B4D3FA", "#85B8F6", "#4C90EE", "#2168F3"],
    "chart-palette-diverging": ["#1A44A5", "#EEF4FE", "#4EA5F8"],
}
FONT_FAMILY = "'Inter', 'Noto Sans SC', -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif"
FONT_TOKENS = {  # 卡内只用八个文字角色；字号按设备固定，最小 12px
    "rre-card-font-title": {"size": 16, "weight": 600},   # 卡片标题，单行，超出省略
    "rre-card-font-unit": {"size": 12, "weight": 500},    # 标题右侧单位标签
    "rre-card-font-hero": {"size": 36, "weight": 700},    # 指标卡、仪表盘、子弹图主值
    "rre-card-font-affix": {"size": 16, "weight": 500},   # 紧跟主值的 %、万元
    "rre-card-font-read": {"size": 16, "weight": 600},    # 辅助指标值、环心值、合计
    "rre-card-font-label": {"size": 12, "weight": 400},   # 辅助指标名、坐标轴、图例、表头
    "rre-card-font-table": {"size": 13, "weight": 400},   # 单元格、榜单、漏斗阶段名
    "rre-card-font-prose": {"size": 14, "weight": 400},   # 文本正文
}
SIZE_TOKENS = {  # px；不随卡片宽度变化，只有图形区随卡片伸缩
    "rre-card-pad": 20, "rre-card-gap": 20, "rre-card-title-line": 22, "rre-card-head-gap": 12,
    "rre-card-fact-line": 22, "rre-card-fact-gap": 6, "rre-card-row-height": 36, "rre-card-legend-gap": 16,
    "rre-card-axis-gap": 8, "rre-card-axis-title-gap": 4, "rre-card-bar-max": 24,
    "radius-card": 16, "rre-radius-panel": 18, "rre-radius-tile": 999, "rre-radius-chip": 999,
    "rre-radius-control": 999, "rre-radius-pill": 999, "rre-data-radius": 12,
}
OTHER_TOKENS = {
    "font-family": FONT_FAMILY,
    "shadow-card": "0 8px 28px rgba(33, 104, 243, .07)",
}
LAYOUT_OPTIONS = {  # 非令牌的页面选项
    "header_style": "banner",   # banner：主题蓝面板 + 反白文字；plain：卡片底色
    "toc": True,
    "max_width": 1360,
    "custom_css": "",
}

DEFAULT_TOKENS: dict[str, Any] = {**COLOR_TOKENS, **PALETTE_TOKENS, **FONT_TOKENS, **SIZE_TOKENS, **OTHER_TOKENS}

# 旧版主题键 → 令牌
LEGACY_ALIASES = {
    "primary": "color-brand", "accent": "color-brand", "palette": "chart-palette-categorical",
    "background": "color-bg-page", "surface": "color-bg-card", "text": "color-text-primary",
    "muted": "color-text-secondary", "border": "color-border-card", "up_color": "color-trend-up",
    "down_color": "color-trend-down", "font_family": "font-family", "radius": "radius-card", "gap": "rre-card-gap",
    "shadow": "shadow-card", "header_text": "color-text-inverse",
}

_SAFE = re.compile(r"^[#\w\s,.'\"()%+\-/]*$")


def resolve(*layers: dict | None) -> tuple[dict, list[str]]:
    """按顺序叠加主题层，返回 (令牌+页面选项, 告警)。"""
    out: dict[str, Any] = {**DEFAULT_TOKENS, **LAYOUT_OPTIONS}
    warns: list[str] = []
    for layer in layers:
        for k, v in (layer or {}).items():
            k = str(k).lstrip("-")
            k = LEGACY_ALIASES.get(k, k)
            if k in ("header_bg", "summary_bg", "number_font", "chart_label_size"):
                continue  # 旧版选项，已由令牌体系取代
            if k not in out:
                warns.append(f"theme.{k} 不是已知令牌，已忽略")
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
        if isinstance(v, list) and v and all(isinstance(c, str) and _SAFE.match(c) for c in v):
            return True, v, ""
        return False, None, "必须是颜色字符串数组"
    if k in FONT_TOKENS:
        if isinstance(v, str):  # 允许 "16px 600"
            m = re.match(r"^\s*(\d+)(?:px)?\s*[·/ ]\s*(\d{3})\s*$", v)
            if not m:
                return False, None, "格式应为 {size, weight} 或 '16px 600'"
            v = {"size": int(m.group(1)), "weight": int(m.group(2))}
        if not isinstance(v, dict) or "size" not in v:
            return False, None, "格式应为 {size, weight}"
        size = int(v["size"])
        if size < 12:
            return False, None, "字号不得小于 12px"
        return True, {"size": size, "weight": int(v.get("weight", 400))}, ""
    if k in SIZE_TOKENS or k == "max_width":
        try:
            return True, int(str(v).replace("px", "")), ""
        except ValueError:
            return False, None, "必须是像素整数"
    if k == "toc":
        return True, bool(v), ""
    if isinstance(v, str) and _SAFE.match(v):
        return True, v, ""
    return False, None, "包含不安全字符"


def css_vars(t: dict) -> str:
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
    parts.append(f"--font-family:{t['font-family']};--shadow-card:{t['shadow-card']};--rf-max:{int(t['max_width'])}px;")
    return ":root{" + "".join(parts) + "}"


def runtime_tokens(t: dict) -> dict:
    """传给浏览器端运行时（ECharts 配置）使用的令牌。"""
    return {k: t[k] for k in DEFAULT_TOKENS}


GENERAL_RULE_CONTENT = """## 企业通用视觉规范（设计令牌）

### 颜色
- **页面与卡片**：颜色只来自主题令牌，组件里不写色值；派生色用 color-mix 从令牌混出。
  页面底色 `--color-bg-page` #EEF4FE；卡片底色 `--color-bg-card` #FFFFFF；卡片描边 `--color-border-card` #DCE7F8；
  主题蓝 `--color-brand` #2168F3（标题前标记、多项对比图、判不了好坏的卡）。
- **文字**：标题与数值用主文字色 `--color-text-primary` #17396D；单位、标签、坐标轴用次文字色 `--color-text-secondary` #5F7DA8；
  深色底上的文字用反白 `--color-text-inverse` #FFFFFF。
- **健康度四色**：只有指标卡、仪表盘、子弹图判整卡好坏，卡片主色取绿、黄、红、蓝之一；状态同时用图标、正负号或文字表达。
  绿·向好 `--color-status-positive` #16A866（达标、向好）；黄·关注 `--color-status-warning` #F2B22E（临近、关注）；
  红·承压 `--color-status-negative` #F0585B（未达、承压）；蓝·不判 `--color-brand` #2168F3（判不了好坏、多项对比）。
  → 模板中用图表的 `status` 声明判定口径；没有明确好坏标准的卡不要声明 status（保持蓝色）。
- **升降色**：增减按指标方向上色，没有方向就不着色；用户确认过的升降色优先。
  上升 `--color-trend-up` #16A866；下降 `--color-trend-down` #F0585B。
  → KPI 副指标用 `trend: up`（越大越好）/ `trend: down`（越小越好）/ `trend: none`（不着色，只显示 ▲▼ 与正负号）。
- **图表色板**：图形只用色板与卡片主色的深浅；多系列按分类色板顺序取色。
  分类色板 `--chart-palette-categorical`（系列、类目）；顺序色阶 `--chart-palette-sequential`（热力、强度）；
  发散色阶 `--chart-palette-diverging`（有正有负的着色）。

### 字体
卡内只用八个文字角色，字号按设备固定、不随卡宽缩放，最小 12px；放不下时截断、抽稀或收起，不缩小文字。
字体族：'Inter', 'Noto Sans SC', -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif。

| 角色 | 令牌 | 字号·字重 | 用途 |
|---|---|---|---|
| 标题 | --rre-card-font-title | 16px · 600 | 卡片标题，单行，超出省略 |
| 单位 | --rre-card-font-unit | 12px · 500 | 标题右侧的单位标签 |
| 主值 | --rre-card-font-hero | 36px · 700 | 指标卡、仪表盘、子弹图的主值 |
| 主值后缀 | --rre-card-font-affix | 16px · 500 | 紧跟主值的 %、万元 |
| 读数值 | --rre-card-font-read | 16px · 600 | 辅助指标值、环心值、合计 |
| 标签 | --rre-card-font-label | 12px · 400 | 辅助指标名、坐标轴、图例、表头 |
| 表格正文 | --rre-card-font-table | 13px · 400 | 单元格、榜单、漏斗阶段名 |
| 文本正文 | --rre-card-font-prose | 14px · 400 | 文本卡正文 |

### 间距与尺寸
内边距、间距、行高与柱宽上限按设备固定，卡片随一行张数变宽变窄时这些值不变；只有图形区随卡片伸缩。
卡片内边距 20px；卡片之间 20px；标题行高 22px；标题区到正文 12px；辅助读数行高 22px、行距 6px；表格行高 36px；
图例项间距 16px；刻度文字到轴线 8px；轴标题到刻度文字 4px；单柱最宽 24px。

### 形状与比例
卡片圆角 16px；面板圆角 18px；读数格/标签/控件/胶囊圆角 999px；数据图形圆角 12px；
卡片投影 `--shadow-card` 0 8px 28px rgba(33,104,243,.07)。
"""


# ============================================================ 业务人员可读的元数据（样式规则编辑器使用）
PRECEDENCE = "业务场景规则 ≥ 通用规则 ≥ 内置默认：同一项设置以业务场景规则为准，其次通用规则，都没有设置时用内置默认"

TOKEN_GROUPS: list[dict] = [
    {"key": "brand", "label": "主题与页面", "desc": "报告的主色调与页面、卡片底色", "tokens": [
        {"key": "color-brand", "label": "主题色", "type": "color", "desc": "章节标题标记、多项对比图、无法判断好坏的卡片"},
        {"key": "color-bg-page", "label": "页面底色", "type": "color", "desc": "整个报告页面的背景"},
        {"key": "color-bg-card", "label": "卡片底色", "type": "color", "desc": "每张图表卡片的背景"},
        {"key": "color-border-card", "label": "卡片描边与分隔线", "type": "color", "desc": "卡片边框、表格分隔线"},
    ]},
    {"key": "text", "label": "文字颜色", "desc": "标题、数值与辅助说明的颜色", "tokens": [
        {"key": "color-text-primary", "label": "主文字", "type": "color", "desc": "标题、主数值"},
        {"key": "color-text-secondary", "label": "次要文字", "type": "color", "desc": "单位、标签、坐标轴、注释"},
        {"key": "color-text-inverse", "label": "深色底上的文字", "type": "color", "desc": "例如报告头部横幅上的标题"},
    ]},
    {"key": "status", "label": "好坏判定颜色", "desc": "只用于指标卡、仪表盘、子弹图判断整卡好坏", "tokens": [
        {"key": "color-status-positive", "label": "向好 / 达标", "type": "color"},
        {"key": "color-status-warning", "label": "关注 / 临近", "type": "color"},
        {"key": "color-status-negative", "label": "承压 / 未达", "type": "color"},
    ]},
    {"key": "trend", "label": "涨跌颜色", "desc": "同比、环比的升降着色（中国习惯“红涨绿跌”可在此调换）", "tokens": [
        {"key": "color-trend-up", "label": "上升", "type": "color"},
        {"key": "color-trend-down", "label": "下降", "type": "color"},
    ]},
    {"key": "palette", "label": "图表配色", "desc": "图形只用这些颜色", "tokens": [
        {"key": "chart-palette-categorical", "label": "分类配色", "type": "palette", "desc": "多个系列、多个类别时按顺序取色"},
        {"key": "chart-palette-sequential", "label": "深浅色阶", "type": "palette", "desc": "热力图等表示强弱，由浅到深"},
        {"key": "chart-palette-diverging", "label": "正负色阶", "type": "palette", "desc": "有正有负的数据：负值 → 中间 → 正值"},
    ]},
    {"key": "font", "label": "字体字号", "desc": "卡片内只用八种文字，字号固定、最小 12px", "tokens": [
        {"key": "font-family", "label": "字体", "type": "text", "desc": "按顺序优先使用"},
        {"key": "rre-card-font-title", "label": "卡片标题", "type": "font", "sample": "营业收入"},
        {"key": "rre-card-font-unit", "label": "单位标签", "type": "font", "sample": "万元"},
        {"key": "rre-card-font-hero", "label": "主数值", "type": "font", "sample": "128.46"},
        {"key": "rre-card-font-affix", "label": "主数值后缀", "type": "font", "sample": "万元"},
        {"key": "rre-card-font-read", "label": "辅助数值", "type": "font", "sample": "110.27"},
        {"key": "rre-card-font-label", "label": "标签与坐标轴", "type": "font", "sample": "同比 · 华东"},
        {"key": "rre-card-font-table", "label": "表格文字", "type": "font", "sample": "华东 4,210"},
        {"key": "rre-card-font-prose", "label": "正文", "type": "font", "sample": "本月收入高于目标。"},
    ]},
    {"key": "size", "label": "间距尺寸", "desc": "单位 px；卡片变宽变窄时这些值不变，只有图形区伸缩", "tokens": [
        {"key": "rre-card-pad", "label": "卡片内边距", "type": "px"},
        {"key": "rre-card-gap", "label": "卡片之间的间距", "type": "px"},
        {"key": "rre-card-title-line", "label": "标题行高", "type": "px"},
        {"key": "rre-card-head-gap", "label": "标题到正文", "type": "px"},
        {"key": "rre-card-fact-line", "label": "辅助数值行高", "type": "px"},
        {"key": "rre-card-fact-gap", "label": "辅助数值行距", "type": "px"},
        {"key": "rre-card-row-height", "label": "表格行高", "type": "px"},
        {"key": "rre-card-legend-gap", "label": "图例间距", "type": "px"},
        {"key": "rre-card-axis-gap", "label": "刻度文字到轴线", "type": "px"},
        {"key": "rre-card-axis-title-gap", "label": "轴标题到刻度", "type": "px"},
        {"key": "rre-card-bar-max", "label": "柱子最大宽度", "type": "px"},
    ]},
    {"key": "shape", "label": "圆角与投影", "desc": "999 表示两端全圆（胶囊形）", "tokens": [
        {"key": "radius-card", "label": "卡片圆角", "type": "px"},
        {"key": "rre-radius-panel", "label": "面板圆角（头部、结论框）", "type": "px"},
        {"key": "rre-radius-tile", "label": "辅助数值底块圆角", "type": "px"},
        {"key": "rre-radius-chip", "label": "标签圆角", "type": "px"},
        {"key": "rre-radius-control", "label": "筛选控件圆角", "type": "px"},
        {"key": "rre-radius-pill", "label": "按钮圆角", "type": "px"},
        {"key": "rre-data-radius", "label": "柱子圆角", "type": "px"},
        {"key": "shadow-card", "label": "卡片投影", "type": "text"},
    ]},
    {"key": "page", "label": "页面选项", "desc": "报告整体版式", "tokens": [
        {"key": "header_style", "label": "报告头部", "type": "choice",
         "options": [{"value": "banner", "label": "主题色横幅"}, {"value": "plain", "label": "白色简洁"}]},
        {"key": "toc", "label": "显示左侧目录", "type": "bool"},
        {"key": "max_width", "label": "页面最大宽度", "type": "px"},
        {"key": "custom_css", "label": "自定义 CSS（技术人员）", "type": "code", "advanced": True},
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
    """layers: [(level, rule_name, theme)]，按 内置默认 → 通用 → 场景 顺序。返回 (生效值, 每项来源)。"""
    value, _ = resolve()
    source = {k: {"level": "default", "rule": "内置默认"} for k in value}
    for level, rule_name, theme in layers:
        resolved, _ = resolve(theme)
        for k in (theme or {}):
            kk = LEGACY_ALIASES.get(str(k).lstrip("-"), str(k).lstrip("-"))
            if kk in resolved:
                value[kk] = resolved[kk]
                source[kk] = {"level": level, "rule": rule_name}
    return value, source
