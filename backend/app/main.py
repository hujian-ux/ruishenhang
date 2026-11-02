"""企业 AI 智能体中台 · 后端单体入口（先单体后拆容器）。

挂载 openapi.yaml 的 10 个 path 路由；网关中间件统一注入 X-Caller-Context
并承载第18章单一过滤点；所有下游路由信任 caller 上下文。
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.middleware import GatewayMiddleware
from app.routers import agents, approval, auth, invoke, kb, models, rag, selection, usage
from app.services import approval_store, pg_store, session_store


@asynccontextmanager
async def lifespan(app: FastAPI):
    """M6：PG 就绪时把会话/审批单载回内存（重启不丢）；不可达时静默用内存。"""
    session_store.load_from_pg()
    approval_store.load_from_pg()
    pg_store._connect()  # 触发建表/探活（一次性）
    yield


app = FastAPI(title="企业 AI 智能体中台", version="0.4.0", lifespan=lifespan)

# 网关中间件：身份注入 + 单一过滤点前置（拆容器后整体迁到 api-gateway）
app.add_middleware(GatewayMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 开发期放开；生产收紧为前端域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix=settings.API_PREFIX)
app.include_router(rag.router, prefix=settings.API_PREFIX)
app.include_router(agents.router, prefix=settings.API_PREFIX)
app.include_router(invoke.router, prefix=settings.API_PREFIX)  # M4: /agent/invoke + 会话记忆
app.include_router(selection.router, prefix=settings.API_PREFIX)
app.include_router(kb.router, prefix=settings.API_PREFIX)
app.include_router(models.router, prefix=settings.API_PREFIX)
app.include_router(usage.router, prefix=settings.API_PREFIX)
app.include_router(approval.router, prefix=settings.API_PREFIX)


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "env": settings.APP_ENV}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8080, reload=True)
