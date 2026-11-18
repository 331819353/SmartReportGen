---
title: 2026年1-10月零售销售经营分析报告
subtitle: 数据范围 2026-01 至 2026-10 · 同比口径为 2025 年同月 · 金额单位：元
scenario: 零售销售经营分析
analysis_goal: 回答“今年的销售增长从哪里来、毛利率为什么下滑”，定位区域、渠道、品类与商品对增长和毛利的贡献
filters:
  - {id: month, label: 月份, type: range, field: month}
  - {id: region, label: 大区, type: multi, field: region}
  - {id: province, label: 省份, type: multi, field: province}
  - {id: channel, label: 渠道, type: multi, field: channel}
  - {id: category, label: 品类, type: multi, field: category}
presets:
  - {name: 一季度, values: {month: ["2026-01", "2026-03"]}}
  - {name: 二季度, values: {month: ["2026-04", "2026-06"]}}
  - {name: 三季度, values: {month: ["2026-07", "2026-09"]}}
  - {name: 直播电商, values: {channel: [直播电商]}}
  - {name: 西南大区, values: {region: [西南]}}
queries:
  sales:
    label: 2026 年 1-10 月销售（含去年同月对比列），粒度：月×大区×省份×渠道×品类×商品
    sql: |
      WITH base AS (
        SELECT extract(year FROM order_date)::int AS y, to_char(order_date, 'MM') AS mm,
               region, province, channel, category, product,
               sum(amount) AS amount, sum(profit) AS profit, count(*) AS orders
        FROM demo_retail.sales_order
        WHERE order_date >= date '2025-01-01' AND to_char(order_date, 'MM') <= '10'
        GROUP BY 1, 2, 3, 4, 5, 6, 7
      )
      SELECT '2026-' || mm AS month, region, province, channel, category, product,
             coalesce(cy.amount, 0) AS amount, coalesce(cy.profit, 0) AS profit, coalesce(cy.orders, 0) AS orders,
             coalesce(ly.amount, 0) AS amount_ly, coalesce(ly.profit, 0) AS profit_ly, coalesce(ly.orders, 0) AS orders_ly
      FROM (SELECT * FROM base WHERE y = 2026) cy
      FULL OUTER JOIN (SELECT * FROM base WHERE y = 2025) ly USING (mm, region, province, channel, category, product)
    measures:
      gmv: {expr: "sum(amount)", name: 销售额, format: wan, unit: 元}
      gmv_ly: {expr: "sum(amount_ly)", name: 去年同期销售额, format: wan, unit: 元}
      gmv_yoy: {expr: "sum(amount)/sum(amount_ly)-1", name: 销售额同比, format: percent}
      gp: {expr: "sum(profit)", name: 毛利额, format: wan, unit: 元}
      gp_yoy: {expr: "sum(profit)/sum(profit_ly)-1", name: 毛利额同比, format: percent}
      gm: {expr: "sum(profit)/sum(amount)", name: 毛利率, format: percent}
      gm_ly: {expr: "sum(profit_ly)/sum(amount_ly)", name: 去年同期毛利率, format: percent}
      gm_chg: {expr: "sum(profit)/sum(amount) - sum(profit_ly)/sum(amount_ly)", name: 毛利率同比变化, format: pp}
      orders: {expr: "sum(orders)", name: 订单数, format: int, unit: 单}
      orders_yoy: {expr: "sum(orders)/sum(orders_ly)-1", name: 订单数同比, format: percent}
      aov: {expr: "sum(amount)/sum(orders)", name: 客单价, format: float2, unit: 元}
      aov_yoy: {expr: "(sum(amount)/sum(orders))/(sum(amount_ly)/sum(orders_ly))-1", name: 客单价同比, format: percent}
  target:
    label: 大区月度目标达成（目标只到大区粒度，省份/渠道/品类筛选不作用于本查询）
    sql: |
      SELECT t.month, t.region, coalesce(a.amount, 0) AS amount, t.target_amount
      FROM demo_retail.sales_target t
      LEFT JOIN (SELECT to_char(order_date, 'YYYY-MM') AS month, region, sum(amount) AS amount
                 FROM demo_retail.sales_order WHERE order_date >= date '2026-01-01' GROUP BY 1, 2) a
        ON a.month = t.month AND a.region = t.region
      WHERE t.month BETWEEN '2026-01' AND '2026-10'
    measures:
      tgt_actual: {expr: "sum(amount)", name: 实际销售额, format: wan, unit: 元}
      tgt_rate: {expr: "sum(amount)/sum(target_amount)", name: 目标达成率, format: percent}
      tgt_amt: {expr: "sum(target_amount)", name: 销售目标, format: wan, unit: 元}
      tgt_gap: {expr: "sum(amount)-sum(target_amount)", name: 目标差额, format: wan, unit: 元}
