# SmartReportGen 报告模板语法（markdown）

> 本文保留已有结构化模板的兼容语法。下文的聚合、比率公式及零售示例不构成新报告的计算授权；要求事实直取的报告采用 SKILL.md 的 Agent HTML 流程，并遵守 `references/fact-contract.md`。

模板 = **YAML front matter**（数据、度量、筛选、主题）+ **markdown 正文**（章节、结论、图表）。
查询在服务端执行，结果作为数据包嵌入 HTML；筛选、聚合、图表在浏览器端实时计算，因此**所有筛选联动都无需重新查询**。

~~~markdown
---
title: 报告标题                    # 必填
subtitle: 副标题（数据范围等）
scenario: 零售销售经营分析          # 业务场景名（用于推断默认数据源）
datasource: 演示-零售PG             # 可选；普通用户取数须使用模板关联场景的数据源
analysis_goal: 一句话分析目标        # 显示在头部
theme: {...}                       # 可选：设计令牌覆盖，见「主题」（样式规则会自动生效）
filters: [...]                     # 全局筛选器，见「筛选」
presets: [...]                     # 预设场景（一键切换筛选组合）
queries:                           # 必填，至少一个
  <查询id>:
    label: 说明
    datasource: 可选，指定数据源（名称或 id）；设置数据过滤的场景不能换用其他源
    sql: |
      SELECT ...                   # 只读 SELECT/WITH，方言与数据源一致
    measures:                      # 基于该查询结果列的度量
      <度量id>: {expr: "sum(amount)", name: 销售额, format: wan, unit: 元}
---

可选导语段落……

## 一、章节标题（一个维度视角）
> 本节汇总结论：必须量化，可嵌入实时指标 {{gmv}}、{{margin|percent}}。

### 子结论 1：一句话结论
解释文字（markdown）。

```chart
type: bar
title: 图表标题
x: region
y: gmv
note: 该图如何支撑上面的结论
```
~~~

## 1. 正文结构

| 元素 | 写法 | 渲染 |
|---|---|---|
| 章节 | `## 标题` | 左侧目录 + 章节标题，模板至少需要一个章节 |
| 本节汇总结论（可选） | 章节标题后第一个 `> 引用块` | 强调色结论卡片 |
| 子结论 | `### 标题` | 结论标题 + 解释文字 + 其下所有图表 |
| 图表 | ` ```chart ` YAML 块 | 连续的图表块组成一个自适应栅格 |
| 实时指标 | `{{度量id}}` 或 `{{度量id\|格式}}` | 随筛选实时更新的数字 |

正文 markdown 支持段落、列表、加粗、表格；不支持原始 HTML（会被转义）。

## 2. 查询与度量

- **列名统一转小写**（Oracle 列也会转小写）；请用 ASCII 别名：`SUM(amount) AS amount`。
- **先在 SQL 中聚合到“图表 + 筛选”所需的最细粒度**（如 月份×大区×省份×渠道×品类），一般几百到几千行；明细级数据请勿直接嵌入（上限 `QUERY_MAX_ROWS`，超出会截断告警）。
- 日期请在 SQL 中格式化为字符串：PG `to_char(d,'YYYY-MM')`，MySQL/StarRocks `DATE_FORMAT(d,'%Y-%m')`，Oracle `TO_CHAR(d,'YYYY-MM')`，API(DuckDB) `strftime(d,'%Y-%m')`。
- API 数据源：表名为接口逻辑名，SQL 使用 DuckDB 方言。

**度量表达式**（浏览器端对“当前筛选后的分组行”计算）：
- 聚合函数：`sum(col)`、`avg(col)`、`count(*)`、`count(col)`、`count_distinct(col)` / `count(distinct col)`、`min(col)`、`max(col)`
- 可用 `+ - * / ( )` 与数字组合：`sum(profit)/sum(amount)`、`sum(amount)/sum(amount_ly)-1`
- **比率类指标必须写成“聚合之比”**（如毛利率），不要预先在 SQL 中算好比率再 sum。
- 同比/环比：在 SQL 中准备好对比列（如 `amount_ly` 去年同期），再用 `sum(amount)/sum(amount_ly)-1`。
- 预聚合后计数：订单数用 `sum(orders)`（SQL 中 `count(*) AS orders`），不要用 `count(*)`。
- 去重计数（客户数）在预聚合后不可再相加：如需按筛选正确去重，查询需保留去重键粒度，或接受“分组内去重之和”的近似并在 note 中说明。

**format**：`auto`（自动万/亿）、`wan`、`yi`、`money`（¥+自动单位）、`int`、`float1`、`float2`、`percent`（0.235→23.5%）、`percent0`、`pp`（百分点）、`raw`、自定义 `0.00` / `0.0%`。
`unit` 为附加单位（如 `元`、`单`、`小时`）：指标卡主值显示为“4,227.94 **万元**”（主值后缀），图表卡标题右侧自动显示单位标签（如“万元”“%”）。

## 3. 图表块（```chart）

