#!/usr/bin/env python3
"""单文件报告保存门禁：静态扫描 + 绑定文件哈希的人工/浏览器复核记录；仅依赖标准库。"""
from __future__ import annotations

import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys

SURFACES = ('prose', 'cards', 'tables', 'charts', 'formatters', 'empty')
BEHAVIORS = ('facts', 'filters_reset', 'sign_threshold_boundaries', 'missing_zero_duplicate_scale',
             'offline', 'desktop_narrow')
STATES = ('up', 'down', 'neutral', 'met', 'miss', 'warning')
EXPAND_MARKERS = ('data-srg-snapshot', 'data-srg-vendor', 'data-srg-asset', 'data-srg-style')
# 仅固定版本的纯库脚本可跳过应用格式化扫描；改名、data 属性及未知摘要均不能豁免。
VENDOR_HASHES = {'52958d5fee0a05e911c340b414836f8a23c383b24fd69fdc2a8a2bff2b362692'}
BANNED = re.compile(r'百分点|(?<![\w])(?:pp|pct)(?![\w])|(?<=[\d%])(?:pp|pct)(?![a-z])', re.I)
FORMATTERS = re.compile(r'\b(?:toFixed|toPrecision|toLocaleString)\s*\(|\bIntl\s*(?:\.|\[)|\bNumberFormat\b')
NETWORK = re.compile(r'\b(?:fetch|XMLHttpRequest|WebSocket|EventSource|Worker|importScripts|sendBeacon)\b|\bimport\s*(?:\(|[\w{*])')
COLOR_LITERAL = re.compile(r'#[\da-f]{3,8}\b|\b(?:rgba?|hsla?)\s*\(|[\'"](?:red|green|blue)[\'"]', re.I)
# 只把字符串中的百分号视为格式逻辑，保留绘图坐标可能使用的取模运算。
JS_STRING = re.compile(r"'(?:(?:\\.)|[^'\\])*'|\"(?:(?:\\.)|[^\"\\])*\"|`(?:(?:\\.)|[^`\\])*`", re.S)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


class Document(HTMLParser):
    """分离展示文字、业务 JSON、库代码和应用代码，避免 CSS 百分比与源字段产生误报。"""
    def __init__(self, text: str):
        super().__init__(convert_charrefs=True)
        self.scripts: list[dict] = []
        self.styles: list[str] = []
        self.style_tags: list[dict] = []
        self.visible: list[str] = []
        self.errors: list[str] = []
        self.ids: set[str] = set()
        self.current: dict | None = None
        self.current_style: dict | None = None
        self.in_style = False
        self.feed(text)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get('id'):
            if attrs['id'] in self.ids:
                self.errors.append(f'R-06 重复 id：{attrs["id"]}')
            self.ids.add(attrs['id'])
        if tag == 'script':
            self.current = {'attrs': attrs, 'text': ''}
            self.scripts.append(self.current)
            if 'src' in attrs:
                self.errors.append('R-06 脚本必须内联原文，不能用 data URL 绕过代码审计')
        if tag == 'style':
            self.current_style = {'attrs': attrs, 'text': ''}
            self.style_tags.append(self.current_style)
            self.in_style = True
        if any(key.lower().startswith('on') for key in attrs):
            self.errors.append('R-08 禁止行内事件脚本；请放入统一应用脚本')
        # 普通超链接不属于子资源；样式、脚本、图片和嵌套文档必须内嵌。
        for key in ('src', 'srcset', 'poster', 'data', 'action', 'formaction', 'srcdoc'):
            if key in attrs and not (key in ('src', 'poster') and attrs[key].startswith('data:')):
                self.errors.append(f'R-06 非内嵌资源/行为：{tag}.{key}')
        if tag == 'link' and attrs.get('href') and not attrs['href'].startswith('data:'):
            self.errors.append('R-06 link 资源未内嵌')
        if tag in ('use', 'image') and any(not value.startswith(('data:', '#'))
                for key, value in attrs.items() if key in ('href', 'xlink:href')):
            self.errors.append('R-06 SVG 资源未内嵌')
        # 样式属性中的 % 是布局单位，不做数字符号扫描；可访问名称和标题仍属展示面。
        for key in ('title', 'aria-label', 'alt', 'placeholder', 'value'):
            if key in attrs:
                self.visible.append(attrs[key])
        if 'style' in attrs:
            self.styles.append(attrs['style'])
            if any(f'srg-{state}' in attrs.get('class', '').split() for state in STATES) and re.search(
                    r'(?:^|;)\s*color\s*:', attrs['style'], re.I):
                self.errors.append('R-09 语义状态元素禁止用行内 color 覆盖统一 class')
        if tag in ('iframe', 'object', 'embed', 'base'):
            self.errors.append(f'R-06 不支持嵌套文档或 base：{tag}')
        if tag == 'meta' and attrs.get('http-equiv', '').lower() == 'refresh':
            self.errors.append('R-06 禁止自动跳转')

    def handle_endtag(self, tag):
        if tag == 'script':
            self.current = None
        if tag == 'style':
            self.in_style = False
            self.current_style = None

    def handle_data(self, data):
        if self.current is not None:
            self.current['text'] += data
        elif self.in_style:
            self.styles.append(data)
            if self.current_style is not None:
                self.current_style['text'] += data
        else:
            self.visible.append(data)