---

> 历史结构化语法示例：下文公式与符号只说明兼容能力，不构成事实补算授权或 R-07 的例外。新建及修订报告须按当前生效规则使用统一展示入口并通过保存门禁；示例数字不得当作本次事实。


> 这是保留供维护旧结构化模板的历史示例，含聚合公式、空值补零及示意结论，不能作为事实直取报告的数据、计算授权或当前经营结论。新报告须重新核实直接事实，并按 SKILL.md 的 Agent HTML 流程生成。

本报告基于全国六大区、17 个省份的零售订单数据，按**整体 → 区域 → 渠道 → 品类与商品**四个视角展开。顶部筛选器对全部图表与文中的实时指标（红色数字）生效，点击带「⇄ 可联动」标记的图形可直接筛选对应维度。

## 一、经营概览
> 1-10 月实现销售额 {{gmv}}，同比 {{gmv_yoy}}；毛利率 {{gm}}，同比 {{gm_chg}}；目标达成率 {{tgt_rate}}。整体呈现**“规模稳增、毛利承压”**：增长主要来自西南大区与直播电商，而低毛利的直播电商占比快速上升，是毛利率下滑的主要原因。

### 销售额同比增长约 9%，毛利率同比下降约 0.65 个百分点
订单数与销售额同步增长，客单价基本持平，说明增长来自**订单量**而非提价。

```chart
type: kpi
y: gmv
sub: [{measure: gmv_yoy, trend: up}]
```
```chart
type: kpi
y: gp
sub: [{measure: gp_yoy, trend: up}]
```
```chart
type: kpi
y: gm
sub: [{measure: gm_chg, trend: up}]
```
```chart
type: kpi
y: orders
sub: [{measure: orders_yoy, trend: up}]
```
```chart
type: kpi
y: aov
sub: [{measure: aov_yoy, trend: up}]
```

### 各月销售额普遍高于去年同期，6 月（618）为年内峰值
```chart
type: combo
title: 月度销售额与同比
x: month
y:
  - gmv
  - {measure: gmv_ly, as: line, axis: left}
  - {measure: gmv_yoy, as: line, axis: right}
span: 8
note: 柱为今年、浅蓝线为去年同月、深蓝线为同比；同比曲线显示增长在全年持续存在，而非个别月份冲高。
```
```chart
type: line
title: 月度毛利率对比
x: month
y: [gm, gm_ly]
span: 4
note: 今年各月毛利率整体低于去年同期，毛利承压是结构性问题。
```

### 整体目标达成率略低于 100%，华北、西南缺口最大
```chart
type: gauge
title: 目标达成率
query: target
y: tgt_rate
max: 1.2
status: {direction: up, good: 1.0, warn: 0.95, labels: {positive: 达标, warning: 临近, negative: 未达}}
span: 4
note: 达成率 ≥100% 为达标、≥95% 为临近、否则未达。目标只设到大区粒度，省份/渠道/品类筛选不影响本图。
```
```chart
type: bullet
title: 各大区目标达成
query: target
x: region
y: [tgt_actual, tgt_amt]
status: {direction: up, good: 1.0, warn: 0.95, labels: {positive: 达标, warning: 临近, negative: 未达}}
sort: value_asc
span: 8
note: 细条为实际销售额，深色刻线为目标，右侧为达成率；华北（负增长）与西南（目标定得更激进）达成率最低，西北超额完成。
```

## 二、区域维度：西南高增长，华北负增长
> 华东仍是第一大区（默认口径 1,311.6 万，占比约 31%），但增量主要由**西南（同比 +38.7%）**与**华南（+14.7%）**贡献；**华北（-7.7%）**与**西北（-3.0%）**同比下滑。

