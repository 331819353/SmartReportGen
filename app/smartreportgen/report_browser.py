"""服务端 Chromium 验收。证据由固定运行时与查询快照生成。"""
from __future__ import annotations

import atexit
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, ROUND_HALF_UP, localcontext
from itertools import product
import json
from math import isfinite, prod
from os import getenv
import threading
from threading import BoundedSemaphore

from .report_spec import ReportDefinition, ReportSpec, _project

_WORKERS = max(1, min(8, int(getenv("SRG_BROWSER_WORKERS", "4"))))
_slots = BoundedSemaphore(_WORKERS)
_executor = ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="srg-chromium")
_thread_state = threading.local()
_pool_lock = threading.Lock()
_worker_threads: set[int] = set()
_MAX_BROWSER_USES = 200

_SNAPSHOT_JS = """({cards, free}) => {
  const contractEl = document.getElementById('srg-display-contract');
  const rawColors = contractEl ? JSON.parse(contractEl.textContent).colors : {};
  const palette = Object.fromEntries(Object.entries(rawColors).map(([key, color]) =>
    [key, 'rgb(' + [1, 3, 5].map(i => parseInt(color.slice(i, i + 2), 16)).join(', ') + ')']));
  const metrics = [...document.querySelectorAll('[data-metric]')].map(n => [
    n.dataset.metric,
    Boolean(n.dataset.metricState),
    n.textContent,
    [...n.classList].find(c => /^srg-(up|down|neutral|met|miss|warning)$/.test(c)) ?? null,
    getComputedStyle(n).color,
  ]);
  const outputs = {};
  for (const item of cards) {
    const host = document.getElementById(item.id);
    if (!host) {
      outputs[item.id] = {missing: true, emptyCount: 0, canvasCount: 0};
      continue;
    }
    const emptyCount = host.querySelectorAll('[data-empty]').length;
    const canvasCount = host.querySelectorAll('canvas').length;
    const info = {emptyCount, canvasCount};
    if (item.type === 'table') {
      info.rows = [...host.querySelectorAll('tbody tr')].map(r =>
        [...r.cells].map(c => [c.textContent, getComputedStyle(c).color]));
    } else {
      const chartDom = host.querySelector('.srg-chart');
      const chart = chartDom ? echarts.getInstanceByDom(chartDom) : null;
      if (chart) {
        const option = chart.getOption();
        if (item.type === 'waterfall') {
          info.waterfall = option.series[0].data.map(d => [
            d.name, d.kind, d.displayed, d.itemStyle.color.toLowerCase(), d.label.position, d.value[1], d.value[2]
          ]);
          if (item.free_waterfall) {
            chart.getZr().flush();
            const series = chart.getModel().getSeriesByIndex(0);
            const points = series.getData();
            info.drawn = option.series[0].data.map((point, index) => {
              const group = points.getItemGraphicEl(index);
              const rect = group?.children().find(element => element.type === 'rect');
              const label = group?.children().find(element => element.type === 'text');
              return [
                rect?.style.fill?.toLowerCase() ?? null,
                label?.style.text ?? null,
                label ? (label.style.y >= rect.shape.y + rect.shape.height ? 'bottom' :
                  label.style.y < rect.shape.y ? 'top' : 'inside') : null,
                option.tooltip[0].formatter({name: point.name, data: point}),
              ];
            });
          }
        } else {
          info.series = option.series.map(s =>
            s.data.map(d => [d.displayed, d.itemStyle?.color?.toLowerCase() ?? null]));
        }
      }
    }
    outputs[item.id] = info;
  }
  let freeHidden = false;
  let freeText = null;
  if (free) {
    const slotNodes = document.querySelectorAll('.srg-free :is(srg-val,.srg-card-output,.srg-chart,canvas,select,#srg-reset)');
    freeHidden = [...slotNodes].some(node => {
      const rect = node.getBoundingClientRect();
      return !rect.width || !rect.height || rect.left < -1 || rect.right > innerWidth + 1
        || getComputedStyle(node).visibility !== 'visible'
        || parseFloat(getComputedStyle(node).fontSize) < 1
        || !node.checkVisibility({opacityProperty: true, visibilityProperty: true});
    });
    const freeRoot = document.querySelector('.srg-free');
    if (freeRoot) {
      const walker = document.createTreeWalker(freeRoot, NodeFilter.SHOW_TEXT);
      const visible = [];
      for (let item = walker.nextNode(); item; item = walker.nextNode()) {
        if (!item.parentElement.closest('srg-val,.srg-card-output,#srg-controls')) visible.push(item.data);
        else visible.push('|');
      }
      freeText = visible.join('');
    }
  }
  const tocOk = [...document.querySelectorAll('.rf-toc a')].every(a => document.getElementById(a.hash.slice(1)));
  const widthOk = document.documentElement.scrollWidth <= innerWidth;
  const bodyText = document.body.innerText;
  return {colors: rawColors, palette, metrics, outputs, freeHidden, freeText, tocOk, widthOk, bodyText};
}"""


