# SmartReportGen 部署文档

本文覆盖：环境要求 → 部署 → 配置 → **单点登录（海尔 IAM，默认）** → 生产加固 → 初始化 → 接入 Agent（Claude Code / Codex / 其他）→ 运维 → 故障排查。

---

## 1. 架构

```
           ┌──────────────── Agent（Claude Code / Codex / Cursor / …）────────────────┐
           │  技能 skills/smart-report（流程与方法）   MCP 客户端（Bearer 员工令牌）     │
           └──────────────────────────────────┬───────────────────────────────────────┘
                                              │ Streamable HTTP  :18601/mcp
 浏览器（管理员 / 报告读者）                   ▼
   │  :18600        ┌──────────────┐   ┌──────────────┐        ┌──────────────────────┐
   └──────────────► │ api          │   │ mcp          │ ─────► │ 业务数据源（只读账号） │
                    │ 管理台/REST   │   │ 工具 + 鉴权   │        │ MySQL / PG / Oracle / │
                    │ 报告浏览      │   │ 表级权限      │        │ StarRocks / HTTP API  │
                    └──────┬───────┘   └──────┬───────┘        └──────────────────────┘
                           └───────┬──────────┘                 AI 网关（Embedding）· 海尔 IAM（单点登录、核实工号）
                                   ▼
                    ┌───────────────────────────────┐
                    │ postgres（pgvector/pg16）       │  元数据：datasource / dataset / style_rule /
                    │ 卷 pgdata                      │  report_template / report / permission / sys
                    └───────────────────────────────┘
```

| 服务 | 镜像 | 容器端口 | 默认宿主机绑定 | 说明 |
|---|---|---|---|---|
| `postgres` | `pgvector/pgvector:pg16` | 5432 | `127.0.0.1:55433` | 元数据库，数据在卷 `pgdata` |
| `api` | `smartreportgen:latest`（本仓库构建） | 8000 | `127.0.0.1:18600` | 管理台、管理 API（`/docs`）、报告浏览 `/reports/{id}`、健康检查 `/healthz` |
| `mcp` | 同上，启动命令不同 | 8001 | `127.0.0.1:18601` | MCP（`/mcp`），健康检查 `/healthz` |

`api` 启动时负责建表、迁移、演示数据初始化与向量重建；`mcp` 依赖 `api` 健康后才启动。

## 2. 环境要求

| 项目 | 要求 |
|---|---|
| 系统 | Linux x86_64 / arm64，或 macOS（Docker Desktop / OrbStack） |
| Docker | Docker Engine ≥ 24，Compose v2（`docker compose`） |
| 资源 | 最低 2 vCPU / 4 GB 内存 / 10 GB 磁盘。空闲时实测内存约：api 135 MB、mcp 130 MB、postgres 40 MB；报告渲染时按查询数据量增加 |
| 镜像体积 | `smartreportgen` 约 630 MB，`pgvector/pgvector:pg16` 约 965 MB |
| 构建期网络 | 访问 PyPI（或内网镜像，见 §6）；Docker Hub（或内网镜像仓库） |
| 运行期网络 | 容器能访问：业务数据源、AI 网关（向量接口）、海尔 IAM（`HAIER_IAM_HOST`）。用户浏览器能访问 IAM 登录页。报告页面的 ECharts 已内置，**浏览器无需外网** |
| IAM 应用 | 在海尔 IAM 为 SmartReportGen 申请（或复用）应用，取得 Client ID / Secret，并登记回调地址 `{PUBLIC_BASE_URL}/auth/iam/callback` |

## 3. 快速部署

```bash
git clone <仓库地址> smartreportgen && cd smartreportgen
cp .env.example .env
```

编辑 `.env`，至少设置：

```bash
# 生成随机密钥（各执行一次，分别填入）
openssl rand -base64 32
```

