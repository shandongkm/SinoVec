"""
SinoVec 配置常量模块

所有魔法数字统一从环境变量读取，支持生产环境调优。
分组说明：
  - 去重阈值：控制语义相似记忆的合并/删除策略
  - 时间与时效：控制记忆衰减速度和去重窗口
  - 层级晋升：控制 HOT/WARM/COLD 层划分比例
  - 内容质量门：控制添加记忆的最低长度要求
  - 检索权重：控制向量搜索和 BM25 的融合权重（根据查询类型动态选择）
  - 重排与 MMR：控制 LLM 重排触发条件和多样性去重
  - 检索参数：控制召回数量、查询扩展量等
  - LLM 配置：Ollama 生成参数
"""
from __future__ import annotations

import os

_env = os.getenv


# ── 去重阈值 ──────────────────────────────────────────────
COSINE_DIST_NEAR     = float(_env("MEM_COSINE_DIST_NEAR",     "0.10"))
COSINE_DIST_MERGE    = float(_env("MEM_COSINE_DIST_MERGE",    "0.15"))
COSINE_DIST_DEEP     = float(_env("MEM_COSINE_DIST_DEEP",     "0.10"))
COSINE_DIST_SESS_GAP = float(_env("MEM_COSINE_DIST_SESS_GAP", "0.30"))
OVERLAP_LO           = float(_env("MEM_OVERLAP_LO",           "0.30"))

# ── 时间与时效 ────────────────────────────────────────────
DECAY_HALF_LIFE_DAYS  = int(_env("MEM_DECAY_HALF_LIFE_DAYS",  "30"))
DEDUP_WINDOW_HOURS    = int(_env("MEM_DEDUP_WINDOW_HOURS",     "6"))
HOT_MAX_DAYS          = int(_env("MEM_HOT_MAX_DAYS",           "2"))
WARM_MAX_DAYS         = int(_env("MEM_WARM_MAX_DAYS",          "7"))
ACCESS_INTERVAL_HOURS = int(_env("MEM_ACCESS_INTERVAL_HOURS",  "1"))
LINEAGE_CLEANUP_DAYS  = int(_env("MEM_LINEAGE_CLEANUP_DAYS",  "90"))

# ── 层级晋升比例 ──────────────────────────────────────────
HOT_RATIO  = float(_env("MEM_HOT_RATIO",  "0.10"))
WARM_RATIO = float(_env("MEM_WARM_RATIO", "0.60"))

# ── 内容质量门 ────────────────────────────────────────────
MIN_CONTENT_CHARS   = int(_env("MEM_MIN_CONTENT_CHARS",   "15"))
SHORT_CONTENT_CHARS = int(_env("MEM_SHORT_CONTENT_CHARS", "30"))

# ── 检索权重 ───────────────────────────────────────────────
VEC_W_SHORT      = float(_env("MEM_VEC_W_SHORT",      "0.85"))
BM25_W_SHORT     = float(_env("MEM_BM25_W_SHORT",     "0.15"))
VEC_W_PROPER     = float(_env("MEM_VEC_W_PROPER",     "0.35"))
BM25_W_PROPER    = float(_env("MEM_BM25_W_PROPER",    "0.65"))
VEC_W_OVERLAP_LO = float(_env("MEM_VEC_W_OVERLAP_LO", "0.55"))
BM25_W_OVERLAP_LO= float(_env("MEM_BM25_W_OVERLAP_LO", "0.45"))
VEC_W_DEFAULT    = float(_env("MEM_VEC_W_DEFAULT",    "0.70"))
BM25_W_DEFAULT   = float(_env("MEM_BM25_W_DEFAULT",    "0.30"))

# ── 重排与 MMR ─────────────────────────────────────────────
RERANK_MIN_CANDIDATES = int(_env("MEM_RERANK_MIN_CANDIDATES", "5"))
RERANK_DEFAULT_SCORE  = float(_env("MEM_RERANK_DEFAULT_SCORE", "0.50"))
MMR_LAMBDA           = float(_env("MEM_MMR_LAMBDA",           "0.50"))

# ── 检索参数 ───────────────────────────────────────────────
TOP_K_RERANK          = int(_env("MEM_TOP_K_RERANK",         "20"))
QUERY_EXPANSION_MAX  = int(_env("MEM_QUERY_EXPANSION_MAX",  "5"))
MIN_QUERY_TERM_LEN    = int(_env("MEM_MIN_QUERY_TERM_LEN",   "2"))
RECALL_ANALYSIS_LIMIT= int(_env("MEM_RECALL_ANALYSIS_LIMIT", "50"))

# ── LLM 配置 ───────────────────────────────────────────────
OLLAMA_TEMPERATURE = float(_env("MEM_OLLAMA_TEMPERATURE", "0.30"))
OLLAMA_MAX_TOKENS  = int(_env("MEM_OLLAMA_MAX_TOKENS",   "500"))

# ── 工作区路径 ─────────────────────────────────────────────
WORKSPACE_ENV = os.environ.get("MEMORY_WORKSPACE", "/root/.openclaw/workspace")