通用参数：

| 参数 | 说明 |
|---|---|
| `type` | 必填，见下表 |
| `id` | 可选，字母数字下划线，默认 chart1… |
| `title` / `note` | 标题 / 解读，可按图表内容填写 |
| `query` | 查询 id；若 y 引用了已定义度量可省略 |
| `x` | 分类/时间字段 |
| `y` | 度量：度量 id、表达式字符串，或对象 `{measure|expr|field+agg, name, format, as: bar\|line, axis: left\|right, color}`；可为列表 |
| `series` | 拆分系列的字段（此时 y 只能一个） |
| `sort` | `value_desc`（默认，分类）/ `value_asc` / `x_asc`（时间默认）/ `x_desc` / `none` |
| `top` | 只取前 N（时间轴取最近 N 个）；饼图/环图其余合并为“其他” |
| `span` | 用于排布的栅格宽度 1–12（12=整行）；**不填则自动布局** |
| `height` | 图形或表格内容区的基准高度 60–1200 像素；不填按类型和所在行计算 |
| `labels` | 是否显示数值标签（柱图类别 ≤12 默认显示） |
| `stack` / `smooth` / `zoom` / `legend` | 堆叠 / 平滑曲线 / 缩放条 / 图例开关 |
| `series_limit` | series 拆分时最多系列数（默认 8，其余合并“其他”） |
| `x_label` / `y_label` | 轴名称 |
| `unit` | 标题右侧单位标签；不填时由度量的 format + unit 自动推导（万元、%、单…） |
| `status` | 健康度判定，仅 `kpi` / `gauge` / `bullet` 可用，见「健康度」 |

| type | 用途 | 必要参数 |
|---|---|---|
| `kpi` | 指标卡 | `y`（单个）；`sub: [度量 或 {measure: x, trend: up\|down\|none}]` 辅助读数行，见「升降色」 |
| `line` / `area` / `stacked_area` | 时间趋势 | `x`, `y` 或 `series` |
| `bar` / `stacked_bar` | 分类对比 / 构成变化 | `x`, `y` 或 `series` |
| `hbar` | 横向类别对比 | `x`, `y` |
| `combo` | 柱+线双轴（如 销售额 + 增长率） | `x`, `y` ≥2：首个柱（左轴），其余线（右轴），可用 `as`/`axis` 覆盖 |
| `pie` / `donut` | 构成占比 | `x`, `y` |
| `funnel` | 转化漏斗 | `x`, `y` |
| `treemap` | 多类别占比 | `x`, `y` |
| `scatter` | 两指标关系 / 象限 | `group`（每个点的维度），`y: [x轴度量, y轴度量, 气泡大小(可选)]` |
| `heatmap` | 两维交叉 | `x`（列）, `series`（行）, `y`（单个） |
| `gauge` | 达成率/满意度 | `y`, `max`（数值或度量 id；percent 格式默认 1） |
| `bullet` | 子弹图：实际 vs 目标 | `y: [实际度量, 目标度量]`；可选 `x`（每个类目一行）；不填 x 时显示主值 + 目标 + 达成率 |
| `table` | 明细/排行表 | 聚合表：`dims: [字段]` + `y: [度量]`；明细表：`columns: [字段]`；`top`，`headers: {字段: 表头}`，`bar_in_cell` |

## 4. 布局（自适应栅格）

- 同一结论下连续的图表块按 **12 栅格**（内部 60 等分）排布。每个实际显示行的卡片按最高的一张对齐外框，分配行内剩余宽度。单卡行占满整行，卡片间距、内边距和字号由设计令牌固定。
- 未写 `span` 时按类型给默认宽：kpi=3、pie/donut/funnel/gauge=4、bar/line/scatter/treemap/bullet=6、table/heatmap/combo=12，逐行装箱后**把行内剩余空间平均分配给自动项**（如 2 个饼图→各 6，3 个柱图→6+6、末行单个自动铺满 12）。
- 内置样式的 KPI 主值为 19px，桌面端连续五张自动宽度的指标卡可等分一行，六张排为两行三列。
- 窄屏（≤980px）目录移到报告上方，小于半行的卡片以半行作为排布宽度。手机端图表单列，小型指标卡可两列显示，换行后每行仍铺满可用宽度。

