# 企业 AI 智能体中台 · 后端生产镜像
# 设计：先单体后拆容器 —— 本地 dev 直接跑；本镜像用于「后拆容器」上线阶段。
# 依赖说明：
#   - 基础依赖（fastapi/uvicorn/sqlalchemy/qdrant-client/minio/redis/...）来自 pyproject 主依赖；
#   - langgraph 是智能体运行时刚需，但为懒加载，单独装（避免把可选的 sentence-transformers/torch 巨型依赖带进镜像）；
#   - 本地 bge-m3 嵌入（sentence-transformers）在容器内不用（走 SiliconFlow API 嵌入或内存伪向量兜底），故不装。
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# 先装依赖（利用层缓存）：pyproject 不含重型 ML 包
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e . && pip install --no-cache-dir langgraph

# 再拷代码（代码变动不触发上面那层重装）
COPY app ./app
COPY scripts ./scripts
# 仓库内置 115 包源：首次启动自动灌库 + few-shot 配对依赖；客户无需外部目录
COPY solutions ./solutions

# 非 root 运行
RUN useradd -m -u 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

# 启动即内存模式（外部 PG/Qdrant/MinIO/Redis 不可达时自动兜底），与本地演示一致；
# 上线要持久化时，在 compose/环境里设 QDRANT_HOST/MINIO_ENDPOINT/REDIS_URL/PG DSN 即可切换。
HEALTHCHECK --interval=15s --timeout=3s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