def _reset_thread_browser() -> None:
    browser = getattr(_thread_state, "browser", None)
    pw = getattr(_thread_state, "pw", None)
    _thread_state.browser = None
    _thread_state.pw = None
    _thread_state.exe = None
    _thread_state.uses = 0
    if browser is not None:
        try:
            browser.close()
        except Exception:
            pass
    if pw is not None:
        try:
            pw.stop()
        except Exception:
            pass


def _get_browser(executable_path: str):
    with _pool_lock:
        _worker_threads.add(threading.get_ident())
    browser = getattr(_thread_state, "browser", None)
    exe = getattr(_thread_state, "exe", None)
    uses = getattr(_thread_state, "uses", 0)
    if browser is not None and (exe != executable_path or not browser.is_connected() or uses >= _MAX_BROWSER_USES):
        _reset_thread_browser()
        browser = None
    if browser is None:
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        try:
            browser = pw.chromium.launch(
                executable_path=executable_path,
                args=["--disable-dev-shm-usage"],
                timeout=15000,
            )
        except Exception:
            try:
                pw.stop()
            except Exception:
                pass
            raise
        _thread_state.pw = pw
        _thread_state.browser = browser
        _thread_state.exe = executable_path
        _thread_state.uses = 0
    _thread_state.uses = getattr(_thread_state, "uses", 0) + 1
    return browser


@atexit.register
def _close_pool() -> None:
    with _pool_lock:
        count = len(_worker_threads)
    if count:
        barrier = threading.Barrier(count)

        def _cleanup() -> None:
            try:
                barrier.wait(timeout=2)
            except threading.BrokenBarrierError:
                pass
            _reset_thread_browser()

        futures = [_executor.submit(_cleanup) for _ in range(count)]
        for future in futures:
            try:
                future.result(timeout=3)
            except Exception:
                pass
    _executor.shutdown(wait=False)


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


def _card_facts(card, rows, selected):
    sources = getattr(card, "sources", None)
    if not sources:
        return [
            row for row in rows[card.query]
            if _matches(row, card.by, selected)
            and all(_equal(row[k], v) for k, v in getattr(card, "where", {}).items())
        ]
    axis = {f for src in sources for f in src.by}
    active = {f for f in axis if selected[f] is not None}
    binding = next((src for src in sources if set(src.by) == active), None)
    if not binding:
        return []
    return [
        row for row in rows[binding.query]
        if _matches(row, binding.by, selected)
        and all(_equal(row[k], v) for k, v in binding.where.items())
    ]


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