## 5. 全局筛选（filters）与联动

```yaml
filters:
  - {id: month, label: 月份, type: range, field: month, default: "last:12"}
  - {id: region, label: 大区, type: multi, field: region}
  - {id: province, label: 省份, type: multi, field: province}   # 自动与上方「大区」级联
  - {id: channel, label: 渠道, type: select, field: channel}
  - {id: day, label: 日期, type: date_range, field: order_date}
presets:
  - {name: 近12个月, values: {month: "last:12"}}
  - {name: 华东重点, values: {region: [华东], channel: 直播电商}}
```

| 参数 | 说明 |
|---|---|
| `type` | `select` 单选 / `multi` 多选（带搜索、全选）/ `range` 有序区间（月份、年份等离散值）/ `date_range` 日期区间 |
| `field` | 字段名；**作用于所有包含该列的查询** |
| `targets` | `{查询id: 列名}`，当某查询中列名不同时映射 |
| `exclude` | `[查询id]`，不受该筛选影响的查询（如“全年目标”） |
| `default` | select：值 / `latest` / `first`；multi：值列表；range：`[起, 止]` 或 `last:N` |
| `options` | 固定选项列表（默认从数据中自动提取） |
| `cascade` | 默认 true：选项随**前面**的筛选器级联收窄，上级变化时自动剔除失效取值 |

联动机制：
1. 全部图表、KPI、正文 `{{度量}}` 随筛选实时重算。
2. **图表点击联动**：当图表的 `x` 字段与某个 select/multi 筛选器字段相同（同一查询列），点击柱/扇区即切换该筛选；该图表自身保持全量并高亮选中项，其余图表随之过滤。
3. 预设（presets）一键切换多场景筛选组合；“重置”恢复默认值。

## 6. 健康度、升降色与主题

### 健康度（status）——只有指标卡、仪表盘、子弹图判整卡好坏
```yaml
status: {measure: tgt_rate, direction: up, good: 1.0, warn: 0.95, labels: {positive: 达标, warning: 临近, negative: 未达}}
```
- 判定值：`measure`（默认取图表第一个度量；bullet 默认取 实际/目标）。
- `direction: up`（越大越好）：≥ good → 绿·向好；≥ warn → 黄·关注；否则红·承压。`direction: down` 反之。
- 卡片主色取绿/黄/红之一（顶部色条、状态标签、仪表盘与子弹图图形），状态同时用图标 + 文字表达；`labels` 可改文字。
- 省略 `status` 时使用主题色（`--color-brand`）。

### 升降色（kpi 的 sub）
- `trend: up`：越大越好，上升用 `--color-trend-up`（绿），下降用 `--color-trend-down`（红）。
- `trend: down`：越小越好（成本、投诉率、处理时长），上升为红、下降为绿。
- `trend: none`：没有好坏方向，只显示 ▲▼ 与正负号，不着色。
- 用户明确确认过的升降色（如“红涨绿跌”）优先：在模板 theme 中覆盖 `color-trend-up` / `color-trend-down`。

### 主题（theme）
报告的视觉来自样式项（CSS 变量）。生效顺序为 **智能体决定（模板 `theme`）≥ 业务场景规则 ≥ 通用规则 ≥ 内置经营报告样式**。

- 后三者合并为一份指定样式，由 `get_style_rules` 的 `specified` 返回。视觉样式 `theme` 在渲染时自动生效，展示要求 `items` 用于状态标记、数字格式及文案等。章节、图表类型和筛选条件按用户需求、场景分析思路和查询证据决定。
- `max_width` 限制封面与正文的共同宽度，`toc` 控制目录是否显示。内置版式说明由 `get_style_rules` 返回。
- 报告中所有同类卡片共用样式。明细表与汇总表共用表头、行高、隔行底色及列对齐，明细表中的数字列沿用数值列对齐。柱状图使用相同的柱宽上限、圆角规则、轴线、网格、图例、标签及配色规则，正负值仍按数据含义展示。
- 你可以有自己的判断：在模板 `theme` 中设置的项**按你的设置展示**，包括覆盖指定样式（校验/渲染会提示覆盖了哪些项）。
  默认遵守约束，只在确有理由时覆盖（如用户明确要求、报告内容需要），并在最终回复中说明。