```ini
SECRET_KEY=<随机串>          # 加密数据源密码
JWT_SECRET=<随机串>          # 签发 MCP 员工令牌、管理台会话
ADMIN_TOKEN=<随机串>         # 应急登录（IAM 不可用时），妥善保管
SEED_DEMO=false              # 生产环境不要演示数据（体验时可设 true）
PUBLIC_BASE_URL=https://report.example.com
MCP_PUBLIC_URL=https://report.example.com/mcp

# 单点登录：海尔 IAM（默认 AUTH_MODE=iam）
HAIER_IAM_HOST=https://iama.haier.net
HAIER_IAM_CLIENT_ID=<IAM 应用 Client ID>
HAIER_IAM_CLIENT_SECRET=<IAM 应用 Client Secret>
IAM_BOOTSTRAP_ADMINS=<首批管理员工号，逗号分隔>

# 向量模型（OpenAI 兼容接口），以 AI 网关的 Qwen3-Embedding-4B 为例
AI_BASE_URL=https://<网关地址>/v1/
AI_API_KEY=<网关 API Key>
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=Qwen3-Embedding-4B
EMBEDDING_DIM=1024
EMBEDDING_SEND_DIMENSIONS=true
EMBEDDING_QUERY_INSTRUCTION="Instruct: 根据用户的数据分析需求，检索最匹配的业务场景数据集\nQuery:"
```

```bash
chmod 600 .env
docker compose up -d --build
docker compose ps          # 三个服务均为 healthy
docker compose logs -f api # 首次启动可看到建表、迁移与向量化日志
```

访问 `PUBLIC_BASE_URL`，点击「使用海尔 IAM 单点登录」。`IAM_BOOTSTRAP_ADMINS` 中的工号首次登录即为管理员，进入完整管理台；其他员工登录后只看到「我的令牌」。IAM 暂不可用时，可展开“管理员应急登录”使用 `ADMIN_TOKEN`。

> **重要**：`SECRET_KEY`、`JWT_SECRET` 请在**首次启动前**确定。留空时系统会自动生成并存进数据库；之后再改成别的值，已保存的数据源密码将无法解密（需重新填写），已签发的令牌全部失效（需重新签发）。

## 4. 配置说明（`.env`）

### 4.1 基础与安全

| 变量 | 默认 | 说明 |
|---|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | report / report / smartreportgen | 元数据库账号（首次初始化卷时生效） |
| `PG_HOST_PORT` | 55433 | PG 映射到宿主机 127.0.0.1 的端口（只用于运维，可删除该映射） |
| `SECRET_KEY` | 空（自动生成） | 数据源密码/令牌的 Fernet 加密密钥来源 |
| `ADMIN_TOKEN` | 空 | 应急管理员令牌（IAM 不可用时登录管理台；`AUTH_MODE=local` 时为唯一登录方式）。**IAM 未配置且本项为空时，管理端处于无鉴权的开发模式** |
| `APP_NAME` | SmartReportGen | 页面与报告页脚显示名 |
| `API_BIND` / `API_HOST_PORT` | 127.0.0.1 / 18600 | 管理台监听地址与端口 |
| `MCP_BIND` / `MCP_HOST_PORT` | 127.0.0.1 / 18601 | MCP 监听地址与端口 |
| `PUBLIC_BASE_URL` | http://localhost:18600 | 对外访问地址：报告链接用它拼接（经反向代理时填 https 域名） |
| `MCP_PUBLIC_URL` | http://localhost:18601/mcp | 管理台展示给用户的 MCP 地址 |

### 4.2 单点登录（海尔 IAM）

| 变量 | 默认 | 说明 |
|---|---|---|
| `AUTH_MODE` | **iam** | `iam`：管理台走 IAM 单点登录，员工自助签发 MCP 令牌，MCP 也接受 IAM access token；`local`：不用 IAM，只用 `ADMIN_TOKEN` + 管理员签发令牌 |
| `HAIER_IAM_HOST` | https://iama.haier.net | IAM 地址（服务端换票、核实账号用） |
| `HAIER_IAM_CLIENT_ID` / `HAIER_IAM_CLIENT_SECRET` | 空 | IAM 应用凭证；与 HOST 三项齐全才算“IAM 可用” |
| `HAIER_IAM_SCOPE` | read | 授权范围 |
| `IAM_REDIRECT_URI` | 空 = `{PUBLIC_BASE_URL}/auth/iam/callback` | 回调地址，必须与 IAM 应用中登记的完全一致 |
| `HAIER_IAM_AUTHORIZE_HOST` | 空 = HOST | 浏览器跳转用的 IAM 地址，仅当它与服务端访问地址不同时填写（如经内网代理访问 IAM） |
| `IAM_BOOTSTRAP_ADMINS` | 空 | 首批管理员工号：首次经 IAM 登录管理台时成为管理员（若此前已因调用 MCP 注册为普通用户，首次登录时提升一次；之后被管理员降级不会再自动提升） |
| `IAM_AUTO_REGISTER` | true | 员工首次调用 MCP（IAM 票、个人令牌或 SSO 令牌）或首次登录管理台时自动注册为**普通用户**（无数据权限，待授权） |
| `DEFAULT_USER_ROLE` | user | 自动注册员工的默认角色；对该角色授权即可给全体员工基础数据权限 |
| `MCP_ACCEPT_IAM_TOKEN` | true | MCP 直接接受 IAM access token（`Authorization: Bearer` / `Access-Token` / `X-IAM-Access-Token`），每张票都向 IAM 核实工号 |
| `IAM_TOKEN_CACHE_SECONDS` | 300 | IAM 票核实结果缓存秒数（失败结果缓存 30 秒） |
| `SESSION_HOURS` | 12 | 管理台会话时长（HttpOnly Cookie） |
| `SELF_SERVICE_TOKENS` / `TOKEN_MAX_DAYS` | true / 90 | 员工登录后可在「我的令牌」自助签发 MCP 令牌，以及最长有效期 |