def audit_html(text: str, *, expanded: bool = False, fixture: bool = False, runtime_hash: str | None = None) -> list[str]:
    doc = Document(text)
    errors = list(doc.errors)
    contracts = [s for s in doc.scripts if s['attrs'].get('id') == 'srg-display-contract']
    contract = {}
    if len(contracts) != 1 or contracts[0]['attrs'].get('type') != 'application/json':
        errors.append('R-07 缺少唯一 JSON 展示契约 srg-display-contract')
    else:
        try:
            contract = json.loads(contracts[0]['text'])
            if not isinstance(contract, dict):
                raise ValueError('契约不是对象')
        except (ValueError, TypeError):
            contract = {}
            errors.append('R-07 展示契约 JSON 无效')
    if contract.get('version') not in (1, 2) or contract.get('rate_symbols') not in ('percent-only', 'semantic'):
        errors.append('R-07 契约版本或 rate_symbols 无效')
    if contract.get('fixture') and not fixture:
        errors.append('R-10 正式门禁禁止夹具标记；仅独立示例可显式使用 --fixture')
    sources = contract.get('rule_sources')
    if not isinstance(sources, list) or not sources or any(not isinstance(s, str) or not s.strip() for s in sources):
        errors.append('R-01 契约必须记录生效规则来源/ID')
    states = STATES if contract.get('version') == 2 else STATES[:-1]
    colors = contract.get('colors')
    if not isinstance(colors, dict) or any(not re.fullmatch(r'#[\da-fA-F]{6}', str(colors.get(s, ''))) for s in states):
        errors.append('R-09 缺少契约要求的六位状态色值')
    canonical = Path(__file__).with_name('report-formatters.js').read_text(encoding='utf-8').strip()
    helpers = [s for s in doc.scripts if s['attrs'].get('id') == 'srg-formatters']

    def _valid_helper(script: dict, *, allow_placeholder: bool) -> bool:
        hattrs = script['attrs']
        htext = script['text'].strip()
        asset_ref = hattrs.get('data-srg-asset')
        htype = hattrs.get('type', '').split(';', 1)[0].strip().lower()
        if 'src' in hattrs or htype not in ('', 'text/javascript', 'application/javascript'):
            return False
        if htext == canonical and asset_ref in (None, 'embedded'):
            return True
        if allow_placeholder and asset_ref == 'report-formatters.js' and htext == '':
            return True
        return False

    if len(helpers) != 1 or not _valid_helper(helpers[0], allow_placeholder=not expanded):
        if len(helpers) == 1 and expanded and helpers[0]['attrs'].get('data-srg-asset') == 'report-formatters.js':
            errors.append('R-08 展开文件必须原文内联 report-formatters.js，不能保留未展开的 data-srg-asset')
        else:
            errors.append('R-08 必须原文内联唯一 report-formatters.js，禁止局部改写或伪造入口')
    elif contracts and doc.scripts.index(contracts[0]) > doc.scripts.index(helpers[0]):
        errors.append('R-08 展示契约必须位于格式化模块之前')
    if contract.get('rate_symbols') == 'percent-only':
        # 逐节点及合并文本都扫描，捕捉“百<span>分</span>点”等拆分文案。
        if any(BANNED.search(s) for s in doc.visible) or BANNED.search(''.join(doc.visible)):
            errors.append('R-07 展示文字/属性包含 pp、百分点或 pct')
    for style_tag in doc.style_tags:
        sattrs, stext = style_tag['attrs'], style_tag['text'].strip()
        if any(m in sattrs for m in ('data-srg-snapshot', 'data-srg-vendor', 'data-srg-asset')):
            errors.append('R-06 <style> 标签不支持脚本展开属性')
        if 'data-srg-style' in sattrs:
            style_ref = (sattrs.get('data-srg-style') or '').strip()
            if not style_ref:
                errors.append('R-06 data-srg-style 缺少样式引用值')
            elif expanded:
                if style_ref != 'embedded' or not stext:
                    errors.append('R-06 展开文件仍有未展开的 data-srg-style 引用')
            else:
                if style_ref != 'embedded' and stext:
                    errors.append('R-06 源文档待展开的 data-srg-style 标签正文必须为空')
                elif style_ref == 'embedded' and not stext:
                    errors.append('R-06 data-srg-style="embedded" 样式内容为空')
    snapshot_count = 0
    for script in doc.scripts:
        attrs, code = script['attrs'], script['text']
        if len([m for m in EXPAND_MARKERS if m in attrs]) > 1:
            errors.append('R-06 单个标签不能同时使用多种 data-srg 展开属性')
        if 'data-srg-style' in attrs:
            errors.append('R-06 data-srg-style 只能用于 <style> 标签')
        if 'data-srg-asset' in attrs and attrs.get('id') != 'srg-formatters':
            errors.append('R-08 data-srg-asset 仅允许用于 id="srg-formatters" 的格式化脚本')
        if attrs.get('type') == 'application/json':
            if 'data-srg-vendor' in attrs or 'data-srg-asset' in attrs:
                errors.append('R-06 运行时/格式化模块不能使用 application/json 类型')
            # 原始 JSON 字段名、单位与规则原文不当作已展示的标签；绑定后的输出仍须浏览器复核。
            if attrs.get('data-srg-snapshot'):
                snapshot_count += 1
                if expanded and attrs['data-srg-snapshot'] != 'embedded':
                    errors.append('R-06 展开文件仍有快照引用')
                if expanded:
                    try:
                        payload = json.loads(code)
                        blocks = payload.get('data', {}) if runtime_hash and attrs.get('id') == 'srg-data' else {'snapshot': payload}
                        if not blocks or any(not isinstance(p.get('columns'), list) or not isinstance(p.get('rows'), list)
                                             for p in blocks.values()):
                            raise ValueError('快照结构无效')
                    except (ValueError, AttributeError):
                        errors.append('R-06 展开文件仍有空引用或无效快照')
            continue
        if attrs.get('id') == 'srg-formatters' and _valid_helper(script, allow_placeholder=not expanded):
            continue
        script_type = attrs.get('type', '').split(';', 1)[0].strip().lower()
        if 'data-srg-vendor' in attrs:
            vendor_ref = attrs.get('data-srg-vendor')
            valid_vtype = 'src' not in attrs and script_type in ('', 'text/javascript', 'application/javascript')
            if not expanded and vendor_ref == 'echarts' and not code.strip() and valid_vtype:
                continue
            if vendor_ref == 'embedded' and digest(code.strip()) in VENDOR_HASHES and valid_vtype:
                continue
            if expanded and vendor_ref == 'echarts':
                errors.append('R-06 展开文件仍有未展开的内置运行时引用 data-srg-vendor')
            else:
                errors.append('R-06 非法或伪造的 data-srg-vendor 内置运行时引用')
        elif 'data-srg-asset' not in attrs and digest(code.strip()) in VENDOR_HASHES:
            continue
        if helpers and doc.scripts.index(script) < doc.scripts.index(helpers[0]):
            errors.append('R-08 应用脚本必须位于格式化模块之后')
        if runtime_hash and digest(code.strip()) == runtime_hash:
            continue  # 仅服务端自身的编译运行时可以按固定摘要跳过通用应用脚本扫描。
        names = r'(?:fmtNum|fmtPct|fmtDelta|metricState|applyMetric|stateColor)'
        if FORMATTERS.search(code) or re.search(rf'\bfunction\s+{names}\b|\b(?:const|let|var)\s+{names}\s*=', code):
            errors.append('R-08 应用脚本存在第二套数字格式化/入口定义')
        # 只放行布局属性的字面百分比。裸字符串、插值及数字文本拼接仍经过统一格式化检查。
        pct = r"""['"][+-]?\d+(?:\.\d+)?%['"]"""
        layout = rf"\b(?:left|right|top|bottom|width|height|radius|center|barWidth)\s*:\s*(?:\[\s*{pct}\s*,\s*{pct}\s*\]|{pct})(?=\s*[,}}])"
        layout_free = re.sub(layout, "", code)
        literals = JS_STRING.findall(layout_free)
        if any('%' in s and s[1:-1] != '单位：%' for s in literals) or re.search(
                r'\\(?:x25|u0025|u\{0*25\})|fromCharCode\s*\(\s*37\s*\)', code, re.I):
            errors.append('R-08 应用脚本包含硬编码/转义 %；请调用统一入口')
        if re.search(r'\bunit\s*:\s*[\'"](?:%|pp|pct)[\'"]', code, re.I):
            errors.append('R-08 禁止用 fmtNum 的 unit 绕过率值格式化')
        if contract.get('rate_symbols') == 'percent-only' and BANNED.search(code):
            errors.append('R-07 应用脚本包含禁用率差符号')
        if NETWORK.search(code):
            errors.append('R-06 应用脚本包含网络/动态加载能力')
        # 阴影等纯装饰属性不表达好坏，系列和文案的状态色仍须使用统一入口。
        decoration = re.sub(r"""\b(?:shadowColor|borderColor|backgroundColor)\s*:\s*(['"])(?:#[\da-fA-F]{3,8}|rgba?\([^'"]+\))\1""", "", code)
        decoration = re.sub(r"""\b(?:axisLine|splitLine|axisTick)\s*:\s*\{\s*lineStyle\s*:\s*\{\s*color\s*:\s*(['"])(?:#[\da-fA-F]{3,8}|rgba?\([^'"]+\))\1""", "", decoration)
        if COLOR_LITERAL.search(decoration):
            errors.append('R-09 应用脚本硬编码颜色；请使用 stateColor 或已定义 CSS token')
    if snapshot_count == 0 and not (fixture and contract.get('fixture')):
        errors.append('R-06 正式报告缺少快照数据块；旧内联数据需迁移为带引用的快照块')
    for css in doc.styles:
        if re.search(r'@import|url\s*\(\s*[\'"]?\s*(?!data:|#)[^\s\'"\)]', css, re.I):
            errors.append('R-06 CSS 含非内嵌资源')
    # 状态类固定为变量引用；自定义主题只调整契约中的色值，不能另写章节颜色。
    css = ''.join(doc.styles)
    if re.search(r'--srg-(?:up|down|neutral|met|miss|warning)\s*:', css):
        errors.append('R-09 状态色值只能在展示契约定义，CSS 不得重复定义')
    for state in states:
        pattern = rf'\.srg-{state}\s*\{{\s*color\s*:\s*var\(--srg-{state}\)\s*;?\s*\}}'
        if not re.search(pattern, css):
            errors.append(f'R-09 缺少统一状态类 .srg-{state}')
        for declarations in re.findall(rf'\.srg-{state}\b[^{{}}]*\{{([^{{}}]*)\}}', css):
            for value in re.findall(r'(?:^|;)\s*color\s*:\s*([^;}}]+)', declarations, re.I):
                if value.strip() != f'var(--srg-{state})':
                    errors.append(f'R-09 .srg-{state} 存在冲突的颜色覆盖')
    return list(dict.fromkeys(errors))


