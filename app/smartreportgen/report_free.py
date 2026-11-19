"""自由版式模板：解析作者的 HTML/CSS，编译数据槽位，复用受管报告运行时。"""
from __future__ import annotations

from collections import Counter
from html import escape
from html.parser import HTMLParser
import re
from typing import Annotated, Literal

import tinycss2
from pydantic import Field, model_validator

from .report_queries import Key, StrictModel
from .report_spec import HEX, Card, ReportDefinition

TextHTML = Annotated[str, Field(min_length=1, max_length=32_000)]
TextCSS = Annotated[str, Field(max_length=20_000)]
Size = Annotated[int, Field(ge=0, le=1200)]
Percent = Annotated[str, Field(pattern=r"^(?:\d{1,2}(?:\.\d{1,3})?|100)%$")]
Length = Size | Percent
Opacity = Annotated[float, Field(ge=0, le=1)]
IDENT = re.compile(r"[a-zA-Z_][a-zA-Z0-9_-]{0,79}")
TAGS = frozenset("""
    main header footer section article aside nav div h1 h2 h3 h4 h5 h6 p span strong em b i small
    blockquote cite figure figcaption ul ol li dl dt dd br hr a time mark sup sub
    table caption colgroup col thead tbody tfoot tr th td
""".split())
VOID = {"br", "hr", "col"}
SLOTS = {"srg-val", "srg-output", "srg-module", "srg-filters"}
CHINESE_DIGITS = "零〇一二两三四五六七八九十壹贰叁肆伍陆柒捌玖拾"
CHINESE_VALUE = re.compile(
    rf"[{CHINESE_DIGITS}][{CHINESE_DIGITS}百佰千仟万萬亿億]*(?:元|吨|件|家|人|台|个|成|倍|[%％]|个百分点)"
    rf"|[百千万萬]分之[{CHINESE_DIGITS}]"
)


def _author_text(text, where):
    # 年份、账期和业务数字都可绑定事实字段。HTML 的列宽、CSS 尺寸等结构参数不走此检查。
    compact = re.sub(r"[\s\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]+", "", text)
    if CHINESE_VALUE.search(compact) or any(char.isnumeric() and not char.isalpha() for char in compact):
        raise ValueError(f"{where} 含未绑定数字，请使用数据槽位")


class Grid(StrictModel):
    left: Length | None = None
    right: Length | None = None
    top: Length | None = None
    bottom: Length | None = None
    containLabel: bool | None = None


class TextStyle(StrictModel):
    fontSize: Annotated[int, Field(ge=10, le=48)] | None = None
    color: HEX | None = None


class SeriesStyle(StrictModel):
    borderColor: HEX | None = None
    borderWidth: Size | None = None
    borderRadius: Size | list[Size] | None = None
    shadowBlur: Size | None = None
    shadowColor: HEX | None = None
    opacity: Opacity | None = None


class LineStyle(StrictModel):
    width: Annotated[int, Field(ge=1, le=20)] | None = None
    type: Literal["solid", "dashed", "dotted"] | None = None
    opacity: Opacity | None = None


class SeriesOption(StrictModel):
    itemStyle: SeriesStyle | None = None
    lineStyle: LineStyle | None = None
    areaStyle: SeriesStyle | None = None
    label: TextStyle | None = None
    center: Annotated[list[Length], Field(min_length=2, max_length=2)] | None = None
    radius: Length | list[Length] | None = None
    barMaxWidth: Size | None = None
    symbol: Literal["circle", "rect", "roundRect", "triangle", "diamond", "pin", "arrow"] | None = None
    symbolSize: Size | None = None

    @model_validator(mode="after")
    def validate_shape(self):
        if isinstance(self.radius, list) and len(self.radius) != 2:
            raise ValueError("ECharts 环形 radius 须提供内外两个半径")
        return self


class ChartOption(StrictModel):
    # 原生 ECharts 视觉属性按对象合并。series/data、dataset、formatter 等事实入口由运行时拥有。
    grid: Grid | None = None
    textStyle: TextStyle | None = None
    backgroundColor: HEX | None = None
    series: list[SeriesOption] = Field(default_factory=list, max_length=11)


class FreeOutput(Card):
    option: ChartOption | None = None

    @model_validator(mode="after")
    def validate_option(self):
        if self.type == "kpi":
            raise ValueError("自由版式指标使用 <srg-val>，指标卡外观由 HTML/CSS 决定")
        _author_text(self.title, "图表或表格标题")
        for column in self.columns:
            _author_text(column.label, "字段显示名称")
            _author_text(column.format.unit, "字段单位")
        if self.option:
            if self.type == "table":
                raise ValueError("ECharts option 只适用于图表")
            if self.type == "waterfall" and self.option.series:
                raise ValueError("瀑布图的柱子、标签位置和状态色由系统绘图模块负责")
            if len(self.option.series) > len(self.columns) - 1:
                raise ValueError("ECharts series 视觉设置超出已绑定数值序列")
        return self


class Module(StrictModel):
    html: TextHTML
    css: TextCSS = ""
    outputs: dict[Key, FreeOutput] = Field(default_factory=dict, max_length=12)


