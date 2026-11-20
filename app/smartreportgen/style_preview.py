"""使用固定示例事实预览样式，沿用报告的原生 CSS 和离线图表。"""
from __future__ import annotations

from html import escape

from . import design, repo, report_data
from .report_draft import DraftSection, HtmlDraft, MARKER, assemble_source_html

_SAMPLE_TREND = """
const styles = getComputedStyle(document.documentElement);
const color = key => styles.getPropertyValue('--' + key).trim();
const palette = (key, count) => Array.from({length: count}, (_, index) => color(key + '-' + (index + 1))).filter(Boolean);
const months = ['1月', '2月', '3月', '4月', '5月', '6月'];
const regions = ['华东', '华南', '华北'];
const values = [[520, 530, 564, 585, 601, 635], [360, 374, 388, 409, 440, 461], [300, 330, 312, 350, 367, 380]];
const axis = { axisLabel: {color: color('color-text-secondary')} };
const categoryColors = palette('chart-palette-categorical', 7);
const trend = SRG.initChart('sample-trend', {
  color: categoryColors,
  tooltip: {trigger:'axis'}, legend: {bottom:0}, grid: {containLabel:true, bottom:42},
  xAxis: {type:'category', data:months, ...axis}, yAxis: {type:'value', ...axis},
  series: regions.map((name, i) => ({name, type:'line', data:values[i]}))
});
SRG.initChart('sample-share', {
  color: categoryColors,
  tooltip: {trigger:'item'},
  series:[{type:'pie', radius:['45%','72%'], data:[
    {name:'门店', value:45}, {name:'线上商城', value:35}, {name:'直播电商', value:20}
  ]}]
});
SRG.initChart('sample-growth', {
  tooltip: {trigger:'axis'}, grid:{containLabel:true, left:16, right:16},
  xAxis: {type:'category', data:regions, ...axis}, yAxis:{type:'value', ...axis},
  series: [{type:'bar', data:[12, 18, -7].map(value => ({
    value, itemStyle: {color: color('chart-palette-diverging-' + (value < 0 ? '1' : '3'))}
  }))}]
});
SRG.initChart('sample-goal', {
  color:[color('color-brand'), color('color-border-card')], tooltip:{trigger:'axis'},
  legend:{bottom:0}, grid:{containLabel:true, bottom:42},
  xAxis:{type:'value', ...axis}, yAxis:{type:'category', data:regions, ...axis},
  series:[{name:'实际',type:'bar',data:[3435,2432,1989]}, {name:'目标',type:'bar',data:[3500,2500,2150]}]
});
SRG.initChart('sample-heat', {
  tooltip:{}, grid:{containLabel:true, bottom:48},
  xAxis:{type:'category', data:months, ...axis}, yAxis:{type:'category', data:regions, ...axis},
  visualMap:{min:300,max:650, orient:'horizontal', left:'center', bottom:0,
    inRange:{color:palette('chart-palette-sequential', 6)}},
  series:[{type:'heatmap',data:values.flatMap((row, i) => row.map((value, j) => [j,i,value]))}]
});
document.getElementById('sample-region').addEventListener('change', event => {
  for (const name of regions) {
    trend.dispatchAction({type: !event.target.value || event.target.value === name ? 'legendSelect' : 'legendUnSelect', name});
  }
  for (const row of document.querySelectorAll('[data-sample-region]')) {
    row.hidden = Boolean(event.target.value && event.target.value !== row.dataset.sampleRegion);
  }
});
"""


def _card(title: str, content: str, *, span: int = 30, kind: str = "") -> str:
    return (f'<article class="rf-card {kind}" style="--span:{span}">'
            f'<header class="rf-card-head"><h3 class="rf-card-title">{title}</h3></header>'
            f'{content}</article>')