### 4.3 MCP 令牌（JWT）与权限

| 变量 | 默认 | 说明 |
|---|---|---|
| `JWT_SECRET` | 空（自动生成） | 管理台签发令牌（HS256）的密钥 |
| `JWT_PUBLIC_KEY` | 空 | 接入企业 SSO 令牌时填其公钥（PEM，换行写 `\n`） |
| `JWT_ALGORITHMS` | HS256 | 允许的算法，如 `HS256,RS256` |
| `JWT_EMP_CLAIM` / `JWT_NAME_CLAIM` | emp_no / name | 令牌中工号、姓名所在的 claim |
| `JWT_ISSUER` / `JWT_AUDIENCE` | smartreportgen / smartreportgen-mcp | 校验 iss / aud（SSO 令牌需一致或按实际修改） |
| `MCP_ALLOW_ADMIN_TOKEN` | true | 允许用 `ADMIN_TOKEN` 以管理员身份调用 MCP（调试用，**生产设为 false**） |
| `REPORT_SHARE_DAYS` | 7 | MCP 返回的报告链接中查看凭证 `?t=` 的有效天数 |

### 4.4 向量检索

| 变量 | 默认 | 说明 |
|---|---|---|
| `EMBEDDING_PROVIDER` | hash | `openai`＝OpenAI 兼容接口；`hash`＝离线哈希（无语义，仅兜底） |
| `AI_BASE_URL` / `AI_API_KEY` | 空 | 网关地址与密钥（`EMBEDDING_BASE_URL` / `EMBEDDING_API_KEY` 同义，后者优先） |
| `EMBEDDING_MODEL` | 空 | 模型名，如 `Qwen3-Embedding-4B`、`bge-m3`、`text-embedding-v3` |
| `EMBEDDING_DIM` | 1024 | 向量维度，必须与模型实际输出一致；>2000 时不建 HNSW 索引 |
| `EMBEDDING_SEND_DIMENSIONS` | false | 模型支持 `dimensions` 参数时设 true（用于截断维度） |
| `EMBEDDING_QUERY_INSTRUCTION` | 空 | 指令式模型的查询前缀（只作用于检索词） |
| `EMBEDDING_TIMEOUT` | 30 | 向量接口超时（秒） |

向量接口不可用时，检索自动降级为纯关键词，结果中带告警，不会中断。

### 4.5 查询与演示数据

| 变量 | 默认 | 说明 |
|---|---|---|
| `QUERY_MAX_ROWS` | 50000 | 单个查询嵌入报告的最大行数（超出截断并告警） |
| `QUERY_TIMEOUT_SECONDS` | 60 | 数据源查询超时 |
| `SEED_DEMO` | true | 首次启动是否写入演示数据源、数据集、员工与授权 |
| `DEMO_READER_PASSWORD` | demo_reader_pwd | 演示只读账号密码 |

修改 `.env` 后执行 `docker compose up -d` 使其生效（会重建对应容器）。

## 5. 单点登录与身份

### 5.1 身份从哪里来

