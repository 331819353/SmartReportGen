"""以离线 Chromium 校验报告 DOM、图表、脚本和交互。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from os import getenv
import re
import threading
from threading import BoundedSemaphore
from typing import Any

from .report_document import enforce_offline_policy, external_references
from .telemetry import measure

_WORKERS = max(1, min(8, int(getenv("SRG_BROWSER_WORKERS", "4"))))
_slots = BoundedSemaphore(_WORKERS)
_executor: ThreadPoolExecutor | None = None
_thread_state = threading.local()
_pool_lock = threading.Lock()
_worker_threads: set[int] = set()
_MAX_BROWSER_USES = 200

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


def close_pool() -> None:
    global _executor
    # 必须在服务退出前把清理任务派回 Playwright 所属线程。普通 atexit 执行时线程池已关闭。
    with _pool_lock:
        executor, _executor = _executor, None
        count = len(_worker_threads)
        _worker_threads.clear()
    if executor is None:
        return
    if count:
        barrier = threading.Barrier(count)

        def _cleanup() -> None:
            try:
                barrier.wait(timeout=2)
            except threading.BrokenBarrierError:
                pass
            _reset_thread_browser()

        futures = [executor.submit(_cleanup) for _ in range(count)]
        for future in futures:
            try:
                future.result(timeout=3)
            except Exception:
                pass
    executor.shutdown()



class ReportVerifyError(ValueError):
    def __init__(self, message: str, stage: str, diagnostics: dict[str, Any]):
        super().__init__(message)
        self.stage = stage
        self.diagnostics = diagnostics


_SECTION_SOURCE = re.compile(r"(?:srg-section-|\[SRG )([A-Za-z_][A-Za-z0-9_-]*)[.\]]")
_FRAME_WAIT = """() => new Promise(resolve =>
  requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(resolve, 25))))"""
_CONTROLS = 'select, button[data-filter], [role="tab"], [type="reset"], [data-reset], #srg-reset'
_RUNTIME_PROBE = """<script>(() => {
  const results = {requests: [], errors: []};
  window.__srgProbe = results;
  // CSP 先于网络路由阻断部分请求。保留 URL 和节点所属的章节，便于从草稿定位。
  addEventListener('securitypolicyviolation', event => {
    const url = event.blockedURI;
    if (!url || ['inline', 'eval', 'wasm-eval'].includes(url)) return;
    const node = event.target instanceof Element ? event.target : null;
    // 浏览器可能合并相同 URL 的资源加载，单次事件也要检查页面中所有对应节点。
    const resources = [...document.querySelectorAll('[src],[href],[poster]')].filter(element =>
      [element.src, element.href, element.poster].includes(url));
    if (node) resources.push(node);
    const sections = new Set(resources.map(element => element.closest('[data-srg-section]')?.dataset.srgSection)
      .filter(Boolean));
    if (!sections.size) sections.add(null);
    for (const section_id of sections) {
      results.requests.push({url, section_id, location: event.sourceFile || event.violatedDirective});
    }
  });
  addEventListener('error', event => {
    if (event.message) results.errors.push({message: event.message, line: event.lineno, location: event.filename});
  });
})();</script>"""
_INSPECT = """expected => {
  function visible(node) {
    if (!node || !node.getClientRects().length) return false;
    return node.checkVisibility({opacityProperty: true, visibilityProperty: true});
  }
  function textVisible(root) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let node; (node = walker.nextNode()); ) {
      const parent = node.parentElement;
      if (!node.textContent.trim() || parent.closest('script,style,template,noscript') || !visible(parent)) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      const rect = range.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) return true;
    }
    return false;
  }
  const sections = new Map([...document.querySelectorAll('[data-srg-section]')]
    .map(node => [node.dataset.srgSection, node]));
  const emptySections = [];
  for (const id of expected) {
    const node = sections.get(id);
    if (!node || !(textVisible(node) || [...node.querySelectorAll('table,svg,canvas')].some(visible))) {
      emptySections.push(id);
    }
  }
  const charts = [...document.querySelectorAll('[_echarts_instance_], .echarts, [data-chart]')];
  const badCharts = charts.filter(node => {
    const chart = window.echarts?.getInstanceByDom(node);
    const box = node.getBoundingClientRect();
    const hasSurface = [...node.querySelectorAll('canvas,svg')].some(surface => {
      const rect = surface.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 &&
        (surface.tagName !== 'CANVAS' || (surface.width > 0 && surface.height > 0));
    });
    // 不比较像素和数据点。检查实例有绘制对象，且图表容器与实际绘图表面有尺寸。
    const unrendered = chart ? !chart.getOption() || !chart.getZr().storage.getDisplayList().length
      : node.matches('[_echarts_instance_], .echarts');
    return unrendered || box.width <= 0 || box.height <= 0 || !hasSurface;
  }).map(node => ({id: node.id || null, section_id: node.closest('[data-srg-section]')?.dataset.srgSection ?? null}));
  return {
    blank: !document.body?.innerText.trim() || !textVisible(document.body),
    emptySections, badCharts, chartCount: charts.length,
    checked: expected.length ? expected : [...sections.keys()],
    emptyTables: [...document.querySelectorAll('table')].filter(table => !table.querySelector('tbody tr')).length,
  };
}"""


def _failure(message: str, **diagnostics) -> ReportVerifyError:
    return ReportVerifyError(message, "chromium.verify", diagnostics)


def duplicate_id_error(duplicates: list[dict], stage: str) -> ReportVerifyError:
    # 保留所有涉及的章节供分派修复，限制单次返回的详细冲突数量。
    ids = list(dict.fromkeys(section_id for item in duplicates for section_id in item["section_ids"]))
    names = "、".join(item["id"] for item in duplicates[:5])
    return ReportVerifyError(f"页面存在重复的元素 id：{names}。各章节请使用独立的 id 前缀", stage,
                             {"duplicate_ids": duplicates[:20], "section_ids": ids, "section_id": ids[0]})


def _section(text: str) -> str | None:
    match = _SECTION_SOURCE.search(text)
    return match[1] if match else None


def _verify_on_thread(page_html: str, expected_sections: list[str] | None, executable_path: str,
                      allow_shared_duplicate_ids: bool) -> dict:
    from playwright.sync_api import Error as BrowserError
    from playwright.sync_api import TimeoutError as BrowserTimeout

    requests, errors = [], []
    observed_html = enforce_offline_policy(page_html, probe=_RUNTIME_PROBE)
    section_lines = [
        (observed_html.count("\n", 0, match.start()) + 1,
         observed_html.count("\n", 0, match.end()) + 1, match[1])
        for match in re.finditer(r'<script data-srg-for="([A-Za-z_][A-Za-z0-9_-]*)">.*?</script>',
                                 observed_html, flags=re.S)
    ]
    try:
        with measure("chromium.launch"):
            browser = _get_browser(executable_path)
            context = browser.new_context(offline=True, service_workers="block",
                                          viewport={"width": 1280, "height": 900}, accept_downloads=False)
    except BrowserError as exc:
        raise _failure("无法启动 Chromium", message_detail=str(exc)) from exc

    def block(route):
        url = route.request.url
        if url.lower().startswith(("data:", "blob:", "about:")):
            route.continue_()
        else:
            requests.append(url)
            route.abort()

    def console(msg):
        if msg.type == "error":
            errors.append({"kind": "console", "message": msg.text, "section_id": _section(
                f"{msg.location.get('url', '')} {msg.text}")})

    def page_error(error):
        stack = error.stack or ""
        errors.append({"kind": "pageerror", "message": str(error), "section_id": _section(stack), "stack": stack})

    def check_runtime(static=(), control=None):
        data = page.evaluate("""checkIds => {
          const ids = new Map();
          // 脚本和筛选操作会改变 DOM。以当前主文档为准，template 内容自然不参与。
          if (checkIds) {
            for (const node of document.querySelectorAll('[id]')) {
              if (!node.id) continue;
              if (!ids.has(node.id)) ids.set(node.id, {id: node.id, count: 0, sectionIds: new Set()});
              const entry = ids.get(node.id);
              entry.count++;
              entry.sectionIds.add(node.closest('[data-srg-section]')?.dataset.srgSection || '__shared__');
            }
          }
          return {
            probe: window.__srgProbe || {errors: [], requests: []},
            wrapped: Array.isArray(window.__srgErrors) ? window.__srgErrors : [],
            duplicates: [...ids.values()].filter(entry => entry.count > 1)
              .map(({id, count, sectionIds}) => ({id, count, section_ids: [...sectionIds]}))
          };
        }""", expected_sections is not None)
        detail = [{"kind": "section", "section_id": item.get("section"), "message": item.get("message"),
                   "stack": item.get("stack")} for item in data["wrapped"] if isinstance(item, dict)]
        for item in data["probe"]["errors"]:
            line = item.get("line") or 0
            section_id = _section(item.get("location") or "")
            if not section_id and item.get("location", "").startswith("about:"):
                section_id = next((ident for start, end, ident in section_lines if start <= line <= end), None)
            detail.append({**item, "kind": "script", "section_id": section_id})
        grouped_requests = list(static)
        known = {(item["url"], item["section_id"]) for item in grouped_requests}
        for item in data["probe"]["requests"]:
            section_id = item["section_id"] or _section(item["location"])
            # 多个章节可能请求同一 URL，按 URL 和章节去重才能完整保留归因。
            key = (item["url"], section_id)
            if key not in known:
                grouped_requests.append({**item, "section_id": section_id})
                known.add(key)
        known_urls = {url for url, _ in known}
        for url in dict.fromkeys(requests):
            if url not in known_urls:
                grouped_requests.append({"url": url, "section_id": None})
        if grouped_requests or errors or detail:
            # 模块包装器的诊断比 Playwright 的全局 console 事件更精确，优先返回。
            diagnostics = {"errors": [*detail, *errors][:20], "external_requests": grouped_requests[:20]}
            if control:
                diagnostics["control"] = control
            ids = list(dict.fromkeys(item["section_id"] for item in [*detail, *errors, *grouped_requests]
                                     if item.get("section_id")))
            if control and control.get("section_id") and control["section_id"] not in ids:
                ids.append(control["section_id"])
            diagnostics["section_ids"] = ids
            diagnostics["section_id"] = ids[0] if ids else None
            raise _failure("报告尝试加载外部资源" if grouped_requests else "报告脚本运行或资源加载出错", **diagnostics)
        duplicates = data["duplicates"]
        if allow_shared_duplicate_ids:
            # 历史整页保留共享骨架已有的重名，新增章节与旧页面相撞仍需修复。
            duplicates = [item for item in duplicates if item["section_ids"] != ["__shared__"]]
        if duplicates:
            error = duplicate_id_error(duplicates, "chromium.verify")
            if control:
                error.diagnostics["control"] = control
            raise error

    try:
        context.route("**/*", block)
        page = context.new_page()
        page.set_default_timeout(3500)
        page.on("pageerror", page_error)
        page.on("console", console)
        with measure("chromium.load"):
            page.set_content(observed_html, wait_until="load", timeout=15000)
            page.evaluate(_FRAME_WAIT)
        with measure("chromium.inspect"):
            check_runtime(external_references(page_html))
            inspection = page.evaluate(_INSPECT, expected_sections or [])
            if inspection["blank"] or inspection["emptySections"] or inspection["badCharts"]:
                empty = inspection["emptySections"]
                charts = inspection["badCharts"]
                ids = list(dict.fromkeys([*empty, *(item["section_id"] for item in charts if item["section_id"])]))
                raise _failure("报告页面、章节或图表未呈现", blank_page=inspection["blank"], empty_sections=empty,
                               invalid_charts=charts, section_ids=ids, section_id=ids[0] if ids else None)

        with measure("chromium.interaction_smoke"):
            tested = 0
            controls = page.locator(_CONTROLS).all()
            for index, locator in enumerate(controls):
                if tested >= 8:
                    break
                if not locator.is_visible() or not locator.is_enabled():
                    continue
                info = locator.evaluate("""node => ({
                  id: node.id, tag: node.tagName.toLowerCase(),
                  section_id: node.closest('[data-srg-section]')?.dataset.srgSection ?? null
                })""")
                info["selector"] = f'#{info["id"]}' if info["id"] else f"{_CONTROLS} (index {index})"
                try:
                    if info["tag"] == "select":
                        alternative = locator.evaluate("""node => [...node.options].findIndex(option =>
                          !option.selected && !option.disabled && !option.closest('optgroup[disabled]'))""")
                        if alternative < 0:
                            continue
                        locator.select_option(index=alternative)
                    else:
                        locator.click()
                    page.evaluate(_FRAME_WAIT)
                except BrowserError as exc:
                    check_runtime(control=info)
                    raise _failure("交互控件未能完成操作", control=info, section_id=info["section_id"],
                                   section_ids=[info["section_id"]] if info["section_id"] else [],
                                   message_detail=str(exc)) from exc
                tested += 1
                check_runtime(control=info)

        with measure("chromium.inspect"):
            page.set_viewport_size({"width": 390, "height": 844})
            page.evaluate(_FRAME_WAIT)
            check_runtime()
            warnings = []
            if page.evaluate("() => document.documentElement.scrollWidth > innerWidth + 4"):
                warnings.append("390px 窄屏出现横向溢出")
            if inspection["emptyTables"]:
                warnings.append(f'{inspection["emptyTables"]} 张表没有数据行')

        return {"status": "passed", "engine": "chromium", "sections_checked": inspection["checked"],
                "chart_count": inspection["chartCount"], "interactive_controls_tested": tested,
                "viewport_widths": [1280, 390], "external_requests": 0, "browser_errors": 0, "warnings": warnings}
    except BrowserTimeout as exc:
        raise _failure("Chromium 加载或操作超时", message_detail=str(exc), errors=errors[:20],
                       external_requests=list(dict.fromkeys(requests))[:20]) from exc
    except BrowserError as exc:
        raise _failure("Chromium 无法完成报告校验", message_detail=str(exc), errors=errors[:20]) from exc
    finally:
        context.close()


def verify_html(page_html: str, expected_sections: list[str] | None = None, *,
                allow_shared_duplicate_ids: bool = False) -> dict:
    global _executor
    with measure("chromium.acquire_slot"):
        if not _slots.acquire(timeout=30):
            raise _failure("浏览器校验队列繁忙，请稍后重试")
    try:
        executable_path = getenv("SRG_CHROMIUM_PATH", "/usr/bin/chromium")
        with _pool_lock:
            if _executor is None:
                _executor = ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="srg-chromium")
            context = copy_context()
            future = _executor.submit(context.run, _verify_on_thread, page_html, expected_sections, executable_path,
                                      allow_shared_duplicate_ids)
        return future.result()
    finally:
        _slots.release()
