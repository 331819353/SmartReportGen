"""通过 Chromium 覆盖 Markdown 模板编辑器的选区转换和来回切换。"""
import os
from pathlib import Path

import pytest
import smartreportgen


@pytest.fixture(scope="module")
def template_browser():
    chromium = Path(os.environ.get("SRG_CHROMIUM_PATH", "/usr/bin/chromium"))
    if not chromium.is_file():
        pytest.skip("需要部署镜像中的 Chromium")
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as instance:
        browser = instance.chromium.launch(executable_path=str(chromium), args=["--no-sandbox"])
        yield browser
        browser.close()


@pytest.fixture
def template_page(template_browser):
    # 测试读取发布用资源，避免只验证源码而漏掉 Docker 实际加载的 bundle。
    assets = Path(next(iter(smartreportgen.__path__))) / "static/admin"
    page = template_browser.new_page()
    page.route("http://template-editor.test/", lambda route: route.fulfill(
        content_type="text/html", body='<meta charset="utf-8"><div id="editor"></div><article id="reader"></article>',
    ))
    page.route("http://template-editor.test/*.js", lambda route: route.fulfill(
        content_type="text/javascript", path=assets / "template-editor.bundle.js",
    ))
    page.goto("http://template-editor.test/")
    page.evaluate("""async () => {
        const { createGuideEditor, renderGuide } = await import('/template-editor.bundle.js');
        window.loadEditor = async markdown => {
            await window.guide?.destroy();
            window.markdown = markdown;
            window.guide = await createGuideEditor(
                document.querySelector('#editor'), markdown, value => { window.markdown = value; }, () => {}
            );
        };
        window.renderGuide = renderGuide;
    }""")
    yield page
    page.close()


def select_text(page, start, end):
    """从实际 DOM 设置选区，等编辑器收到浏览器的 selectionchange。"""
    page.evaluate("""([start, end]) => {
        const root = document.querySelector('.ProseMirror');
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        const nodes = [];
        while (walker.nextNode()) nodes.push(walker.currentNode);
        const first = nodes.find(node => node.data === start), last = nodes.find(node => node.data === end);
        root.focus();
        getSelection().setBaseAndExtent(first, 0, last, last.length);
        document.dispatchEvent(new Event('selectionchange'));
    }""", [start, end])
    # 等待编辑器处理 DOM 选区变化，再模拟点击工具栏，避免刷新间隔不同导致竞态。
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")


def test_template_multiline_list_keeps_unselected_text_and_formats(template_page):
    page = template_page
    original = "保留首行\n复核**销量**\\\n检查毛利\n保留末行"
    page.evaluate("loadEditor", original)
    select_text(page, "复核", "检查毛利")

    assert page.evaluate("guide.command('bullet')")
    assert page.locator(".ProseMirror > p").all_text_contents() == ["保留首行", "保留末行"]
    assert page.locator(".ProseMirror ul > li").all_text_contents() == ["复核销量", "检查毛利"]
    assert page.locator(".ProseMirror li strong").inner_text() == "销量"

    assert page.evaluate("guide.command('ordered')")
    assert page.locator(".ProseMirror ol > li").count() == 2
    assert page.evaluate("guide.command('undo')")
    assert page.locator(".ProseMirror ul > li").count() == 2
    assert page.evaluate("guide.command('undo')")
    assert page.evaluate("window.markdown") == original


def test_template_code_combines_blocks_and_preserves_line_breaks(template_page):
    page = template_page
    page.evaluate("loadEditor", "SELECT region\\\nFROM sales\n\nWHERE enabled = 1;\n\n后续内容")
    select_text(page, "SELECT region", "WHERE enabled = 1;")

    assert page.evaluate("guide.command('code')")
    code = "SELECT region\nFROM sales\nWHERE enabled = 1;"
    assert page.locator(".ProseMirror pre code").all_text_contents() == [code]
    assert page.evaluate("guide.command('codeLanguage', 'sql')")
    saved = page.evaluate("window.markdown")
    assert saved == "```sql\n" + code + "\n```\n\n后续内容\n"

    page.evaluate("loadEditor", saved)
    assert page.locator(".ProseMirror pre").get_attribute("data-language") == "sql"
    assert page.locator(".ProseMirror pre code").all_text_contents() == [code]
    assert page.locator(".ProseMirror > p").all_text_contents() == ["后续内容"]