| 场景 | 默认方式（AUTH_MODE=iam） | 应急 / 备选 |
|---|---|---|
| 管理员、员工登录管理台 | 海尔 IAM 单点登录 → 工号 → 查权限表：管理员看到完整管理台，员工只看到「我的令牌」 | `ADMIN_TOKEN` 应急登录 |
| agent 调用 MCP | 员工登录后在「我的令牌」自助签发个人令牌（JWT，含工号，可吊销） | 管理员在「权限与令牌」代签；或 agent 宿主直接出示 IAM access token |
| 查看报告 | 报告创建人与管理员登录后可看；MCP 返回的链接自带 7 天查看凭证 | — |

工号来自 IAM 的 `account/info`（字段 `accountNumber`，兼容 `loginName` / `userName` / 嵌套的 `account`）。**IAM 的票本身对本系统不透明**：
票即使形如 `base64({"accountNumber":…}).签名`，也不会被解码后直接采信，一律向 IAM 核实。

### 5.2 接入海尔 IAM 的步骤

1. 在 IAM 为 SmartReportGen 申请应用（或经同意复用已有应用），登记回调地址：`{PUBLIC_BASE_URL}/auth/iam/callback`（如 `https://report.example.com/auth/iam/callback`）。
2. 在 `.env` 填 `HAIER_IAM_CLIENT_ID`、`HAIER_IAM_CLIENT_SECRET`、`IAM_BOOTSTRAP_ADMINS`，然后 `docker compose up -d`。
3. 首批管理员用 IAM 登录 → 「权限与令牌」中为员工或角色授权。员工首次调用 MCP 或首次登录时会自动注册为普通用户（角色 `user`，无权限）；若希望全员都能看某些数据，对角色 `user` 授权即可。
4. 员工登录 → 「我的令牌」签发个人 MCP 令牌，配置到自己的 agent（页面上直接给出 Claude Code / Codex 命令）。

IAM 接口约定（与海尔 IAM 实际行为一致）：
- 登录跳转：`{HOST}/oauth/authorize`
- 换票：`POST {HOST}/api/oauth/token/code`，JSON 请求体，`Application-Key` 头。注意 `/oauth/token` 是 IAM 前端页面，POST 它会得到 405。
- 读账号：`GET {HOST}/api/oauth/account/info`，`Access-Token` 头。
- 回包是 `resultCode` 信封，`0` 表示成功。

### 5.3 角色与可见范围

管理台只有两种角色：**管理员**（权限表中 `is_admin`）与**普通用户**。服务端对普通用户采用“默认拒绝 + 白名单”，界面隐藏只是辅助：

| 功能 | 管理员 | 普通用户 |
|---|---|---|
| 数据源 | 新建、编辑、测试、浏览表、SQL 查询、删除 | 只读：只看到自己有权限的数据集所用的数据源（不显示连接信息） |
| 数据集 | 新建、编辑、删除、重建索引 | 只读：只看到有权限的业务场景，表只显示有权限的 |
| 报告模板 / 报告 | 查看全部人的（列表显示创建人、创建时间） | 只看到、只能修改 / 删除自己创建的（显示创建时间） |
| 样式规则 | 配置通用规则；可查看所有人的业务场景样式 | 通用规则只读；为自己有权限的业务场景配置个人样式，只对自己生成的报告生效 |
| 权限与令牌 | 可见、可管理 | 不可见（接口拒绝） |
| 我的令牌 | 可用 | 可用 |

普通用户在管理台校验 / 预览 / 生成报告时，与通过 MCP 一样按其数据权限校验每条 SQL。

## 6. 生产加固清单

1. **密钥**：`SECRET_KEY`、`JWT_SECRET`、`ADMIN_TOKEN`、`HAIER_IAM_CLIENT_SECRET` 使用强随机值或正式凭证；`.env` 权限 600，不进版本库（`.dockerignore` 已排除）。
2. **单点登录**：`AUTH_MODE=iam`，IAM 三项配齐，确认启动日志没有“IAM 未配置齐全”或“开发模式（无鉴权）”告警。
3. **MCP**：`MCP_ALLOW_ADMIN_TOKEN=false`；`TOKEN_MAX_DAYS` 按安全要求设定；员工离职或令牌泄露时在管理台停用员工或吊销令牌（停用员工后其个人令牌与 IAM 票都不再有数据权限）。
4. **HTTPS**：`PUBLIC_BASE_URL` 使用 https，会话 Cookie 会自动带 Secure 标记。
5. **网络**：服务端口保持绑定 127.0.0.1，由反向代理提供 HTTPS 对外；PG 端口映射可删除。
6. **数据源账号只读**：系统自带只读 SQL 校验、表级权限、查询超时与行数上限，但这些是第二道防线。数据源账号本身必须只有 SELECT 权限。
7. **不加载演示数据**：`SEED_DEMO=false`。若已加载，可在管理台删除演示数据集、数据源、员工，然后清理演示库对象（不可恢复，先备份）：
   ```bash
   docker compose exec postgres psql -U report -d smartreportgen -c "DROP SCHEMA demo_retail, demo_supply CASCADE; DROP ROLE demo_reader;"
   ```
