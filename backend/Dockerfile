# 后端单体镜像（用于「后拆容器」阶段，默认 dev 本地运行，不在 compose 启用）
FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[ml]" || pip install --no-cache-dir -e .
COPY app ./app
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
