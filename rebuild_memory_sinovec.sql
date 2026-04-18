-- SinoVec 数据库表结构
-- 需要 PostgreSQL 14+ 和 pgvector 扩展
-- zhparser 扩展检测和 fts 列创建见 init-zhparser.sh

-- 启用 pgvector 扩展
CREATE EXTENSION IF NOT EXISTS vector;

-- 记忆主表（不含 fts 列，fts 列由 init-zhparser.sh 根据 zhparser 可用性决定添加）
CREATE TABLE IF NOT EXISTS sinovec (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vector          vector(512) NOT NULL,
    payload         JSONB NOT NULL,

    source          TEXT DEFAULT 'memory',
    recall_count    INT DEFAULT 0,          -- 被召回次数（用于 recall-analysis）
    last_access_time TIMESTAMPTZ,
    access_count    INT DEFAULT 0,           -- 访问次数（用于热度晋升）
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 向量索引（IVFFlat，适合 1万~100万数据量）
CREATE INDEX IF NOT EXISTS idx_sinovec_vector
    ON sinovec USING ivfflat (vector vector_cosine_ops)
    WITH (lists = 100);

-- payload JSONB 属性索引
CREATE INDEX IF NOT EXISTS idx_sinovec_source
    ON sinovec (source);
CREATE INDEX IF NOT EXISTS idx_sinovec_source_jsonb
    ON sinovec ((payload->>'source'));  -- session 片段按 source 查询加速
CREATE INDEX IF NOT EXISTS idx_sinovec_created
    ON sinovec (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sinovec_access
    ON sinovec (access_count DESC);
CREATE INDEX IF NOT EXISTS idx_sinovec_recall
    ON sinovec (recall_count ASC);

-- 用户 ID 索引（支持多用户隔离查询）
CREATE INDEX IF NOT EXISTS idx_sinovec_user_id
    ON sinovec ((payload->>'user_id'));

-- 自动更新 updated_at 触发器
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS sinovec_updated_at ON sinovec;
CREATE TRIGGER sinovec_updated_at
    BEFORE UPDATE ON sinovec
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();


-- ═══════════════════════════════════════════════════════════
-- 血缘记录表：追踪每一次合并/删除操作
-- 用于 recall-analysis、session-gap、审计回溯
-- ═══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS memory_lineage (
    id          SERIAL PRIMARY KEY,
    source_id   UUID NOT NULL,                   -- 被操作的记忆 ID
    operation   TEXT NOT NULL,                   -- 'merge' | 'delete' | 'extract' | 'promote'
    reason      TEXT,                            -- 操作原因（如 cos_dist, time_diff）
    target_id   UUID,                            -- 合并到的目标记忆 ID（merge 时）
    details     JSONB,                           -- 额外详情（向量距离、时效差等）
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 血缘索引（加速按 source_id / target_id 查询）
CREATE INDEX IF NOT EXISTS idx_lineage_source
    ON memory_lineage (source_id);
CREATE INDEX IF NOT EXISTS idx_lineage_target
    ON memory_lineage (target_id);
CREATE INDEX IF NOT EXISTS idx_lineage_created
    ON memory_lineage (created_at DESC);

-- fts 列的创建由 init-zhparser.sh 完成（见 init-zhparser.sh）
