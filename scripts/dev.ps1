# 企业 AI 智能体中台 · 本地一键起（Windows PowerShell）
# 决策：先单体后拆容器；后端单体直接运行，基础设施由 docker-compose 提供。
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Join-Path $root ".."

Write-Host "==> 1/3 起基础设施 (docker compose)"
Push-Location $root
docker compose up -d
Pop-Location

Write-Host "==> 2/3 安装并起后端单体 (:8080)"
Push-Location (Join-Path $root "backend")
python -m venv .venv 2>$null
& .\.venv\Scripts\Activate.ps1 2>$null
pip install -e ".[dev]" -q
Start-Process -NoNewWindow powershell -ArgumentList "uvicorn app.main:app --reload --port 8080"
Pop-Location

Write-Host "==> 3/3 起前端 (:5173)"
Push-Location (Join-Path $root "web")
npm install -q 2>$null
npm run dev
Pop-Location
