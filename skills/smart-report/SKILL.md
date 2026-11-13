---
name: smart-report
description: 智能报告生成（SmartReportGen）。当用户需要“生成/做一份/出一个 …分析报告、经营报告、数据报告、看板式报告、HTML 报告”，或要求基于业务数据分析某个业务场景（销售、库存、客服、财务等）并输出报告时使用。通过 smartreportgen MCP 检索业务场景与数据表、探查数据、设计报告模板（markdown）、检索样式规则，最终生成并存储可交互的 HTML 报告。
---

# 智能报告生成（SmartReportGen）

依赖 MCP 服务 `smartreportgen`（Streamable HTTP，默认 `http://localhost:18600/mcp`）。
若工具不可用，提示用户先部署服务并为所用 agent 注册 MCP（见部署文档 `docs/DEPLOYMENT.md`「接入 Agent」），不要凭空编造数据。

本技能与 agent 无关（Claude Code、Codex、Cursor 等均可使用）。没有技能机制的 agent 可调用 MCP 工具 `get_skill_guide` 获取本文全文。

**认证与权限**：Codex CLI 可用 `codex mcp login smartreportgen` 通过浏览器登录海尔 IAM，并自动携带 IAM access token。使用 Bearer 配置的客户端可传入 海尔 IAM access token。工号决定可访问的场景和表。
- 检索、取表、探查、查询、校验、渲染都只覆盖**已授权**的场景与表；任何 SQL（含 JOIN / 子查询）引用未授权表都会被拒绝。
- 收到 `error_type: permission_denied` 时**不要改写 SQL 绕过**，直接告诉用户缺少哪张表/哪个场景的权限、应向管理员申请；可在已授权范围内给出降级方案（例如去掉依赖该表的章节）。
- 401 / 令牌无效或过期时，按客户端的认证方式重新登录或更新令牌。Codex CLI 使用 `codex mcp login smartreportgen`。
- 模板与报告按创建人隔离：只能读取、修改、渲染本人创建的模板。

产出物：一份存储在报告库中的**报告模板（markdown）**和由它生成的**HTML 报告**。
最终回复给出需海尔 IAM 登录的报告链接、核心结论摘要和需要用户确认的口径假设。

## 流程总览

| 步骤 | 目的 | MCP 工具 |
|---|---|---|
| a | 解析需求 → 检索已授权场景 → 获取分析思路和已授权的数据表 | `search_business_scenarios` → `get_dataset_tables` |
| b | 探查数据 → 确定分析目标 → 设计章节/结论/图表/布局 | `describe_table`、`run_query`、`get_template_spec` |
| c | 按需设计全局筛选与联动 | （写入模板 front matter） |
| d | 获取样式约束（合集）→ 默认遵守，必要时按判断调整，其余自由发挥 | `get_style_rules` |
| e | 校验并存储模板 → 生成 HTML | `validate_report_template` → `save_report_template` → `render_report_html` |
| f | 存储 HTML（render 已自动存储；若对 HTML 做了改写用 save_report） | `save_report` / `get_report` |

---

## a. 解析需求，锁定业务场景

1. 先从用户需求中提炼：**分析对象、时间范围、关注维度、关注指标、对比方式（同比/环比/目标）、受众**。缺失项不必追问，采用合理默认并在最终回复中说明。
2. 调用 `search_business_scenarios(query=用户原话, top_k=3)`。
   - 取第 1 名；若返回 `tip` 提示相关度接近或偏低，阅读各候选的 business_description 判断，仍无法确定时**向用户确认**再继续。
   - 无命中：可能是业务术语不同，也可能是**没有该场景权限**（检索只覆盖已授权场景）。换术语重试一次，仍无则告知用户并停止。
3. 调用 `get_dataset_tables(dataset_id)` 获取场景的分析思路（`analysis_approach`）、**你有权限的**数据表与数据源类型（决定 SQL 方言：postgresql / mysql / starrocks / oracle / api=DuckDB）。
   若返回 `unauthorized_table_count > 0`，说明还有表无权访问：只用已授权的表设计报告，并在最终回复中说明哪些分析因权限受限未覆盖。

## b. 探查数据，确定分析目标与报告结构

**先探查，后设计；结论必须来自查询结果，禁止编造数字。**

1. 把场景的 `analysis_approach` 作为探查和设计的重要依据。结合用户关注的问题，确定先查哪些指标、对比哪些对象、验证哪些假设。若场景未填写分析思路，就从用户需求与实际数据出发。
2. 对相关表调用 `describe_table`，核对字段含义、数据粒度、关联键、实际覆盖范围和缺失情况。用 `run_query` 验证对比口径、定位变化或异常，并取得支撑结论的证据。发现数据与分析思路不符时，按核实结果调整分析。
3. 用一句业务人员看得懂的话说明报告要回答的问题，写入 `analysis_goal`。结合用户需求、场景分析思路和查询证据，组织章节与结论，选择能讲清证据的图表或文字，并决定展示顺序和布局。
4. 用 `get_template_spec` 阅读模板语法与渲染能力，按所需粒度准备查询。检查跨表关联、同比等对比口径，以及筛选后的比率和去重计数能否正确重算。

