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
    # M6：对话默认路由的 model_id（LiteLLM model_list 里的一项）；探活通过走真生成，否则 stub
    LLM_DEFAULT_MODEL = _env("LLM_DEFAULT_MODEL", "gpt-4o-mini")

    # 决策：中文 Embedding 选 bge（bge-m3，维度 1024）
    EMBEDDING_MODEL = _env("EMBEDDING_MODEL", "bge-m3")
    EMBEDDING_DIM = int(_env("EMBEDDING_DIM", "1024"))
    # 重排层 cross-encoder（第36章 W2）；未安装时退化为启发式打分
    RERANK_MODEL = _env("RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")

    CALLER_SIGNING_SECRET = _env("CALLER_SIGNING_SECRET", "dev-only-change-me")
    DEFAULT_TENANT = _env("DEFAULT_TENANT", "t_demo")


settings = Settings()
