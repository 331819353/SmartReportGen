# SmartReportGen HTTP 接入说明

管理台、HTTP API 和 MCP 共用同一个服务地址。本地地址为 `http://localhost:18600`，部署后使用管理员提供的 HTTPS 地址。可在 [Swagger UI](/docs) 调试接口，也可以下载 [OpenAPI JSON](/openapi.json)。

## 海尔 IAM 认证

1. 在客户端使用已登记的海尔 IAM 应用完成用户授权。浏览器访问 IAM 的 `GET /oauth/authorize`，携带 `client_id`、`redirect_uri`、`response_type=code`、`scope` 和 `state`。
2. 回调收到 `code` 后先验证 `state`。持有 IAM 应用密钥的客户端后端调用 IAM 的 `POST /api/oauth/token/code`，以 JSON 传入 `client_id`、`client_secret`、`code`，请求头 `Application-Key` 填 IAM 应用的 `client_id`。IAM 返回 `resultCode=0` 时，从 `data` 中读取 IAM access token。客户端宿主已提供 IAM access token 时可直接使用。浏览器或桌面应用中无需配置 SmartReportGen 的 IAM 应用密钥。
3. 向 SmartReportGen 发请求时传 `Authorization: Bearer <IAM access token>`。HTTP 和 MCP 使用同一张 IAM 票。令牌的有效期和吊销由 IAM 管理。Codex 等兼容客户端可用下文的 OAuth 登录入口自动取票。
4. SmartReportGen 调用 IAM `GET /api/oauth/account/info` 核实身份，再按员工工号、角色和表授权判断访问范围。管理员权限由 SmartReportGen 员工记录决定。管理员可停用员工或撤销其数据授权。

```bash
BASE_URL=http://localhost:18600
# HAIER_IAM_ACCESS_TOKEN 来自客户端的海尔 IAM 登录流程
curl -fsS "$BASE_URL/api/me/permissions" \
  -H "Authorization: Bearer $HAIER_IAM_ACCESS_TOKEN"
```

浏览器打开 `/auth/iam/login?next=/` 可进入管理台。回调成功后浏览器使用 HttpOnly 会话 Cookie，IAM 原票加密保存在服务端。`POST /auth/logout` 结束当前浏览器会话。独立 HTTP 客户端使用上面的 Bearer 请求方式。使用 Swagger 时可在 **Authorize** 中填写 IAM 原票，也可在同一个站点登录管理台后使用浏览器会话。

## 请求与响应约定

- JSON 请求体使用 `Content-Type: application/json`。查询参数中的中文及路径中的工号应按 URL 编码。
- 管理接口成功通常返回 `200`。列表直接返回数组，对象直接返回 JSON，删除通常返回 `{"ok":true}`。
- `400` 表示业务校验、SQL、连接或渲染错误。模板校验接口以 `200` 和 `ok:false` 返回校验详情。`401` 表示身份缺失、过期或凭证无效，`403` 表示权限不足，`404` 表示资源不存在或当前身份不可见，`409` 表示资源重复或引用冲突，`422` 表示参数格式错误，`503` 表示 IAM 核验暂不可用。普通错误结构为 `{"detail":"错误说明"}`，`422` 的 `detail` 是校验问题数组。OAuth 错误使用 `error`、`error_description`，MCP 调用按 MCP 协议返回。
- 表格中的「登录」指已登记、启用的 IAM 员工。「本人」允许资源创建人操作，管理员可操作所有人创建的资源。权限和报告归属都在服务端校验。

## 管理总览与账号

| 方法 | 路径 | 权限 | 返回 |
|---|---|---|---|
| GET | `/healthz` | 公开 | `{"ok":true}`，检查服务与元数据库 |
| GET | `/api/auth/config` | 公开 | `mode`、`iam_configured`、`mcp_oauth`、`me` |
| GET | `/api/auth/check` | 公开 | `required:true`、`ok` |
| GET | `/auth/iam/login` | 公开 | 302 跳转 IAM，`next` 可指定站内返回路径 |
| GET | `/auth/iam/callback` | IAM 回调 | `code`、`state`。登录成功后 302 返回，浏览器获得会话 Cookie |
| POST | `/auth/logout` | 浏览器会话 | `{"ok":true}` |
| GET | `/api/me` | IAM 身份 | `logged_in`、`emp_no`、`name`、`is_admin`、`via`、`registered`、`roles` |
| GET | `/api/me/permissions` | IAM 身份 | `registered`、`all`、`datasets`，每项含 `dataset_id`、`business_scenario`、`tables` |
| GET | `/api/meta` | 登录 | `stats`、`datasource_types`、`embedding`、`mcp_url`、`public_base_url`、`app_name` |