## c. 全局筛选与联动

- 结合用户需求、场景的分析思路和实际数据，按受众会切换的分析口径选择筛选器。常用筛选组合可用 `presets`，需要联动时按模板语法配置图表和筛选。
- 同一个筛选在不同查询中字段不同时可用 `targets` 映射。对不应受筛选影响的查询可用 `exclude`，并向读者说明适用范围。

## d. 样式设计

生效顺序为 **你的决定 ≥ 业务场景规则 ≥ 通用规则 ≥ 内置经营报告样式**。默认主题提供排版、页面布局和配色。根据数据和受众决定如何呈现报告。

1. 调用 `get_style_rules(business_scenario, query=用户需求)`，得到 `specified`——业务场景规则（当前用户本人为该场景配置的）
   ≥ 通用规则（管理员配置的）≥ 内置样式 按优先级合并的约束：
   - `specified.theme` 提供视觉样式取值，例如封面、目录、章节面板、图表配色和表格排版，渲染时自动生效。
   - `specified.items`：展示要求（好坏判定 / 涨跌颜色 / 数字格式 / 文案要求 / 其他展示要求），各类别内业务场景规则的条目排在前面，同类冲突时以排在前面的为准。
   - 内置页面布局中，指标卡、图表卡和表格卡在同一行时按最高的卡片等高拉伸，保留卡片间距并分配行内剩余宽度。单张卡片占满所在行，最后一行和窄屏换行后也遵循此规则。可用 `flex-wrap`、`align-items: stretch` 和卡片的 `flex-grow` 实现。
   - 同一报告内同类卡片跨章节复用同一套 CSS 类和图表基础配置。所有表格共用表头样式、行高、隔行底色及文字列和数值列的对齐规则。所有柱状图共用柱宽上限、圆角规则、坐标轴、网格、图例、标签和配色规则。其他同类卡片按同样方式复用。排序、单位、轴范围及正负、状态、分类配色按数据含义设置。
   - 章节安排、图表类型和筛选条件在 b、c 节决定。样式条目中涉及这些分析决策的文字不作为展示约束。
2. **默认遵守约束，确有理由时按自己的判断调整**：例如用户在对话中明确提出的要求（如“红涨绿跌”）、某条要求不适合本报告的数据。
   视觉样式在模板 `theme` 中覆盖即可（按你的设置展示，校验会提示覆盖了哪些项）；不采纳的展示要求不写进模板。
   所有偏离约束的地方都要在最终回复的“口径与假设”中说明。
3. 未指定的视觉样式与展示要求**自由决定**：在模板 `theme` 中设置任何未指定的样式项（可用项见 `get_template_spec`「主题」），也可以不设置。
   保持整份报告一致，例如数据密集的报告减小卡片内边距与表格行高，面向管理层的概览让指标卡与头部居中（`kpi_align: center`、`header_align: center`）。

## e. 校验、存储与生成

1. `validate_report_template(markdown, dataset_id)`：修复全部 `errors`；核对 `warnings`，检查返回的行数与筛选取值是否符合预期。循环直到 `ok: true`。
2. 核对结论文本中的每个静态数字都能在 `run_query` 结果中找到出处；与实时指标冲突时以实时指标为准改写文本。
3. `save_report_template(name, markdown, dataset_id, user_requirement, analysis_goal, business_summary)` → 得到 `template_id`（修改已有模板时传 `template_id`）。用通俗语言填写 `business_summary`，让同事知道模板适合回答哪些问题、重点看哪些指标或对比。使用业务名称，省去 SQL、字段名和模板参数。
4. `render_report_html(template_id)` → 生成 HTML 并**自动存入报告库**，返回 `report_id` 与`url`（报告创建人或管理员使用海尔 IAM 登录后可看）。
   在桌面和窄屏分别检查卡片行是否等高并铺满可用宽度，并对照不同章节中的同类卡片，检查表格排版与同类图表样式在初始和筛选状态下一致。

## f. 存储 HTML

- `render_report_html` 已完成存储。仅当需要对生成的 HTML 做额外改写时：`get_report(report_id, include_html=true)` → 修改 → `save_report(name, html, template_id)`。优先修改模板再重新渲染，而不是直接改 HTML。

## 最终回复格式

```
已生成报告：<标题>
- 报告链接：<url>（离线单文件：<url>/download）
- 模板：#<template_id> v<version>，业务场景：<场景>
- 核心结论：摘要，附支持结论的关键数字
- 口径与假设：时间范围、同比口径、未覆盖的维度/数据缺口、偏离样式约束之处及理由
- 权限说明（如有）：因无权访问而未纳入的表/分析
```

## 参考

- 模板完整语法：`references/template-spec.md`（与 MCP `get_template_spec` 相同）
- 完整示例（零售销售经营分析）：`references/example-retail.md`
- 自适应卡片布局示例：`references/example-report.html`。
