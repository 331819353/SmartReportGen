# SmartReportGen · 智能报告生成（Skill + MCP）

用自然语言新建或修改**带全局筛选联动的交互式 HTML 分析报告**。典型流程：

1. 管理员部署数据源，配置员工可引用的表和固定行范围。
2. 员工提出新建或修改报告的需求。修改从报告库定位基准，多个匹配候选由用户确认。新建先找已有场景，缺少适用场景时，由员工从已授权的数据源自行建场景并填写分析思路。
3. 模型根据场景的分析思路、授权表元数据、数据证据和样式规则设计并保存模板。已有汇总值优先按维度筛选、直取。
4. 以模板为基础按章节与卡片多轮调整，由服务端编译 HTML。纯展示修订可复用原快照。
5. 服务端验收通过后，保存对应 HTML 及版本记录，交付固定链接和离线文件。

[主流程与 MCP 用法](skills/smart-report/SKILL.md) · [报表模板、图形与样式契约](skills/smart-report/references/declarative-report.md)

标准布局使用结构化模板。需要自由 HTML/CSS 的报告可使用[自由版式模板](skills/smart-report/references/free-report.md)，通过 MCP 逐模块修改并由内置运行时绑定业务数字。两类模板共用正式验收和版本发布。

## 快速开始

```bash
cp .env.example .env          # 配置海尔 IAM、SECRET_KEY 与向量模型（详见 docs/DEPLOYMENT.md）
docker compose up -d --build
```

| 服务 | 地址 | 说明 |
|---|---|---|
| 管理台 + REST API | http://localhost:18600 | 接入说明 `/api-guide`，Swagger `/docs` |
| MCP | http://localhost:18600/mcp | Streamable HTTP，仅接受海尔 IAM Bearer 令牌 |
| PostgreSQL(pgvector) | 127.0.0.1:55433 | 用户/库见 `.env` |

首次启动（`SEED_DEMO=true`）写入演示数据源与数据集：零售销售（PostgreSQL，只读账号 `demo_reader`）、供应链库存（PostgreSQL）、客服工单（HTTP API）三个业务场景。
报告默认使用内置经营报告样式。通用规则由管理员配置，个人场景样式由员工配置。`IAM_BOOTSTRAP_ADMINS` 中的工号首次登录即为管理员，其余员工首次登录或调用 MCP 时自动注册为普通用户。

### 接入 Agent（Claude Code / Codex / 其他）

技能独立放在 `skills/smart-report/`，与 agent 无关；MCP 服务也内置同一份技能（工具 `get_skill_guide`、提示词 `smart_report`）。

从海尔 IAM 取得 access token 后，在启动 agent 的终端中将它读入环境变量。

```bash
printf '海尔 IAM access token: '
IFS= read -r -s SMARTREPORT_TOKEN
printf '\n'
export SMARTREPORT_TOKEN
```

| Agent | 在本仓库内 | 全局安装 |
|---|---|---|
| Claude Code | 注册 MCP 服务后启动 `claude`，项目技能自动发现 | `claude mcp add --transport http smartreportgen http://localhost:18600/mcp --header "Authorization: Bearer $SMARTREPORT_TOKEN"`，再执行 `scripts/install-skill.sh claude` |
| Codex | 注册 MCP 服务后，在同一终端启动 Codex，项目技能自动发现 | `codex mcp add smartreportgen --url http://localhost:18600/mcp --bearer-token-env-var SMARTREPORT_TOKEN`，再执行 `scripts/install-skill.sh codex` |
| Cursor / VS Code / Gemini CLI / OpenCode / 其他 | 配置模板见 `integrations/` | 同左；无技能机制时让 agent 读 `AGENTS.md` 或调用 `get_skill_guide` |

接入后可直接向 agent 提需求，例如：“帮我做一份 2026 年 1-10 月零售销售经营分析报告，重点看各大区、渠道和品类的增长与毛利变化，并和去年同期对比”。

**完整的部署、配置、生产加固、备份升级与故障排查见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)。**

## 认证与权限

**角色与可见范围**（服务端默认拒绝，界面同步隐藏）：