总览最近报告使用 `GET /api/reports`，按 `id` 降序取前几条。管理员账号、角色及表授权接口见下文。

## 数据源

| 方法 | 路径 | 权限 | 请求或返回 |
|---|---|---|---|
| GET | `/api/datasources` | 登录 | 可见数据源数组。普通用户仅见已授权场景关联的数据源的基础信息 |
| GET | `/api/datasources/{ds_id}` | 登录 | 数据源详情。管理员可见 `config`、`has_secret` |
| POST | `/api/datasources` | 管理员 | 保存数据源，返回详情 |
| PUT | `/api/datasources/{ds_id}` | 管理员 | 更新传入字段，`config` 整体替换 |
| DELETE | `/api/datasources/{ds_id}` | 管理员 | 删除，仍被业务场景引用时返回 `400` |
| POST | `/api/datasources/test` | 管理员 | 测试未保存的配置，传入 `id` 且省略 `secret` 可沿用原密钥。返回 `ok`、`version`、`latency_ms` |
| POST | `/api/datasources/{ds_id}/test` | 管理员 | 测试已保存的连接，返回 `ok`、`version`、`latency_ms` |
| GET | `/api/datasources/{ds_id}/tables` | 管理员 | 可选 `schema`，返回 `schema`、`table`、`name`、`comment` 数组 |
| GET | `/api/datasources/{ds_id}/describe` | 管理员 | 必填 `table`，`sample_rows` 默认 10。返回 `columns`、`row_count`、`sample_columns`、`sample_rows`、`profile`、`profile_basis` |
| POST | `/api/datasources/{ds_id}/query` | 管理员 | `{"sql":"SELECT ...","limit":200}`，返回 `columns`、`rows`、`row_count`、`truncated`、`datasource`、`dialect` |

新增必填 `name`、`type`、`config`，可传 `description`、`enabled`（默认 `true`）、`secret`。数据库密码或上游 API 密钥放在 `secret`，只写。更新省略 `secret` 时沿用已保存的密钥，传 `clear_secret:true` 可清除。

| `type` | `config` |
|---|---|
| `postgresql` | `host`、`port`（5432）、`database`、`username`，可选 `search_path`、`sslmode` |
| `mysql` | `host`、`port`（3306）、`database`、`username`，可选 `charset` |
| `starrocks` | `host`、`port`（9030）、`database`、`username`，可选 `charset` |
| `oracle` | `host`、`port`（1521）、`username`、`service_name` 或 `sid`，可选 `schema` |
| `api` | `base_url`、`endpoints`，可选 `records_path`、`headers`、`auth_header`、`auth_scheme`、`timeout`、`cache_seconds`、`method` |

HTTP API 数据源示例。`tickets` 是供业务场景和 DuckDB SQL 引用的逻辑表名。

```json
{
  "name": "客服工单接口",
  "type": "api",
  "config": {
    "base_url": "https://tickets.example.com/v1",
    "records_path": "data.items",
    "endpoints": {"tickets": {"path": "/tickets", "method": "GET", "params": {}, "description": "客服工单"}}
  }
}
```

`endpoints` 的单项还可填写 `body` 和覆盖 `records_path`。SQL 支持只读 `SELECT` / `WITH`。普通员工的数据查询使用其已授权的模板校验或预览接口，或通过 MCP 调用场景级数据工具。

## 业务场景

| 方法 | 路径 | 权限 | 请求或返回 |
|---|---|---|---|
| GET | `/api/datasets` | 登录 | 可选 `q` 按场景名、业务描述、标签过滤，返回场景摘要和表数 |
| GET | `/api/datasets/{dataset_id}` | 登录 | 场景详情，包含 `analysis_approach` 和当前身份可见的 `tables` |
| POST | `/api/datasets` | 管理员 | 创建业务场景，返回详情 |
| PUT | `/api/datasets/{dataset_id}` | 管理员 | 更新传入字段。传 `tables` 时替换整份表清单 |
| DELETE | `/api/datasets/{dataset_id}` | 管理员 | 删除场景及其表授权 |
| GET | `/api/datasets/search` | 登录 | `q` 为业务需求，`top_k` 默认 5。返回 `query_tokens`、`hits`、`warnings`，命中含 `dataset`、`rrf`、`relevance` 和可用的关键词、向量分数 |
| POST | `/api/datasets/reindex` | 管理员 | 重建数据集和样式规则索引，返回 `datasets`、`style_rules`、`not_vectorized` |
| GET | `/api/scenarios` | 登录 | 可见的业务场景名称数组，供样式筛选和选择器使用 |