8. **备份**：按 §9.2 配置每日 `pg_dump`。

### 反向代理（Nginx 示例）

```nginx
server {
    listen 443 ssl http2;
    server_name report.example.com;
    ssl_certificate     /etc/nginx/certs/report.crt;
    ssl_certificate_key /etc/nginx/certs/report.key;
    client_max_body_size 20m;

    # 管理台、管理 API、报告页面
    location / {
        proxy_pass http://127.0.0.1:18600;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;          # 大报告渲染可能较慢
    }

    # MCP（Streamable HTTP）
    location /mcp {
        proxy_pass http://127.0.0.1:18601/mcp;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header Authorization $http_authorization;
        proxy_set_header Connection "";
        proxy_buffering off;              # 兼容流式响应
        proxy_read_timeout 300s;
    }
}
```

对应 `.env`：`PUBLIC_BASE_URL=https://report.example.com`、`MCP_PUBLIC_URL=https://report.example.com/mcp`。

## 7. 内网 / 离线部署

- **PyPI 镜像**：
  ```bash
  docker compose build --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
  ```
- **离线迁移镜像**：在能联网的机器上构建并导出，到目标机导入：
  ```bash
  docker compose build
  docker save smartreportgen:latest pgvector/pgvector:pg16 | gzip > smartreportgen-images.tar.gz
  # 目标机：
  gunzip -c smartreportgen-images.tar.gz | docker load
  docker compose up -d          # 不带 --build
  ```
- **前端资源**：管理台的 Vue、报告的 ECharts 均随镜像提供，浏览器无需访问 CDN。“下载离线版”会把 ECharts 内联进单个 HTML 文件。
- **数据源在宿主机上**：容器内用 `host.docker.internal` 访问宿主机服务（compose 已配置 `extra_hosts`）。

## 8. 初始化（管理台）

按以下顺序配置（`SEED_DEMO=true` 时已有演示数据可参考）：

1. **数据源**：新建并“测试连接”。支持 MySQL、PostgreSQL、Oracle（thin 模式，无需 Instant Client）、StarRocks（MySQL 协议，默认端口 9030）、HTTP API（JSON 载入 DuckDB 查询）。
2. **数据集**：每个数据集对应一个业务场景，填写业务场景名、业务描述（越具体越利于检索）、数据源，以及数据表名和表描述。可以“从数据源选择”。用页面上的“混合检索测试”验证命中效果。
3. **样式规则**：生效顺序是“智能体决定 ≥ 业务场景规则 ≥ 通用规则 ≥ 内置样式（仅颜色）”。后三者合并为一份指定样式（视觉样式 + 生成要求），作为约束交给智能体；智能体默认遵守，确有理由时可按自己的判断调整；没有指定的由智能体自由决定。通用规则由管理员配置；业务场景样式由每个用户自己配置，只对其本人生成的报告生效（报告按模板创建人取样式）。
   - 通用规则对所有报告生效；业务场景规则写该场景的特殊要求。
   - 每条规则包含“规则条目”（分类、一句话、有示例可选）和“视觉样式”（中文设置项，只保存调整过的项），右侧实时预览。
   - 用“查看最终效果”确认某场景叠加后的样式，每项都标明来源。
4. **权限与令牌**：
   - 员工首次调用 MCP 或首次登录时自动注册为普通用户（角色 `user`）；员工列表会显示注册来源。也可以预先手工登记（工号、角色）。
   - 按角色或员工授权“业务场景 / 数据表”。
   - 在“权限查询”中核对某个员工的有效权限。
   - MCP 令牌默认由员工登录后在「我的令牌」自助签发。管理员也可以代签（如服务账号），令牌只显示一次。

## 9. 运维

### 9.1 升级

```bash
git pull
docker compose build
docker compose up -d
```