| 功能 | 管理员 | 普通用户 |
|---|---|---|
| 数据源 | 新建、编辑、测试、浏览表、SQL 查询、删除 | 可引用开放给本人或角色的表，连接配置和密钥由管理员维护 |
| 数据集 | 新建、编辑、删除、重建索引 | 可创建和管理本人的场景，可查看另行授权的场景和表 |
| 报告模板 / 报告 | 查看全部人的（列表显示创建人、创建时间） | 只看到、只能修改 / 删除自己创建的（显示创建时间） |
| 样式规则 | 配置通用规则；可查看所有人的业务场景样式 | 通用规则只读；为自己有权限的业务场景配置个人样式，只对自己生成的报告生效 |
| 员工与授权 | 可见、可管理 | 不可见（接口拒绝） |
| 我的权限与接入 | 可用 | 可用 |


- **身份验证**：管理台使用海尔 IAM 单点登录，HTTP 和 MCP 接受同一枚 IAM access token。工号通过 IAM `account/info` 核实，权限由 SmartReportGen 管理。`IAM_BOOTSTRAP_ADMINS` 在配置的员工首次登录管理台时初始化管理员。
- **HTTP 接入**：[HTTP 接入说明](docs/HTTP_API.md) 覆盖管理台操作和调用示例。部署后访问 `/api-guide` 或使用 `/docs` 调试。
- **权限表**（`permission` schema）：`employee`（工号、角色、是否管理员、是否启用）、`table_grant`（对员工或角色授权某业务场景的某张表，`*` 为该场景全部表）。
  工号未登记或停用 → 无任何数据权限。
- **管控点**：
  - 检索只在有权限的业务场景内进行，返回的表只包含有权限的表。
  - `describe_table` 与 `run_query` 仅使用当前场景中已授权的表和视图。场景可以为每张表或视图配置字段保留值；服务端在 JOIN 和聚合前自动过滤，画像使用相同的数据范围。
  - 无法解析的 SQL、FROM 中的表函数、能在字符串里执行 SQL 或读外部数据的函数（`query_to_xml`、`dblink`、`DBMS_XMLGEN`、`read_csv` 等）一律拒绝。API 数据源的 DuckDB 在载入数据后关闭外部访问。
  - 模板校验与渲染时，每条查询同样按权限校验。
  - 报告模板由创建人和管理员管理，渲染仍按调用人的数据权限校验。
- **报告分享**：在报告库点击「分享」，按已启用的员工工号授予「可查看」或「可编辑」。接收人用海尔 IAM 登录后，可通过分享链接查看，或在授权范围内修订报告。正式正文修订经预览验收后追加版本。创建人和管理员可以更改或撤销分享权限。

## 样式：设计令牌

生效顺序为 **当前用户明确要求 > 业务场景规则 > 通用规则 > 内置样式**。MCP `get_style_rules` 按优先级合并视觉样式和展示要求。内置经营报告主题包含蓝绿渐变封面、两级目录、章节面板、紧凑指标卡、图表配色和表格排版。章节、图表类型和筛选条件由报告模板决定。个人场景样式只对本人生成的报告生效。令牌定义见 `app/smartreportgen/design.py`。

内置规则通过 `reporting_rules` 返回，并在管理台只读展示。报告事实来自正式字段或场景负责人登记的官方指标。缺少预聚合数据时可采用受控分组计算，缺失事实保留缺值。服务端执行权限、字段、格式和离线行为门禁，业务解释由使用者据实核实。[事实取数契约](skills/smart-report/references/fact-contract.md)

管理台「样式规则」面向业务人员：
- **展示要求**：设置好坏判定、涨跌颜色、数字格式、文案等。章节安排、选图建议和筛选条件可写在业务场景的「分析思路」中。
- **视觉样式**：按报告页面的组成部分分为 12 类（主题、排版、页面布局、报告头部、筛选栏、章节与结论、卡片、指标卡、数据配色、图表、表格、高级），中文设置项，包括取色器、色板、字号字重、尺寸等。规则只保存自己调整的项，其余项标明“沿用自内置样式 / 通用规则，或“未设置（由智能体决定）””。
- **实时预览**：用示例报告实时预览效果；“查看最终效果”可以看某个业务场景叠加后的样式，每项都标明来源。