```json
{
  "business_scenario": "客服工单分析",
  "business_description": "跟踪客服渠道的工单量、处理时长与超时情况。",
  "analysis_approach": "先检查工单覆盖日期、各渠道样本量和处理时长缺失率。按同口径比较最近两月及各渠道，并验证超时上升是否集中在部分问题类型。",
  "datasource_id": 12,
  "tags": ["客服", "时效"],
  "enabled": true,
  "tables": [{"table_name": "tickets", "table_description": "客服工单记录"}]
}
```

新增必填 `business_scenario`、`business_description`、`datasource_id`。`analysis_approach` 用于说明探查、比较及需要验证的假设，也可附章节、图表和筛选建议。使用数据源的表列表接口取得 `table_name`，数组顺序决定场景详情中的展示顺序。

## 报告模板

| 方法 | 路径 | 权限 | 请求或返回 |
|---|---|---|---|
| GET | `/api/templates` | 登录 | 可选 `q` 搜索模板名、场景、用途、目标和需求。普通员工只见本人模板 |
| GET | `/api/templates/{tpl_id}` | 本人 | 含 `markdown`、`business_summary`、`summary`、`versions` 的详情 |
| POST | `/api/templates` | 登录 | 保存模板，返回详情，初始版本为 `1` |
| PUT | `/api/templates/{tpl_id}` | 本人 | 提交 `markdown` 及要更新的管理字段。源码变化时新建版本 |
| DELETE | `/api/templates/{tpl_id}` | 本人 | 删除模板，已生成的报告保留 |
| GET | `/api/templates/{tpl_id}/versions/{version}` | 本人 | 返回 `version`、`markdown`、`created_at`，可再次提交源码恢复版本 |
| POST | `/api/templates/validate` | 登录 | `markdown`、`dataset_id`、`execute`（默认 `true`）。返回 `ok`、`errors`、`warnings`、`summary`，执行查询时另含 `queries`、`filters` |
| POST | `/api/templates/preview` | 登录 | `markdown`、`dataset_id`，管理员可指定 `owner` 预览其样式。返回 `text/html` |
| POST | `/api/templates/{tpl_id}/render` | 本人 | 可选 `{"name":"报告名"}`，同步执行查询、渲染并保存报告。返回报告元数据和 `url`、`warnings` |

写入模板时传 `markdown`。`name` 可省略并沿用源码 `title`，其余字段包括 `dataset_id`、`business_summary`、`user_requirement`、`analysis_goal`。展示给业务人员时优先使用 `business_summary` 和 `summary.sections`，源码供编辑和调试。模板语法见 [报告模板说明](../skills/smart-report/references/template-spec.md)。

```bash
# template.md 是 UTF-8 Markdown 文件，DATASET_ID 是已授权场景的整数 ID
jq -n --rawfile markdown template.md --argjson dataset_id "$DATASET_ID" \
  '{markdown:$markdown,dataset_id:$dataset_id,business_summary:"按月查看客服工单处理效率"}' > template.json
curl -fsS "$BASE_URL/api/templates/validate" \
  -H "Authorization: Bearer $HAIER_IAM_ACCESS_TOKEN" \
  -H 'Content-Type: application/json' --data-binary @template.json
```

校验返回 `ok:true` 后，用相同 JSON 请求 `POST /api/templates`。将返回的 `id` 填入生成接口。每次调用生成接口都会新增一份报告。报告中的数据和样式是生成时的快照，交互筛选在浏览器内执行。

## 报告库

| 方法 | 路径 | 权限 | 请求或返回 |
|---|---|---|---|
| GET | `/api/reports` | 登录 | 可选 `q` 搜索报告名、模板名、创建人，也可传 `template_id`。普通员工只见本人报告 |
| GET | `/api/reports/{report_id}` | 本人 | 报告元数据，包括 `name`、`template_id`、`template_version`、`meta`、`created_by`、`url` |
| GET | `/reports/{report_id}` | 本人 | `text/html` 报告页面。浏览器从 IAM 登录后可直接打开 |
| GET | `/reports/{report_id}/download` | 本人 | `text/html` 附件，`standalone` 默认 `true`，内联图表库以支持离线使用 |
| DELETE | `/api/reports/{report_id}` | 本人 | 删除报告 |

```bash
curl -fsS "$BASE_URL/reports/$REPORT_ID/download?standalone=true" \
  -H "Authorization: Bearer $HAIER_IAM_ACCESS_TOKEN" -o report.html
```

## 报告样式

