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
    analysis_file_name   text,
    analysis_file_markdown text,
    analysis_file_updated_at timestamptz,
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
    row_filters       jsonb NOT NULL DEFAULT '[]'::jsonb, -- 同字段多值取其一，不同字段同时满足
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
    template_type    text NOT NULL DEFAULT 'business' CHECK (template_type IN ('structured', 'business')),  -- 业务 Markdown，兼容历史记录
    markdown         text NOT NULL,
    summary          jsonb NOT NULL DEFAULT '{}'::jsonb,  -- Markdown 指南标题与章节摘要
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

-- 共享权限绑定单份报告与 IAM 工号，允许在接收人登记前预先授权。
CREATE TABLE IF NOT EXISTS report.report_share (
    report_id    bigint NOT NULL REFERENCES report.report(id) ON DELETE CASCADE,
    emp_no       text NOT NULL,
    access_level text NOT NULL CHECK (access_level IN ('view', 'edit')),
    PRIMARY KEY (report_id, emp_no)
);
-- 兼容已有授权记录。删除已登记员工时由管理台在同一事务撤销其报告权限。
ALTER TABLE report.report_share DROP CONSTRAINT IF EXISTS report_share_emp_no_fkey;
CREATE INDEX IF NOT EXISTS report_share_employee_idx ON report.report_share (emp_no, report_id);

CREATE TABLE IF NOT EXISTS permission.table_grant (
    id           bigserial PRIMARY KEY,
    subject_type text NOT NULL CHECK (subject_type IN ('employee', 'role')),
    subject      text NOT NULL,                 -- 工号或角色编码
    dataset_id   bigint NOT NULL REFERENCES dataset.dataset(id) ON DELETE CASCADE,
    table_name   text NOT NULL DEFAULT '*',     -- '*' 表示该数据集（业务场景）下全部数据表
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (subject_type, subject, dataset_id, table_name)
);