class _Fragment(HTMLParser):
    """重建全部允许的标记。作者字符串不会原样进入离线文档。"""
    def __init__(self, html, where, replacements, metrics, ids, links):
        super().__init__(convert_charrefs=True)
        self.where, self.replacements, self.metrics = where, replacements, metrics
        self.ids, self.links = ids, links
        self.stack, self.parts = [], []
        self.used, self.references = Counter(), set()
        self.feed(html)
        self.close()
        if self.stack:
            self.fail(f"缺少 </{self.stack[-1]}>")
        if not "".join(self.parts).strip():
            self.fail("版式不能为空")

    def fail(self, message):
        line, col = self.getpos()
        raise ValueError(f"{self.where} HTML 第 {line} 行第 {col + 1} 列，{message}")

    def handle_starttag(self, tag, attrs):
        if self.stack and self.stack[-1] in SLOTS:
            self.fail("数据槽位必须为空")
        if len(self.stack) >= 40:
            self.fail("HTML 嵌套过深")
        values = dict(attrs)
        if len(values) != len(attrs):
            self.fail("HTML 属性不能重复")
        if tag in SLOTS:
            self.slot(tag, values)
        elif tag in TAGS:
            self.parts.append(f"<{tag}{self.attributes(tag, values)}>")
        else:
            self.fail(f"不支持 <{tag}>")
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        if tag not in VOID:
            self.fail("非空标签须显式书写闭合标签")
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.fail(f"关闭标签 </{tag}> 与当前结构不匹配")
        self.stack.pop()
        if tag not in SLOTS:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        if self.stack and self.stack[-1] in SLOTS:
            if data.strip():
                self.fail("数据槽位必须为空")
            return
        try:
            _author_text(data, "版式正文")
        except ValueError as exc:
            self.fail(str(exc))
        self.parts.append(escape(data, quote=False))

    def handle_decl(self, decl):
        self.fail("只接受 HTML 片段")

    def unknown_decl(self, data):
        self.fail("只接受 HTML 片段")

    def handle_pi(self, data):
        self.fail("只接受 HTML 片段")

    def slot(self, tag, attrs):
        if tag == "srg-val":
            name = attrs.get("m")
            if name not in self.metrics or set(attrs) - {"m", "badge", "state"} or any(
                    attrs[key] not in ("", None) for key in ("badge", "state") if key in attrs):
                self.fail("srg-val 须引用已声明指标，badge 和 state 为可选布尔属性")
            self.references.add(name)
            badge = ' class="srg-badge"' if "badge" in attrs else ""
            state = ' data-metric-state="true"' if "state" in attrs else ""
            self.parts.append(f'<srg-val{badge} data-metric="{name}"{state}>—</srg-val>')
            return
        name = attrs.get("m") if tag != "srg-filters" else ""
        if set(attrs) != ({"m"} if tag != "srg-filters" else set()) or (tag, name) not in self.replacements:
            self.fail(f"<{tag}> 引用了未定义的模块、筛选或数据输出")
        self.used[tag, name] += 1
        self.parts.append(self.replacements[tag, name])

    def attributes(self, tag, attrs):
        parts = []
        for key, value in attrs.items():
            value = value or ""
            valid = False
            if key in {"class", "id"}:
                tokens = value.split()
                valid = bool(tokens) and all(IDENT.fullmatch(token) and not token.startswith(
                    ("rf-", "srg-", "card-", "filter-")) for token in tokens)
                if key == "id":
                    valid = valid and len(tokens) == 1 and value == tokens[0] and value not in self.ids
                    self.ids.add(value)
            elif key in {"aria-label", "title"}:
                _author_text(value, self.where)
                valid = True
            elif tag in {"th", "td"} and key in {"colspan", "rowspan"}:
                valid = bool(re.fullmatch(r"[1-9][0-9]?", value))
            elif tag == "th" and key == "scope":
                valid = value in {"row", "col", "rowgroup", "colgroup"}
            elif key == "href" and tag == "a" and value.startswith("#") and IDENT.fullmatch(value[1:]):
                self.links.add(value[1:])
                valid = True
            if not valid:
                self.fail(f"<{tag}> 的 {key} 属性无效或保留给报告运行时")
            parts.append(f' {key}="{escape(value, quote=True)}"')
        return "".join(parts)


def _check_css_tokens(tokens):
    for token in tokens:
        # 禁止 CSS 自行生成业务文本、引用资源或通过自定义函数间接加载数据。
        if token.type in {"error", "url"} or token.type == "function" and token.lower_name in {
                "url", "src", "attr", "counter", "counters", "image-set", "-webkit-image-set", "image"}:
            raise ValueError("自由版式 CSS 不可引用资源、数字内容或外部属性")
        children = getattr(token, "arguments", getattr(token, "content", None))
        if children is not None:
            _check_css_tokens(children)


