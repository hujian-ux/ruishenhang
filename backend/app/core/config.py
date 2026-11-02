"""运行时配置：从环境变量读取，提供默认值，无额外依赖。"""
import os

def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)

class Settings:
    APP_NAME = _env("APP_NAME", "enterprise-ai-platform")
    APP_ENV = _env("APP_ENV", "dev")
    API_PREFIX = _env("API_PREFIX", "/api/v1")

    PG_HOST = _env("PG_HOST", "127.0.0.1")
    PG_PORT = int(_env("PG_PORT", "5432"))
    PG_DB = _env("PG_DB", "aiplat")
    PG_USER = _env("PG_USER", "app")
    PG_PASSWORD = _env("PG_PASSWORD", "dev")

    QDRANT_HOST = _env("QDRANT_HOST", "127.0.0.1")
    QDRANT_PORT = int(_env("QDRANT_PORT", "6333"))

    MINIO_ENDPOINT = _env("MINIO_ENDPOINT", "127.0.0.1:9000")
    MINIO_USER = _env("MINIO_USER", "minioadmin")
    MINIO_PASSWORD = _env("MINIO_PASSWORD", "minioadmin")
    MINIO_BUCKET = _env("MINIO_BUCKET", "kb-files")

    REDIS_URL = _env("REDIS_URL", "redis://127.0.0.1:6379/0")

    LITELLM_BASE_URL = _env("LITELLM_BASE_URL", "http://127.0.0.1:4000")
    # 可选：LiteLLM 代理主密钥（新版代理默认要求鉴权）。本地开发可留空，
    # 并给 litellm 设 LITELLM_DANGEROUSLY_PERMIT_WEAK_OR_UNSET_MASTER_KEY=true。
    LITELLM_MASTER_KEY = _env("LITELLM_MASTER_KEY", "")
    # M6：对话默认路由的 model_id（LiteLLM model_list 里的一项）；探活通过走真生成，否则 stub
    LLM_DEFAULT_MODEL = _env("LLM_DEFAULT_MODEL", "gpt-4o-mini")

    # M7 计量配额：部门滚动 30 天 token 上限（0 = 不限额）。超限调用直接 429，
    # 不是事后告警——计费在调用链路上是红线，不只是报表。
    USAGE_QUOTA_TOKENS = int(_env("USAGE_QUOTA_TOKENS", "2000000"))

    # M8 试点：月度成本预算线（CNY，0 = 不设预算线）。趋势页按此画水位与月末外推，
    # 超过 80% 预警、100% 标红——两周试点靠它判断"这个部门到底烧不烧得起"。
    USAGE_MONTHLY_BUDGET_CNY = float(_env("USAGE_MONTHLY_BUDGET_CNY", "500"))
    # M8 批量导入：单次扫描文件数上限、入索引字符上限（超出部分只存原文不建索引）
    INGEST_MAX_FILES = int(_env("INGEST_MAX_FILES", "1000"))
    INGEST_INDEX_CHAR_CAP = int(_env("INGEST_INDEX_CHAR_CAP", "20000"))
    # M8 试点：是否加载 18 篇演示语料。真实语料进场后必须设 0，
    # 否则演示文档会参与竞争、稀释真实召回（实测会把 Recall@10 抬高成假象）。
    SEED_DEMO_CORPUS = _env("SEED_DEMO_CORPUS", "1") == "1"

    # 决策：中文 Embedding 选 bge（bge-m3，维度 1024）
    # 真实模型开关：默认 0（确定性伪向量，离线/CI 稳定，不会触发 HF 下载）。
    # 开 1 时本地须已缓存模型（或设 HF_ENDPOINT=https://hf-mirror.com 走国内镜像）。
    USE_LOCAL_EMBEDDING = _env("USE_LOCAL_EMBEDDING", "0") == "1"
    EMBEDDING_MODEL = _env("EMBEDDING_MODEL", "bge-m3")
    EMBEDDING_DIM = int(_env("EMBEDDING_DIM", "1024"))
    # 重排层 cross-encoder（第36章 W2）；未安装时退化为启发式打分
    RERANK_MODEL = _env("RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")

    # M9 规模化：Embedding 结果持久化缓存（sqlite 单文件）。
    # 重启载回 5000 篇时，命中缓存 = 不重算向量；真 bge 模型下这是分钟级 vs 秒级的差别。
    # 缓存键含模型名与维度，换模型自然失效，不会串味。
    EMBED_CACHE = _env("EMBED_CACHE", "1") == "1"
    EMBED_CACHE_PATH = _env("EMBED_CACHE_PATH", "data/embed_cache.db")
    # 批量写入的落盘节流（条数）：攒够再一次性提交，避免每条一个事务
    EMBED_CACHE_FLUSH_EVERY = int(_env("EMBED_CACHE_FLUSH_EVERY", "500"))

    # M9 向量后端：memory（numpy 内存矩阵，默认）| qdrant | auto（远程可达则用 qdrant）
    # 本地没装 Docker 也能验证 qdrant 路径：QDRANT_MODE=local 用 qdrant-client 的文件模式，
    # 跑的是真实 Qdrant 引擎，只是不占端口。生产切远程只改 QDRANT_MODE=http。
    VECTOR_BACKEND = _env("VECTOR_BACKEND", "memory")
    QDRANT_MODE = _env("QDRANT_MODE", "http")
    QDRANT_PATH = _env("QDRANT_PATH", "data/qdrant")
    QDRANT_COLLECTION = _env("QDRANT_COLLECTION", "kb_chunks")
    QDRANT_TIMEOUT_S = float(_env("QDRANT_TIMEOUT_S", "1.0"))

    # M9 规模化：索引内存上限保护。超过该子块数时检索自动收窄候选
    # （向量全扫仍保留，BM25 只走倒排候选），避免单机被超大语料拖垮。
    INDEX_MAX_CHILDREN = int(_env("INDEX_MAX_CHILDREN", "200000"))
    # tombstone 占比超过该比例时压缩索引（删除多、增量少时回收内存）
    INDEX_COMPACT_RATIO = float(_env("INDEX_COMPACT_RATIO", "0.25"))

    CALLER_SIGNING_SECRET = _env("CALLER_SIGNING_SECRET", "dev-only-change-me")
    DEFAULT_TENANT = _env("DEFAULT_TENANT", "t_demo")


settings = Settings()