- 没有指定的样式项由你自由决定，也可以不设置。

```yaml
theme:                          # 示例：智能体的决定
  rre-page-font-title: {size: 26, weight: 700}
  rre-card-pad: 16
  kpi_align: center
  filter_align: center
  table_zebra: true
```

样式项按**报告页面的组成部分**分为 12 类（与管理台「报告样式」中的分类一致）。对齐项取值 `left` / `center`（数值列为 `right` / `center`）；控件、按钮、标签内的文字始终垂直居中：

| 类型 | 令牌 |
|---|---|
| 主题 | `color-brand` `color-bg-page` `color-heading` `color-text-primary` `color-text-secondary` `color-text-inverse` `font-family` |
| 排版 | `rre-page-font-title`（报告大标题）`rre-page-font-section`（章节标题）、卡片内八个文字角色 `rre-card-font-{title,unit,hero,affix,read,label,table,prose}`（值为 `{size, weight}`，12~72px）、`rre-prose-line-height`（1.2~2.4） |
| 页面布局 | `max_width` `rre-page-pad` `rre-section-gap` `rre-card-gap` `toc` `rre-toc-width` `rre-toc-gap` |
| 报告头部 | `header_style`（banner / plain）`color-header-start` `color-header-middle` `color-header-end` `rre-header-pad` `rre-radius-panel` `shadow-header` `header_align` |
| 筛选栏 | `filter_sticky`（默认在封面内，开启时移到封面下方吸顶）`rre-control-height` `rre-radius-control` `rre-radius-pill` `filter_align`（筛选值对齐） |
| 章节与结论 | `rre-section-pad` `rre-radius-section` `shadow-section` `color-bg-summary` `rre-summary-tint` `rre-summary-border-tint`（主题色占比 0~40%）`section_align` |
| 卡片 | `color-bg-card` `color-border-card` `rre-card-border-width` `radius-card` `shadow-card` `rre-card-pad` `rre-card-title-line` `rre-card-head-gap` `rre-radius-chip` `card_title_align` |
| 指标卡 | `rre-kpi-pad` `rre-kpi-gap` `rre-kpi-min-height` `rre-card-fact-line` `rre-card-fact-gap` `rre-radius-tile` `rre-status-bar` `kpi_align` |
| 数据配色 | `chart-palette-categorical` `chart-palette-sequential` `chart-palette-diverging`、`color-status-{positive,warning,negative}`（蓝·不判 = `color-brand`）、`color-trend-up` `color-trend-down` |
| 图表 | `rre-card-bar-max` `rre-data-radius` `rre-chart-line-width` `chart_grid`（dashed / solid / none）`rre-card-legend-gap` `rre-card-axis-gap` `rre-card-axis-title-gap` |
| 表格 | `rre-card-row-height` `color-table-head-bg`（留空 = 卡片底色）`table_zebra` `table_text_align` `table_num_align` |
| 高级 | `custom_css` |

图表着色规则（运行时自动执行）：多系列按分类色板顺序取色；单系列柱图有正有负时用发散色阶两端；热力图用顺序色阶；
健康度卡用状态色；其余图形用色板与卡片主色的深浅。需要指定某条系列的颜色时，`y` 支持 `color`。
未知令牌会被忽略并告警；颜色须为 `#RRGGBB`、`rgb()/rgba()`、`hsl()` 或颜色名，否则忽略。

## 7. 校验与常见错误

`validate_report_template` 会：解析语法 → 执行全部查询 → 检查度量字段、x/series/group/dims、筛选字段是否存在于对应查询结果列 → 返回各查询行数/列/样本与筛选取值预览。常见错误：
- 字段大小写：结果列一律小写。
- `series` 与多个 y 同时使用。
- 内联表达式没有 `query`（多查询模板中）。
- 比率用 `avg(rate)` 导致口径错误 → 改为 `sum(a)/sum(b)`。
- 筛选字段在所有查询中都不存在 → 筛选无效。
- 通过 MCP 校验/渲染时，每条 SQL 都按当前员工的表权限检查，引用未授权的表会报“无权访问数据表”。
