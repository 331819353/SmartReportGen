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

## 权限与展示

- 管理台使用海尔 IAM 单点登录，HTTP 和 MCP 接受 IAM access token。场景检索、表画像、取数及报告读取均按当前授权校验。[角色与权限](docs/DEPLOYMENT.md#53-角色与可见范围) · [HTTP 接入](docs/HTTP_API.md)
- 在报告库按员工工号授予查看或编辑权限。接收人还须拥有该版本的来源数据权限。修订经整页验收后追加版本。[报告库接口](docs/HTTP_API.md#报告库)
- 管理台「样式规则」支持配置视觉样式、数字格式和状态判定，并预览生效结果。场景样式优先于通用样式，用户可对本次报告明确指定展示要求。[展示契约](skills/smart-report/references/display-contract.md)

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
  static/report/            筛选联动、事实展示和图表运行时
  static/admin/             管理台（Vue 3，无构建）
scripts/install-skill.sh    把技能安装到各 agent 的用户级技能目录（复制或软链接）
scripts/mcp_call.py         MCP 调试客户端
```
