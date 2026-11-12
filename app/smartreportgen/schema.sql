-- SmartReportGen 元数据库结构。按数据类型拆分 schema：
--   datasource       数据源
--   dataset          数据集（业务场景）及其数据表
--   style_rule       样式规则（通用 / 业务场景）
--   report_template  报告模板（markdown）及版本历史
--   report           报告（html）
--   sys              系统设置
-- {dim} 由程序按 EMBEDDING_DIM 替换。
CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS sys;
CREATE SCHEMA IF NOT EXISTS datasource;
CREATE SCHEMA IF NOT EXISTS dataset;
CREATE SCHEMA IF NOT EXISTS style_rule;
CREATE SCHEMA IF NOT EXISTS report_template;
CREATE SCHEMA IF NOT EXISTS report;

CREATE TABLE IF NOT EXISTS sys.setting (
    key        text PRIMARY KEY,
    value      text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS datasource.datasource (
    id          bigserial PRIMARY KEY,
    name        text NOT NULL UNIQUE,
    type        text NOT NULL CHECK (type IN ('mysql', 'postgresql', 'oracle', 'starrocks', 'api')),
    config      jsonb NOT NULL DEFAULT '{}'::jsonb,
    secret      text,                       -- Fernet 加密后的密码 / 令牌
    description text NOT NULL DEFAULT '',
    enabled     boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dataset.dataset (
    id                   bigserial PRIMARY KEY,
    business_scenario    text NOT NULL UNIQUE,
    business_description text NOT NULL DEFAULT '',
    analysis_approach    text NOT NULL DEFAULT '',  -- 供 Agent 参考的业务分析思路
    datasource_id        bigint REFERENCES datasource.datasource(id) ON DELETE RESTRICT,
    tags                 text[] NOT NULL DEFAULT '{}',
    enabled              boolean NOT NULL DEFAULT true,
    -- 关键词检索字段（jieba 分词）：a=场景名/标签，b=业务描述，c=表名/表描述
    kw_a                 text[] NOT NULL DEFAULT '{}',
    kw_b                 text[] NOT NULL DEFAULT '{}',
    kw_c                 text[] NOT NULL DEFAULT '{}',
    kw_all               text[] NOT NULL DEFAULT '{}',
    embedding            vector({dim}),
    embedding_sig        text,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS dataset_kw_all_gin ON dataset.dataset USING gin (kw_all);

CREATE TABLE IF NOT EXISTS dataset.dataset_table (
    id                bigserial PRIMARY KEY,
    dataset_id        bigint NOT NULL REFERENCES dataset.dataset(id) ON DELETE CASCADE,
    table_name        text NOT NULL,
    table_description text NOT NULL DEFAULT '',
    sort              int NOT NULL DEFAULT 0,
    UNIQUE (dataset_id, table_name)
);

CREATE TABLE IF NOT EXISTS style_rule.style_rule (
    id                bigserial PRIMARY KEY,
    name              text NOT NULL,
    scope             text NOT NULL CHECK (scope IN ('general', 'scenario')),
    business_scenario text,                 -- scope=scenario 时对应 dataset.business_scenario
    content           text NOT NULL DEFAULT '',   -- 规则说明（markdown）
    theme             jsonb NOT NULL DEFAULT '{}'::jsonb,  -- 主题变量（可选）
    priority          int NOT NULL DEFAULT 100,   -- 数值越大越晚应用（覆盖前者）
    enabled           boolean NOT NULL DEFAULT true,
    kw_a              text[] NOT NULL DEFAULT '{}',
    kw_b              text[] NOT NULL DEFAULT '{}',
    kw_c              text[] NOT NULL DEFAULT '{}',
    kw_all            text[] NOT NULL DEFAULT '{}',
    embedding         vector({dim}),
    embedding_sig     text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CHECK (scope = 'general' OR business_scenario IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS style_rule_kw_all_gin ON style_rule.style_rule USING gin (kw_all);

CREATE TABLE IF NOT EXISTS report_template.report_template (
    id               bigserial PRIMARY KEY,
    name             text NOT NULL,
    dataset_id       bigint REFERENCES dataset.dataset(id) ON DELETE SET NULL,
    user_requirement text NOT NULL DEFAULT '',
    analysis_goal    text NOT NULL DEFAULT '',
    business_summary text NOT NULL DEFAULT '',  -- 展示给业务人员的模板说明
    markdown         text NOT NULL,
    summary          jsonb NOT NULL DEFAULT '{}'::jsonb,  -- 解析摘要：章节/图表/筛选数量
    version          int NOT NULL DEFAULT 1,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS report_template.report_template_version (
    template_id bigint NOT NULL REFERENCES report_template.report_template(id) ON DELETE CASCADE,
    version     int NOT NULL,
    markdown    text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (template_id, version)
);

CREATE TABLE IF NOT EXISTS report.report (
    id               bigserial PRIMARY KEY,
    name             text NOT NULL,
    template_id      bigint REFERENCES report_template.report_template(id) ON DELETE SET NULL,
    template_version int,
    html             text NOT NULL,
    meta             jsonb NOT NULL DEFAULT '{}'::jsonb,  -- 渲染信息：行数、耗时、告警
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS report_template_idx ON report.report (template_id);

-- ============ 权限（员工 / 表授权 / 访问令牌） ============
CREATE SCHEMA IF NOT EXISTS permission;

CREATE TABLE IF NOT EXISTS permission.employee (
    emp_no     text PRIMARY KEY,                -- 员工工号（与令牌中的工号 claim 对应）
    name       text NOT NULL DEFAULT '',
    department text NOT NULL DEFAULT '',
    roles      text[] NOT NULL DEFAULT '{}',    -- 角色编码，可对角色整体授权
    is_admin   boolean NOT NULL DEFAULT false,  -- 管理员：可访问全部数据表
    enabled    boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS permission.table_grant (
    id           bigserial PRIMARY KEY,
    subject_type text NOT NULL CHECK (subject_type IN ('employee', 'role')),
    subject      text NOT NULL,                 -- 工号或角色编码
    dataset_id   bigint NOT NULL REFERENCES dataset.dataset(id) ON DELETE CASCADE,
    table_name   text NOT NULL DEFAULT '*',     -- '*' 表示该数据集（业务场景）下全部数据表
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (subject_type, subject, dataset_id, table_name)
);

CREATE TABLE IF NOT EXISTS permission.access_token (
    jti          text PRIMARY KEY,
    emp_no       text NOT NULL,
    note         text NOT NULL DEFAULT '',
    expires_at   timestamptz,
    revoked      boolean NOT NULL DEFAULT false,
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz
);

-- 兼容已有库，旧记录可以在管理台补充说明。
ALTER TABLE dataset.dataset ADD COLUMN IF NOT EXISTS analysis_approach text NOT NULL DEFAULT '';
ALTER TABLE report_template.report_template ADD COLUMN IF NOT EXISTS business_summary text NOT NULL DEFAULT '';
ALTER TABLE report_template.report_template ADD COLUMN IF NOT EXISTS created_by text;
-- 样式规则的结构化条目 [{category, text}]；content 由条目生成，供模型阅读
ALTER TABLE style_rule.style_rule ADD COLUMN IF NOT EXISTS items jsonb NOT NULL DEFAULT '[]'::jsonb;
-- 创建人：通用规则由管理员配置；业务场景规则归属创建人，只对其本人的报告生效
ALTER TABLE style_rule.style_rule ADD COLUMN IF NOT EXISTS created_by text;
-- 员工来源：manual（管理员登记）/ iam_login（IAM 登录管理台自动注册）/ mcp（首次调用 MCP 自动注册）
ALTER TABLE permission.employee ADD COLUMN IF NOT EXISTS registered_via text NOT NULL DEFAULT 'manual';
ALTER TABLE report.report ADD COLUMN IF NOT EXISTS created_by text;