表结构与数据迁移在 `api` 启动时自动、幂等执行（迁移标记记录在 `sys.setting`）。报告的样式与脚本在渲染时内联，**已生成的报告不会随升级变化**，需要时重新渲染。

### 9.2 备份与恢复

```bash
# 备份（建议每日定时）
mkdir -p backup
docker compose exec -T postgres pg_dump -U report -d smartreportgen -Fc > backup/smartreportgen-$(date +%F).dump

# 恢复到空库（会覆盖同名对象）
docker compose exec -T postgres pg_restore -U report -d smartreportgen --clean --if-exists < backup/smartreportgen-2026-11-12.dump
docker compose restart api mcp
```

备份包含全部元数据（数据源密文、数据集、模板、报告、权限、令牌记录）。数据源密码用 `SECRET_KEY` 加密，恢复时请使用相同的 `.env`。

### 9.3 日志与健康检查

```bash
docker compose ps
docker compose logs -f api mcp
curl -s http://127.0.0.1:18600/healthz
curl -s http://127.0.0.1:18601/healthz
```

### 9.4 常见变更

| 变更 | 操作 | 影响 |
|---|---|---|
| 更换向量模型或维度 | 改 `.env` 中 `EMBEDDING_*` → `docker compose up -d` | 启动时自动调整向量列并重建全部向量（日志有“重建向量”） |
| 只改查询指令前缀 | 同上 | 无需重建，立即生效 |
| 轮换 `JWT_SECRET` | 改 `.env` → 重启 | 所有令牌失效，需要重新签发 |
| 轮换 `SECRET_KEY` | 改 `.env` → 重启 → 在管理台重新填写每个数据源的密码 | 旧密文不可解密 |
| 吊销某员工令牌 | 管理台 → 权限与令牌 → 访问令牌 → 吊销 | 立即生效 |
| 调整权限 | 管理台授权 / 撤销 | 下一次 MCP 调用立即生效 |

## 10. 接入 Agent

技能只有一份：仓库根目录 `skills/smart-report/`，与具体 agent 无关。
- 仓库内已有软链接：`.claude/skills/smart-report`、`.agents/skills/smart-report` 都指向它，在仓库目录中启动 agent 可直接发现。
- 用户级安装用 `scripts/install-skill.sh`。

所有 agent 都需要一个代表员工本人的令牌，有两种来源：
- **默认**：员工用 IAM 登录管理台 → 「我的令牌」→ 签发。
- **agent 宿主已持有用户的 IAM access token**（如平台注入的 `HAIER_IAM_ACCESS_TOKEN`）：可以直接把它当令牌，服务端会向 IAM 核实。也可以放在 `X-IAM-Access-Token` 头中。

令牌放在环境变量中，**在启动 agent 的同一个 shell 里设置**：

```bash
export SMARTREPORT_TOKEN=<「我的令牌」签发的令牌，或 IAM access token>
```

### 10.1 Claude Code

- 在本仓库内使用：项目 `.mcp.json` 已配置（读取 `${SMARTREPORT_TOKEN}`），技能经 `.claude/skills/` 自动发现。直接运行 `claude`，首次会提示信任项目 MCP。
- 在其他项目中使用：
  ```bash
  claude mcp add --transport http smartreportgen https://report.example.com/mcp --header "Authorization: Bearer $SMARTREPORT_TOKEN"
  ```
  ```bash
  scripts/install-skill.sh claude      # 安装到 ~/.claude/skills
  ```

### 10.2 Codex

- 在本仓库内使用：项目 `.codex/config.toml` 已配置（`bearer_token_env_var = "SMARTREPORT_TOKEN"`），需要在 Codex 中**信任本项目**后才生效；技能经 `.agents/skills/` 发现。
- 全局配置：
  ```bash
  codex mcp add smartreportgen --url https://report.example.com/mcp --bearer-token-env-var SMARTREPORT_TOKEN
  ```
  ```bash
  scripts/install-skill.sh codex       # 安装到 ~/.codex/skills 与 ~/.agents/skills
  ```
  等价的手写配置（`~/.codex/config.toml`）：
  ```toml
  [mcp_servers.smartreportgen]
  url = "https://report.example.com/mcp"
  bearer_token_env_var = "SMARTREPORT_TOKEN"
  ```
- Codex 也会读取仓库根目录的 `AGENTS.md`，其中说明了技能位置与使用约束。

