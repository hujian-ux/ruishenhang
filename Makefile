# 企业 AI 智能体中台 · 本地开发入口
# 决策：先单体后拆容器。后端单体直接运行，基础设施由 docker-compose 提供。

.PHONY: infra up down initdb backend web test lint

infra:            ## 起基础设施（postgres/qdrant/minio/redis/litellm）
	docker compose up -d

down:             ## 停基础设施
	docker compose down

initdb:           ## 执行权限元数据 schema（docker-entrypoint 已自动执行，手动重跑用此）
	docker compose exec -T postgres psql -U app -d aiplat -f /docker-entrypoint-initdb.d/01_acl.sql

backend:          ## 起后端单体（默认 :8080）
	cd backend && pip install -e ".[dev]" && uvicorn app.main:app --reload --port 8080

web:              ## 起前端（默认 :5173）
	cd web && npm install && npm run dev

test:             ## 后端 smoke 测试
	cd backend && pytest -q

# 一键：基础设施 + 后端
up: infra backend
