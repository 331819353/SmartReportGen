# 多轮拼装 HTML 报告

## 草稿和数据

`create_html_draft` 的 `snapshots` 示例为 `{"summary":"<snapshot_id>","trend":"<snapshot_id>"}`。数据块 ID 由字母或下划线开头，可含数字和连字符，须与页面其他 ID 区分。修订草稿以 `base` 复用原数据，用新快照覆盖对应绑定即可换数。

`SRG.raw("summary")` 返回 `{columns, rows}`，`SRG.rows("summary")` 返回按列名访问的行对象。Decimal 与超出 JavaScript 安全整数范围的数据以 `{"$decimal":"原始文本"}` 保留精度。显示精确值时保留原始文本。绘图用 `SRG.num(v)` 解包为 Number，空值返回 NaN。画图时用 `null` 表示缺值。

可将 `prepare_report_context` 返回的 `style.builtin_style.style_id` 用于草稿，主题会内联。ECharts 默认内联。需要格式化辅助时，可在骨架加入 `<script id="srg-formatters" data-srg-asset="report-formatters.js"></script>`，以 `SrgDisplay` 使用。

## 第一轮初始化页面与筛选

以下三段分别填入 `head_css`、`shell_html` 和 `shared_js`。示例假定快照提供 `region`、`amount`、`month`，包括正式的「全部」汇总行。

```css
.report-controls { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }
.kpi { padding: 16px; border: 1px solid #dbe2ea; border-radius: 10px; }
.kpi output { color: var(--color-heading, #172c4a); font-size: 1.7rem; font-weight: 700; }
.trend-chart { width: 100%; height: 320px; }
.table-scroll { max-width: 100%; overflow-x: auto; }
```

```html
<div class="srg-page">
  <header>
    <h1>月度经营报告</h1>
    <div class="report-controls">
      <label for="region">区域</label>
      <select id="region"><option>全部</option><option>华东</option><option>华南</option></select>
    </div>
  </header>
  <main id="srg-main"><!--SRG_SECTIONS--></main>
</div>
```

```javascript
const select = document.getElementById('region');
SRG.filters = {
  subscribe(render) {
    const refresh = () => render(select.value);
    select.addEventListener('change', refresh);
    refresh();
  }
};
```

筛选项可来自源端维度快照。指标卡、图表、表格和动态结论订阅同一状态。章节间共享的函数和状态挂在 `SRG` 上。

## 第二轮添加核心指标

用 `upsert_html_section` 填写 `section_id="overview"` 的 HTML 与 JS。

```html
<h2>经营概览</h2>
<div class="kpi"><div>销售额（万元）</div><output id="total-sales">—</output></div>
```

```javascript
const rows = SRG.rows('summary');
const total = document.getElementById('total-sales');
SRG.filters.subscribe(region => {
  const value = rows.find(item => item.region === region)?.amount;
  total.textContent = value?.$decimal ?? value ?? '—';
});
```

## 第三轮添加趋势图

填写 `section_id="trend_chart"`，可传 `after_section_id="overview"` 设置顺序。

```html
<h2>月度趋势</h2>
<div class="trend-chart" id="sales-trend"></div>
<p id="trend-empty" hidden>当前区域暂无趋势数据。</p>
```

```javascript
const rows = SRG.rows('trend');
const axis = {type: 'category', data: []};
const chart = SRG.initChart('sales-trend', {
  animation: false, tooltip: {trigger: 'axis'}, xAxis: axis,
  yAxis: {type: 'value', name: '万元'}, series: [{type: 'bar', data: []}]
});
SRG.filters.subscribe(region => {
  const points = rows.filter(item => item.region === region);
  document.getElementById('trend-empty').hidden = points.length > 0;
  chart.setOption({
    xAxis: {...axis, data: points.map(item => item.month)},
    series: [{data: points.map(item => {
      const value = SRG.num(item.amount);
      return Number.isFinite(value) ? value : null;
    })}]
  });
});
```

## 局部修订与验收

章节 DOM 先组装，随后执行共享脚本和各章节脚本。章节脚本各自有作用域，可复用局部变量名。局部 CSS 可用 `#srg-sec-<section_id>` 限定范围。批量参数 `sections` 支持只更新指定字段、用显式空字符串清除字段、用从零开始的 `index` 移动章节。删除传 `delete=true`。全局更新可与章节同轮提交。

用 `get_html_draft(draft_id, include_code=true, section_id="trend_chart")` 读取待修复片段。共享脚本错误的章节标识为 `__shared__`。修改后的草稿需重新验收。管理 API 的 `GET /api/report-drafts` 提供草稿状态及最近操作。
