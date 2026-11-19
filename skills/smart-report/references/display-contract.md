# 展示契约与保存门禁

本文落实[技能执行契约](../SKILL.md#执行契约强制)的 R-01、R-07–R-10，并细化主流程第 6 步的保存门禁。本文第 1–5 节为展示契约的内部章节编号。先读取生效规则，再写 HTML；不能在生成后用默认规则解释违例。静态脚本只能检测已定义的结构和已知违例，不能证明任意 JavaScript 或业务结论正确；因此浏览器复核与事实对账也是保存条件。

## 1. 将要求变为断言

在业务 Markdown 模板中保存下表，外部规则保留原文与来源。一个规则涉及多个位置时逐项列出，不能只写“全篇一致”。

| ID | 来源/优先级 | 断言 | 适用面 | 允许模式 | 检查方法/裁决 |
|---|---|---|---|---|---|
| R-07 / U-NUM-01（示例） | 当前用户明确要求 | 率值及源端率差仅用 `%`；禁止 `pp`、`百分点`、`pct` | 正文、卡片、表格、图表轴/标签/tooltip/图例、JS、空态 | 无 | 启用 `percent-only`；内置率差许可不构成例外 |
| R-09（示例） | 适用的场景色值规则 | 同指标同状态使用同一色值 | 结论、卡片、表格、图形、标签、tooltip | 无 | 原始值判断；统一状态映射 |

“例外/允许模式”只记录生效规则明确许可的内容，无许可写“无”。高优先级与低优先级的要求能够同时满足时一并执行；低层条目的许可不能解除高层禁止。用户明确修改展示规则时记录替代关系；Agent 不能用“更适合数据”自行放宽。

## 2. 唯一格式化与颜色入口

HTML 中顺序固定为：展示契约 JSON → 快照 JSON → 原文内联格式化模块（或源文档占位 `<script id="srg-formatters" data-srg-asset="report-formatters.js"></script>`）→ 应用脚本。契约中的值从生效规则表填写，不根据示例推定其他报告的默认要求。源文档也可用 `<style data-srg-style="<style_id|base>"></style>` 和 `<script data-srg-vendor="echarts"></script>` 交给服务端在 `preview_report_html` / `save_report` 时展开为离线单文件，展开后的文件仍须通过全部静态与浏览器审计。

```html
<style data-srg-style="实际style_id"></style>
<script type="application/json" id="srg-display-contract">
{"version":1,"rate_symbols":"percent-only","rule_sources":["U-NUM-01：用户要求率值与率差全篇仅用百分号"],"colors":{"up":"#047857","down":"#B91C1C","neutral":"#475569","met":"#047857","miss":"#B91C1C"}}
</script>
<script type="application/json" id="srg-data-sales" data-srg-snapshot="实际快照ID"></script>
<script data-srg-vendor="echarts"></script>
<script id="srg-formatters" data-srg-asset="report-formatters.js"></script>
<script>
const { fmtNum, fmtPct, fmtDelta, metricState, stateColor, applyMetric } = SrgDisplay;
// fact 必须来自唯一的直接事实；缺失或重复事实绑定为 null，不取首条。
applyMetric(document.getElementById('yoy'),
  fmtPct(fact?.yoy, {scale: 'decimal', signed: true}),
  metricState(fact?.yoy, {kind: 'trend', scale: 'decimal'}));
</script>
```

格式化模块位于技能的 `assets/report-formatters.js`，审计脚本为同目录 `assets/audit_report.py`。使用 `data-srg-asset="report-formatters.js"` 占位标签时标签体必须为空，服务端展开后写入完全一致的原文；直接内联时也必须保持原文一致，不能按记忆重写。没有本地技能的 agent 分别调用 `get_skill_guide(asset="report-formatters.js")`、`get_skill_guide(asset="audit_report.py")`，将返回原文保存为同目录的两个文件。脚本仅依赖 Python 3 标准库。静态检查放行内置 ECharts 的固定内容摘要；使用其他运行时时需先维护并测试审计脚本的精确摘要清单，不能仅加 vendor 标签来豁免。

- `fmtNum(v, {digits, divisor, unit, signed})`：数字及已确认的单位换算；禁止用 `unit` 偷渡率值符号。
- `fmtPct(v, {scale, digits, signed})`：率值。`scale='decimal'` 表示 0.0978 → 9.78%；`scale='percent'` 表示 9.78 → 9.78%。未知尺度返回 `—`。
- `fmtDelta(v, {kind, scale, digits, ...})`：只接收一个源端直接差值。`kind='rate'` 保留率差语义，按契约输出符号；`kind='number'` 格式化源端金额/数量差额。不接收两个业务值做减法。
- `metricState(v, {kind, scale, threshold})`：`kind='trend'` 判断正/负/零；`kind='attainment'` 按确认阈值判断，阈值与值须同尺度。缺数、未知尺度、缺阈值为 neutral。
- `applyMetric(element, text, state)` 清除旧状态并写文本；`stateColor(state)` 返回契约中色值，图表柱、标签、tooltip 共用。

禁止在应用脚本、模板字符串、表格单元格、图表配置中另写 `Intl`、`toFixed`、`toPrecision`、`toLocaleString`、`value + '%'` 或手工拼接率值符号。图表轴、标签、tooltip 的数值也必须调用上述入口；新增展示面先登记入口。不能依靠函数改名、转义或拆分字符串绕过扫描。静态正文数字可写成核实后的文字，但必须逐项核对规则；动态图表布局的坐标与技术索引不属于业务格式化。

固定的状态 CSS 必须内联：

```css
.srg-up { color: var(--srg-up); }
.srg-down { color: var(--srg-down); }
.srg-neutral { color: var(--srg-neutral); }
.srg-met { color: var(--srg-met); }
.srg-miss { color: var(--srg-miss); }
```

以下是未被用户或场景覆盖时的映射示例；实际表格须记录本报告的每个受控指标、源字段、尺度与阈值。趋势色表示升降，不能自动解释为经营好坏；成本等指标的好坏须有明确规则。

| 指标 | 原始条件 | class | 色值（示例） |
|---|---|---|---|
| 同比/源端差额 | > 0 | srg-up | #047857 |
| 同比/源端差额 | < 0 | srg-down | #B91C1C |
| 同比/源端差额 | = 0 | srg-neutral | #475569 |
| 完成率 | ≥ 同尺度确认阈值 | srg-met | #047857 |
| 完成率 | < 同尺度确认阈值 | srg-miss | #B91C1C |
| 任一指标 | 缺失/重复/未知尺度 | srg-neutral | #475569 |

契约 JSON 是语义色值的唯一定义位置。其他 CSS token 可用于背景、网格和普通分类系列；不能为同一升降/达标状态覆盖上述 class 或在章节、桥图、表格中另选色。图表代码不得硬编码色值；用 `stateColor` 或已定义的非语义 CSS token。

## 3. 正反例与合法命中

| 场景 | 错误 | 正确 |
|---|---|---|
| 生效规则为 percent-only | 毛利率 9.78%，同比差 -3.66pp、低于目标 0.67pp | 毛利率 9.78%，同比差 -3.66%、低于目标 0.67%（均有源端率差事实） |
| 事实字段为率差 | 因禁用 pp，把该事实改称“相对降幅”或重新计算 | 字段语义仍标为“率差/目标差”；模板记录符号约定，不改变原值及尺度 |
| 表格格式化 | `row.delta == null ? '—' : row.delta.toFixed(2) + 'pp'` | `fmtDelta(row.delta, {kind:'rate', scale:'percent'})` |
| 空值 | `fmtPct(value || 0)` 并保留红绿 class | `applyMetric(el, fmtPct(value, {scale}), metricState(value, {kind:'trend', scale}))` |
| 图表配色 | 表格正值绿字，桥图正值蓝柱 | 两处都使用 `metricState`；图形调用 `stateColor` |
| 阈值边界 | 先舍入为 100 再判断达标 | 用未舍入源值与同尺度阈值比较，显示格式独立 |

百分比与百分点在通常统计语义下不等价。上述第一行是特定生效展示约定，不能据此把源端百分点数值当作相对变化率。没有统一禁用要求时使用 `rate_symbols='semantic'`：已确认的源端率差由 `fmtDelta` 输出 pp，率值由 `fmtPct` 输出 %。

审计器允许的命中范围固定，不得临时删改扫描规则：

| 位置 | 允许内容 | 边界 |
|---|---|---|
| 静态轴/表头单位及 JS 字面量 | `单位：%` | 不能用于业务值拼接；应用代码中只放行该完整字面量 |
| CSS / 行内 style | `width:100%`、布局值 | 不是数字格式化；仍检查外部资源 |
| application/json 数据/契约 | 原始单位、字段名、规则原文 | 不属于已显示文案；绑定后展示内容必须按六类落地面复核 |
| 固定格式化模块 | 已维护的符号、Intl 实现 | 原文完整匹配才放行，禁止局部修改 |
| 固定摘要的内嵌 ECharts | 库内部格式化和坐标代码 | 只有内容摘要匹配才放行，应用代码不可混入 |

HTML 注释不算展示内容。普通展示文本、aria-label、title、alt、图表应用脚本均在扫描范围。不得因 CSS 的合法百分号或快照中的原始 pp 单位造成误报，就跳过全文检查。

## 4. 六类落地面清单（逐项完成）

| 复核 key | 落地面 | 必查内容 |
|---|---|---|
| prose | 静态正文/结论 | 每个符号、单位、率差语义、事实证据；动态数字随筛选变化 |
| cards | 指标卡 | 值、同比、达成率的入口、精度、文案及实际颜色 |
| tables | 表格 | 表头单位、每类单元格、合计行；不得用三元表达式另写格式化 |
| charts | 图表 | 轴标签、数据标签、tooltip、图例；桥图/瀑布图与文字的状态色一致 |
| formatters | JS 格式化 | 只用原文模块，所有数字出口引用统一入口，无另写逻辑 |
| empty | 空态/缺数 | 显示 `—`/暂无数据，不空值转零、不残留旧颜色和判断 |

复核记录还必须包含六组行为证据：`facts`（默认及代表性筛选逐值对账）、`filters_reset`（每个维度/组合/重置）、`sign_threshold_boundaries`（正负零及阈值高/低/等）、`missing_zero_duplicate_scale`（空结果、缺数、真零、重复、未知尺度）、`offline`（离线打开和切换无子资源/接口请求及运行错误）、`desktop_narrow`（实际容器的宽窄屏、溢出及目录）。正式数据不含边界时使用明确标注的独立夹具；正式报告不混入夹具。

## 5. 可执行门禁（失败即停止保存）

以下路径为示例，可使用任务工作目录的绝对路径。MCP-only agent 先按[本文第 2 节：唯一格式化与颜色入口](#2-唯一格式化与颜色入口)取回两个审计资产。

```sh
# ① 含快照及内置资产引用的源文档，必须先通过静态门禁。
python3 skills/smart-report/assets/audit_report.py /tmp/report-source.html --phase source
# ② 用 preview_report_html 展开（记下 preview_id）并下载为 /tmp/report-preview.html，再创建待填复核记录。
# 此步预期返回 1：pending 记录不构成通过，也不会覆盖已存在的记录。
python3 skills/smart-report/assets/audit_report.py /tmp/report-preview.html --source /tmp/report-source.html --write-review /tmp/report-review.json
# ③ 在单次集中浏览器会话完成离线、事实、六类落地面及全部边界复核，逐项填写 status 和 evidence。
# ④ 最终门禁：必须返回 0，才可提交记录绑定的源文档（或直接传该次预览的 preview_id）。
python3 skills/smart-report/assets/audit_report.py /tmp/report-preview.html --source /tmp/report-source.html --review /tmp/report-review.json
```

`status` 使用 `pass`，证据需写出实际选择条件、观察结果、截图或运行记录位置；禁止未经检查批量填 pass。仅不存在的卡片/表格/图表允许 `na` 并解释原因，其他项不可跳过。脚本只核查记录完整性和文件哈希，不会替你完成浏览器检查或认证证据真实性。

退出码：`0` 通过当前阶段；`1` 命中规则/复核未完成；`2` 文件、JSON 或运行错误。任何非 0 均阻断相应流程。最终审计会复查源文档；输出中包含源文档及展开文件 SHA-256。任意文案、格式函数、表格表达式、图表配置或规则变更后，重新审计源文档、重新预览、重新复核并绑定新哈希；不能修改哈希冒用旧证据。

存量报告在被修改时完整迁移到当前展示契约后执行门禁；已有快照继续用所选 `base_version` 的引用。旧版手工内联 JSON 可在保留全部原值的前提下整理为 `{columns, rows}`，使用 `data-srg-snapshot="embedded"` 数据块，不因迁移展示入口重新取数。无权访问的数据不重新抓取。缺少本地执行或浏览器能力时，明确报告未完成的检查，不能绕过保存门禁。

此门禁由 skill 工作流执行；现有 `save_report`/`revise_report` 接口不会验证本地审计记录。不得将本地通过描述为服务端强制校验，也不得因此省略工作流门禁。

[example-report.html](example-report.html) 是明确标注的独立夹具，可用 `--phase source --fixture` 检查。`--fixture` 的通过结果不能作为正式报告保存依据；正式门禁拒绝契约中的夹具标记。
