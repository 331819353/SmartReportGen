# 子智能体（Subagent）并行编排指南

本指南用于将报告生成的重耗时步骤拆分为可并行的子任务。遵循「先探查取数、串行锁定契约、再并行拼装、最后双轨验收与串行入库」的拓扑。

## 适用边界与鉴权前提

- **按需启用**：单表、少量简单章节或单点微调可由主智能体直接处理。以下情况适合子智能体并行：
  - 授权表较多（`total_tables > 10`）或需对多张表做 `mode="full"` 全量统计与口径摸底；
  - 需围绕多个互不依赖的业务主题域执行取数；
  - 报告包含多个独立图表或明细章节；
  - 验收同时暴露多个章节报错；
  - 需一次批量生成或重跑多份不同报告。
- **鉴权与隔离**：快照、样式、草稿与预览均按员工工号隔离。子智能体直接调用 MCP 工具时，须由宿主提供可核实为同一员工的海尔 IAM Bearer 凭证。若宿主未提供该能力，主智能体统一负责 MCP 读写，子智能体接收契约与必要样本后编写章节或复核。

## 1. 指南样式解析与多表深度探查并行（阶段 1–2）

`prepare_report_context` 使用最多 4 个线程采集前 10 张授权表的轻量画像。`describe_tables` 每页也用最多 4 个线程，传 `include_profile=true` 才返回轻量画像。表数 ≤ 10 且只需轻量样本时，一次准备调用即可。适合并行的拆分方式：

- **指南与样式子智能体**：调用 `get_report_template` 与 `get_style_rules`，整理必选章节、指标公式、单位尺度及 `specified.items` 展示要求。
- **数据摸底子智能体（按表簇并行）**：翻页拉取剩余表结构（`describe_tables(offset=...)`）、对大表跑 `describe_table(mode="full")` 检查总行数与极值，或执行只读探查 SQL 核实时间跨度、基期覆盖与维表连接唯一性，仅向主智能体回传结构化口径摘要。
- **修订双路准备**：换账期修订时，一路调 `get_report(include_source=true)` 拆解旧版章节与复用块，另一路并行核查新账期数据完整性。

## 2. 多模块事实快照取数（阶段 2，条件并行）

`run_query(snapshot=true)` 将完整的查询结果原子发布到以 UUID 寻址的独立文件。数据源的连接池、接口缓存等仍可能限制并发：

- **可并行**：无交叉核对要求的独立数据块（如筛选器维度枚举、月度趋势、Top 明细表、独立辅助视图）可分派多个数据子智能体并发执行并回传 `snapshot_id`。
- **勾稽数据同批读取**：需要相互核对的多组结果（如总览 KPI 总额与各区域拆解明细之和）须合并在单条聚合 SQL 中，或通过单次 `query_report_data` 同批取得。该工具接受最多 16 条查询计划。PostgreSQL、MySQL 事务型表和 Oracle 使用同一只读事务，API 固定到已确认的接口数据，StarRocks 使用单条计划。[事实取数契约](fact-contract.md)

## 3. 串行同步屏障：初始化草稿与契约（阶段 3）

进入章节并行编写前，**必须由主智能体串行完成 `create_html_draft`**，并向所有章节子智能体下发统一的四项契约：

1. **快照清单**：`snapshots` 映射的 `blockId`、工具返回的实际列名、`$decimal` 包装值及数值单位；
2. **筛选协议**：`shared_js` 中 `SRG.filters.subscribe(state => ...)` 的状态结构（如统一筛选对象字段名与默认值）；
3. **有序章节槽位**：在 `create_html_draft` 中传入 `sections=[{"section_id": "...", "title": "..."}, ...]` 预置顺序和标题。修订时补丁依次应用到基准章节。主智能体统一管理顺序、快照绑定和共享配置。
4. **DOM 与样式约定**：每章使用独占的元素 `id` 前缀（如 `sec-<section_id>-*`）。章内节点也可通过 `SRG.section(sectionId).querySelector(...)` 与 `data-*` 属性定位。局部 CSS 选择器挂载在 `#srg-sec-<section_id>` 下。所有脚本访问同一个文档与 `window.SRG`。

## 4. 章节 HTML / CSS / JS 并行拼装（阶段 4）

服务端为每个章节生成独立 `<section id="srg-sec-<section_id>" data-srg-section="<section_id>">` 容器，并将各章 JS 隔离在独立函数作用域内执行，同时以跨进程分片锁保护草稿写入。根据环境选择以下任一保序模式：

- **模式 A：纯产代码 + 单次批量写入（默认推荐，兼容无 MCP 子智能体）**
  各章节子智能体按契约并行输出 `{section_id, title, html, css, js}`；主智能体核对契约一致后，调用**一次** `upsert_html_section(draft_id, sections=[...])` 原子写入全部章节。
- **模式 B：预置槽位 + 子智能体并发直写**
  主智能体已在 `create_html_draft` 中通过 `sections` 建好有序槽位。各子智能体并发调用 `upsert_html_section`，传自己的 `section_id` 和待写入的内容字段，省略位置参数和需要保留的预置标题，服务端会原地更新。

章节槽位缺失时，省略位置参数的并发写入按获得草稿锁的顺序追加。`after_section_id` 指向尚未写入的章节时会返回排序参照不存在的错误。

## 5. 双轨校验、按章节并行热修与串行入库（阶段 5）

- **Chromium 校验 × 静态口径复核并行**：主智能体调用 `verify_html_draft` 验证离线可运行性（无外部请求、无脚本异常、图表有尺寸、控件可操作）的同时，可并行派发只读复核子智能体对照[展示契约](display-contract.md)核查全页口径一致性、单位尺度、涨跌色及空值表达（「—」与 `0`）。
- **按 `diagnostics.section_ids` 并行修复**：校验返回 `verify_failed` 时，查看当前失败阶段可归因的章节。源 HTML 和验收期间生成的重名元素 `id` 都会报告在 `diagnostics.duplicate_ids` 中：
  - 若有 ≥ 2 个非 `__shared__` 章节需修复，主智能体按 `section_id` 并行唤醒对应子智能体（可用 `get_html_draft(draft_id, include_code=true, section_id=...)` 或 `section_ids=[...]` 按需读取故障章节源码），完成后统一重跑校验；
  - 若故障涉及 `__shared__`（共享脚本或骨架），主智能体通过 `get_html_draft(draft_id, include_code=true, section_id="__shared__")` 读取并串行修复。
- **串行入库屏障**：主智能体在章节写入和复核收敛后单次提交 `save_report` 或 `revise_report`。修订时以实际读取的 `revision` 为 `base_version`，用同时返回的 `latest_version` 作为 `expected_version` 检查其他写入者是否已更新报告。

## 6. 跨报告级端到端并行

批量生成多份独立报告（如各区域分报告）或并行产出多版布局供用户挑选时，可按每份报告 1 个子智能体全链路执行。草稿锁根据工号和 `draft_id` 映射到 256 个分片，同一分片的操作串行。各 MCP 进程的 Chromium 池默认同时校验 4 份报告，用 `SRG_BROWSER_WORKERS` 可配置为 1–8。