def _css(text, scope):
    if "<" in text or "\x00" in text:
        raise ValueError("自由版式 CSS 不支持 HTML 标记")
    tokens = tinycss2.parse_component_value_list(text, skip_comments=True)
    _check_css_tokens(tokens)

    def declarations(content):
        items = tinycss2.parse_declaration_list(content, skip_comments=True, skip_whitespace=True)
        for item in items:
            if item.type != "declaration":
                raise ValueError("CSS 声明无效，嵌套样式请展开")
            name = item.lower_name
            value = tinycss2.serialize(item.value).strip().lower()
            if (name.startswith(("counter-", "--srg-", "list-style", "-webkit-mask")) or name == "all"
                    or name == "content" and value not in ("normal", "none", "''", '""')):
                raise ValueError(f"自由版式 CSS 禁止 {name} 的当前用法")
        return tinycss2.serialize(items)

    def stylesheet(content, depth=0):
        if depth > 8:
            raise ValueError("CSS 条件规则嵌套过深")
        result = []
        for rule in tinycss2.parse_rule_list(content, skip_comments=True, skip_whitespace=True):
            if rule.type == "at-rule" and rule.lower_at_keyword in {"media", "supports", "container"} and rule.content:
                result.append(f"@{rule.lower_at_keyword}{tinycss2.serialize(rule.prelude)}"
                              f"{{{stylesheet(rule.content, depth + 1)}}}")
            elif rule.type == "at-rule" and rule.lower_at_keyword == "keyframes" and rule.content and not scope:
                frames = tinycss2.parse_rule_list(rule.content, skip_comments=True, skip_whitespace=True)
                if any(frame.type != "qualified-rule" for frame in frames):
                    raise ValueError("CSS 关键帧无效")
                body = "".join(f"{tinycss2.serialize(frame.prelude)}{{{declarations(frame.content)}}}" for frame in frames)
                result.append(f"@keyframes{tinycss2.serialize(rule.prelude)}{{{body}}}")
            elif rule.type == "qualified-rule":
                selector = tinycss2.serialize(rule.prelude).strip()
                # CSS 模块作用域沿 DOM 子树生效，不允许补丁重排其他模块。
                selector = _prefix_selectors(rule.prelude, scope) if scope else selector
                result.append(f"{selector}{{{declarations(rule.content)}}}")
            else:
                raise ValueError("自由版式 CSS 仅支持普通样式、响应式条件和页面级关键帧")
        return "\n".join(result)

    output = stylesheet(tokens)
    if "<" in output:
        raise ValueError("自由版式 CSS 不能解码为 HTML 标记")
    return output


def _prefix_selectors(tokens, scope):
    # 只分割顶层逗号，保留 :is() 等伪类参数里的选择器列表。
    parts, current = [], []
    for token in tokens:
        if token.type == "literal" and token.value == ",":
            parts.append(tinycss2.serialize(current).strip())
            current = []
        else:
            current.append(token)
    parts.append(tinycss2.serialize(current).strip())
    if any(not part or part.startswith((">", "+", "~", "|")) for part in parts):
        raise ValueError("模块 CSS 选择器须从模块内元素开始")
    return ", ".join(f":where({scope}) {part}" for part in parts)


class FreeReport(ReportDefinition):
    layout: TextHTML
    css: TextCSS = ""
    modules: dict[Key, Module] = Field(min_length=1, max_length=16)

    def card_outputs(self):
        for name, module in self.modules.items():
            for key, card in module.outputs.items():
                yield self.output_id(name, key), card

    @staticmethod
    def output_id(module, key):
        return f"card-{module}-{key}"

    def render_layout(self, controls=""):
        ids, links, used = set(), set(), set()
        css = [_css(self.css, "")]
        replacements = {("srg-filters", ""): controls} if self.filters else {}
        for name, module in self.modules.items():
            slots = {}
            for key, card in module.outputs.items():
                height = f' style="--srg-card-height:{card.height}px"' if card.height else ""
                slots["srg-output", key] = (
                    f'<div class="srg-card-output" id="{self.output_id(name, key)}"{height}></div>')
            fragment = _Fragment(module.html, f"模块 {name}", slots, self.metrics, ids, links)
            if fragment.used != Counter(slots.keys()):
                raise ValueError(f"模块 {name} 的每个输出槽位须且仅须使用一次")
            used.update(fragment.references)
            replacements["srg-module", name] = f'<div data-srg-module="{name}">{"".join(fragment.parts)}</div>'
            css.append(_css(module.css, f'[data-srg-module="{name}"]'))
        shell = _Fragment(self.layout, "页面布局", replacements, self.metrics, ids, links)
        if shell.used != Counter(replacements.keys()):
            raise ValueError("页面须且仅须放置每个模块及全局筛选槽位一次")
        if links - ids:
            raise ValueError("页面内部链接引用未定义锚点")
        return '<div class="srg-free">' + "".join(shell.parts) + "</div>", "\n".join(filter(None, css)), (
            used | shell.references)

    @model_validator(mode="after")
    def validate_layout(self):
        _author_text(self.title, "报告标题")
        for item in self.filters:
            _author_text(item.label, "筛选器名称")
        for metric in self.metrics.values():
            _author_text(metric.format.unit, "指标单位")
        _, _, used = self.render_layout()
        self.validate_bindings(used, allow_text=True)
        return self