### 10.3 其他 Agent

`integrations/` 目录提供 Cursor、VS Code Copilot、Gemini CLI、OpenCode 与通用 MCP 客户端的配置模板，说明见 `integrations/README.md`。

对于没有技能机制的 agent，以下任一方式都能拿到完整流程：
1. 读 `AGENTS.md` / `skills/smart-report/SKILL.md`；
2. 调用 MCP 工具 `get_skill_guide`（MCP 初始化说明会提示 agent 这样做）；
3. 使用 MCP 提示词 `smart_report`。

### 10.4 接入自检

在 agent 中让它调用 MCP 工具 `whoami`。应返回员工工号与有权限的业务场景。也可以用命令行检查：

```bash
curl -s -X POST https://report.example.com/mcp \
  -H "Authorization: Bearer $SMARTREPORT_TOKEN" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"check","version":"1"}}}'
```

返回 `serverInfo.name = smartreportgen` 即连通；返回 401 时，响应体会给出原因（缺少令牌、签名无效、已过期、已吊销）。

## 11. 故障排查

| 现象 | 可能原因与处理 |
|---|---|
| 构建时 pip 报 SSL / 超时 | 网络不稳或无法访问 PyPI：重试，或按 §7 使用 `PIP_INDEX_URL` 镜像 |
| `api` 一直 unhealthy | `docker compose logs api`：数据库未就绪、`.env` 配置错误、端口占用 |
| 端口被占用 | 修改 `API_HOST_PORT` / `MCP_HOST_PORT` / `PG_HOST_PORT` |
| agent 调 MCP 返回 401 | 令牌未设置：确认启动 agent 的 shell 中 `echo $SMARTREPORT_TOKEN` 非空；令牌过期或吊销：到「我的令牌」重新签发；IAM 票：确认未过期，且 `MCP_ACCEPT_IAM_TOKEN=true`、IAM 已配置。响应体中有具体原因 |
| 点击 IAM 登录后 IAM 报回调地址不匹配 | IAM 应用登记的回调必须与 `{PUBLIC_BASE_URL}/auth/iam/callback`（或 `IAM_REDIRECT_URI`）完全一致，包括协议、域名、端口 |
| 登录页提示“IAM 单点登录未配置” | `HAIER_IAM_HOST / CLIENT_ID / CLIENT_SECRET` 未配齐；先用 `ADMIN_TOKEN` 应急登录 |
| 单点登录失败（resultCode=…） | IAM 拒绝：Client ID/Secret 错误、code 已用或过期（重新登录即可）；详细原因见 `docker compose logs api` |
| IAM 登录后只看到「我的令牌」 | 该工号不是管理员：由现有管理员在「权限与令牌」中设为管理员，或首次部署时写入 `IAM_BOOTSTRAP_ADMINS` |
| 登录后提示“暂无数据权限” | 员工已自动注册为普通用户但未授权：由管理员授权业务场景 / 数据表，或对角色 `user` 授权全员基础权限 |
| Codex 看不到 MCP 或技能 | 项目未被信任（项目级 `.codex/config.toml` 不生效）→ 改用 `codex mcp add` 全局配置；技能目录名须为 `SKILL.md` 所在文件夹 |
| Windows 克隆后技能软链接无效 | 运行 `scripts/install-skill.sh`（Git Bash / WSL），或手动复制 `skills/smart-report` 到对应目录 |
| 工具返回 `permission_denied` | 员工无该业务场景或表的权限：在管理台“权限查询”核对，按需授权 |
| 检索结果带“已降级为纯关键词检索” | 向量接口不可用：检查 `AI_BASE_URL` / `AI_API_KEY`、容器到网关的网络、`EMBEDDING_DIM` 是否与模型输出一致 |
| 数据源“测试连接”失败 | 容器到数据库的网络（宿主机服务用 `host.docker.internal`）、账号密码、Oracle 服务名/SID、StarRocks 端口 9030 |
| 报告链接打开 401 | 链接中的查看凭证过期（默认 7 天）或被截断：重新生成报告，或用管理员登录管理台查看 |
| 报告页面空白 | 浏览器无法访问 `/static/vendor/echarts.min.js`（反向代理未转发 `/static`）：使用“下载离线版”或修正代理 |
| 升级后报告样式没变 | 样式在渲染时内联，重新渲染报告即可 |
