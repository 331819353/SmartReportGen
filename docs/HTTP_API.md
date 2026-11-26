# SmartReportGen HTTP 接入说明

本指南面向调用 SmartReportGen 的开发者。管理台、HTTP API 和 MCP 共用一个服务入口。接口路径均相对于部署地址 `BASE_URL`，例如 `https://report.example.com/api/me`。域名、工号、资源 ID 和数据都是示例。部署实例的 Swagger 在 `$BASE_URL/docs`，OpenAPI 文档在 `$BASE_URL/openapi.json`，本指南的在线版在 `$BASE_URL/api-guide`。

[首次调用](#quickstart) · [获取凭证](#authentication) · [生成并下载报告](#report-quickstart) · [常见问题](#troubleshooting) · [接口参考](#api-reference)

<a id="quickstart"></a>

## 首次调用

在测试环境开始。向部署方确认下面的信息。命令可在 Bash 或 Zsh 中运行，需要 `curl` 7.76 或以上版本，换票和报告示例还需要 `jq`。

| 需要确认 | 向谁确认 | 用途 |
|---|---|---|
| 调用机器可达的 HTTPS 部署地址 `BASE_URL`，以及是否需要企业内网或 VPN | 部署方 | 拼接 `/healthz`、`/api/me` 等路径。`http://localhost:18600` 仅适用于服务就在本机的场景 |
| 测试海尔账号，以及获得该账号 IAM access token 的方式 | IAM 或集成负责人 | 服务端调用使用 Bearer。已有 IAM 登录的系统使用其获得的用户令牌，自建登录按[获取凭证](#authentication)一节对接 IAM |
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
| Codex 或其他 MCP 客户端 | 从海尔 IAM 取得用户 access token，用 `Authorization: Bearer <access token>` 调用。见 [MCP 客户端接入](#mcp-authentication) |
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

IAM 返回 `resultCode=0` 时从 `data.access_token` 读取令牌，部分 IAM 环境字段为 `data.accessToken` 或 `data.token`。示例把令牌保存在变量中，换票失败时应停止调用并检查 IAM 返回的状态。避免把凭证写入日志。换票后用 `/api/me` 核对工号。令牌失效后由 IAM 登录流程更新凭证。

<a id="report-quickstart"></a>

## 生成并下载报告

报告新建从 MCP 发起。用 `prepare_report_context` 获取场景及 Markdown 指南，先通过 `save_report_template` 保存（或选用完全匹配的已有）报告模板并绑定其 `template_id`、`template_version`，用 `run_query(snapshot=true)` 获取快照，用 `create_html_draft` 与 `upsert_html_section` 拼装章节。将 `draft_id` 传给 `save_report`，服务端校验模板对应关系并展开为离线 HTML，通过 Chromium 后返回 `report_id`、`version`、`template_id`、`template_version` 及 `download_url`。需先审阅时，调用 `verify_html_draft` 获取 `preview_id` 和预览链接，确认后将 `preview_id` 传给 `save_report`。修订正文时需同步更新模板并传入新版模板参数。

用同一个 IAM 账号访问预览及报告，或使用获得报告分享及底层数据授权的账号查看已保存版本。

```bash
curl -fsS "$BASE_URL/reports/$REPORT_ID/download?version=$VERSION" \
  -H "Authorization: Bearer $SMARTREPORT_TOKEN" -o report.html
```

[HTML 拼装指南](../skills/smart-report/references/html-assembly.md)

## 常见问题

| 现象 | 如何处理 |
|---|---|
| 连接失败、TLS 报错，或 `/healthz` 返回网关错误 | 在实际发起请求的机器检查域名、端口、证书和内网或 VPN。确认用的是部署方给出的 `BASE_URL` |
| `/healthz` 正常，`/api/me` 返回 `401` | 检查是否发送了 `Authorization: Bearer <IAM access token>`，并通过 IAM 重新取得有效令牌。Postman 的 Bearer Token 框只填令牌本身 |
| `/api/me` 返回 `200`，但 `registered:false` | IAM 已识别该工号。请管理员检查 SmartReportGen 的员工登记和启用状态 |
| `403`、场景列表为空，或校验结果提示无权访问表 | 用 `/api/me/permissions` 核对授权。管理员接口要求管理员身份，模板修改要求创建人权限，报告操作按报告分享级别校验 |
| `404` | 对照接口的 HTTP 方法与路径，使用列表或创建响应中的真实 ID。数据源或场景对该账号不可见时也可能返回 `404` |
| `422` 或 `400` | 查看 `detail`，核对路径和查询参数类型、JSON 字段及 `Content-Type` |
| 报告编辑返回 `409` | 重新读取报告及其最新版本号，对照冲突前的基准版本后再保存 |
| 收到 `302` 或 HTML 登录页 | 程序调用报告正文或下载接口时发送 Bearer，并检查响应 `Content-Type`。调试时先用 `api -i "$BASE_URL/实际请求路径"` 查看状态和 `Location` |
| IAM 校验返回 `503` | 请部署方检查 IAM 配置和服务到 IAM 的连通性。浏览器也可用 `/api/auth/config` 查看 `iam_configured` |

提交接入问题时，提供环境、请求时间、HTTP 方法、路径、状态码及脱敏响应。省略 access token、授权码、Cookie、应用密钥和业务数据正文。

## 请求与响应约定

接口表中的「登录」指已登记且启用的 IAM 员工，「IAM 身份」也包括已核实但未登记或已停用的员工，「本人」指创建人或管理员。标为「管理员」的接口需要已启用的管理员账号。路径中的 `{...}` 是必填参数，资源 ID 和模板版本号为整数，`emp_no` 为字符串。查询参数和路径值按 URL 编码。请求体标为 JSON 时使用 `Content-Type: application/json`。表中「无」表示无需额外参数或请求体，仍须按权限列提供凭证。

成功响应默认是 `200 application/json`。列表直接返回数组，无结果时返回 `[]`。响应中的资源时间戳为 ISO 8601 字符串。JSON 示例中的动态字段随环境及数据变化。

| 状态 | 含义 |
|---|---|
| `400` | 业务参数、连接、SQL 或浏览器校验失败 |
| `401` | 缺少有效身份或 IAM 票无效、过期。REST 接口返回 `WWW-Authenticate` |
| `403` | 账号未登记、已停用或权限不足。部分资源接口用 `404` 隐藏不可见资源 |
| `404` | 资源不存在、不可见或路径不匹配 |
| `409` | 数据库唯一键或引用冲突，或编辑报告时版本已变化 |
| `422` | FastAPI 请求校验失败，例如必填查询参数缺失、路径参数类型错误、必填 JSON 对象缺失 |
| `500` | 未处理的服务端异常 |
| `503` | IAM 尚未配置或暂时无法核验凭证 |

REST 业务错误通常为 `{"detail":"错误说明"}`，`422` 的 `detail` 是校验问题数组，例如 `{"detail":[{"type":"missing","loc":["query","q"],"msg":"Field required","input":null}]}`。浏览器登录、报告下载和 MCP 传输的响应格式在各自接口下说明。

<a id="api-reference"></a>

## 公开页面与健康检查

以下路由均公开，无额外参数或请求体。静态资源路径除外，其文件名由路径指定。

| 方法与路径 | 成功响应 | 说明 |
|---|---|---|
| `GET /` | `200 text/html` | 管理台页面，带 `Cache-Control: no-cache`。页面加载后查询当前登录身份 |
| `GET /healthz` | `200`，`{"ok":true}` | 检查 HTTP 服务及元数据库连通性。失败返回非 2xx |
| `GET /api-guide` | `200 text/html` | 本文的在线版本 |
| `GET /api-guide/html-assembly` | `200 text/plain` | HTML 草稿与快照绑定指南 |
| `GET /docs` | `200 text/html` | Swagger UI |
| `GET /docs/oauth2-redirect` | `200 text/html` | FastAPI 自带的 Swagger OAuth 回调页 |
| `GET /redoc` | `200 text/html` | ReDoc 页面 |
| `GET /openapi.json` | `200 application/json` | OpenAPI 文档，含 `openapi`、`info`、`paths`、`components` 等字段 |
| `GET /static/{path}` | `200`，文件对应的媒体类型 | 静态资源，`{path}` 如 `admin/app.js`。支持条件请求（`304`）和字节范围（`206`），文件不存在返回 `404` |

FastAPI 自带文档路由和静态资源还支持 `HEAD`，返回响应头，响应体为空。

## 浏览器登录与账号

| 方法与路径 | 权限 | 请求 | 响应 |
|---|---|---|---|
| `GET /api/auth/config` | 公开 | 无。可带 Cookie 或 Bearer 查询当前身份 | `{"mode":"iam","iam_configured":true,"me":{"logged_in":false}}`。登录时 `me` 为下方身份对象 |
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
| `GET /api/datasources/{ds_id}/tables` | 管理员或获数据源选表授权 | 路径 `ds_id`，可选查询参数 `schema` | 数据库账号可见的表和视图。每项有 `schema`、`table`、`name`、`comment`、`kind`（`table`、`view` 或 `materialized_view`）。不存在返回 `404` |
| `GET /api/datasources/{ds_id}/columns` | 管理员或获数据源选表授权 | 路径 `ds_id`，必填查询参数 `table` | 字段元数据数组：`name`（小写）、`source_name`（原始名称）、`type`、`nullable`、`comment`。数据库数据源不读取业务行 |
| `GET /api/datasources/{ds_id}/values` | 管理员或获数据源选表授权 | 必填 `table` 和 `column`（使用字段的 `source_name`）；可选 `q`（字面包含搜索）、`limit`（默认 50，最多 100）、`offset`（默认 0，最大 10000） | 字段实际去重值 `{"values":[null,"华东","华南"],"next_offset":null}`。有下一页时 `next_offset` 为整数。候选值不受场景中已有过滤条件影响 |
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

### 数据源开放配置

管理员在数据源的 `config.scenario_tables` 中配置普通用户可以引用的表或视图。按工号 `employees` 或角色 `roles` 匹配，至少填写其中一项。表名需与该数据源目录一致。可在已有数据源上补充配置，普通用户随后就能自行创建场景。

```json
{
  "scenario_tables": [
    {
      "table_name": "tickets",
      "description": "客服工单",
      "roles": ["user"],
      "row_filters": [{"column": "region", "values": ["华东"]}]
    }
  ]
}
```

普通用户请求 `GET /api/datasources` 时，`can_build_scene:true` 标记可用于创建场景的数据源。表和字段目录基于当前授权返回，候选值按固定行范围查询。个人场景的附加筛选与固定行范围同时生效。删除表授权、撤销角色或停用数据源后，该账号不能再通过个人场景读取受影响的数据。既有共享场景继续按原场景授权工作。

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

SQL 只接受单条只读 `SELECT` 或 `WITH` 查询，HTTP API 数据源使用 DuckDB 方言。`dialect` 返回数据源类型，列名转为小写，重名列会追加后缀。`row_count` 是本次实际返回的行数，`truncated:true` 表示有更多行。普通员工通过 MCP 场景级工具查询授权数据。

## 业务场景

| 方法与路径 | 权限 | 请求 | 响应 |
|---|---|---|---|
| `GET /api/datasets` | 登录 | 可选查询参数 `q`，按场景名、业务描述或标签做不区分大小写的包含搜索 | 场景摘要数组，按 ID 升序。普通用户可见本人创建的场景及另行获授权的启用场景 |
| `POST /api/datasets` | 登录 | JSON 场景配置。必填 `business_scenario`、`business_description`、`datasource_id`，可选 `analysis_approach`、`tags`、`enabled`、`tables`、`semantic_metrics`。普通用户至少选一张已开放的表 | 场景详情。缺字段、空表名或重复表名返回 `400`，重名或引用冲突返回 `409` |
| `GET /api/datasets/{dataset_id}` | 登录 | 路径 `dataset_id` | 场景详情。普通用户仅见获授权的表。不存在或不可见返回 `404` |
| `PUT /api/datasets/{dataset_id}` | 本人或管理员 | 路径 `dataset_id`。JSON 传要修改的字段，传入 `tables` 时整体替换，个人场景至少保留一张可引用的表 | 更新后的场景详情。不存在返回 `404`，参数校验失败返回 `400`，重名或引用冲突返回 `409` |
| `DELETE /api/datasets/{dataset_id}` | 本人或管理员 | 路径 `dataset_id`，无请求体 | `{"ok":true}`，删除场景及其表清单和授权，已关联的模板解除场景关联 |
| `GET /api/datasets/{dataset_id}/analysis-file` | 登录 | 路径 `dataset_id`，可选查询参数 `download=true` | 读取场景绑定的 `.md` 分析思路文件元数据与正文（含 `analysis_file_name`、`analysis_file_markdown`、`analysis_file_updated_at`、`read_only`）；`download=true` 时以 `text/markdown` 附件下载，未绑定文件或不可见返回 `404` |
| `PUT /api/datasets/{dataset_id}/analysis-file` | 本人或管理员 | `multipart/form-data` 上传 `file`（仅限 `.md` 后缀、UTF-8 编码、最大 1 MB；重新上传直接替换） | 更新后的场景详情（含 `.md` 分析思路文件），同步刷新检索索引 |
| `DELETE /api/datasets/{dataset_id}/analysis-file` | 本人或管理员 | 路径 `dataset_id`，无请求体 | `{"ok":true,"dataset_id":...}`，移除已绑定的 `.md` 分析思路文件并刷新检索索引 |
| `GET /api/datasets/search` | 登录 | 必填查询参数 `q` 为业务需求，可选整数 `top_k`，默认 5 | 混合检索结果，见下方。仅检索启用场景，普通用户限已授权场景 |
| `GET /api/datasets/template` | 登录 | 无 | 下载 `.xlsx` 批量导入模板（含 `业务场景`、`场景数据表`、`官方指标` 表头及当前账号可用数据源清单） |
| `GET /api/datasets/export` | 登录 | 可选查询参数 `q`、`ids`（逗号分隔正整数） | 下载 `.xlsx` 场景导出文件。管理员导出全部匹配场景，普通用户导出本人创建的个人场景 |
| `POST /api/datasets/import` | 登录 | `multipart/form-data` 上传 `file`（`.xlsx`，最大 5 MB）；可选查询参数 `dry_run`（默认 `false`）、`mode`（`upsert` 或 `create_only`） | 批量预检或导入结果（含 `ok`、`summary`、`items`、`errors`）。`dry_run=false` 且校验失败返回 `400` |
| `POST /api/datasets/batch` | 登录 | JSON 传 `scenarios`（1–200 项），可选 `mode`（`upsert` 或 `create_only`）、`dry_run`（布尔值） | 同 `/api/datasets/import` |
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

场景摘要以 `table_count` 替代 `analysis_approach`、`analysis_file_markdown` 和 `tables`（摘要保留 `analysis_file_name`、`analysis_file_updated_at`）。每个业务场景最多可绑定 1 份 `.md` 分析思路文件（`analysis_file_name`、`analysis_file_markdown`、`analysis_file_updated_at`），通过 `/api/datasets/{dataset_id}/analysis-file` 独立维护，普通编辑与 Excel 批量导入不会覆盖该文件。个人场景返回 `owner_emp_no`。普通用户查看本人场景时返回 `read_only:false`，表的 `source_row_filters` 表示只读的源范围。管理员详情不含 `table_count`。

**混合检索结果**

返回 `query_tokens`（分词字符串数组）、`hits`（命中数组）、`warnings`（字符串数组，向量不可用时说明关键词降级）。每个命中有 `id`、`rrf`（融合排序分）、`relevance`（0–1）、`dataset`（上述场景详情）。有向量命中时附 `vector_score` 和 `vector_rank`，有关键词命中时附 `keyword_score`、`keyword_rank` 和 `matched_terms`（字符串数组）。排名从 1 开始，结果按 `rrf` 降序。无命中时 `hits:[]`。

**批量导入与 Excel 工作表说明**

`/api/datasets/template` 与 `/api/datasets/export` 返回统一结构的 `.xlsx` 工作簿，包含 `业务场景`、`场景数据表`、`官方指标` 三张数据表（模板另附 `填写说明` 表及当前账号可用数据源参考）。

- `业务场景`：`业务场景*`、`数据源*`（名称或 ID）、`业务描述*`、`分析思路`、`标签`（逗号分隔）、`启用`（`是`/`否`）、`授权角色`、`授权工号`（仅管理员可填写整场景授权列）。
- `场景数据表`：`业务场景*`、`表名*`、`表描述`、`行过滤条件`（支持 JSON 数组，或 `region=华东,华南,NULL; status=1` 简明写法；导出时固定输出紧凑 JSON 以保证无损往返）。
- `官方指标`：`业务场景*`、`指标标识*`、`所属表*`、`公式类型*`、`主字段/分子*`、`分母字段`、`允许分组维度`、`允许过滤维度`、`比例尺度`、`说明`、`高级条件JSON`。

`POST /api/datasets/batch` 支持直接传入 JSON 数组批量校验并写入，单批最多 200 个场景。任一场景校验失败或存在孤立子表行时整批不写入数据库：

```json
{
  "mode": "upsert",
  "dry_run": false,
  "scenarios": [
    {
      "business_scenario": "客服工单分析",
      "datasource_name": "客服工单接口",
      "business_description": "跟踪工单量、处理时长与超时情况。",
      "tables": [{"table_name": "tickets", "row_filters": "status=已解决"}],
      "grant_roles": ["user"]
    }
  ]
}
```

成功或 `dry_run=true` 返回 `{"ok":true,"dry_run":false,"mode":"upsert","summary":{"total":1,"create_count":1,"update_count":0,"error_count":0,"warning_count":0,"grants_added":1},"items":[...],"errors":[]}`。

## 报告模板

模板是 `business` 类型的 Markdown 指南，也是生成和修订报告的必备产物。Agent 在新建报告前须保存或确认归属同一业务场景的模板，并在草稿与报告入库时绑定 `template_id` 和 `template_version`；修改报告正文时须同步更新对应模板版本。历史声明式模板及自由版式定义在读取时转换成 Markdown，保留分析文案、绑定及 SQL、HTML/CSS 片段。历史版本的存档原文保留。

| 方法与路径 | 权限 | 请求与响应 |
|---|---|---|
| `GET /api/templates` | 登录 | 可用 `q` 与 `dataset_id` 筛选。返回本人及授权场景的模板，管理员可查看全部 |
| `POST /api/templates` | 登录 | 提交 Markdown、名称或正文一级标题，以及可选业务字段。返回 `version:1` |
| `POST /api/templates/import` | 登录 | `multipart/form-data` 一键导入 1–50 个 `.md` / `.markdown` 文件（单文件最大 2 MB）。必填 `dataset_id`（正整数，普通用户须具备该场景权限）；可选 `mode`（`upsert` 同名更新本人模板版本 / `create` 始终新建，默认 `upsert`）、`items_meta`（JSON 对象数组，按文件顺序指定 `name`、`business_summary`、`analysis_goal`、`user_requirement`）。缺省名称时依次取 Frontmatter `name`/`title`、正文一级标题或文件名。单批任一文件校验失败则整批不写入并返回 `400` |
| `GET /api/templates/{tpl_id}` | 本人或有场景权限 | 返回指南正文、概要及 `versions`。传 `download=true`（及可选 `version`）时以 `text/markdown` 附件下载 `.md` 文件 |
| `PUT /api/templates/{tpl_id}` | 创建人或管理员 | 提交 Markdown 与待更新字段。可传 `expected_version`，冲突返回 `409` |
| `GET /api/templates/{tpl_id}/versions/{version}` | 本人或有场景权限 | 返回 `version`、`markdown`、`created_at` |
| `DELETE /api/templates/{tpl_id}` | 创建人或管理员 | 删除模板及其历史版本；若仍有报告关联该模板则返回 `400` 拒绝删除 |

写入字段包括 `name`、`markdown`、`dataset_id`、`user_requirement`、`analysis_goal`、`business_summary`。`markdown` 必填。更新时省略可选业务字段会沿用原值，`dataset_id:null` 可解除关联。正文或模板业务字段变化时追加版本。载入历史版本的 `markdown` 再提交更新即可恢复正文。摘要包含 `title`、`sections` 和 `format:"markdown"`。

## 报告草稿

`GET /api/report-drafts` 返回按最近操作排序的草稿摘要。普通员工仅见自己的草稿，管理员可查看所有草稿的进度。每项包含 `draft_id`、`revision`、`status`、有序 `sections`、最近的 `events`、`last_verification` 和 `last_error`。草稿与预览在工作卷中保留 24 小时，分块源码随正式报告版本入库。MCP 的 `get_html_draft` 可按需读取单个章节源码。

## 报告库

| 方法与路径 | 权限 | 请求 | 响应 |
|---|---|---|---|
| `GET /api/reports` | 登录 | 可选查询参数 `template_id`（整数）、`q`（搜索报告名、模板名、创建人姓名或工号） | 可访问的报告摘要数组，每份报告一项，按报告 ID 降序。每项显示调用者默认查看的版本 |
| `POST /api/reports/merge` | 对每份报告有查看权限 | JSON 传 `reports`，按合并顺序提供至少两个 `{"id":15,"version":1}` 项。可选 `name`（默认“合并报告”） | `200 text/html` 单文件附件。缺少查看权限返回 `403`，报告或版本不存在返回 `404`，重复选择同一报告或旧版本有外部依赖返回 `400` |
| `GET /api/reports/{report_id}` | 本人或获分享 | 可选查询参数 `version`（正整数）、`include_html`、`include_source`（布尔值，均默认 `false`） | 报告详情与固定版本链接。按需增加 `html`（字符串）及 `markdown`（字符串或 `null`） |
| `GET /api/reports/{report_id}/versions` | 本人或获分享 | 无额外参数 | 默认查看的版本详情（无链接字段）及 `versions` 数组，按版本号降序 |
| `PUT /api/reports/{report_id}/selection` | 本人或获分享 | JSON 必填 `version`，正整数或 `null`。例如 `{"version":2}` | 保存当前账号的版本选择，返回所选版本详情。`{"version":null}` 恢复跟随最新。格式错误返回 `422` |
| `PUT /api/reports/{report_id}` | 本人或获编辑权限 | JSON 必填 `name`。修改正文传其中一项 `draft_id`、`preview_id` 或 `html`，且须同步更新关联模板并传入新版 `template_id`/`template_version`（或已在草稿/预览中绑定新版）。可选 `base_version`、`expected_version`、`revision`、`change_note` | 在同一报告追加版本，返回新版本详情。并发冲突返回 `409`，字段校验失败返回 `422`，内容、模板同步或渲染失败返回 `400` |
| `DELETE /api/reports/{report_id}` | 本人 | 无额外参数 | `{"ok":true}`，删除报告及版本和分享记录 |
| `GET /api/reports/{report_id}/shares` | 本人 | 无额外参数 | 分享对象数组，按工号升序 |
| `GET /api/reports/{report_id}/share-recipients` | 本人 | 可选查询参数 `q`（姓名或工号，最多 100 个字符） | 最多 20 个已启用接收人，仅返回 `emp_no`、`name` |
| `POST /api/reports/{report_id}/shares` | 本人 | JSON 必填 `emp_no`（1–256 个字符）、`access_level`（`view` 或 `edit`） | 分享对象，已存在时更新权限。接收人须登记、启用且有当前版本全部来源数据的权限。工号无效、停用或未登记时返回 `400`，缺数据权限返回 `403`，字段校验失败返回 `422` |
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
| 列表摘要 | 以上详情去掉 `dataset_id`、`source_available`、三个 URL 字段，增加 `template_name`、`template_latest_version`、`creator_name`（均可为 `null`） |
| 详情、编辑、版本选择 | 以上结构（另附 `template_name`、`template_latest_version`）。仅详情 GET 可按查询参数返回 `html` 或 `markdown`。`source_available` 表示当前账号有权获取该版本保存的分块源码或历史源码。请求源码但无法提供时，`markdown` 为 `null` |
| 版本列表 | 以上详情去掉三个 URL 字段，增加 `versions` 数组。每项含 `version`、`base_version`、`name`、`template_id`、`template_version`、`template_name`、`created_at`、`created_by`、`creator_name`、`change_note` |
| 生成结果 | `id`、`name`、`template_id`、`template_version`、`meta`、`created_at`、`created_by`、`size`、`revision`、`latest_version`、`current_version`、`base_version`、三个 URL 字段和顶层 `warnings`。三个版本号初始为 `1`，`base_version` 为 `null`，链接省略 `version` |

`revision` 是本次返回的报告版本，`base_version` 是其来源版本，`latest_version` 是报告最新版本，`current_version` 是该账号默认查看的版本，`version_selected` 表示账号是否主动固定了版本。`template_version` 是模板版本号。报告详情的三个链接均固定到本次返回的 `revision`。

`size` 是已保存 HTML 的字符数，`meta.html_bytes` 是字节数。旧报告或早期保存记录可能只有部分 `meta` 字段，`template_id`、`template_version`、`dataset_id`、`created_by`、`version_created_by` 可为 `null`；新报告与新修订必须绑定有效的 `template_id` 和 `template_version`。

`access_level` 表示当前调用者对报告的权限。`view` 可查看、下载和选择自己的版本，`edit` 还可保存新版本，`manage` 还可管理分享和删除报告，由创建人及管理员持有。

**编辑报告请求示例**（`PUT /api/reports/15`）

```json
{"name":"客服工单日报","base_version":1,"expected_version":1,"change_note":"更新报表名称"}
```

| 字段 | 类型及用途 |
|---|---|
| `name` | 必填，非空字符串。保存时去除首尾空白 |
| `draft_id` | 由当前账号从相同报告及基准版本创建的草稿，服务端按需校验 |
| `html` | 完整 HTML，可用 `dataset_id` 指定场景，服务端校验后追加版本 |
| `preview_id` | 与报告和基准版本匹配、由当前账号生成的服务端验收预览 |
| `template_id` | 正整数，修改正文时若切换或同步更新模板，可在此指定模板 ID |
| `template_version` | 正整数，修改正文须绑定更新后的模板版本号（或在草稿/预览中绑定） |
| `base_version` | 可选正整数，从详情的 `revision` 读取。指定编辑使用的版本，允许从历史版本生成新版本。省略时使用个人选定版本或最新版本，旧链接默认用原快照 |
| `expected_version` | 可选正整数，从详情的 `latest_version` 读取。传入后，如果报告在读取后产生了新版本，保存返回 `409` |
| `revision` | 兼容旧客户端的可选正整数，同时作为 `base_version` 与 `expected_version`。传入时省略另外两个版本字段 |
| `change_note` | 可选字符串，默认空，最多 500 字符 |

例如当前已有 v1–v4，使用 `base_version:2`、`expected_version:4` 成功保存时产生 v5，响应中 `base_version` 为 2，`revision` 和 `latest_version` 为 5。保存后编辑者恢复跟随最新版本。正文变更使用通过 Chromium 校验的内容。草稿分块源码随版本保存。收到 `409` 时先读取最新内容和版本，再决定如何合并修改。

**分享请求与响应示例**（`POST /api/reports/15/shares`）

请求为 `{"emp_no":"E10002","access_level":"view"}`，响应为 `{"emp_no":"E10002","name":"示例同事","enabled":true,"registered":true,"access_level":"view"}`。分享列表返回同结构的数组。`registered` 表示工号是否已在系统登记，`enabled` 表示账号当前是否启用。已登记接收人使用获授权的账号登录。访问历史版本仍会逐版本复核数据权限。

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
| `reporting_rules` | 只读内置报告规则数组，每项含 `id`、`label`、`text`、`level:"builtin"`、`source` |

**`GET /api/style-rules/resolve` 响应字段**

| 字段 | 结构 |
|---|---|
| `business_scenario` | 请求中的场景名，省略时为 `null` |
| `precedence` | 生效顺序说明 |
| `specified.theme` | 合并后已设置的样式键和值 |
| `specified.theme_sources` | 同一批样式键对应的来源说明字符串 |
| `specified.items` | 合并后的展示要求，每项含 `category`、`category_label`、`text`、`level`（`general` 或 `scenario`）、`source` |
| `reporting_rules` | 与 `GET /api/style-tokens` 相同的跨场景内置规则。事实与交付要求独立生效，展示默认值按 `precedence` 应用 |
| `general_rules`、`scenario_rules` | 生效的通用规则和当前用户个人场景规则数组。规则的 `items` 附 `category_label`。有检索命中时规则附 `relevance`，结构同业务场景检索命中去掉 `dataset` |
| `warnings` | 字符串数组，例如相关度检索的向量降级提示 |

MCP 的 `get_style_rules` 和 `prepare_report_context` 也提供同一份 `reporting_rules`。草稿可以引用 `style_id` 内联主题。场景负责人可通过 `semantic_metrics` 配置官方指标。

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

<a id="mcp-authentication"></a>

## MCP 客户端接入

按[首次调用](#quickstart)输入海尔 IAM access token 后，在同一个终端中配置 Codex。默认地址是 `BASE_URL` 加 `/mcp`，部署方提供独立地址时使用该地址。

```bash
MCP_URL="$BASE_URL/mcp"
export SMARTREPORT_TOKEN="${HAIER_IAM_ACCESS_TOKEN:?请先输入海尔 IAM access token}"
codex mcp add smartreportgen --url "$MCP_URL" --bearer-token-env-var SMARTREPORT_TOKEN
```

在同一个终端启动 Codex 后调用 `whoami`，确认工号、`registered` 和 `access`。其他客户端可直接配置 Bearer 请求头。令牌到期后，从 IAM 取得新令牌并更新客户端使用的凭证。

## MCP HTTP 传输

`/mcp` 使用 Streamable HTTP。每次请求均须携带 `Authorization: Bearer <海尔 IAM access token>`。服务当前使用无状态模式，不需要 `Mcp-Session-Id`。初始化后由客户端发送协商得到的 `MCP-Protocol-Version`。

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