渲染时输出为同名 CSS 变量（`--color-brand`、`--rre-card-font-hero-size`…）。
报告组件只引用令牌，派生色用 `color-mix` 从令牌混出。规则同步落在交互逻辑里：
- 健康度四色只用于指标卡、仪表盘、子弹图（`status` 声明判定口径），状态同时以图标 + 文字表达。
- 升降色按指标方向上色（`trend: up / down / none`）。
- 多系列按分类色板取色，有正有负的柱用发散色阶，热力图用顺序色阶。
- 字号按视觉角色配置，默认最小 12px。报告页面在窄屏上重排卡片和目录。

## 目录结构

```
skills/smart-report/        技能（唯一来源）：SKILL.md、单文件 HTML 示例与结构化模板参考
  ↑ .claude/skills/smart-report、.agents/skills/smart-report 为指向它的软链接（Claude Code / Codex 等自动发现）
AGENTS.md                   通用 agent 指引（Codex、Cursor、Copilot 等读取）
.mcp.json / .codex/config.toml   Claude Code / Codex 的项目级 MCP 配置（海尔 IAM 认证）
integrations/               其他 agent 的 MCP 配置模板
docs/DEPLOYMENT.md          部署文档
docs/HTTP_API.md            HTTP 接入说明
docker-compose.yml          gateway(统一入口) + api(管理台/REST) + mcp + postgres(pgvector)
app/smartreportgen/
  api.py                    FastAPI：管理 API、管理台、报告浏览/下载、演示 API
  mcp_server.py             MCP 工具 + 令牌认证 + 内置技能（get_skill_guide / smart_report 提示词）
  auth.py / permission.py   IAM 验证、管理台会话 / 权限计算与 SQL 表引用校验
  design.py                 设计令牌默认值与主题解析
  schema.sql                PG 表结构（按类型分 schema）
  connectors/               mysql / postgresql / oracle / starrocks / api(DuckDB)
  search.py / embedding.py  混合检索（pgvector + 关键词 IDF，RRF 融合）/ 向量化
  template_parser.py / renderer.py   模板解析校验 / 模板 → HTML
  static/report/            报告运行时（筛选联动、客户端聚合、ECharts）与令牌化样式
  static/admin/             管理台（Vue 3，无构建）
scripts/install-skill.sh    把技能安装到各 agent 的用户级技能目录（复制或软链接）
scripts/mcp_call.py         MCP 调试客户端
```

## 数据存储（PostgreSQL schema）

| schema | 内容 |
|---|---|
| `datasource` | 数据源配置；密码/令牌以 Fernet（SECRET_KEY 派生）加密 |
| `dataset` | 业务场景、业务描述、数据源、数据表名/描述；关键词数组 + `vector(N)` 向量 |
| `style_rule` | 通用 / 业务场景样式规则：规则正文 + 设计令牌 + 优先级 |
| `report_template` | markdown 模板与历史版本（含创建人） |
| `report` | 生成的 HTML 与渲染信息（含创建人） |
| `permission` | 员工、表授权、IAM 会话 |
| `sys` | 向量签名、迁移标记等系统设置 |

演示业务数据位于 `demo_retail` / `demo_supply`，只是演示数据源，不属于元数据。

## 其他说明

- **向量模型**：当前 `.env` 接入 AI 网关的 `Qwen3-Embedding-4B`（`AI_BASE_URL` / `AI_API_KEY`），通过 `dimensions` 截断为 1024 维以使用 HNSW 索引，查询侧加指令前缀 `EMBEDDING_QUERY_INSTRUCTION`。
  在 8 条口语化需求上，场景检索命中率由离线哈希向量的 4/8 提升到 8/8。未配置时回退为 `EMBEDDING_PROVIDER=hash`（离线特征哈希，没有语义理解能力）；改模型或维度后重启会自动重建向量。
  向量接口不可用时检索自动降级为纯关键词，并在结果 `warnings` 中说明。
- **报告数据**：服务端从同一批快照投影展示所需字段并内嵌 HTML，浏览器负责选择事实及显示。刷新数据时重新查询并保存报告新版本。
- **安全**：数据源请使用只读账号（系统的只读 SQL 校验是第二道防线）；对外暴露前设置 `SECRET_KEY` 和海尔 IAM 应用凭证，并通过 HTTPS 提供服务。
- **已验证**：PostgreSQL、MySQL 8.4、Oracle 23ai、HTTP API 连接器；StarRocks 仅按 MySQL 协议路径验证。权限越权用例见验证记录（JOIN、子查询、同名 CTE、带引号的标识符、information_schema、query_to_xml、read_csv 均被拒绝）。