| 方法 | 路径 | 权限 | 请求或返回 |
|---|---|---|---|
| GET | `/api/style-tokens` | 登录 | 样式编辑器可用项、内置值、展示要求类别和生效顺序 |
| GET | `/api/style-rules` | 登录 | 管理员查看全部。普通员工查看通用和本人场景规则 |
| GET | `/api/style-rules/{rule_id}` | 登录 | 规则详情。个人场景规则限本人或管理员 |
| POST | `/api/style-rules` | 按范围 | 创建样式规则，返回详情 |
| PUT | `/api/style-rules/{rule_id}` | 按范围 | 更新规则。提供 `items` 或 `theme` 时整体替换该字段 |
| DELETE | `/api/style-rules/{rule_id}` | 按范围 | 删除规则 |
| GET | `/api/style-rules/resolve` | 登录 | 可选 `scenario`、`q`，返回 `specified`、`general_rules`、`scenario_rules`、`warnings`。`specified` 含合并主题、来源和展示要求 |
| POST | `/api/style-rules/effective` | 登录 | `scenario`、`exclude_id`、`draft` 均可选，返回 `theme`、`sources`、`precedence` |
| POST | `/api/style-rules/preview` | 登录 | 同 `effective` 请求体，使用示例数据生成 `text/html` 预览 |

规则新增必填 `name` 和 `scope`。通用规则用 `scope:general`，由管理员写入。个人规则用 `scope:scenario` 并传 `business_scenario`，需要该场景权限。`priority` 默认 100，同范围内较大值优先。`theme` 仅传需要覆盖的样式项。`draft` 可传与样式规则相同的结构，`exclude_id` 用于编辑时先排除原规则。

```json
{
  "name": "客服报表展示",
  "scope": "scenario",
  "business_scenario": "客服工单分析",
  "priority": 100,
  "enabled": true,
  "items": [{"category": "number", "text": "处理时长以小时展示，保留一位小数。"}],
  "theme": {"color-brand": "#1A72E8"}
}
```

## 员工与数据授权

| 方法 | 路径 | 权限 | 请求或返回 |
|---|---|---|---|
| GET | `/api/employees` | 管理员 | 员工数组，含工号、姓名、部门、角色、管理员与启用状态、登记来源、直授数 |
| POST | `/api/employees` | 管理员 | 新增员工，`emp_no` 必填，可传 `name`、`department`、`roles`、`is_admin`、`enabled` |
| PUT | `/api/employees/{emp_no}` | 管理员 | 更新员工，提交姓名、部门、角色和状态的完整表单。工号由路径确定 |
| DELETE | `/api/employees/{emp_no}` | 管理员 | 删除员工、直授记录和本地浏览器会话。需要持续禁止该 IAM 工号访问时，将员工设为停用 |
| GET | `/api/grants` | 管理员 | 授权列表，含 `id`、`subject_type`、`subject`、`dataset_id`、`table_name`、`business_scenario` |
| POST | `/api/grants` | 管理员 | 传 `subject_type`、`subject`、`dataset_id`、`tables`。返回 `added`，已有的相同授权会跳过 |
| DELETE | `/api/grants/{grant_id}` | 管理员 | 撤销一条授权 |
| GET | `/api/permissions/check` | 管理员 | 必填 `emp_no`，返回 `registered`、`all`、`datasets`，员工存在时含 `employee` |

```json
{"subject_type":"employee","subject":"E10001","dataset_id":12,"tables":["tickets"]}
```

`subject_type` 也可为 `role`，此时 `subject` 为角色编码。`tables` 必须显式传入非空数组，`["*"]` 代表当前及未来关联的全部数据表。

## Codex CLI 与 MCP HTTP

```bash
codex mcp add smartreportgen --url "$BASE_URL/mcp" --oauth-client-id smartreportgen-cli
codex mcp login smartreportgen
```

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/.well-known/oauth-protected-resource/mcp` | MCP 资源发现，也支持省略末尾 `/mcp` 的路径 |
| GET | `/.well-known/oauth-authorization-server` | OAuth 授权服务发现 |
| GET | `/auth/mcp/authorize` | 浏览器授权，支持 `smartreportgen-cli`、本机 loopback 回调与 S256 PKCE |
| POST | `/auth/mcp/token` | 提交 `application/x-www-form-urlencoded`，参数为 `grant_type=authorization_code`、`code`、`client_id`、`redirect_uri`、`code_verifier`，返回 IAM 原始 `access_token` |
| POST / GET / DELETE | `/mcp` | MCP Streamable HTTP。客户端按协议初始化、发现和调用工具 |

兼容客户端使用授权发现与 PKCE 自动取得 IAM 票。SmartReportGen 的 OAuth 入口代办 IAM 应用的授权码换票。令牌到期后重新登录，报告链接通过 IAM 登录或同一枚 Bearer 访问。
