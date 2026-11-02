-- M6 持久化表（写穿模式：内存为主存，PG 为持久副本）
-- 由 docker-compose 挂载 infra/init 自动执行；psycopg 侧也有 CREATE TABLE IF NOT EXISTS 兜底
CREATE TABLE IF NOT EXISTS agent_sessions (
    session_id TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS approval_tickets (
    approval_id TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT now()
);
