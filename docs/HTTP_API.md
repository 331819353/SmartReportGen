# SmartReportGen HTTP 接入说明

本指南面向调用 SmartReportGen 的开发者。管理台、HTTP API 和 MCP 共用一个服务入口。接口路径均相对于部署地址 `BASE_URL`，例如 `https://report.example.com/api/me`。域名、工号、资源 ID 和数据都是示例。部署实例的 Swagger 在 `$BASE_URL/docs`，OpenAPI 文档在 `$BASE_URL/openapi.json`，本指南的在线版在 `$BASE_URL/api-guide`。

[首次调用](#quickstart) · [获取凭证](#authentication) · [生成并下载报告](#report-quickstart) · [常见问题](#troubleshooting) · [接口参考](#api-reference)

<a id="quickstart"></a>

## 首次调用

在测试环境开始。向部署方确认下面的信息。命令可在 Bash 或 Zsh 中运行，需要 `curl` 7.76 或以上版本，换票和报告示例还需要 `jq`。

| 需要确认 | 向谁确认 | 用途 |
|---|---|---|
| 调用机器可达的 HTTPS 部署地址 `BASE_URL`，以及是否需要企业内网或 VPN | 部署方 | 拼接 `/healthz`、`/api/me` 等路径。`http://localhost:18600` 仅适用于服务就在本机的场景 |
| 测试海尔账号，以及获得该账号 IAM access token 的方式 | IAM 或集成负责人 | 服务端调用使用 Bearer。已有 IAM 登录的系统使用其获得的用户令牌，自建登录按下节对接 IAM |
| 测试工号是否已启用，以及可用业务场景和表 | SmartReportGen 管理员 | 生成报告需要相应表权限。只查看已有报告时，请创建人或管理员分享该报告 |

先检查连通性。把下列 `BASE_URL` 的值替换成部署方提供的地址，末尾不带 `/`。预期响应为 `200` 和 `{"ok":true}`。

```bash
BASE_URL='https://report.example.com'
curl -sS -i --connect-timeout 10 "$BASE_URL/healthz"
```

拿到 IAM access token 后，在同一终端执行。输入原始令牌，终端不回显。`api` 是后续示例复用的 curl 函数，HTTP 失败时保留服务端响应体并返回非零状态。

```bash
printf '海尔 IAM access token: '
IFS= read -r -s HAIER_IAM_ACCESS_TOKEN
printf '\n'

api() {
  curl --silent --show-error --fail-with-body --connect-timeout 10 \
    -H "Authorization: Bearer ${HAIER_IAM_ACCESS_TOKEN:?请先输入海尔 IAM access token}" "$@"
}

api "$BASE_URL/api/me"
api "$BASE_URL/api/me/permissions"
api --get "$BASE_URL/api/datasets" --data-urlencode 'q=客服'
```

1. `/api/me` 返回 `200` 后，确认 `emp_no` 是预期工号，`registered` 为 `true`。`registered:false` 时请管理员登记或启用账号。
2. `/api/me/permissions` 返回 `datasets`。普通账号的空数组表示当前没有有效表权限，请管理员为该工号或数据角色授权。管理员的 `all:true` 表示具备全部数据权限。
3. `/api/datasets` 按 `q` 过滤当前账号可见的场景。拿到命中的 `id` 后，用 `GET /api/datasets/{dataset_id}` 读取 `tables[].table_name`。返回 `[]` 时可去掉 `q`，检查是否只是没有匹配项。查询参数含中文时用 `--data-urlencode`。

在 Postman 中可把 `base_url` 设为同一个部署地址，选择 `GET {{base_url}}/api/me`，在 **Authorization** 选择 **Bearer Token** 并填入原始令牌。JSON 写入请求选择 **Body → raw → JSON**。Swagger 也可用 **Authorize** 填入原始令牌，或在同站点先登录管理台再调用。若第三方网页与 API 不同源，默认部署未配置 CORS，使用第三方后端转发或请部署方配置允许的来源。

<a id="authentication"></a>

## 获取凭证

| 客户端 | 获取与使用方式 |
|---|---|
| 已对接海尔 IAM 的服务或宿主 | 使用其登录流程签发给当前用户、可通过 IAM 账号核验的 access token。每次向 SmartReportGen 发送 `Authorization: Bearer <access token>` |
| 需要自己实现登录的第三方服务 | 向 IAM 负责人申请或确认应用，登记第三方应用的回调地址。由服务端按下方授权码流程换取 access token |
| Codex 等支持本机 OAuth 回调的 MCP 客户端 | 将 MCP 地址和 `smartreportgen-cli` 配置给客户端，让客户端发起浏览器授权并管理令牌。见 [MCP OAuth](#mcp-oauth) |
| 在浏览器中操作管理台 | 从 `$BASE_URL/auth/iam/login?next=/` 发起登录，浏览器自动使用 `srg_iam_session` Cookie。管理台的登录回调不会向第三方应用返回 IAM access token |

SmartReportGen 用海尔 IAM 的 `GET /api/oauth/account/info` 核实令牌对应的员工，再检查本地账号状态、系统身份和数据授权。REST 的 Bearer 值是 IAM access token，`client_secret`、授权码和管理台 Cookie 各有独立用途。服务端接入需要用户授权，没有 SmartReportGen API Key 或 `client_credentials` 换票入口。显式发送的 `Authorization` 优先于浏览器 Cookie。

**第三方自建 IAM 登录**

1. 从 IAM 负责人获取授权地址、换票地址、`client_id`、服务端保存的 `client_secret`、获准的 `scope` 和登记的 `redirect_uri`。回调地址须与登记值完全一致。
2. 引导用户的浏览器访问 IAM 的 `GET /oauth/authorize`，对查询值做 URL 编码，发送 `client_id`、`redirect_uri`、`response_type=code`、`scope` 和由第三方应用生成并绑定本次会话的 `state`。
3. 第三方应用的回调收到 `code` 后验证 `state`，然后由后端向 **IAM 地址**发送 `POST /api/oauth/token/code`。以下变量由第三方应用提供，`IAM_BASE_URL` 是 IAM 换票地址的基址，`IAM_AUTH_CODE` 来自刚验证的回调。

```bash
# 在第三方后端环境中提供 IAM_BASE_URL、IAM_CLIENT_ID、IAM_CLIENT_SECRET 和 IAM_AUTH_CODE。
HAIER_IAM_ACCESS_TOKEN=$(
  set -o pipefail
  jq -en '{client_id:env.IAM_CLIENT_ID, client_secret:env.IAM_CLIENT_SECRET, code:env.IAM_AUTH_CODE}' |
    curl --silent --show-error --fail-with-body "$IAM_BASE_URL/api/oauth/token/code" \
      -H "Application-Key: $IAM_CLIENT_ID" -H 'Content-Type: application/json' --data-binary @- |
    jq -er 'select((.resultCode | tostring) == "0") | (.data.access_token // .data.accessToken // .data.token) | strings | select(length > 0)'
)
```

IAM 返回 `resultCode=0` 时从 `data.access_token` 读取令牌，部分 IAM 环境字段为 `data.accessToken` 或 `data.token`。示例把令牌保存在变量中，换票失败时应停止调用并检查 IAM 返回的状态。避免把凭证写入日志。换票后用 `/api/me` 核对工号。令牌失效后由 IAM 登录流程更新凭证。SmartReportGen 的 `/auth/mcp/token` 只接收其 MCP 授权流程签发的一次性授权码。

<a id="report-quickstart"></a>

## 运行结构化模板并下载报告

新报告请按 smart-report 流程由 Agent 直接编写单文件 HTML，验证筛选联动与结论着色，再用 MCP `save_report` 关联业务模板版本并保存。下面的 HTTP 示例演示如何运行服务端结构化模板。先完成 [首次调用](#quickstart)，再把 `DATASET_ID` 和 `TABLE_NAME` 替换为获授权的测试场景和表名。示例 SQL 适用于普通表名或 `schema.table`，带引号等特殊表名时请按数据源方言编写 SQL。此示例会保存一份模板和一份报告。

```bash
(
  set -eu
  DATASET_ID=12
  TABLE_NAME='tickets'

  # 核对表名来自当前账号可见的场景清单，再构造只读查询。
  dataset=$(api "$BASE_URL/api/datasets/$DATASET_ID")
  printf '%s\n' "$dataset" | jq -e --arg t "$TABLE_NAME" \
    'any(.tables[]; .table_name == $t) and ($t | test("^[A-Za-z_][A-Za-z0-9_]*(\\.[A-Za-z_][A-Za-z0-9_]*)?$"))' >/dev/null
  template=$(jq -n --argjson dataset_id "$DATASET_ID" --arg table "$TABLE_NAME" '
    {
      dataset_id: $dataset_id,
      business_summary: "统计授权表的记录数",
      markdown: "---\ntitle: 接入验证报告\nqueries:\n  q1:\n    sql: SELECT COUNT(*) AS row_count FROM \($table)\n---\n\n## 数据概况\n\n统计授权表的记录数。"
    }')

  # HTTP 200 仍可能校验失败。只有 ok 为 true 才保存和生成。
  validation=$(api "$BASE_URL/api/templates/validate" \
    -H 'Content-Type: application/json' --data-binary "$template")
  printf '%s\n' "$validation" | jq '{ok, errors, warnings, queries}'
  printf '%s\n' "$validation" | jq -e '.ok == true' >/dev/null

  saved=$(api "$BASE_URL/api/templates" \
    -H 'Content-Type: application/json' --data-binary "$template")
  TEMPLATE_ID=$(printf '%s\n' "$saved" | jq -er '.id | numbers')

  report=$(api -X POST "$BASE_URL/api/templates/$TEMPLATE_ID/render")
  REPORT_ID=$(printf '%s\n' "$report" | jq -er '.id | numbers')
  printf '%s\n' "$report" | jq '{id, name, url, warnings}'

  api "$BASE_URL/reports/$REPORT_ID/download?standalone=true" --output "report-$REPORT_ID.html"
  printf '已下载 report-%s.html\n' "$REPORT_ID"
)
```

`POST /api/templates` 返回模板 `id`，用于 `/api/templates/{tpl_id}/render`。生成响应的 `id` 是另一项资源的报告 ID，用于报告详情和下载。返回的 `url` 可交给有查看权限的员工在浏览器登录后打开。应用也可以向下载接口发 Bearer 请求，按 HTML 文件处理响应。模板的完整语法可从部署实例的 `$BASE_URL/api-guide/template-spec` 获取。

如果只想下载已有报告，调用 `api "$BASE_URL/api/reports"`，从结果中选一个报告 `id` 后直接调用下载接口。模板校验、预览和生成可能同步执行查询，为调用配置合适的响应超时。创建模板和生成报告都可能新增记录。调用超时且结果未知时，先查 `/api/templates` 或 `/api/reports` 确认是否已成功，再决定是否重试。

<a id="troubleshooting"></a>

## 常见问题

| 现象 | 如何处理 |
|---|---|
| 连接失败、TLS 报错，或 `/healthz` 返回网关错误 | 在实际发起请求的机器检查域名、端口、证书和内网或 VPN。确认用的是部署方给出的 `BASE_URL` |
| `/healthz` 正常，`/api/me` 返回 `401` | 检查是否发送了 `Authorization: Bearer <IAM access token>`，并通过 IAM 重新取得有效令牌。Postman 的 Bearer Token 框只填令牌本身 |
| `/api/me` 返回 `200`，但 `registered:false` | IAM 已识别该工号。请管理员检查 SmartReportGen 的员工登记和启用状态 |
| `403`、场景列表为空，或校验结果提示无权访问表 | 用 `/api/me/permissions` 核对授权。管理员接口要求管理员身份，模板修改要求创建人权限，报告操作按报告分享级别校验 |
| `404` | 对照接口的 HTTP 方法与路径，使用列表或创建响应中的真实 ID。数据源或场景对该账号不可见时也可能返回 `404` |
| `422` 或 `400` | 查看 `detail`，核对路径和查询参数类型、JSON 字段及 `Content-Type`。模板校验返回 `200` 时也要检查 `ok` 和 `errors` |
| 报告编辑返回 `409` | 重新读取报告及其最新版本号，对照冲突前的基准版本后再保存 |
| 收到 `302` 或 HTML 登录页 | 程序调用报告正文或下载接口时发送 Bearer，并检查响应 `Content-Type`。调试时先用 `api -i "$BASE_URL/实际请求路径"` 查看状态和 `Location` |
| IAM 校验返回 `503` | 请部署方检查 IAM 配置和服务到 IAM 的连通性。浏览器也可用 `/api/auth/config` 查看 `iam_configured` |

提交接入问题时，提供环境、请求时间、HTTP 方法、路径、状态码及脱敏响应。省略 access token、授权码、Cookie、应用密钥和业务数据正文。

## 请求与响应约定

接口表中的「登录」指已登记且启用的 IAM 员工，「IAM 身份」也包括已核实但未登记或已停用的员工，「本人」指创建人或管理员。标为「管理员」的接口需要已启用的管理员账号。路径中的 `{...}` 是必填参数，资源 ID 和模板版本号为整数，`emp_no` 为字符串。查询参数和路径值按 URL 编码。请求体标为 JSON 时使用 `Content-Type: application/json`。表中「无」表示无需额外参数或请求体，仍须按权限列提供凭证。

成功响应默认是 `200 application/json`。列表直接返回数组，无结果时返回 `[]`。响应中的资源时间戳为 ISO 8601 字符串。JSON 示例中的动态字段随环境及数据变化。

| 状态 | 含义 |
|---|---|
| `400` | 业务参数、连接、SQL 或渲染失败。模板校验接口有单独的 `200` 与 `ok:false` 约定 |
| `401` | 缺少有效身份或 IAM 票无效、过期。REST 接口返回 `WWW-Authenticate` |
| `403` | 账号未登记、已停用或权限不足。部分资源接口用 `404` 隐藏不可见资源 |
| `404` | 资源不存在、不可见或路径不匹配 |
| `409` | 数据库唯一键或引用冲突，或编辑报告时版本已变化 |
| `422` | FastAPI 请求校验失败，例如必填查询参数缺失、路径参数类型错误、必填 JSON 对象缺失 |
| `500` | 未处理的服务端异常 |
| `503` | IAM 尚未配置或暂时无法核验凭证 |

REST 业务错误通常为 `{"detail":"错误说明"}`，`422` 的 `detail` 是校验问题数组，例如 `{"detail":[{"type":"missing","loc":["query","q"],"msg":"Field required","input":null}]}`。浏览器登录、报告下载和 MCP OAuth 的特殊响应格式在各自接口下说明。

<a id="api-reference"></a>

## 公开页面与健康检查

以下路由均公开，无额外参数或请求体。静态资源路径除外，其文件名由路径指定。

| 方法与路径 | 成功响应 | 说明 |
|---|---|---|
| `GET /` | `200 text/html` | 管理台页面，带 `Cache-Control: no-cache`。页面加载后查询当前登录身份 |
| `GET /healthz` | `200`，`{"ok":true}` | 检查 HTTP 服务及元数据库连通性。失败返回非 2xx |
| `GET /api-guide` | `200 text/html` | 本文的在线版本 |
| `GET /api-guide/template-spec` | `200 text/plain` | Markdown 报告模板规范全文 |
| `GET /docs` | `200 text/html` | Swagger UI |
| `GET /docs/oauth2-redirect` | `200 text/html` | FastAPI 自带的 Swagger OAuth 回调页 |
| `GET /redoc` | `200 text/html` | ReDoc 页面 |
| `GET /openapi.json` | `200 application/json` | OpenAPI 文档，含 `openapi`、`info`、`paths`、`components` 等字段 |
| `GET /static/{path}` | `200`，文件对应的媒体类型 | 静态资源，`{path}` 如 `admin/app.js`。支持条件请求（`304`）和字节范围（`206`），文件不存在返回 `404` |

FastAPI 自带文档路由和静态资源还支持 `HEAD`，返回响应头，响应体为空。

## 浏览器登录与账号

| 方法与路径 | 权限 | 请求 | 响应 |
|---|---|---|---|
| `GET /api/auth/config` | 公开 | 无。可带 Cookie 或 Bearer 查询当前身份 | `{"mode":"iam","iam_configured":true,"mcp_oauth":true,"me":{"logged_in":false}}`。登录时 `me` 为下方身份对象 |
| `GET /api/auth/check` | 公开 | 无。可带 Cookie 或 Bearer | `{"required":true,"ok":true}`。身份缺失、未登记或已停用时 `ok:false` |
| `GET /auth/iam/login` | 公开 | 可选查询参数 `next`，默认 `/`，仅接受站内路径 | `302`，`Location` 为 IAM 授权地址，`Set-Cookie` 设置 10 分钟有效的 HttpOnly 登录状态 Cookie `srg_iam_login`。请求主机与配置的 IAM 回调主机不同时，先 `302` 到回调主机的登录入口。IAM 未配置返回 `503 text/html` |
| `GET /auth/iam/callback` | IAM 浏览器回调 | 查询参数 `code`、`state` 及登录状态 Cookie。IAM 授权失败时可带 `error` | `302` 跳转至登录时指定的站内路径，设置 HttpOnly 会话 Cookie `srg_iam_session` 并清除登录状态 Cookie。失败时返回 HTML，`400` 为授权或状态无效，`403` 为未登记或已停用，`503` 为 IAM 暂不可用。MCP 回调分支见下文 |
| `POST /auth/logout` | 公开 | 浏览器自动带上 `srg_iam_session`（如有），无请求体 | `{"ok":true}`，销毁该浏览器会话并通过 `Set-Cookie` 清除 Cookie |
| `GET /api/me` | IAM 身份 | 无 | 身份对象，见下方示例 |
| `GET /api/me/permissions` | IAM 身份 | 无 | 有效权限对象，见下方示例 |
| `GET /api/meta` | 登录 | 无 | 管理台配置与总览统计，见下方示例 |

认证探测接口 `/api/auth/config` 和 `/api/auth/check` 在未带 Bearer 且 Cookie 无法核验时按未登录返回。显式传无效 Bearer 返回 `401`，IAM 暂不可用时返回 `503`。浏览器登录和退出响应带 `Cache-Control: no-store`，HTTPS 配置下登录与会话 Cookie 带 `Secure`。退出管理台只结束本地会话，IAM access token 的有效期和吊销由 IAM 管理。

**身份对象**（`GET /api/me` 或 `/api/auth/config` 中的 `me`）

```json
{"logged_in":true,"emp_no":"E10001","name":"示例员工","is_admin":false,"via":"iam","registered":true,"roles":["user"]}
```

`via` 为 `iam`（Bearer）或 `session`（浏览器）。`registered` 表示已登记且启用。无凭证时 `GET /api/me` 返回 `401`，公开配置接口的 `me` 则只含 `logged_in:false`。

**有效权限对象**（`GET /api/me/permissions`）

```json
{
  "registered": true,
  "all": false,
  "datasets": [{"dataset_id":12,"business_scenario":"客服工单分析","tables":["tickets"]}]
}
```

已启用管理员的 `all` 为 `true`，各场景的 `tables` 为 `["*"]`。普通用户返回当前有效的表名列表。工号未登记或已停用时返回 `{"registered":false,"all":false,"datasets":[]}`。

**总览配置**（`GET /api/meta`）

```json
{
  "datasource_types": [
    {"value":"mysql","label":"MySQL"},
    {"value":"postgresql","label":"PostgreSQL"},
    {"value":"oracle","label":"Oracle"},
    {"value":"starrocks","label":"StarRocks"},
    {"value":"api","label":"HTTP API"}
  ],
  "embedding": {
    "provider":"hash","model":"hash-v1（离线兜底，无语义）","dim":1024,
    "signature":"hash:1024:","query_instruction":false,"base_url":""
  },
  "mcp_url":"http://localhost:18600/mcp",
  "public_base_url":"http://localhost:18600",
  "stats":{"datasources":1,"datasets":1,"templates":1,"reports":1,"style_rules":1,"employees":0},
  "auth":true,"auth_mode":"iam","app_name":"SmartReportGen"
}
```

管理员看到全局统计，普通用户看到自己可用的业务数据及其模板、可见报告和可见样式规则数量，`employees` 固定为 0。总览的最近报告使用 `GET /api/reports`，按报告 ID 降序返回。

## 数据源

| 方法与路径 | 权限 | 请求 | 响应 |
|---|---|---|---|
| `GET /api/datasources` | 登录 | 无 | 数据源对象数组。管理员见全部，普通用户仅见可用场景关联的数据源基础字段 |
| `POST /api/datasources` | 管理员 | JSON 数据源配置，必填 `name`、`type`、`config`。可选 `description`、`enabled`、`secret` | 数据源对象。配置校验失败返回 `400`，重名返回 `409` |
| `GET /api/datasources/{ds_id}` | 登录 | 路径 `ds_id` | 数据源对象。普通用户仅见基础字段。不存在或不可见返回 `404` |
| `PUT /api/datasources/{ds_id}` | 管理员 | 路径 `ds_id`。JSON 传需要更新的字段，传入 `config` 时整体替换，`{"clear_secret":true}` 清除已存密钥 | 更新后的数据源对象。不存在返回 `404`，配置非法返回 `400`，重名返回 `409` |
| `DELETE /api/datasources/{ds_id}` | 管理员 | 路径 `ds_id`，无请求体 | `{"ok":true}`。被业务场景引用时返回 `400` |
| `POST /api/datasources/test` | 管理员 | JSON 传 `type`、`config`，可选 `name`、`secret`、`id`。传 `id` 且未提供非空 `secret` 时使用该数据源的已存密钥，本次测试仍使用请求中的连接配置 | 连接测试对象，见下方示例。借用密钥的 ID 不存在返回 `404` |
| `POST /api/datasources/{ds_id}/test` | 管理员 | 路径 `ds_id`，无请求体 | 使用已保存的配置，返回连接测试对象。数据源不存在返回 `404` |
| `GET /api/datasources/{ds_id}/tables` | 管理员 | 路径 `ds_id`，可选查询参数 `schema` | 数据库账号可见的表和视图。每项有 `schema`、`table`、`name`、`comment`、`kind`（`table`、`view` 或 `materialized_view`）。不存在返回 `404` |
| `GET /api/datasources/{ds_id}/columns` | 管理员 | 路径 `ds_id`，必填查询参数 `table` | 字段元数据数组：`name`（小写）、`source_name`（原始名称）、`type`、`nullable`、`comment`。数据库数据源不读取业务行 |
| `GET /api/datasources/{ds_id}/values` | 管理员 | 必填 `table` 和 `column`（使用字段的 `source_name`）；可选 `q`（字面包含搜索）、`limit`（默认 50，最多 100）、`offset`（默认 0，最大 10000） | 字段实际去重值 `{"values":[null,"华东","华南"],"next_offset":null}`。有下一页时 `next_offset` 为整数。候选值不受场景中已有过滤条件影响 |
| `GET /api/datasources/{ds_id}/describe` | 管理员 | 路径 `ds_id`，必填查询参数 `table`，可选整数 `sample_rows`，默认 10 | 表结构、样本和列画像，见下方示例。数据源不存在返回 `404`，表查询失败可返回 `400` |
| `POST /api/datasources/{ds_id}/query` | 管理员 | 路径 `ds_id`。JSON 传必填 `sql`，可选整数 `limit`，默认 200，有效返回上限限制在 1–5000 | 查询结果，见下方示例。无数据源返回 `404`，空 SQL、非只读语句或执行失败返回 `400` |

`name` 为非空字符串，`type` 为下表所列字符串，`config` 为对象。`description` 为字符串，新增时默认空字符串。`enabled` 和 `clear_secret` 为布尔值，新增默认启用。密码或上游 API 密钥放在只写的字符串字段 `secret`。更新省略密钥时沿用已存密钥，非空 `secret` 优先于 `clear_secret`。测试未保存配置时，`id` 为数据源整数 ID。

| `type` | `config` |
|---|---|
| `postgresql` | `host`、`port`（默认 5432）、`database`、`username`，可选 `search_path`、`sslmode` |
| `mysql` | `host`、`port`（默认 3306）、`database`、`username`，可选 `charset`（默认 `utf8mb4`） |
| `starrocks` | `host`、`port`（默认 9030）、`database`、`username`，可选 `charset`（默认 `utf8mb4`） |
| `oracle` | `host`、`port`（默认 1521）、`username`、`service_name` 或 `sid`，可选 `schema` |
| `api` | `base_url`、`endpoints`，可选 `records_path`、`headers`、`auth_header`（默认 `Authorization`）、`auth_scheme`（默认 `Bearer`）、`timeout`（请求超时秒数，默认 30）、`cache_seconds`（默认 60）、`method`（默认 `GET`） |

Oracle 的 `config.schema` 用作默认浏览范围及 SQL 会话的当前 Schema，省略时使用登录用户的默认 Schema。数据库账号仍需有目标表的查询权限。查询表列表时，Oracle 可用查询参数 `schema` 指定另一个 Schema，MySQL 和 StarRocks 可用它指定库名，PostgreSQL 可用它筛选 Schema，HTTP API 返回配置的逻辑表，忽略该参数。数据库数据源的表对象中 `name` 通常为 `schema.table`，供场景及表探查接口引用。

**新增请求示例**（`POST /api/datasources`）

```json
{
  "name":"客服工单接口",
  "type":"api",
  "config":{
    "base_url":"https://tickets.example.com/v1",
    "records_path":"data.items",
    "endpoints":{"tickets":{"path":"/tickets","method":"GET","params":{},"description":"客服工单"}}
  }
}
```

`endpoints` 以逻辑表名为键。单项可传 `path`、`method`、查询对象 `params`、JSON `body`、`description` 及覆盖全局的 `records_path`。

**数据源对象**

```json
{
  "id":12,"name":"客服工单接口","type":"api","description":"","enabled":true,
  "created_at":"2026-01-01T00:00:00+00:00","updated_at":"2026-01-01T00:00:00+00:00",
  "config":{
    "base_url":"https://tickets.example.com/v1",
    "records_path":"data.items",
    "endpoints":{"tickets":{"path":"/tickets","method":"GET","params":{},"description":"客服工单"}}
  },
  "has_secret":false
}
```

`config` 和 `has_secret` 仅管理员可见，`secret` 不在响应中。测试响应示例为 `{"ok":true,"version":"接口 tickets 返回 50 条记录","latency_ms":12}`。`version` 是数据库版本或测试结果说明，`latency_ms` 单位为毫秒。连接器可返回 `400` 或 `ok:false`，调用方应检查 HTTP 状态和 `ok`。

**探查表响应示例**（`GET /api/datasources/12/describe?table=tickets&sample_rows=1`）

```json
{
  "datasource":"客服工单接口","datasource_type":"api","table":"tickets",
  "columns":[{"name":"resolve_hours","type":"DOUBLE","nullable":true,"comment":""}],
  "row_count":50,"sample_columns":["resolve_hours"],"sample_rows":[[3.5]],
  "profile":[{"column":"resolve_hours","kind":"number","null_ratio":0.04,"distinct":30,"min":1.0,"max":10.0,"avg":4.0,"full_min":1.0,"full_max":10.0}],
  "profile_basis":"基于前 50 行样本；日期/数值列另附全表最值 full_min/full_max"
}
```

`row_count` 可能为 `null`，列的 `nullable` 也可能为 `null`。获取列元数据出错但仍能返回样本时，`columns` 为 `[]` 并附 `columns_error`。画像的 `kind` 为 `number`、`datetime`、`text`、`boolean` 或 `unknown`，`null_ratio` 为 0–1 或 `null`。`min`、`max`、`avg`、`full_min`、`full_max`、`comment`、`top_values`（数组项含字符串 `value` 和整数 `count`）按列类型与查询结果出现。`sample_rows` 按 `sample_columns` 的顺序返回二维数组，画像基于采样数据而非仅响应中展示的样本行。

**查询示例**（`POST /api/datasources/12/query`）

请求体

```json
{"sql":"SELECT resolve_hours FROM tickets WHERE resolve_hours IS NOT NULL","limit":2}
```

成功响应

```json
{"datasource":"客服工单接口","dialect":"api","columns":["resolve_hours"],"rows":[[3.5],[4.0]],"row_count":2,"truncated":true}
```

SQL 只接受单条只读 `SELECT` 或 `WITH` 查询，HTTP API 数据源使用 DuckDB 方言。`dialect` 返回数据源类型，列名转为小写，重名列会追加后缀。`row_count` 是本次实际返回的行数，`truncated:true` 表示有更多行。普通员工可使用已授权的模板校验、预览或 MCP 场景级工具查询数据。

## 业务场景

| 方法与路径 | 权限 | 请求 | 响应 |
|---|---|---|---|
| `GET /api/datasets` | 登录 | 可选查询参数 `q`，按场景名、业务描述或标签做不区分大小写的包含搜索 | 场景摘要数组，按 ID 升序。普通用户仅见启用且有表权限的场景 |
| `POST /api/datasets` | 管理员 | JSON 场景配置。必填 `business_scenario`、`business_description`、`datasource_id`，可选 `analysis_approach`、`tags`、`enabled`、`tables` | 场景详情。缺字段、空表名或重复表名返回 `400`，重名或引用冲突返回 `409` |
| `GET /api/datasets/{dataset_id}` | 登录 | 路径 `dataset_id` | 场景详情。普通用户仅见获授权的表。不存在或不可见返回 `404` |
| `PUT /api/datasets/{dataset_id}` | 管理员 | 路径 `dataset_id`。JSON 传要修改的字段，传入 `tables` 时整体替换，可用 `[]` 清空 | 更新后的场景详情。不存在返回 `404`，参数校验失败返回 `400`，重名或引用冲突返回 `409` |
| `DELETE /api/datasets/{dataset_id}` | 管理员 | 路径 `dataset_id`，无请求体 | `{"ok":true}`，删除场景及其表清单和授权，已关联的模板解除场景关联 |
| `GET /api/datasets/search` | 登录 | 必填查询参数 `q` 为业务需求，可选整数 `top_k`，默认 5 | 混合检索结果，见下方。仅检索启用场景，普通用户限已授权场景 |
| `POST /api/datasets/reindex` | 管理员 | 无 | 如 `{"datasets":3,"style_rules":2,"not_vectorized":0}`，前两项为重建索引的记录数，末项为操作后仍无向量的记录数 |
| `GET /api/scenarios` | 登录 | 无 | 场景名称字符串数组，如 `["客服工单分析"]`。管理员见全部，普通用户限启用且已授权的场景 |

**场景请求示例**（`POST /api/datasets`）

```json
{
  "business_scenario":"客服工单分析",
  "business_description":"跟踪工单量、处理时长与超时情况。",
  "analysis_approach":"先核对日期范围和缺失率，再按同口径比较各渠道的处理时长。",
  "datasource_id":12,"tags":["客服","时效"],"enabled":true,
  "tables":[{"table_name":"tickets","table_description":"客服工单记录",
    "row_filters":[{"column":"channel","values":["在线","电话"]},{"column":"status","values":["已完成"]}]}]
}
```

`business_scenario`、`business_description` 为非空字符串，`datasource_id` 为整数。`analysis_approach` 和 `table_description` 为字符串，新增时默认为空字符串。`tags` 为字符串数组，`tables` 为对象数组，新增时均默认为 `[]`。`enabled` 为布尔值，新增时默认 `true`。每个表对象必填字符串 `table_name`，使用数据源表列表返回的 `name`。列表顺序决定保存后表项的 `sort`，从 0 开始。

每个表或视图可设 `row_filters`（默认 `[]`，不限制）。同字段多个保留值满足其一即可，多个字段须同时满足；`null` 表示匹配数据库空值。每表最多 20 个字段，每字段 1–100 个 JSON 标量值。字段名推荐使用 `source_name`。服务端在场景 SQL 的 JOIN、聚合和行数限制前应用条件，场景画像也按相同范围计算。管理台数据源的原始 SQL 查询不使用场景条件。

**场景详情示例**

```json
{
  "id":12,"business_scenario":"客服工单分析",
  "business_description":"跟踪工单量、处理时长与超时情况。",
  "analysis_approach":"先核对日期范围和缺失率，再按同口径比较各渠道的处理时长。",
  "datasource_id":12,"datasource_name":"客服工单接口","datasource_type":"api",
  "tags":["客服","时效"],"enabled":true,"vectorized":true,
  "created_at":"2026-01-01T00:00:00+00:00","updated_at":"2026-01-01T00:00:00+00:00",
  "tables":[{"id":31,"table_name":"tickets","table_description":"客服工单记录","sort":0,
    "row_filters":[{"column":"channel","values":["在线","电话"]},{"column":"status","values":["已完成"]}]}]
}
```

场景摘要以 `table_count` 替代 `analysis_approach` 和 `tables`。普通用户的摘要和详情均附 `read_only:true`，`table_count` 统计其可见的表。管理员详情不含 `table_count`。

**混合检索结果**

返回 `query_tokens`（分词字符串数组）、`hits`（命中数组）、`warnings`（字符串数组，向量不可用时说明关键词降级）。每个命中有 `id`、`rrf`（融合排序分）、`relevance`（0–1）、`dataset`（上述场景详情）。有向量命中时附 `vector_score` 和 `vector_rank`，有关键词命中时附 `keyword_score`、`keyword_rank` 和 `matched_terms`（字符串数组）。排名从 1 开始，结果按 `rrf` 降序。无命中时 `hits:[]`。

## 报告模板

业务模板保存面向业务人员的 Markdown 文档，供 agent 开发 HTML。结构化模板可由管理台和服务端直接运行。

| 方法与路径 | 权限 | 请求 | 响应 |
|---|---|---|---|
| `GET /api/templates` | 登录 | 可选查询参数 `q`，按模板名、场景、业务说明、目标、需求搜索 | 模板摘要数组，按更新时间降序。普通用户仅见本人模板 |
| `POST /api/templates` | 登录 | JSON 模板写入对象，必填 `markdown`，其他字段见下方 | 模板详情，初始 `version:1`。源码非法返回 `400`，无权使用指定场景返回 `403` |
| `POST /api/templates/validate` | 登录 | JSON 传 `markdown`，可选整数 `dataset_id`、布尔值 `execute`（默认 `true`） | 校验结果。结构、SQL 或字段校验失败仍返回 `200`，以 `ok:false` 及 `errors` 表示。对请求中指定的场景无权访问时返回 `403` |
| `POST /api/templates/preview` | 登录 | JSON 传 `markdown`，可选整数 `dataset_id`。管理员可传 `owner`（工号）按其个人样式预览 | `200 text/html`，即时渲染的报告页面。源码、SQL 或渲染失败返回 `400`，无权使用指定场景返回 `403` |
| `GET /api/templates/{tpl_id}` | 本人 | 路径 `tpl_id` | 模板详情，额外附 `versions`。不存在返回 `404`，非本人且非管理员返回 `403` |
| `PUT /api/templates/{tpl_id}` | 本人 | 路径 `tpl_id`。JSON 必须提交 `markdown`，可同时更新管理字段 | 更新后的模板详情，源码变化时新增版本。不存在返回 `404`，权限或校验失败同新增及详情 |
| `DELETE /api/templates/{tpl_id}` | 本人 | 路径 `tpl_id`，无请求体 | `{"ok":true}`，删除模板及历史版本，已生成的报告保留。不存在返回 `404`，越权返回 `403` |
| `GET /api/templates/{tpl_id}/versions/{version}` | 本人 | 路径 `tpl_id`、`version` | `{"version":1,"markdown":"历史 Markdown 源码","created_at":"2026-01-01T00:00:00+00:00"}`。模板或版本不存在返回 `404`，越权返回 `403` |
| `POST /api/templates/{tpl_id}/render` | 本人 | 路径 `tpl_id`。可省略请求体，或传 JSON `{"name":"客服工单日报"}`，缺省使用模板名 | 运行结构化模板并保存新报告，字段见报告库一节。业务模板或查询、渲染失败返回 `400`，越权返回 `403`，模板不存在返回 `404` |

**模板写入字段**

| 字段 | 类型 | 说明 |
|---|---|---|
| `markdown` | 字符串，必填 | 完整 Markdown。业务模板保存原文，结构化模板写入时做静态语法校验 |
| `template_type` | `business` 或 `structured`，可选 | `business` 为业务文档，`structured` 为可执行模板。新增省略时沿用 `structured` 默认值，创建后类型固定 |
| `name` | 字符串，可选 | 模板名，省略或空字符串时取源码标题，更新时也是如此 |
| `dataset_id` | 整数或 `null`，可选 | 关联场景。新增省略时无关联，更新省略时沿用原值，传 `null` 解除关联 |
| `user_requirement` | 字符串，可选 | 用户需求 |
| `analysis_goal` | 字符串，可选 | 分析目标。无有效值时尝试读取源码中的 `analysis_goal` |
| `business_summary` | 字符串，可选 | 业务说明 |

更新省略 `user_requirement`、`analysis_goal`、`business_summary` 时沿用原字段。创建人由服务端记录。版本接口的 `markdown` 可重新提交到更新接口以恢复旧内容。`validate` 和 `preview` 用于结构化模板。结构化源码格式见 [报告模板说明](../skills/smart-report/references/template-spec.md)。

**模板写入请求示例**（`POST /api/templates`）

```json
{
  "dataset_id":12,
  "business_summary":"查看客服工单数量。",
  "markdown":"---\ntitle: 客服工单日报\nqueries:\n  q1:\n    sql: SELECT COUNT(*) AS ticket_count FROM tickets\n---\n\n## 工单概况\n\n按日跟踪工单变化。"
}
```

**模板摘要示例**

```json
{
  "id":7,"name":"客服工单日报","dataset_id":12,"business_scenario":"客服工单分析","template_type":"structured",
  "user_requirement":"","analysis_goal":"","business_summary":"查看处理时长。",
  "summary":{"title":"客服工单日报","sections":["工单概况"],"chart_count":0,"filter_count":0,"query_count":1,"chart_types":[]},
  "version":1,"created_at":"2026-01-01T00:00:00+00:00","updated_at":"2026-01-01T00:00:00+00:00",
  "created_by":"E10001","creator_name":"示例员工"
}
```

模板详情在摘要上增加字符串 `markdown`。详情 GET 还增加 `versions`（按版本降序），每项为 `{"version":1,"created_at":"2026-01-01T00:00:00+00:00","size":120}`，`size` 是该版本源码的字符数。新增和更新的响应不含 `versions`。未关联场景时 `dataset_id`、`business_scenario` 可为 `null`，历史记录的 `created_by`、`creator_name` 也可为 `null`。

**校验结果示例**

```json
{
  "ok":true,"errors":[],"warnings":[],
  "summary":{"title":"客服工单日报","sections":["工单概况"],"chart_count":0,"filter_count":0,"query_count":1,"chart_types":[]},
  "queries":{"q1":{"datasource":"客服工单接口","rows":1,"columns":["ticket_count"],"truncated":false,"ms":12,"sample":[[50]]}},
  "filters":{}
}
```

结构校验通过且 `execute:true` 时返回 `queries` 和 `filters`，分别按查询 ID 和筛选器 ID 索引。查询结果的 `rows` 为行数，`sample` 最多 5 行，列顺序与 `columns` 一致，`ms` 为执行毫秒数。筛选器项为 `{"distinct":2,"examples":["在线客服","电话"]}`，`examples` 最多 12 个。指定 `execute:false` 或存在结构错误时，响应只含 `ok`、`errors`、`warnings` 和 `summary`。普通用户实际执行的 SQL 须通过表级权限检查，查询权限失败会出现在 `errors` 中。

已有 UTF-8 Markdown 文件时，可用 `jq --rawfile markdown template.md` 构造请求。完整调用见 [生成并下载报告](#report-quickstart)。HTTP 生成使用模板创建人的个人样式，报告归属模板创建人。报告保存生成时的数据和样式，浏览器在这份数据上执行交互筛选。

## 报告库

| 方法与路径 | 权限 | 请求 | 响应 |
|---|---|---|---|
| `GET /api/reports` | 登录 | 可选查询参数 `template_id`（整数）、`q`（搜索报告名、模板名、创建人姓名或工号） | 可访问的报告摘要数组，每份报告一项，按报告 ID 降序。每项显示调用者默认查看的版本 |
| `POST /api/reports/merge` | 对每份报告有查看权限 | JSON 传 `reports`，按合并顺序提供至少两个 `{"id":15,"version":1}` 项。可选 `name`（默认“合并报告”） | `200 text/html` 单文件附件。缺少查看权限返回 `403`，报告或版本不存在返回 `404`，重复选择同一报告或旧版本有外部依赖返回 `400` |
| `GET /api/reports/{report_id}` | 本人或获分享 | 可选查询参数 `version`（正整数）、`include_html`、`include_source`（布尔值，均默认 `false`） | 报告详情与固定版本链接。按需增加 `html`（字符串）及 `markdown`（字符串或 `null`） |
| `GET /api/reports/{report_id}/versions` | 本人或获分享 | 无额外参数 | 默认查看的版本详情（无链接字段）及 `versions` 数组，按版本号降序 |
| `PUT /api/reports/{report_id}/selection` | 本人或获分享 | JSON 必填 `version`，正整数或 `null`。例如 `{"version":2}` | 保存当前账号的版本选择，返回所选版本详情。`{"version":null}` 恢复跟随最新。格式错误返回 `422` |
| `PUT /api/reports/{report_id}` | 本人或获编辑权限 | JSON 必填 `name`，`html` 或 `markdown` 二选一。可选 `base_version`、`expected_version`、`revision`、`change_note` | 在同一报告追加版本，返回新版本详情。并发冲突返回 `409`，字段校验失败返回 `422`，内容或渲染失败返回 `400` |
| `DELETE /api/reports/{report_id}` | 本人 | 无额外参数 | `{"ok":true}`，删除报告及版本和分享记录 |
| `GET /api/reports/{report_id}/shares` | 本人 | 无额外参数 | 分享对象数组，按工号升序 |
| `GET /api/reports/{report_id}/share-recipients` | 本人 | 可选查询参数 `q`（姓名或工号，最多 100 个字符） | 最多 20 个已启用接收人，仅返回 `emp_no`、`name` |
| `POST /api/reports/{report_id}/shares` | 本人 | JSON 必填 `emp_no`（1–256 个字符）、`access_level`（`view` 或 `edit`） | 分享对象，已存在时更新权限。未登记的工号可预先授权。工号无效、停用或为创建人时返回 `400`，字段校验失败返回 `422` |
| `DELETE /api/reports/{report_id}/shares/{emp_no}` | 本人 | 路径 `report_id`、`emp_no`，无请求体 | `{"ok":true}`，撤销接收员工的访问权限。该授权已不存在时也返回成功 |
| `GET /reports/{report_id}` | 本人或获分享 | 可选正整数查询参数 `version` | `200 text/html`。`Accept` 包含 `text/html` 时返回带版本操作的浏览器页面，其他情况下返回报告正文 |
| `GET /reports/{report_id}/content` | 本人或获分享 | 可选正整数查询参数 `version` | `200 text/html`，报告正文 |
| `GET /reports/{report_id}/download` | 本人或获分享 | 可选查询参数 `version`（正整数）、`standalone`（布尔值，默认 `true`） | `200 text/html` 附件，带 `Content-Disposition: attachment; filename*=UTF-8''<编码文件名>` |

以上带 `{report_id}` 的路径都需要报告整数 ID。报告或所请求版本不存在返回 `404`，权限不足返回 `403`。普通报告 ID 省略 `version` 时使用账号已选版本，没有个人选择时跟随最新。显式 `version` 只影响本次读取。迁移保留的旧报告链接默认定位原快照，详情的 `id` 返回实际报告 ID。

报告页、正文和下载接口对无有效浏览器会话、无 `Authorization` 且 `Accept` 包含 `text/html` 的请求返回 `302`，`Location` 指向 IAM 登录入口。其他未认证请求返回 `401`。浏览器访问但缺少报告查看权限时返回 `403 text/html`。新报告下载直接返回保存的单文件。对仍引用内置 ECharts 的旧报告，`standalone=true` 会内联该图表库，`false` 返回原始 HTML。报告接口的已授权响应带 `Cache-Control: no-store`，正文及附件成功响应另带 `Content-Security-Policy: sandbox allow-scripts` 和 `Referrer-Policy: no-referrer`。

**报告详情示例**

```json
{
  "id":15,"name":"客服工单日报","template_id":7,"template_version":1,
  "meta":{
    "queries":{"q1":{"datasource":"客服工单接口","rows":1,"columns":["ticket_count"],"truncated":false,"ms":12}},
    "warnings":[],
    "summary":{"title":"客服工单日报","sections":["工单概况"],"chart_count":0,"filter_count":0,"query_count":1,"chart_types":[]},
    "render_ms":25,"html_bytes":12400
  },
  "created_at":"2026-01-01T00:00:00+00:00","created_by":"E10001","size":12000,
  "revision":1,"base_version":null,"version_created_at":"2026-01-01T00:00:00+00:00",
  "version_created_by":"E10001","change_note":"","latest_version":1,
  "current_version":1,"version_selected":false,"access_level":"manage",
  "dataset_id":12,"source_available":true,
  "url":"https://report.example.com/reports/15?version=1",
  "content_url":"https://report.example.com/reports/15/content?version=1",
  "download_url":"https://report.example.com/reports/15/download?version=1"
}
```

| 响应 | 字段区别 |
|---|---|
| 列表摘要 | 以上详情去掉 `dataset_id`、`source_available`、三个 URL 字段，增加 `template_name`、`creator_name`（均可为 `null`） |
| 详情、编辑、版本选择 | 以上结构。仅详情 GET 可按查询参数返回 `html` 或 `markdown`。`source_available` 表示当前账号有权获取该版本保存的 Markdown。请求源码但无法提供时，`markdown` 为 `null` |
| 版本列表 | 以上详情去掉三个 URL 字段，增加 `versions` 数组。每项含 `version`、`base_version`、`name`、`created_at`、`created_by`、`creator_name`、`change_note` |
| 生成结果 | `id`、`name`、`template_id`、`template_version`、`meta`、`created_at`、`created_by`、`size`、`revision`、`latest_version`、`current_version`、`base_version`、三个 URL 字段和顶层 `warnings`。三个版本号初始为 `1`，`base_version` 为 `null`，链接省略 `version` |

`revision` 是本次返回的报告版本，`base_version` 是其来源版本，`latest_version` 是报告最新版本，`current_version` 是该账号默认查看的版本，`version_selected` 表示账号是否主动固定了版本。`template_version` 是模板版本号。报告详情的三个链接均固定到本次返回的 `revision`。

`size` 是已保存 HTML 的字符数，`meta.html_bytes` 是字节数。旧报告或通过 MCP 直接保存的 HTML 可能只有部分 `meta` 字段，`template_id`、`template_version`、`dataset_id`、`created_by`、`version_created_by` 可为 `null`。

`access_level` 表示当前调用者对报告的权限。`view` 可查看、下载和选择自己的版本，`edit` 还可保存新版本，`manage` 还可管理分享和删除报告，由创建人及管理员持有。

**编辑报告请求示例**（`PUT /api/reports/15`）

```json
{"name":"客服工单日报","html":"<!doctype html><html><head><meta charset=\"utf-8\"><style>h1{color:#17396d}</style></head><body><h1>客服工单日报</h1><script>document.title = document.querySelector('h1').textContent;</script></body></html>","base_version":1,"expected_version":1,"change_note":"更新标题"}
```

| 字段 | 类型及用途 |
|---|---|
| `name` | 必填，非空字符串。保存时去除首尾空白 |
| `html`、`markdown` | 二选一，字符串。`html` 提交已验证的单文件，CSS、JavaScript 和数据均内联。`markdown` 供旧结构化报告重新执行查询，需有该版源码和当前数据权限 |
| `base_version` | 可选正整数，从详情的 `revision` 读取。指定编辑使用的版本，允许从历史版本生成新版本。省略时使用个人选定版本或最新版本，旧链接默认用原快照 |
| `expected_version` | 可选正整数，从详情的 `latest_version` 读取。传入后，如果报告在读取后产生了新版本，保存返回 `409` |
| `revision` | 兼容旧客户端的可选正整数，同时作为 `base_version` 与 `expected_version`。传入时省略另外两个版本字段 |
| `change_note` | 可选字符串，默认空，最多 500 字符 |

例如当前已有 v1–v4，使用 `base_version:2`、`expected_version:4` 成功保存时产生 v5，响应中 `base_version` 为 2，`revision` 和 `latest_version` 为 5。保存后编辑者恢复跟随最新版本。手工修改 HTML 正文后，新版本不再提供原 Markdown。收到 `409` 时先读取最新内容和版本，再决定如何合并修改。

**分享请求与响应示例**（`POST /api/reports/15/shares`）

请求为 `{"emp_no":"E10002","access_level":"view"}`，响应为 `{"emp_no":"E10002","name":"示例同事","enabled":true,"registered":true,"access_level":"view"}`。分享列表返回同结构的数组。`registered` 表示工号是否已在系统登记，`enabled` 表示账号当前是否启用。未登记工号的两个字段均为 `false`，接收人完成海尔 IAM 登录且账号启用后即可使用授权。

分享权限覆盖同一份报告的全部版本，模板和业务数据权限独立授权。撤销后，接收人再次访问该报告会收到 `403`，先前下载的离线文件仍可打开。跨设备分享时，`PUBLIC_BASE_URL` 须指向接收人可访问的部署地址。

报告库中的“合并报告”会锁定勾选时的版本。可以调整拼接顺序和文件名称，再下载纵向排列的单文件 HTML。各份报告保留原始标题、报告头和正文，隐藏可识别的目录和页脚。下载文件可离线打开，图表和筛选在各自的报告中运行。

## 报告样式

| 方法与路径 | 权限 | 请求 | 响应 |
|---|---|---|---|
| `GET /api/style-tokens` | 登录 | 无 | 样式项元数据，字段见下方 |
| `GET /api/style-rules` | 登录 | 无 | 样式规则对象数组。管理员见全部，普通用户见通用规则与本人场景规则 |
| `POST /api/style-rules` | 按规则范围 | JSON 规则写入对象，必填 `name`、`scope`，个人场景规则还需 `business_scenario` | 新建规则对象。字段或场景非法返回 `400`，权限不足返回 `403` |
| `GET /api/style-rules/{rule_id}` | 登录 | 路径 `rule_id` | 样式规则对象。个人场景规则仅本人和管理员可读，不存在返回 `404`，越权返回 `403` |
| `PUT /api/style-rules/{rule_id}` | 按规则范围 | 路径 `rule_id`。JSON 传要修改的字段，`theme` 和 `items` 按整字段提交 | 更新后的规则对象。不存在返回 `404`，字段或场景非法返回 `400`，权限不足返回 `403` |
| `DELETE /api/style-rules/{rule_id}` | 按规则范围 | 路径 `rule_id`，无请求体 | `{"ok":true}`。规则不存在返回 `404`，权限不足返回 `403`。个人规则所属场景不存在时，场景校验返回 `400` |
| `GET /api/style-rules/resolve` | 登录 | 可选查询参数 `scenario`（业务场景名）、`q`（相关度检索文本） | 当前用户的指定样式，字段见下方。省略场景时仅合并通用规则和内置样式 |
| `POST /api/style-rules/effective` | 登录 | JSON 对象（可传 `{}`）。可选 `scenario`、`exclude_id`、`draft` | `theme`、`sources`、`precedence`，见下方。排除的规则不存在返回 `404`，无权编辑该场景规则返回 `403` |
| `POST /api/style-rules/preview` | 登录 | 与 `effective` 相同的 JSON 请求体 | `200 text/html`，用示例数据渲染样式预览。草稿样式值非法返回 `400`，规则访问错误同 `effective` |

通用规则 `scope:"general"` 由管理员写入。个人规则 `scope:"scenario"` 需要指定 `business_scenario`。普通用户须有该场景权限，更新或删除时还须为规则创建人。通用规则适用于所有人，个人场景规则只应用于对应创建人的场景报告。同一范围内，较大的 `priority` 优先，同优先级时较新的 ID 优先。模板中的 `theme` 可以覆盖规则合并的样式。

**规则写入示例**

```json
{
  "name":"客服报表展示","scope":"scenario","business_scenario":"客服工单分析",
  "priority":100,"enabled":true,
  "items":[{"category":"number","text":"处理时长以小时展示，保留一位小数。"}],
  "theme":{"color-brand":"#1A72E8"}
}
```

`name`、`scope`、`business_scenario` 为字符串。`priority` 为整数，默认 100。`enabled` 为布尔值，默认 `true`。`items` 为对象数组，默认 `[]`，每项含字符串 `category` 和 `text`。`theme` 为对象，默认 `{}`。可传字符串 `content`（兼容已有 Markdown 说明），传入非空 `items` 时由服务端生成说明。样式项及展示要求类别以 `GET /api/style-tokens` 为准。写入时会规范化已知样式项，忽略未知键和已停用的分析类别，非法样式值返回 `400`。更新 `items` 时保留旧记录中的已停用类别供查阅。

**样式规则对象**

```json
{
  "id":4,"name":"客服报表展示","scope":"scenario","business_scenario":"客服工单分析",
  "content":"## 客服报表展示\n### 数字格式\n- 处理时长以小时展示，保留一位小数。",
  "items":[{"category":"number","text":"处理时长以小时展示，保留一位小数。"}],
  "theme":{"color-brand":"#1A72E8"},"priority":100,"enabled":true,"vectorized":true,
  "created_by":"E10001","creator_name":"示例员工",
  "created_at":"2026-01-01T00:00:00+00:00","updated_at":"2026-01-01T00:00:00+00:00"
}
```

`business_scenario` 对通用规则为 `null`，历史记录的 `created_by` 和 `creator_name` 可为 `null`。

**`GET /api/style-tokens` 响应字段**

| 字段 | 结构 |
|---|---|
| `groups` | 数组，每组含 `key`、`label`、`desc`、`tokens`。样式项含 `key`、`label`、`type`、`builtin`，按类型附说明、取值选项或示例 |
| `categories` | 数组，每项含 `key`、`label`、`hint`、`examples`（字符串数组） |
| `defaults`、`suggested` | 两个以样式键为索引的对象，当前均返回内置主题 |
| `retired_analysis_categories` | 已停用类别到名称的映射，键为 `structure`、`chart`、`filter` |
| `precedence` | 字符串，描述样式生效顺序 |
| `reporting_rules` | 只读内置报告规则数组，每项含 `id`、`label`、`text`、`level:"builtin"`、`source`；不参与视觉样式的覆盖 |

**`GET /api/style-rules/resolve` 响应字段**

| 字段 | 结构 |
|---|---|
| `business_scenario` | 请求中的场景名，省略时为 `null` |
| `precedence` | 生效顺序说明 |
| `specified.theme` | 合并后已设置的样式键和值 |
| `specified.theme_sources` | 同一批样式键对应的来源说明字符串 |
| `specified.items` | 合并后的展示要求，每项含 `category`、`category_label`、`text`、`level`（`general` 或 `scenario`）、`source` |
| `reporting_rules` | 与 `GET /api/style-tokens` 相同的内置事实及离线交付约束，独立于 `specified` 的展示优先级 |
| `general_rules`、`scenario_rules` | 生效的通用规则和当前用户个人场景规则数组。规则的 `items` 附 `category_label`。有检索命中时规则附 `relevance`，结构同业务场景检索命中去掉 `dataset` |
| `warnings` | 字符串数组，例如相关度检索的向量降级提示 |

`reporting_rules` 由代码内置，不写入样式规则表，不能通过规则 CRUD 删除或修改。MCP `get_style_rules` 同样返回该字段；`get_skill_guide(reference="fact-contract")` 可读事实取数细则，`reference="green-energy-september-facts"` 可读附件的绿能九月场景参考。后者不代表本次已验证数据或复现样式。规则指导 Agent 直取事实、保留缺失并交付离线单文件，不是任意 SQL、JavaScript 或自然语言结论的自动合规校验器。

**`effective` 与 `preview` 请求示例**

```json
{"scenario":"客服工单分析","exclude_id":4,"draft":{"scope":"scenario","name":"编辑中","theme":{"color-brand":"#1A72E8"}}}
```

`scenario` 为场景名字符串，`exclude_id` 为待替换的整数规则 ID。省略 `exclude_id` 时按当前用户计算，编辑已有个人场景规则时按该规则的创建人计算。`draft` 为临时主题对象，`scope` 决定插入通用或场景层，`name` 用于显示来源，`theme` 提供覆盖值。两个接口均不保存草稿。

`effective` 的 `theme` 仅含已设置的样式项。`sources` 以样式键为索引，例如 `{"color-brand":{"level":"scenario","rule":"编辑中"}}`。`level` 可为 `builtin`、`general`、`scenario`、`unset`，其中 `unset` 表示该项没有指定值。`precedence` 是生效顺序说明。

## 员工与数据授权

| 方法与路径 | 权限 | 请求 | 响应 |
|---|---|---|---|
| `GET /api/employees` | 管理员 | 无 | 员工对象数组，按工号升序。每项额外含整数 `direct_grants`（员工直授条数） |
| `POST /api/employees` | 管理员 | JSON 传必填 `emp_no`。可选 `name`、`department`、`roles`、`is_admin`、`enabled` | 员工对象，`registered_via:"manual"`。空工号、已有工号或布尔值非法返回 `400`，并发重号可返回 `409` |
| `PUT /api/employees/{emp_no}` | 管理员 | 路径 `emp_no`。JSON 传需修改的员工字段，省略字段保留原值，工号由路径确定 | 更新后的员工对象。不存在返回 `404`，布尔值非法返回 `400` |
| `DELETE /api/employees/{emp_no}` | 管理员 | 路径 `emp_no`，无请求体 | `{"ok":true}`，删除员工、直授记录和本地浏览器会话。要持续禁止该 IAM 工号访问时，应将员工设为停用 |
| `GET /api/grants` | 管理员 | 无 | 表授权对象数组，示例见下方 |
| `POST /api/grants` | 管理员 | JSON 传 `subject_type`、`subject`、`dataset_id`、`tables`，均必填 | `{"added":1}`，已有授权会跳过。场景不存在返回 `404`，授权对象或表无效返回 `400` |
| `DELETE /api/grants/{grant_id}` | 管理员 | 路径 `grant_id`，无请求体 | `{"ok":true}`。该 ID 已不存在时也返回成功 |
| `GET /api/permissions/check` | 管理员 | 必填查询参数 `emp_no` | 有效权限对象（`registered`、`all`、`datasets`），工号存在时附 `employee`。工号不存在时返回 `{"registered":false,"all":false,"datasets":[]}` |

**员工写入示例**

```json
{"emp_no":"E10001","name":"示例员工","department":"运营部","roles":["user"],"is_admin":false,"enabled":true}
```

`emp_no`、`name`、`department` 为字符串，`roles` 为字符串数组，`is_admin`、`enabled` 使用 JSON 布尔值。手工登记时 `name`、`department` 默认空字符串，`roles` 默认 `[]`，`is_admin` 默认 `false`，`enabled` 默认 `true`。首次通过 IAM 自动登记的普通用户默认获得部署配置 `DEFAULT_USER_ROLE`（通常为 `user`）。

**员工对象**

```json
{
  "emp_no":"E10001","name":"示例员工","department":"运营部","roles":["user"],
  "is_admin":false,"enabled":true,"registered_via":"manual",
  "created_at":"2026-01-01T00:00:00+00:00","updated_at":"2026-01-01T00:00:00+00:00"
}
```

`registered_via` 可为 `manual`（手工登记）、`iam_login`（管理台 IAM 登录）、`mcp`（首次经 Bearer 接入）。设为管理员时请求 `PUT /api/employees/E10001` 并发送 `{"is_admin":true}`。恢复普通用户时发送 `{"is_admin":false}`。`roles` 用于分配数据表权限。身份变更在该员工下一次 HTTP 或 MCP 请求时生效，管理员权限只对启用账号生效。

**新增授权请求示例**

```json
{"subject_type":"employee","subject":"E10001","dataset_id":12,"tables":["tickets"]}
```

`subject_type` 为 `employee` 或 `role`，`subject` 分别是已登记工号或数据角色编码，均为字符串。`dataset_id` 为整数。`tables` 为非空字符串数组，表名须在场景表清单中。`["*"]` 表示场景中当前及未来关联的全部表。

**表授权对象**

```json
{
  "id":8,"subject_type":"employee","subject":"E10001","dataset_id":12,
  "table_name":"tickets","created_at":"2026-01-01T00:00:00+00:00",
  "business_scenario":"客服工单分析","employee_name":"示例员工"
}
```

角色授权的 `employee_name` 为 `null`，整场景授权的 `table_name` 为 `"*"`。查询员工有效权限时会合并角色授权和直授，并按当前启用的场景及实际表清单计算。

<a id="mcp-oauth"></a>

## Codex CLI 与 MCP OAuth

在已安装 Codex CLI 的终端设置部署方提供的 `MCP_URL`。默认是 `BASE_URL` 加 `/mcp`，如部署方提供了不同地址，以其为准。

```bash
MCP_URL="$BASE_URL/mcp"
codex mcp add smartreportgen --url "$MCP_URL" --oauth-client-id smartreportgen-cli
codex mcp login smartreportgen
```

按浏览器提示完成海尔 IAM 登录后，在客户端调用 `whoami`，确认工号、`registered` 和 `access`。兼容客户端通过 OAuth 发现、浏览器授权及 S256 PKCE 获得 IAM 原票。令牌到期后重新登录。

| 方法与路径 | 权限 | 请求 | 响应 |
|---|---|---|---|
| `GET /.well-known/oauth-protected-resource/mcp` | 公开 | 无 | MCP 资源元数据，字段见下方。也可使用 `GET /.well-known/oauth-protected-resource`，两者返回相同结构 |
| `GET /.well-known/oauth-authorization-server` | 公开 | 无 | 授权服务器元数据，字段见下方 |
| `GET /auth/mcp/authorize` | 公开 | 查询参数见下方 | `302` 跳转至 IAM，设置短时 HttpOnly 回调 Cookie。请求非法返回 `400` |
| `POST /auth/mcp/token` | 授权码与 PKCE | `Content-Type: application/x-www-form-urlencoded`，表单字段见下方 | 如 `{"access_token":"<IAM access token>","token_type":"Bearer","scope":"mcp","expires_in":3600}`。非法或已使用的授权码返回 `400` |

未配置 IAM 时，以上发现、授权和换票接口返回 `404`。OAuth JSON 错误格式为 `{"error":"invalid_request","error_description":"错误说明"}`，未启用时 `error` 为 `server_error`。

**资源发现响应**

```json
{
  "resource":"http://localhost:18600/mcp",
  "authorization_servers":["http://localhost:18600"],
  "bearer_methods_supported":["header"],
  "scopes_supported":["mcp"]
}
```

配置其他 MCP 公开路径时，资源发现路径按该 URL 的路径生成。

**授权服务器发现响应**

```json
{
  "issuer":"http://localhost:18600",
  "authorization_endpoint":"http://localhost:18600/auth/mcp/authorize",
  "token_endpoint":"http://localhost:18600/auth/mcp/token",
  "response_types_supported":["code"],
  "grant_types_supported":["authorization_code"],
  "code_challenge_methods_supported":["S256"],
  "token_endpoint_auth_methods_supported":["none"],
  "scopes_supported":["mcp"],
  "authorization_response_iss_parameter_supported":true
}
```

**授权请求查询参数**

| 参数 | 要求 |
|---|---|
| `client_id` | 必填，`smartreportgen-cli` |
| `response_type` | 必填，`code` |
| `redirect_uri` | 必填，`http://127.0.0.1:<port>/callback` 或 IPv6 本机回环 `[::1]`。端口及 `/callback/<实例 ID>` 的实例后缀可选 |
| `state` | 必填，客户端生成的 1–512 字符状态值，用于验证回调 |
| `code_challenge_method` | 必填，`S256` |
| `code_challenge` | 必填，`code_verifier` 的 SHA-256 摘要，使用无填充的 Base64URL 编码（43 字符） |
| `scope` | 可选，若填写则为 `mcp` |
| `resource` | 可选，若填写须与 MCP 公开 URL 一致 |

授权发起失败时，`error` 可为 `invalid_client`、`invalid_request`、`invalid_scope`、`invalid_target`。IAM 仍调用 `GET /auth/iam/callback`，带 `code`、以 `mcp.` 开头的 IAM `state` 和本次流程的回调 Cookie。成功时服务端返回 `302` 跳回客户端 `redirect_uri`，查询参数为一次性 `code`、原客户端 `state` 及 `iss`。授权码有效期为 2 分钟。IAM 授权失败或暂不可用时，重定向携带 `error`（`access_denied` 或 `temporarily_unavailable`）、`error_description`、`state` 和 `iss`。无法验证流程状态时直接返回 `400` OAuth JSON 错误。

**换票请求表单字段**

| 字段 | 要求 |
|---|---|
| `grant_type` | 必填，`authorization_code` |
| `code` | 必填，客户端回调收到的 SmartReportGen 一次性授权码 |
| `client_id` | 必填，`smartreportgen-cli` |
| `redirect_uri` | 必填，与授权请求中的值一致 |
| `code_verifier` | 必填，生成挑战值时使用的原串，43–128 个字母、数字或 `._~-` 字符 |
| `resource` | 可选，若填写须与 MCP 公开 URL 一致 |

换票成功返回原始 IAM `access_token`，`expires_in` 仅在已知剩余有效期时返回，单位为秒。换票错误的 `error` 可为 `invalid_request`、`invalid_client`、`unsupported_grant_type`、`invalid_target`、`invalid_grant`。OAuth 流程不签发 refresh token。

## MCP HTTP 传输

`/mcp` 使用 Streamable HTTP。每次请求均携带 `Authorization: Bearer <IAM access token>`，也兼容 `X-IAM-Access-Token` 或 `Access-Token`。服务当前使用无状态模式，不需要 `Mcp-Session-Id`。初始化后由客户端发送协商得到的 `MCP-Protocol-Version`。

| 方法与路径 | 请求 | 响应 |
|---|---|---|
| `POST /mcp` | `Content-Type: application/json`，`Accept: application/json, text/event-stream`。请求体为单个 JSON-RPC 2.0 消息 | 有 `id` 的调用返回 `200 application/json`，含同一 `id` 及 `result` 或 `error`。通知返回 `202`，无响应体 |
| `GET /mcp` | 无请求体，`Accept: text/event-stream` | `200 text/event-stream`，用于服务端事件流。调用工具使用 POST |
| `DELETE /mcp` | 无请求体 | `405`，无状态模式不支持显式结束会话 |

身份校验失败时返回 `401`，含 `WWW-Authenticate` 及 JSON `{"error":"unauthorized","message":"错误说明"}`。IAM 暂不可用时返回 `503`，`error` 为 `iam_unavailable`。POST 的 `Content-Type` 缺失或不受支持时返回 `400`，响应体为文本 `Invalid Content-Type header`。消息或协议版本非法时返回 `400` JSON-RPC 错误，`Accept` 不匹配时返回 `406` JSON-RPC 错误。

初始化请求示例

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"example-client","version":"1.0"}}}
```

初始化成功时 `result` 含 `protocolVersion`、`capabilities`、`serverInfo` 和 `instructions`。客户端随后发送 `{"jsonrpc":"2.0","method":"notifications/initialized"}`。`tools/list` 返回 `result.tools`，每项含 `name`、`description`、`inputSchema` 等信息。调用示例

```json
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"whoami","arguments":{}}}
```

`tools/call` 的 `result` 含 `content` 和 `isError`。`content` 是内容块数组，`whoami` 返回 `type:"text"` 的块，其 `text` 是 JSON 字符串。解析后得到 `ok`、`emp_no`、`name`、`is_admin`、`via`、`registered`、`roles`、`access`，其中 `access` 含 `all`、`datasets`。其他工具的入参按 `tools/list` 返回的 schema 发送。

已有 IAM 令牌时，可复用首次调用中的 `api` 函数，通过命令行验证 MCP 身份。`MCP_URL` 按实际部署设置。

```bash
(
  set -eu
  MCP_URL="$BASE_URL/mcp"
  mcp_post() {
    api "$MCP_URL" -H 'Content-Type: application/json' \
      -H 'Accept: application/json, text/event-stream' "$@"
  }

  init=$(mcp_post --data-binary '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"example-client","version":"1.0"}}}')
  protocol=$(printf '%s\n' "$init" | jq -er '.result.protocolVersion | strings')
  mcp_post -H "MCP-Protocol-Version: $protocol" \
    --data-binary '{"jsonrpc":"2.0","method":"notifications/initialized"}'

  result=$(mcp_post -H "MCP-Protocol-Version: $protocol" \
    --data-binary '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"whoami","arguments":{}}}')
  printf '%s\n' "$result" | jq -er '.result.content[] | select(.type == "text") | .text | fromjson'
)
```

## 演示工单接口

| 方法与路径 | 权限 | 请求 | 响应 |
|---|---|---|---|
| `GET /demo-api/tickets` | 公开 | 无 | `200`，`code` 固定为 0、`message` 为 `"ok"`，`data.total` 为合成工单数，`data.items` 为工单数组 |

响应结构示例，下列 `items` 仅展示一项。接口返回完整数组。

```json
{
  "code":0,"message":"ok",
  "data":{
    "total":3200,
    "items":[
      {"ticket_id":"T202600001","created_date":"2026-01-15","month":"2026-01","channel":"在线客服","category":"物流配送","priority":"中","status":"已解决","region":"华东","resolve_hours":3.5,"satisfaction":4}
    ]
  }
}
```

处理中工单的 `resolve_hours` 和 `satisfaction` 为 `null`。该接口用于配置演示 HTTP API 数据源，记录路径为 `data.items`。
