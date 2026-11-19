"""服务端 Chromium 验收。证据由固定运行时与查询快照生成。"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP, localcontext
from itertools import product
import json
from math import isfinite, prod
from os import getenv
from threading import BoundedSemaphore

from .report_spec import ReportDefinition, ReportSpec, _project

_slots = BoundedSemaphore(2)


def _decimal(value):
    if isinstance(value, dict):
        return Decimal(value["$decimal"])
    return Decimal(str(value)) if type(value) in (int, float) else None


def _expect(value, fmt, rate_symbols):
    raw = _decimal(value)
    if fmt.kind == "text":
        if raw is None:
            return ("—" if value is None else str(value).lower() if type(value) is bool else str(value), "neutral")
        # 数字类型的类别只作标签，不参与业务计算。
        fmt = fmt.model_copy(update={"kind": "number", "digits": 0})
    if raw is None:
        return "—", "neutral"
    direction = 1 if fmt.direction == "higher" else -1
    state = "neutral"
    if fmt.signal == "trend" and raw:
        state = "up" if raw * direction > 0 else "down"
    elif fmt.signal == "attainment":
        if (raw >= Decimal(fmt.threshold) if direction > 0 else raw <= Decimal(fmt.threshold)):
            state = "met"
        elif fmt.warning_threshold is not None and (raw >= Decimal(fmt.warning_threshold)
                if direction > 0 else raw <= Decimal(fmt.warning_threshold)):
            state = "warning"
        else:
            state = "miss"
    with localcontext() as ctx:
        ctx.prec = max(80, len(str(raw)) + abs(raw.adjusted()) + 16)
        converted = raw * (100 if fmt.scale == "decimal" else 1) / fmt.divisor
        value = converted.quantize(Decimal(1).scaleb(-fmt.digits), rounding=ROUND_HALF_UP)
    signed = fmt.signed or fmt.kind in ("delta", "rate_delta")
    if not value:
        value = value.copy_abs()
    sign = "+" if signed and value > 0 else ""
    suffix = (("pp" if fmt.kind == "rate_delta" and rate_symbols == "semantic" else "%")
              if fmt.kind in ("rate", "rate_delta") else fmt.unit)
    return f"{sign}{value:,.{fmt.digits}f}{suffix}", state


def _equal(left, right):
    return type(left) is type(right) and left == right or type(left) in (int, float) and type(right) in (int, float) and left == right


def _matches(row, by, selected):
    return all(selected[key] is None or _equal(row[field], selected[key]) for key, field in by.items())


def _waterfall_expected(card, facts, rate_symbols, colors):
    # 独立用 Decimal 对账并确定标签顺序，覆盖运行时的 BigInt 实现。
    category, amount = card.columns
    config = card.waterfall
    role_field = config.role_field or category.field
    first = [row for row in facts if row[role_field] == config.opening]
    last = [row for row in facts if row[role_field] == config.closing]
    if len(first) != 1 or len(last) != 1 or len(facts) > card.limit:
        return None
    middle = [row for row in facts if row[role_field] == config.change] if config.role_field else [
        row for row in facts if row[role_field] not in (config.opening, config.closing)]
    if len(middle) + 2 != len(facts) or len({str(row[category.field]) for row in facts}) != len(facts):
        return None
    values = [(row, _decimal(row[amount.field])) for row in middle]
    opening, closing = _decimal(first[0][amount.field]), _decimal(last[0][amount.field])
    if opening is None or closing is None or any(raw is None for _, raw in values):
        return None
    values.sort(key=lambda entry: 0 if entry[1] > 0 else 2 if entry[1] < 0 else 1)
    unit = amount.format.divisor
    series = []
    def record(row, raw, start, end, role):
        direction = "up" if raw > 0 else "down" if raw < 0 else "neutral"
        lookup = role == "step" and direction != "neutral"
        kind = "rate_delta" if amount.format.kind == "rate" and role == "step" else amount.format.kind
        fmt = amount.format.model_copy(update={"kind": kind, "signed": role == "step"})
        multiplier = 100 if fmt.scale == "decimal" else 1
        displayed, _ = _expect(row[amount.field], fmt, rate_symbols)
        return [str(row[category.field]), role if role == "total" else direction, displayed,
                (colors[direction] if lookup else config.total_color).lower(),
                "bottom" if lookup and direction == "up" else "top",
                float(start) * multiplier / unit, float(end) * multiplier / unit]

    with localcontext() as ctx:
        ctx.prec = 3300  # 包含由快照格式支持的十进制数位与规模。
        current = opening
        series.append(record(first[0], opening, 0, opening, "total"))
        for row, raw in values:
            after = current + raw
            series.append(record(row, raw, current, after, "step"))
            current = after
        if current != closing:
            return None
        series.append(record(last[0], closing, 0, closing, "total"))
    return series if all(isfinite(row[-2]) and isfinite(row[-1]) for row in series) else None


def _check_bridge_render(host, expected, with_labels, same):
    # 直接读 ECharts 的绘制元素与受管提示回调，Canvas 内文本不属于正文 DOM。
    drawn = host.locator(".srg-chart").evaluate("""node => {
        const chart = echarts.getInstanceByDom(node);
        chart.getZr().flush();
        const option = chart.getOption();
        const series = chart.getModel().getSeriesByIndex(0);
        const points = series.getData();
        return option.series[0].data.map((point, index) => {
            const group = points.getItemGraphicEl(index);
            const rect = group?.children().find(element => element.type === 'rect');
            const label = group?.children().find(element => element.type === 'text');
            return [rect?.style.fill?.toLowerCase(), label?.style.text ?? null,
                label ? (label.style.y >= rect.shape.y + rect.shape.height ? 'bottom' :
                    label.style.y < rect.shape.y ? 'top' : 'inside') : null,
                option.tooltip[0].formatter({name:point.name,data:point})];
        });
    }""")
    same(drawn, [[color, label if with_labels else None, position if with_labels else None, name + ' ' + label]
                 for name, _, label, color, position, _, _ in expected], "瀑布图实际绘制及提示")


def _check_free_layout(page):
    from .report_free import _author_text

    # 数值节点和输出圈定为编译器创建的槽位，其他 DOM 文本继续核查。
    hidden = page.locator(".srg-free :is(srg-val,.srg-card-output,.srg-chart,canvas,select,#srg-reset)").evaluate_all(
        """nodes => nodes.some(node => {
            const rect = node.getBoundingClientRect();
            return !rect.width || !rect.height || rect.left < -1 || rect.right > innerWidth + 1
                || getComputedStyle(node).visibility !== 'visible'
                || parseFloat(getComputedStyle(node).fontSize) < 1
                || !node.checkVisibility({opacityProperty:true,visibilityProperty:true});
        })""")
    if hidden:
        raise ValueError("浏览器对账失败：存在不可见或超出页面的自由版式数据槽")
    text = page.locator(".srg-free").evaluate("""node => {
        const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
        const visible = [];
        for (let item = walker.nextNode(); item; item = walker.nextNode()) {
            if (!item.parentElement.closest('srg-val,.srg-card-output,#srg-controls')) visible.push(item.data);
            else visible.push('\u007c');
        }
        return visible.join('');
    }""")
    _author_text(text, "浏览器正文")


def verify(page_html: str, spec: ReportDefinition, snapshots: dict, dataset_id: int) -> dict:
    from playwright.sync_api import Error, sync_playwright
    from .report_spec import audit_module

    data, _ = _project(spec, snapshots, dataset_id)
    rows = {key: [dict(zip(v["columns"], row)) for row in v["rows"]] for key, v in data.items()}
    if not _slots.acquire(timeout=30):
        raise ValueError("浏览器验收繁忙，请稍后重试")
    errors, requests, checked = [], [], 0
    free = not isinstance(spec, ReportSpec)
    unverified_bridges = {output_id for output_id, card in spec.card_outputs() if free and card.type == "waterfall"}
    try:
        with sync_playwright() as browser_api:
            browser = browser_api.chromium.launch(executable_path=getenv("SRG_CHROMIUM_PATH", "/usr/bin/chromium"),
                                                  args=["--disable-dev-shm-usage"], timeout=15000)
            try:
                context = browser.new_context(viewport={"width": 1280, "height": 850}, service_workers="block", offline=True)
                context.route("**/*", lambda route: route.abort())
                page = context.new_page()
                page.set_default_timeout(5000)
                page.on("request", lambda request: requests.append(request.resource_type))
                page.on("pageerror", lambda error: errors.append(str(error)[:300]))
                page.on("console", lambda message: errors.append(message.text[:300]) if message.type == "error" else None)
                page.set_content(page_html, wait_until="load", timeout=12000)
                page.locator("html[data-srg-ready=true]").wait_for(state="attached")
                defaults = {f.id: (f.default if any(_equal(row[f.field], f.default) for row in rows[f.query]) else None)
                            for f in spec.filters}
                selected = dict(defaults)

                def same(actual, expected, label):
                    if actual != expected:
                        raise ValueError(f"浏览器对账失败：{label}")

                def check():
                    nonlocal checked
                    actual = page.locator("[data-metric]").evaluate_all(
                        "(nodes) => nodes.map(n => [n.dataset.metric,Boolean(n.dataset.metricState),n.textContent,[...n.classList].find(c => /^srg-(up|down|neutral|met|miss|warning)$/.test(c)),getComputedStyle(n).color])")
                    palette = page.evaluate("""() => Object.fromEntries(Object.entries(JSON.parse(
                        document.getElementById('srg-display-contract').textContent).colors).map(([key, color]) =>
                        [key, 'rgb(' + [1,3,5].map(i => parseInt(color.slice(i,i+2),16)).join(', ') + ')']))""")
                    expected = {}
                    for key, metric in spec.metrics.items():
                        axes = {f for binding in metric.sources for f in binding.by if selected[f] is not None}
                        binding = next((s for s in metric.sources if set(s.by) == axes), None)
                        facts = [row[binding.field] for row in rows[binding.query]
                                 if _matches(row, binding.by, selected) and all(_equal(row[k], v) for k, v in binding.where.items())] if binding else []
                        expected[key] = _expect(facts[0] if len(facts) == 1 else None, metric.format, spec.rate_symbols)
                    same({result[0] for result in actual}, set(expected), "指标节点")
                    state_labels = {"up": "向好", "down": "承压", "neutral": "无变化", "met": "达标",
                                    "warning": "关注", "miss": "未达标"}
                    for key, is_status, label, css, color in actual:
                        value, state = expected[key]
                        if is_status and value != "—":
                            value = (state_labels[state] if spec.metrics[key].format.signal == "trend" or
                                     state != "neutral" else "—")
                        same((label, css, color), (value, "srg-" + state, palette[state]), key)
                    for output_id, card in spec.card_outputs():
                        host = page.locator("#" + output_id)
                        facts = [row for row in rows[card.query] if _matches(row, card.by, selected)]
                        if card.type == "waterfall":
                            colors = page.evaluate("() => JSON.parse(document.getElementById('srg-display-contract').textContent).colors")
                            expected_path = _waterfall_expected(card, facts, spec.rate_symbols, colors)
                            if expected_path is None:
                                same(host.locator("[data-empty]").count(), 1, card.title)
                            else:
                                same(host.locator("canvas").count() > 0, True, card.title)
                                actual_path = host.locator(".srg-chart").evaluate("""node => echarts.getInstanceByDom(node)
                                    .getOption().series[0].data.map(d => [d.name, d.kind, d.displayed,
                                        d.itemStyle.color.toLowerCase(), d.label.position, d.value[1], d.value[2]])""")
                                same(actual_path, expected_path, card.title)
                                if free:
                                    _check_bridge_render(host, expected_path, card.waterfall.labels, same)
                                    unverified_bridges.discard(output_id)
                        elif card.type == "table" and facts:
                            expect = [[(shown, palette[state]) for shown, state in [
                                _expect(row[c.field], c.format, spec.rate_symbols) for c in card.columns]]
                                for row in facts[:card.limit]]
                            actual_table = host.locator("tbody tr").evaluate_all(
                                "(rows) => rows.map(r => [...r.cells].map(c => [c.textContent,getComputedStyle(c).color]))")
                            same(actual_table, [[[shown, color] for shown, color in row] for row in expect], card.title)
                        elif card.type != "table" and facts and len(facts) <= card.limit and len({
                                str(row[card.columns[0].field]) for row in facts}) == len(facts):
                            same(host.locator("canvas").count() > 0, True, card.title)
                            series = host.locator(".srg-chart").evaluate("""node => echarts.getInstanceByDom(node)
                                .getOption().series.map(s => s.data.map(d => [d.displayed, d.itemStyle?.color?.toLowerCase() ?? null]))""")
                            colors = page.evaluate("() => JSON.parse(document.getElementById('srg-display-contract').textContent).colors")
                            expected_series = []
                            for col in card.columns[1:]:
                                point_states = [_expect(row[col.field], col.format, spec.rate_symbols) for row in facts]
                                expected_series.append([[shown, colors[state].lower() if state != "neutral" else None]
                                                        for shown, state in point_states])
                            same(series, expected_series, card.title)
                        else:
                            same(host.locator("[data-empty]").count(), 1, card.title)
                    if free:
                        _check_free_layout(page)
                    same(page.locator(".rf-toc a").evaluate_all(
                        "(links) => links.every(a => document.getElementById(a.hash.slice(1)))"), True, "目录")
                    same(page.evaluate("document.documentElement.scrollWidth <= innerWidth"), True, "页面宽度")
                    if spec.rate_symbols == "percent-only" and audit_module().BANNED.search(page.locator("body").inner_text()):
                        raise ValueError("展开后的文案包含当前契约禁用的率差符号")
                    if errors or requests:
                        raise ValueError("离线运行出现浏览器错误或外部请求" + (f"：{errors[0]}" if errors else ""))
                    checked += 1

                check()
                # 小组合逐项测试，大组合测试各维度首尾及复合筛选。
                controls = [page.locator(f"#filter-{f.id}") for f in spec.filters]
                options = [loc.locator("option").evaluate_all("(options) => options.map(o => o.value)") for loc in controls]
                def apply(values):
                    for f, locator, option in zip(spec.filters, controls, values):
                        locator.select_option(option)
                        selected[f.id] = json.loads(option)
                    check()

                if options and prod(map(len, options)) <= 12:
                    for values in product(*options):
                        apply(values)
                elif options:
                    for index, (f, locator) in enumerate(zip(spec.filters, controls)):
                        for option in dict.fromkeys((options[index][0], options[index][-1])):
                            locator.select_option(option)
                            selected[f.id] = json.loads(option)
                            check()
                    apply([values[0] for values in options])
                page.set_viewport_size({"width": 390, "height": 844})
                check()
                if spec.filters:
                    page.locator("#srg-reset").click()
                    selected = dict(defaults)
                    check()
                if unverified_bridges:
                    raise ValueError("浏览器对账失败：瀑布图在已验收筛选状态下缺少可对账的桥接事实")
            finally:
                browser.close()
    except Error as exc:
        # 不把进程命令、路径或业务数据放入浏览器启动错误。
        raise ValueError("服务端 Chromium 验收未完成，请查看部署日志与 Chromium 配置") from exc
    finally:
        _slots.release()
    return {"status": "passed", "engine": "chromium", "states_checked": checked,
            "viewport_widths": [1280, 390], "external_requests": 0, "browser_errors": 0}
