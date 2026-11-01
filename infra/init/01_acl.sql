-- 企业 AI 智能体中台 · 权限元数据与业务基表
-- 对应：第18章 单一过滤点 / 多人知识库(scope/owner/dept) / 第25章模型注册 / 第35章智能体
-- 由 docker-compose 的 postgres 在首次启动时自动执行（挂载到 /docker-entrypoint-initdb.d）。

-- ───────────────────────── 知识库权限元数据（第18章过滤点数据源） ─────────────────────────
CREATE TABLE IF NOT EXISTS kb_documents (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    TEXT NOT NULL,
    scope        TEXT NOT NULL CHECK (scope IN ('personal','owner','department','global')),
    owner        TEXT,                 -- 负责人（多人知识库：owner 可见）
    dept         TEXT,                 -- 部门（department 范围强制等于 caller.deptId）
    title        TEXT,
    content      TEXT,                 -- 正文（入库自动向量化）
    qdrant_id    UUID,                 -- 向量侧 id（Qdrant）
    minio_key    TEXT,                 -- 文件侧 key（MinIO）
    created_by   TEXT,                 -- 服务端强制回填，不可伪造
    created_at   TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_kb_scope ON kb_documents (tenant_id, scope, dept);
CREATE INDEX IF NOT EXISTS idx_kb_owner ON kb_documents (tenant_id, owner);

-- ───────────────────────── 智能体注册（第35章选型结论落库） ─────────────────────────
CREATE TABLE IF NOT EXISTS agents (
    agent_id           TEXT PRIMARY KEY,
    tenant_id          TEXT NOT NULL,
    name               TEXT NOT NULL,
    adapter_type       TEXT NOT NULL CHECK (adapter_type IN ('rule','ml_serving','llm','llm_retrieval','ml_llm_retrieval')),
    description        TEXT,
    knowledge_scope    TEXT NOT NULL CHECK (knowledge_scope IN ('personal','owner','department','global')),
    model_id           TEXT,
    status             TEXT NOT NULL DEFAULT 'pending_review' CHECK (status IN ('pending_review','active','rejected')),
    registered_by      TEXT NOT NULL,   -- 服务端强制 = caller.userId
    registered_at      TIMESTAMPTZ DEFAULT now()
);

-- ───────────────────────── 模型注册表（第25章可插拔网关） ─────────────────────────
CREATE TABLE IF NOT EXISTS models (
    model_id            TEXT PRIMARY KEY,
    vendor              TEXT NOT NULL,
    adapter             TEXT NOT NULL,
    capabilities        TEXT[] NOT NULL DEFAULT '{}',
    context_window      INTEGER,
    status              TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive')),
    price_per_1k_prompt  DOUBLE PRECISION DEFAULT 0,
    price_per_1k_completion DOUBLE PRECISION DEFAULT 0,
    default_quota       BIGINT,
    registered_by       TEXT,
    registered_at       TIMESTAMPTZ DEFAULT now()
);

-- ───────────────────────── 用量与计费（第25章计量） ─────────────────────────
CREATE TABLE IF NOT EXISTS usage_records (
    id            BIGSERIAL PRIMARY KEY,
    tenant_id     TEXT NOT NULL,
    dept_id       TEXT,
    user_id       TEXT,
    agent_id      TEXT,
    model_id      TEXT,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    cost          DOUBLE PRECISION DEFAULT 0,
    created_at    TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_usage_dim ON usage_records (tenant_id, dept_id, created_at);

-- ───────────────────────── 审批单（入驻/共享/导出） ─────────────────────────
CREATE TABLE IF NOT EXISTS approvals (
    approval_id   TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    ref_id        TEXT,
    status        TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
    decided_by    TEXT,
    decided_at    TIMESTAMPTZ,
    audit_id      TEXT,
    created_at    TIMESTAMPTZ DEFAULT now()
);

-- 种子：默认模型（含私有化 qwen-local 与本地 bge 嵌入能力），便于 M1 直接演示
INSERT INTO models (model_id, vendor, adapter, capabilities, context_window, status, price_per_1k_prompt, price_per_1k_completion, default_quota, registered_by)
VALUES
  ('gpt-4o-mini','openai','openai_compatible', ARRAY['chat','stream'], 128000, 'active', 0.00015, 0.0006, 1000000, 'seed'),
  ('qwen-local','private','openai_compatible', ARRAY['chat','embed'], 32000, 'active', 0, 0, 1000000, 'seed')
ON CONFLICT (model_id) DO NOTHING;
