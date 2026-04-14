-- SinoVec 数据库表结构
-- 需要 PostgreSQL 14+ 和 pgvector 扩展

-- 启用 pgvector 扩展
CREATE EXTENSION IF NOT EXISTS vector;

-- 记忆主表
CREATE TABLE IF NOT EXISTS mem0 (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vector      vector(512) NOT NULL,
    payload     JSONB NOT NULL,

    -- 全文检索向量
    -- 如已安装 zhparser 扩展，使用中文解析器（效果最佳）
    -- 如无法安装 zhparser，将 'chinese_zh' 改为 'simple'（效果较差）
    fts         tsvector GENERATED ALWAYS AS (to_tsvector('chinese_zh', payload->>'data')) STORED,

    source      TEXT DEFAULT 'memory',
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- 向量索引（IVFFlat，适合 1万~100万数据量）
CREATE INDEX IF NOT EXISTS idx_mem0_vector
    ON mem0 USING ivfflat (vector vector_cosine_ops)
    WITH (lists = 100);

-- 全文检索索引
CREATE INDEX IF NOT EXISTS idx_mem0_fts
    ON mem0 USING gin (fts);

-- payload JSONB 属性索引
CREATE INDEX IF NOT EXISTS idx_mem0_source
    ON mem0 (source);
CREATE INDEX IF NOT EXISTS idx_mem0_created
    ON mem0 (created_at DESC);

-- 自动更新 updated_at 触发器
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS mem0_updated_at ON mem0;
CREATE TRIGGER mem0_updated_at
    BEFORE UPDATE ON mem0
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