def _verify_on_thread(page_html: str, spec: ReportDefinition, rows: dict, executable_path: str) -> dict:
    from playwright.sync_api import Error
    from .report_spec import audit_module

    errors, requests, checked = [], [], 0
    free = not isinstance(spec, ReportSpec)
    outputs = list(spec.card_outputs())
    unverified_bridges = {output_id for output_id, card in outputs if free and card.type == "waterfall"}
    card_meta = [{"id": output_id, "type": card.type, "free_waterfall": free and card.type == "waterfall"}
                 for output_id, card in outputs]
    card_rendered = {output_id: False for output_id, _ in outputs}
    metric_non_missing = {key: False for key in spec.metrics}
    empty_on_default: list[str] = []
    duplicate_on_default: list[str] = []

    try:
        browser = _get_browser(executable_path)
        context = browser.new_context(viewport={"width": 1280, "height": 850}, service_workers="block", offline=True)
        try:
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
                snap = page.evaluate(_SNAPSHOT_JS, {"cards": card_meta, "free": free})
                actual = snap["metrics"]
                palette = snap["palette"]
                colors = snap["colors"]
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
                    if expected[key][0] != "—":
                        metric_non_missing[key] = True
                    elif checked == 0 and key not in empty_on_default:
                        empty_on_default.append(key)

                for output_id, card in outputs:
                    actual_card = snap["outputs"][output_id]
                    facts = _card_facts(card, rows, selected)
                    if card.type == "waterfall":
                        expected_path = _waterfall_expected(card, facts, spec.rate_symbols, colors)
                        if expected_path is None:
                            same(actual_card["emptyCount"], 1, card.title)
                            if checked == 0:
                                empty_on_default.append(card.title)
                                if facts:
                                    duplicate_on_default.append(card.title)
                        else:
                            same(actual_card["canvasCount"] > 0, True, card.title)
                            same(actual_card.get("waterfall"), expected_path, card.title)
                            if free:
                                same(actual_card.get("drawn"),
                                     [[color, label if card.waterfall.labels else None,
                                       position if card.waterfall.labels else None, name + " " + label]
                                      for name, _, label, color, position, _, _ in expected_path],
                                     "瀑布图实际绘制及提示")
                                unverified_bridges.discard(output_id)
                            card_rendered[output_id] = True
                    elif card.type == "table" and facts:
                        expect = [[[shown, palette[state]] for shown, state in [
                            _expect(row[c.field], c.format, spec.rate_symbols) for c in card.columns]]
                            for row in facts[:card.limit]]
                        same(actual_card.get("rows"), expect, card.title)
                        card_rendered[output_id] = True
                    elif card.type != "table" and facts and len(facts) <= card.limit and len({
                            str(row[card.columns[0].field]) for row in facts}) == len(facts):
                        same(actual_card["canvasCount"] > 0, True, card.title)
                        expected_series = []
                        for col in card.columns[1:]:
                            point_states = [_expect(row[col.field], col.format, spec.rate_symbols) for row in facts]
                            expected_series.append([[shown, colors[state].lower() if state != "neutral" else None]
                                                    for shown, state in point_states])
                        same(actual_card.get("series"), expected_series, card.title)
                        card_rendered[output_id] = True
                    else:
                        same(actual_card["emptyCount"], 1, card.title)
                        if checked == 0:
                            empty_on_default.append(card.title)
                            if facts and card.type != "table":
                                duplicate_on_default.append(card.title)
                if free:
                    from .report_free import _author_text

                    if snap["freeHidden"]:
                        raise ValueError("浏览器对账失败：存在不可见或超出页面的自由版式数据槽")
                    _author_text(snap["freeText"] or "", "浏览器正文")
                same(snap["tocOk"], True, "目录")
                same(snap["widthOk"], True, "页面宽度")
                if spec.rate_symbols == "percent-only" and audit_module().BANNED.search(snap["bodyText"]):
                    raise ValueError("展开后的文案包含当前契约禁用的率差符号")
                if errors or requests:
                    raise ValueError("离线运行出现浏览器错误或外部请求" + (f"：{errors[0]}" if errors else ""))
                checked += 1

            check()
            # 小组合逐项测试，大组合测试各维度首尾及复合筛选。
            controls = [page.locator(f"#filter-{f.id}") for f in spec.filters]
            options = page.evaluate("""ids => ids.map(id => {
                const sel = document.getElementById('filter-' + id);
                return sel ? [...sel.options].map(o => o.value) : [];
            })""", [f.id for f in spec.filters]) if spec.filters else []

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
            context.close()
    except Error as exc:
        _reset_thread_browser()
        # 不把进程命令、路径或业务数据放入浏览器启动错误。
        raise ValueError("服务端 Chromium 验收未完成，请查看部署日志与 Chromium 配置") from exc

    always_empty_cards = list(dict.fromkeys(card.title for output_id, card in outputs if not card_rendered[output_id]))
    always_missing_metrics = [key for key, seen in metric_non_missing.items() if not seen]
    empty_default_unique = list(dict.fromkeys(empty_on_default))
    dup_default_unique = list(dict.fromkeys(duplicate_on_default))
    warnings: list[str] = []
    if always_empty_cards:
        warnings.append(f"卡片在全部已验收筛选状态下均为空或未绘图：{'、'.join(always_empty_cards)}")
    if always_missing_metrics:
        warnings.append(f"指标在全部已验收筛选状态下均显示缺数：{'、'.join(always_missing_metrics)}")
    if dup_default_unique:
        warnings.append(f"默认筛选下卡片存在类别重复、超限或桥接不平（建议为卡片配置 sources 或 where）：{'、'.join(dup_default_unique)}")
    remaining_default = [name for name in empty_default_unique if name not in set(always_empty_cards) | set(always_missing_metrics)]
    if remaining_default:
        warnings.append(f"默认初始筛选下存在空卡片或缺失指标：{'、'.join(remaining_default)}")
    return {"status": "passed", "engine": "chromium", "states_checked": checked,
            "viewport_widths": [1280, 390], "external_requests": 0, "browser_errors": 0,
            "empty_on_default": empty_default_unique, "always_empty_cards": always_empty_cards,
            "always_missing_metrics": always_missing_metrics, "warnings": warnings}


def verify(page_html: str, spec: ReportDefinition, snapshots: dict, dataset_id: int) -> dict:
    data, _ = _project(spec, snapshots, dataset_id)
    rows = {key: [dict(zip(v["columns"], row)) for row in v["rows"]] for key, v in data.items()}
    if not _slots.acquire(timeout=30):
        raise ValueError("浏览器验收繁忙，请稍后重试")
    try:
        executable_path = getenv("SRG_CHROMIUM_PATH", "/usr/bin/chromium")
        return _executor.submit(_verify_on_thread, page_html, spec, rows, executable_path).result()
    finally:
        _slots.release()
