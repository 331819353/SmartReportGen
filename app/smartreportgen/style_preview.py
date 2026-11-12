"""样式规则预览：用固定的示例数据渲染一份小报告，展示样式规则在真实报告中的效果（不查询任何数据源）。"""
from __future__ import annotations

import datetime as dt
import random

from . import design, repo
from .renderer import build_html
from .template_parser import parse_template

SAMPLE_MD = """---
title: 样式预览 · 示例经营报告
subtitle: 示例数据，仅用于预览样式效果
scenario: {scenario}
analysis_goal: 查看当前样式规则下，报告的配色、字体、卡片与图表效果
filters:
  - {{id: region, label: 大区, type: multi, field: region}}
  - {{id: month, label: 月份, type: range, field: month}}
queries:
  s:
    sql: SELECT 1
    measures:
      gmv: {{expr: "sum(amount)", name: 销售额, format: wan, unit: 元}}
      yoy: {{expr: "sum(amount)/sum(amount_ly)-1", name: 销售额同比, format: percent}}
      cost_yoy: {{expr: "sum(cost)/sum(cost_ly)-1", name: 成本同比, format: percent}}
      rate: {{expr: "sum(amount)/sum(target)", name: 目标达成率, format: percent}}
      tgt: {{expr: "sum(target)", name: 销售目标, format: wan, unit: 元}}
      sat: {{expr: "sum(sat_sum)/sum(sat_n)", name: 满意度, format: float2, unit: 分}}
      gap: {{expr: "sum(amount)-sum(target)", name: 目标差额, format: wan, unit: 元}}
---

## 一、指标卡与好坏判定
> 示例结论：销售额 {{{{gmv}}}}，同比 {{{{yoy}}}}；目标达成率 {{{{rate}}}}。

### 绿·向好 / 黄·关注 / 红·承压 / 蓝·不判
```chart
type: kpi
y: sat
title: 满意度（向好）
status: {{direction: up, good: 4.5, warn: 4.2}}
```
```chart
type: kpi
y: rate
title: 目标达成率（关注）
status: {{direction: up, good: 1.0, warn: 0.95}}
```
```chart
type: kpi
y: gap
title: 目标差额（承压）
status: {{direction: up, good: 0, warn: -1000000}}
```
```chart
type: kpi
y: gmv
title: 销售额（不判）
sub: [{{measure: yoy, trend: up}}, {{measure: cost_yoy, trend: down}}]
```

## 二、图表配色与版式
> 多系列按分类配色取色，有正有负的柱用正负色阶，热力图用深浅色阶。

### 常用图表
```chart
type: line
title: 月度销售额（分类配色）
x: month
series: region
y: gmv
span: 8
```
```chart
type: donut
title: 渠道构成
x: channel
y: gmv
span: 4
```
```chart
type: bar
title: 各大区同比（正负色阶）
x: region
y: yoy
span: 6
```
```chart
type: bullet
title: 各大区目标达成（子弹图）
x: region
y: [gmv, tgt]
status: {{direction: up, good: 1.0, warn: 0.95}}
span: 6
```
```chart
type: heatmap
title: 大区 × 月份 销售额（深浅色阶）
x: month
series: region
y: gmv
span: 7
height: 300
```
```chart
type: table
title: 大区明细（表格）
dims: [region]
y: [gmv, yoy, rate]
headers: {{region: 大区}}
span: 5
height: 300
```
"""

COLUMNS = ["month", "region", "channel", "amount", "amount_ly", "cost", "cost_ly", "target", "sat_sum", "sat_n"]


def _rows() -> list[list]:
    rng = random.Random(11)
    growth = {"华东": 1.12, "华南": 1.18, "华北": 0.93, "西南": 1.35}
    base = {"华东": 520, "华南": 360, "华北": 300, "西南": 220}
    out = []
    for m in range(1, 7):
        for reg, g in growth.items():
            for ch, share in (("门店", 0.45), ("线上商城", 0.35), ("直播电商", 0.2)):
                amt = base[reg] * share * (1 + 0.06 * m) * 10000 * rng.uniform(0.92, 1.08)
                ly = amt / g
                cost = amt * 0.78
                out.append([f"2026-{m:02d}", reg, ch, round(amt), round(ly), round(cost), round(cost / 1.04),
                            round(amt / (0.97 if reg != "华北" else 0.9)), round(4.6 * 10, 1), 10])
    return out


def render_preview(business_scenario: str | None, exclude_id: int | None = None, draft: dict | None = None,
                   owner: str | None = None) -> str:
    md = SAMPLE_MD.format(scenario=business_scenario or "通用")
    tpl, errors, _ = parse_template(md)
    if errors:
        raise RuntimeError("示例模板错误：" + "；".join(errors))
    theme = design.full(repo.effective_style(business_scenario, exclude_id, draft, owner)["theme"])
    data = {"s": {"columns": COLUMNS, "rows": _rows()}}
    return build_html(tpl, data, theme, dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
