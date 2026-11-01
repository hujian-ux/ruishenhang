#!/usr/bin/env bash
# 企业 AI 智能体中台 · 本地一键起（先单体后拆容器）
set -e
cd "$(dirname "$0")/.."

echo "==> 1/3 起基础设施 (docker compose)"
docker compose up -d

echo "==> 2/3 安装并起后端单体 (:8080)"
cd backend
python -m venv .venv 2>/dev/null || true
# shellcheck disable=SC1091
source .venv/bin/activate 2>/dev/null || true
pip install -e ".[dev]" -q
uvicorn app.main:app --reload --port 8080 &
BACK_PID=$!
cd ..

echo "==> 3/3 起前端 (:5173)"
cd web
npm install -q 2>/dev/null || true
npm run dev &
FRONT_PID=$!
cd ..

echo "==> 就绪：后端 http://127.0.0.1:8080/docs  前端 http://127.0.0.1:5173"
echo "    按 Ctrl+C 退出"
trap "kill $BACK_PID $FRONT_PID 2>/dev/null" EXIT
wait