def render_preview(business_scenario: str | None, exclude_id: int | None = None, draft: dict | None = None,
                   owner: str | None = None) -> str:
    theme = design.full(repo.effective_style(business_scenario, exclude_id, draft, owner)["theme"])
    scenario = escape(business_scenario or "通用")
    filter_class = "rf-filter-sticky" if theme["filter_sticky"] else "rf-filter-in-header"
    filters = (f'<div class="rf-filterbar {filter_class}"><div class="rf-filterbar-inner">'
               '<label class="rf-f">大区 <select id="sample-region" class="rf-select"><option value="">全部</option>'
               '<option>华东</option><option>华南</option><option>华北</option></select></label></div></div>')
    toc = ""
    if theme["toc"]:
        toc = ('<nav class="rf-toc" aria-label="报告目录"><div class="rf-toc-card"><div class="rf-toc-title">目录</div>'
               '<a class="rf-toc-parent" href="#srg-sec-metrics">指标卡</a>'
               '<a class="rf-toc-parent" href="#srg-sec-charts">图表</a>'
               '<a class="rf-toc-parent" href="#srg-sec-details">数据表</a></div></nav>')
    layout = "rf-has-toc" if toc else ""
    zebra = "rf-zebra" if theme["table_zebra"] else ""
    header = "plain" if theme["header_style"] == "plain" else "banner"
    # 使用正式组件演示目录、筛选位置和表格选项，页面只读取固定示例值。
    shell = (f'<div class="rf-wrap rf-layout {layout} {zebra}"><header class="rf-header rf-header-{header}">'
             f'<p class="rf-kicker">{scenario}</p><h1 class="rf-title">样式预览 · 示例经营报告</h1>'
             '<p class="rf-subtitle">展示配色、排版、筛选和图表的示例数据</p>'
             f'{"" if theme["filter_sticky"] else filters}</header>{toc}'
             f'<main class="rf-main">{filters if theme["filter_sticky"] else ""}{MARKER}</main></div>')
    kpis = "".join(
        _card(title, f'<div class="rf-hero" data-state="{state}">{value}</div>', span=15, kind="rf-type-kpi rf-narrow")
        for title, value, state in (("满意度", "4.60 分", "positive"), ("目标达成率", "97%", "warning"),
                                    ("目标差额", "−263 万元", "negative"), ("销售额", "8,190 万元", ""))
    )
    charts = "".join(
        _card(title, f'<div class="rf-chart-body" id="sample-{ident}" style="height:250px"></div>',
              kind="rf-chart", span=span)
        for title, ident, span in (("月度销售额", "trend", 38), ("渠道构成", "share", 22),
                                   ("区域同比", "growth", 30), ("区域目标达成", "goal", 30),
                                   ("地区与月份", "heat", 60))
    )
    summary_rows = "".join(f'<tr data-sample-region="{region}"><th scope="row">{region}</th>'
                           f'<td class="num">{amount}</td><td class="num" data-state="{status}">{growth}</td></tr>'
                           for region, amount, status, growth in (("华东", "3,435", "positive", "+12%"),
                           ("华南", "2,432", "positive", "+18%"), ("华北", "1,989", "negative", "−7%")))
    table = ('<div class="rf-table-wrap"><table class="rf-table"><thead><tr><th>大区</th>'
             '<th class="num">销售额（万元）</th><th class="num">同比</th></tr></thead>'
             f'<tbody>{summary_rows}</tbody></table></div>')
    example = HtmlDraft("style-preview", "", "样式预览", shell_html=shell,
                        head_css=design.report_css(theme), sections=[
        DraftSection("metrics", html=f'<h2 class="rf-section-title">指标卡</h2><div class="rf-grid rf-grid-kpi">{kpis}</div>'),
        DraftSection("charts", html=f'<h2 class="rf-section-title">图表</h2><div class="rf-grid">{charts}</div>', js=_SAMPLE_TREND),
        DraftSection("details", html=f'<h2 class="rf-section-title">数据表</h2>{table}'),
    ])
    return report_data.embed(assemble_source_html(example), owner or "")
