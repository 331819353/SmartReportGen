"""验证展示门禁的实际拦截行为、合法输入和记录失效条件，不访问业务数据。"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'skills' / 'smart-report' / 'assets'
spec = importlib.util.spec_from_file_location('display_audit', ASSETS / 'audit_report.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def document(*, body='毛利率 9.78%，同比差 -3.66%', code='', mode='percent-only', expanded=False):
    contract = {'version': 1, 'rate_symbols': mode, 'rule_sources': ['U-NUM-01：测试规则'],
                'colors': {'up': '#047857', 'down': '#B91C1C', 'neutral': '#475569',
                           'met': '#047857', 'miss': '#B91C1C'}}
    css = ''.join(f'.srg-{state} {{ color: var(--srg-{state}); }}' for state in audit.STATES)
    data = json.dumps({'columns': ['direct_delta'], 'rows': [[-3.66]]}) if expanded else ''
    return (f'<!doctype html><html><head><style>{css} .layout{{width:100%}}</style></head><body>'
            f'<p>{body}</p><script type="application/json" id="srg-display-contract">{json.dumps(contract)}</script>'
            f'<script type="application/json" id="srg-data" data-srg-snapshot="base">{data}</script>'
            f'<script id="srg-formatters">{(ASSETS / "report-formatters.js").read_text()}</script>'
            f'<script>const {{fmtPct,fmtNum,fmtDelta}} = SrgDisplay;{code}</script></body></html>')


class SkillDisplayAuditTest(unittest.TestCase):
    def test_valid_source_and_expanded_snapshots(self):
        self.assertEqual(audit.audit_html(document()), [])
        self.assertEqual(audit.audit_html(document(expanded=True), expanded=True), [])
        self.assertTrue(audit.audit_html(document(), expanded=True))

    def test_banned_display_and_tooltip_paths(self):
        for body in ('同比 -3.66pp', '同比 -3.66 PP', '差 3.66个百分点', '差 3.66pct',
                     '百<span>分</span>点', '<span title="-3.66&#112;p">变动</span>',
                     '<span aria-label="3pct">变动</span>'):
            with self.subTest(body=body):
                self.assertTrue(any(e.startswith('R-07') for e in audit.audit_html(document(body=body))))
        for code in ("const tooltip = () => '3.66pp';", "const label = '百分点';"):
            self.assertTrue(any(e.startswith('R-07') for e in audit.audit_html(document(code=code))))

    def test_formatting_bypasses_are_blocked(self):
        for code in ("const text = v.toFixed(2);", "const text = v.toPrecision(3);",
                     "const text = v.toLocaleString();", "const f = new Intl.NumberFormat();",
                     "const text = `${v}%`;", "const text = v + '\\u0025';",
                     "function fmtPct(v) {return v;}", "const fmtDelta = v => v;",
                     "const text = fmtNum(v, {unit:'pp'});"):
            with self.subTest(code=code):
                self.assertTrue(any(e.startswith('R-08') for e in audit.audit_html(document(code=code))))

    def test_unit_and_raw_data_do_not_trigger_false_positives(self):
        source = document(body='单位：%<span style="width:100%">已核实 9.78%</span>',
                          code="const title = '单位：%'; const text = fmtPct(v, {scale:'percent'});")
        # 原始字段/单位不等于输出；规则原文也必须可以记录禁用词。
        source = source.replace('测试规则', '禁止 pp、百分点、pct')
        source = source.replace('data-srg-snapshot="base"></script>',
                                'data-srg-snapshot="base">{"columns":["pp"],"rows":[["pct"]]}</script>')
        self.assertEqual(audit.audit_html(source, expanded=True), [])
        self.assertEqual(audit.audit_html(document(code='const coordinate = index % 3;')), [])

    def test_semantic_mode_retains_direct_rate_difference_permission(self):
        self.assertEqual(audit.audit_html(document(body='源端率差 -3.66pp', mode='semantic')), [])
        self.assertTrue(audit.audit_html(document(body='源端率差 -3.66pp')))

    def test_formatter_asset_cannot_be_modified_or_relabelled(self):
        source = document().replace("return label.slice(0, -1) + 'pp';", "return '0%';")
        self.assertTrue(any(e.startswith('R-08') for e in audit.audit_html(source)))
        self.assertTrue(audit.audit_html(document().replace('id="srg-formatters"', 'id="vendor"')))
        self.assertTrue(audit.audit_html(document().replace(
            'id="srg-formatters"', 'id="srg-formatters" type="application/json"')))

    def test_duplicate_ids_and_late_contract_are_rejected(self):
        self.assertTrue(audit.audit_html(document(body='<p id="srg-data">重复</p>')))
        source = document()
        start = source.index('<script type="application/json" id="srg-display-contract">')
        end = source.index('</script>', start) + len('</script>')
        late = source[:start] + source[end:]
        late = late.replace('</body>', source[start:end] + '</body>')
        self.assertTrue(any('之前' in e for e in audit.audit_html(late)))

    def test_offline_and_color_violations(self):
        for body, code in (('<img src="https://example.com/a.png">', ''),
                           ('<link rel="stylesheet" href="local.css">', ''),
                           ('<button onclick="run()">切换</button>', ''),
                           ('<script src="data:text/javascript,alert(1)"></script>', ''),
                           ('<svg><use href="outside.svg#icon"></use></svg>', ''),
                           ('', "fetch('/data');"), ('', "import('./chart.js');"),
                           ('', "const itemStyle = {color:'#00ff00'};")):
            with self.subTest(body=body, code=code):
                self.assertTrue(audit.audit_html(document(body=body, code=code)))
        source = document().replace('width:100%', "background:url('https://example.com/a.png')")
        self.assertTrue(any(e.startswith('R-06') for e in audit.audit_html(source)))
        inline = document().replace('width:100%', 'background:url(data:image/png;base64,AAAA)')
        self.assertEqual(audit.audit_html(inline), [])

    def test_vendor_attribute_cannot_disable_scan(self):
        source = document(code="const text = v.toFixed(2);").replace(
            '<script>const', '<script data-vendor="echarts">const')
        self.assertTrue(audit.audit_html(source))
        vendor = ROOT / 'app/smartreportgen/static/vendor/echarts.min.js'
        if vendor.exists():
            source = document().replace('</body>', f'<script>{vendor.read_text()}</script></body>')
            self.assertEqual(audit.audit_html(source), [])

    def test_state_color_cannot_be_overridden_in_other_paths(self):
        for css in ('.chapter .srg-up {color:#0000ff}', ':root {--srg-up:#0000ff}'):
            source = document().replace('</style>', css + '</style>')
            self.assertTrue(any(e.startswith('R-09') for e in audit.audit_html(source)))
        source = document(body='<span class="srg-up" style="color:blue">已核实</span>')
        self.assertTrue(any(e.startswith('R-09') for e in audit.audit_html(source)))

    def test_review_hash_and_incomplete_checks_block_completion(self):
        source, expanded = document(), document(expanded=True)
        review = audit.review_template(expanded, source)
        self.assertTrue(audit.audit_review(review, expanded, source))
        for group in ('surfaces', 'behaviors'):
            for entry in review[group].values():
                entry.update(status='pass', evidence='测试夹具已覆盖，断言通过')
        self.assertEqual(audit.audit_review(review, expanded, source), [])
        self.assertTrue(audit.audit_review(review, expanded + 'changed', source))
        self.assertTrue(audit.audit_review(review, expanded, source + 'changed'))
        review['behaviors']['offline']['status'] = 'na'
        self.assertTrue(audit.audit_review(review, expanded, source))

    def test_fixture_flag_cannot_silently_allow_formal_reports(self):
        example = (ROOT / 'skills/smart-report/references/example-report.html').read_text()
        self.assertTrue(audit.audit_html(example))
        self.assertEqual(audit.audit_html(example, fixture=True), [])

    def test_cli_fail_closed_and_completed_exit_status(self):
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            source, expanded = work / 'source.html', work / 'expanded.html'
            source.write_text(document())
            expanded.write_text(document(expanded=True))
            review = work / 'review.json'
            command = [sys.executable, str(ASSETS / 'audit_report.py'), str(expanded), '--source', str(source)]
            pending = subprocess.run(command + ['--write-review', str(review)], capture_output=True, text=True)
            self.assertEqual(pending.returncode, 1)
            self.assertFalse(json.loads(pending.stdout)['ok'])
            record = json.loads(review.read_text())
            for group in ('surfaces', 'behaviors'):
                for entry in record[group].values():
                    entry.update(status='pass', evidence='已测试的虚构夹具')
            review.write_text(json.dumps(record))
            completed = subprocess.run(command + ['--review', str(review)], capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stdout)
            source.write_text(source.read_text().replace('毛利率', '费率'))
            changed = subprocess.run(command + ['--review', str(review)], capture_output=True, text=True)
            self.assertEqual(changed.returncode, 1)
            invalid = subprocess.run(command + ['--review', str(work / 'missing.json')], capture_output=True, text=True)
            self.assertEqual(invalid.returncode, 2)


if __name__ == '__main__':
    unittest.main()