### 西南三省增速均超 30%，重庆、四川领跑全国
```chart
type: hbar
title: 各大区销售额
x: region
y: gmv
note: 点击大区可联动筛选全报告。
```
```chart
type: bar
title: 各大区销售额同比
x: region
y: gmv_yoy
sort: value_desc
note: 西南增速是第二名华南的 2.6 倍；华北、西北为负。
```
```chart
type: table
title: 省份经营明细
dims: [region, province]
y: [gmv, gmv_yoy, gp, gm]
headers: {region: 大区, province: 省份}
height: 380
note: 按销售额降序；单元格条形长度表示销售额规模。
```

### 华北多数月份同比为负，西南逐月保持 20% 以上增长
```chart
type: heatmap
title: 大区 × 月份 销售额同比
x: month
series: region
y: gmv_yoy
note: 华北 10 个月中有 7 个月同比为负，下滑并非季节性波动；西南各月同比均超过 20%。
```

## 三、渠道维度：直播电商翻倍，渠道结构变化拉低毛利
> 直播电商销售额同比 **+105%**，占比由 11.9% 升至 22.3%；门店同比 **-18.4%**，占比由 40.6% 降至 30.4%。直播电商毛利率约 17%，比其他渠道低 3–5 个百分点。**各渠道自身毛利率基本稳定，整体毛利率下滑主要来自渠道结构变化。**

### 直播电商占比提升超过 10 个百分点，门店份额被持续分流
```chart
type: donut
title: 渠道销售额构成
x: channel
y: gmv
span: 4
note: 点击扇区可联动筛选渠道。
```
```chart
type: stacked_bar
title: 月度销售额渠道构成
x: month
series: channel
y: gmv
span: 8
note: 直播电商（各月）持续放量，门店逐步收缩。
```

### 各渠道毛利率同比基本持平，低毛利渠道占比上升拉低整体
```chart
type: bar
title: 各渠道毛利率：今年 vs 去年
x: channel
y: [gm, gm_ly]
labels: true
note: 各渠道今年与去年的毛利率差异在 ±1.2 个百分点以内。
```
```chart
type: scatter
title: 渠道增长 × 毛利率（气泡=销售额）
group: channel
y: [gmv_yoy, gm, gmv]
note: 右下角的直播电商“高增长、低毛利”，是拉低整体毛利率的关键。
```

## 四、品类与商品维度：数码规模最大，家居贡献高毛利
> 数码品类销售额居首（约 1,698 万），但毛利率仅约 13%；家电、家居合计贡献约七成毛利，其中家居以约 20% 的销售占比贡献约 34% 的毛利（毛利率约 35%）。商品层面，**手机**是最大单品（约 1,149 万，+16.9%），**沙发**是毛利额第一的商品（约 167 万），**平板**是唯一同比下滑的商品（-2.2%）。

### 品类规模与毛利结构错位
```chart
type: combo
title: 各品类销售额与毛利率
x: category
y: [gmv, gm]
span: 7
note: 柱为销售额（左轴），线为毛利率（右轴）：规模最大的数码毛利率最低。
```
```chart
type: donut
title: 毛利额品类构成
x: category
y: gp
span: 5
note: 家电 + 家居贡献约七成毛利。
```

### 商品矩阵：手机高增长低毛利，家居类商品高毛利
```chart
type: scatter
title: 商品销售额 × 毛利率（气泡=毛利额）
group: product
y: [gmv, gm, gp]
note: 右下为“规模大、毛利低”（手机、平板），左上为“规模小、毛利高”（零食礼盒、耳机、床垫）。
```
```chart
type: hbar
title: 商品毛利额 Top 10
x: product
y: gp
top: 10
note: 沙发、床垫两个家居商品位列毛利额前二。
```

## 五、结论与行动建议
> 今年的增长是**“量增、价稳、利薄”**：西南与直播电商是增长引擎，但渠道结构向低毛利倾斜，毛利率同比下降约 0.65 个百分点。下一阶段应在保持增速的同时修复毛利结构。

1. **直播电商提毛利**：优化直播选品，提高耳机、床垫、零食礼盒等高毛利商品的直播占比，控制补贴力度。
2. **华北止跌**：华北 10 个月中有 7 个月同比为负，建议专项复盘门店与经销商渠道，制定季度止跌目标。
3. **西南目标校准**：西南增速最快但目标达成率偏低，建议复核目标设定，并保障库存与履约跟上增长。
4. **数码品类结构优化**：手机拉动规模但毛利低，平板同比下滑，可通过配件、延保等高毛利附加销售提升品类毛利。