-- 管理台会话只引用 IAM 访问令牌，浏览器持有的会话 ID 仅保存摘要。
CREATE TABLE IF NOT EXISTS permission.iam_session (
    id_hash    text PRIMARY KEY,
    emp_no     text NOT NULL REFERENCES permission.employee(emp_no) ON DELETE CASCADE,
    iam_token  text NOT NULL,
    expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS iam_session_expiry ON permission.iam_session(expires_at);

-- 兼容已有库，旧记录可以在管理台补充说明。
ALTER TABLE dataset.dataset ADD COLUMN IF NOT EXISTS analysis_approach text NOT NULL DEFAULT '';
ALTER TABLE dataset.dataset_table ADD COLUMN IF NOT EXISTS row_filters jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE report_template.report_template ADD COLUMN IF NOT EXISTS business_summary text NOT NULL DEFAULT '';
ALTER TABLE report_template.report_template ADD COLUMN IF NOT EXISTS template_type text NOT NULL DEFAULT 'business'
    CHECK (template_type IN ('structured', 'business'));
ALTER TABLE report_template.report_template ADD COLUMN IF NOT EXISTS created_by text;
ALTER TABLE report_template.report_template ALTER COLUMN template_type SET DEFAULT 'business';
-- 样式规则的结构化条目 [{category, text}]；content 由条目生成，供模型阅读
ALTER TABLE style_rule.style_rule ADD COLUMN IF NOT EXISTS items jsonb NOT NULL DEFAULT '[]'::jsonb;
-- 创建人：通用规则由管理员配置；业务场景规则归属创建人，只对其本人的报告生效
ALTER TABLE style_rule.style_rule ADD COLUMN IF NOT EXISTS created_by text;
-- 员工来源：manual（管理员登记）/ iam_login（IAM 登录管理台）/ mcp（IAM Bearer 接入）
ALTER TABLE permission.employee ADD COLUMN IF NOT EXISTS registered_via text NOT NULL DEFAULT 'manual';
ALTER TABLE report.report ADD COLUMN IF NOT EXISTS created_by text;
-- 报告主记录保留最新版本号，写入新版本时锁定该行分配下一个号码。
ALTER TABLE report.report ADD COLUMN IF NOT EXISTS revision bigint NOT NULL DEFAULT 1;

CREATE TABLE IF NOT EXISTS report.report_version (
    report_id        bigint NOT NULL REFERENCES report.report(id) ON DELETE CASCADE,
    version          bigint NOT NULL CHECK (version > 0),
    base_version     bigint,
    name             text NOT NULL,
    html             text NOT NULL,
    meta             jsonb NOT NULL DEFAULT '{}'::jsonb,
    template_id      bigint REFERENCES report_template.report_template(id) ON DELETE SET NULL,
    template_version int,
    markdown         text,
    dataset_id       bigint,
    source_owner     text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    created_by       text,
    change_note      text NOT NULL DEFAULT '',
    PRIMARY KEY (report_id, version),
    FOREIGN KEY (report_id, base_version) REFERENCES report.report_version(report_id, version),
    CHECK (base_version < version)
);

-- 内置脚本按内容哈希共享。升级镜像后，旧报告继续展开保存时使用的精确版本。
CREATE TABLE IF NOT EXISTS report.static_asset (
    sha256 text PRIMARY KEY CHECK (sha256 ~ '^[a-f0-9]{64}$'),
    body   text NOT NULL
);

-- 选择随员工账号保存，历史选择不影响其他员工；没有记录表示跟随最新版。
CREATE TABLE IF NOT EXISTS report.report_selection (
    report_id bigint NOT NULL,
    emp_no    text NOT NULL REFERENCES permission.employee(emp_no) ON DELETE CASCADE,
    version   bigint NOT NULL,
    PRIMARY KEY (report_id, emp_no),
    FOREIGN KEY (report_id, version) REFERENCES report.report_version(report_id, version) ON DELETE CASCADE
);

-- 合并前发出的报告链接仍指向原来的快照。
CREATE TABLE IF NOT EXISTS report.report_alias (
    old_id    bigint PRIMARY KEY,
    report_id bigint NOT NULL,
    version   bigint NOT NULL,
    FOREIGN KEY (report_id, version) REFERENCES report.report_version(report_id, version) ON DELETE CASCADE
);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM sys.setting WHERE key='report_version_migration_v1') THEN
        RETURN;
    END IF;

    -- 仅合并同一创建人、同一模板、同名且有连续模板版本的历史渲染记录。
    -- 授权名单必须完全一致；曾单独编辑、重复渲染或来源不明的记录保留独立入口。
    CREATE TEMP TABLE legacy_report_versions ON COMMIT DROP AS
    WITH candidates AS (
        SELECT r.*, COALESCE((SELECT jsonb_object_agg(s.emp_no, s.access_level)
                              FROM report.report_share s WHERE s.report_id=r.id), '{}'::jsonb) AS shares
        FROM report.report r WHERE r.template_id IS NOT NULL AND r.created_by IS NOT NULL
    ), groups AS (
        SELECT template_id, created_by, name, max(id) AS report_id
        FROM candidates GROUP BY template_id, created_by, name
        HAVING count(*) > 1 AND bool_and(revision=1 AND meta ? 'queries')
            AND count(DISTINCT shares)=1 AND count(DISTINCT template_version)=count(*)
            AND max(template_version)-min(template_version)+1=count(*)
            AND (array_agg(template_version ORDER BY id))=(array_agg(template_version ORDER BY template_version))
    )
    SELECT r.id, g.report_id, row_number() OVER (PARTITION BY g.report_id ORDER BY r.id) AS version
    FROM candidates r JOIN groups g USING (template_id, created_by, name);

    -- 旧编辑器曾覆盖正文。已有修订号照常保留，无法恢复的快照不补造。
    INSERT INTO report.report_version(report_id, version, base_version, name, html, meta,
        template_id, template_version, markdown, dataset_id, source_owner, created_at, created_by)
    SELECT COALESCE(m.report_id,r.id), COALESCE(m.version,r.revision), NULLIF(m.version-1,0),
        r.name, r.html, r.meta, r.template_id, r.template_version,
        CASE WHEN r.revision=1 AND r.meta ? 'queries' THEN tv.markdown END,
        t.dataset_id, t.created_by, r.created_at, r.created_by
    FROM report.report r LEFT JOIN legacy_report_versions m ON m.id=r.id
    LEFT JOIN report_template.report_template t ON t.id=r.template_id
    LEFT JOIN report_template.report_template_version tv ON tv.template_id=r.template_id AND tv.version=r.template_version;

    INSERT INTO report.report_alias(old_id, report_id, version)
    SELECT id, report_id, version FROM legacy_report_versions WHERE id<>report_id;

    UPDATE report.report r SET revision=v.version, name=v.name, html=v.html, meta=v.meta,
        template_id=v.template_id, template_version=v.template_version
    FROM (SELECT DISTINCT ON (report_id) * FROM report.report_version ORDER BY report_id, version DESC) v
    WHERE r.id=v.report_id AND r.revision<>v.version;

    DELETE FROM report.report r USING legacy_report_versions m WHERE r.id=m.id AND m.id<>m.report_id;
    INSERT INTO sys.setting(key, value) VALUES ('report_version_migration_v1','done');
END $$;

-- 官方指标仅由数据集管理员配置，报告计划按名称引用。
ALTER TABLE dataset.dataset ADD COLUMN IF NOT EXISTS semantic_metrics jsonb NOT NULL DEFAULT '{}'::jsonb;

-- 个人场景记录所有者，查询按数据源当前授权计算。未登记所有者的历史共享场景继续使用原有场景授权。
ALTER TABLE dataset.dataset ADD COLUMN IF NOT EXISTS owner_emp_no text;
CREATE INDEX IF NOT EXISTS dataset_owner_idx ON dataset.dataset(owner_emp_no) WHERE owner_emp_no IS NOT NULL;

-- 单场景可选绑定一份 .md 分析思路文件，独立于常规表单与 Excel 批量导入维护。
ALTER TABLE dataset.dataset ADD COLUMN IF NOT EXISTS analysis_file_name text;
ALTER TABLE dataset.dataset ADD COLUMN IF NOT EXISTS analysis_file_markdown text;
ALTER TABLE dataset.dataset ADD COLUMN IF NOT EXISTS analysis_file_updated_at timestamptz;