def review_template(text: str, source: str) -> dict:
    return {'html_sha256': digest(text), 'source_sha256': digest(source),
            'surfaces': {key: {'status': 'pending', 'evidence': ''} for key in SURFACES},
            'behaviors': {key: {'status': 'pending', 'evidence': ''} for key in BEHAVIORS}}


def audit_review(review: dict, text: str, source: str) -> list[str]:
    errors = []
    if not isinstance(review, dict):
        return ['R-10 复核记录必须为 JSON 对象']
    if review.get('html_sha256') != digest(text) or review.get('source_sha256') != digest(source):
        errors.append('R-10 文件已变化或复核记录未绑定当前源文档/展开文件')
    for group, keys in (('surfaces', SURFACES), ('behaviors', BEHAVIORS)):
        entries = review.get(group)
        for key in keys:
            entry = entries.get(key) if isinstance(entries, dict) else None
            if not isinstance(entry, dict) or entry.get('status') not in ('pass', 'na') or not isinstance(entry.get('evidence'), str) or not entry['evidence'].strip():
                errors.append(f'R-10 未完成复核：{group}.{key}')
            elif entry['status'] == 'na' and (group == 'behaviors' or key not in ('cards', 'tables', 'charts')):
                errors.append(f'R-10 此复核项不可跳过：{group}.{key}')
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('html', type=Path)
    parser.add_argument('--phase', choices=('source', 'expanded'), default='expanded')
    parser.add_argument('--source', type=Path, help='预览时提交的含引用源文档，展开后门禁必填')
    parser.add_argument('--review', type=Path, help='完成的六类落地面及行为复核 JSON')
    parser.add_argument('--write-review', type=Path, help='生成待填写复核记录，不代表门禁通过')
    parser.add_argument('--fixture', action='store_true', help='仅验证独立交互夹具，不能用于正式报告保存')
    args = parser.parse_args()
    try:
        text = args.html.read_text(encoding='utf-8')
        source = args.source.read_text(encoding='utf-8') if args.source else text
        errors = audit_html(text, expanded=args.phase == 'expanded', fixture=args.fixture)
        if args.phase == 'expanded':
            if not args.source:
                errors.append('R-10 展开后门禁必须提供 --source')
            else:
                errors.extend(audit_html(source, fixture=args.fixture))
            if args.write_review:
                # 不覆盖已有证据，避免重跑时悄悄抹去未解决项。
                with args.write_review.open('x', encoding='utf-8') as handle:
                    json.dump(review_template(text, source), handle, ensure_ascii=False, indent=2)
            if not args.review:
                errors.append('R-10 缺少 --review 完成记录；生成空白记录不代表通过')
            else:
                errors.extend(audit_review(json.loads(args.review.read_text(encoding='utf-8')), text, source))
        result = {'ok': not errors, 'phase': args.phase, 'fixture': args.fixture, 'html_sha256': digest(text),
                  'source_sha256': digest(source), 'errors': list(dict.fromkeys(errors))}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['ok'] else 1
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({'ok': False, 'errors': [f'R-10 审计未完成：{exc}']}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    sys.exit(main())
