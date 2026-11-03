"""企业 AI 智能体中台 · 后端单体入口（先单体后拆容器）。

挂载 openapi.yaml 的 10 个 path 路由；网关中间件统一注入 X-Caller-Context
并承载第18章单一过滤点；所有下游路由信任 caller 上下文。
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.middleware import AuditTraceMiddleware, GatewayMiddleware
from app.routers import agents, approval, audit, auth, eval as eval_router, invoke, kb, models, rag, selection, usage
from app.services import approval_store, kb_store, pg_store, session_store, usage_store


@asynccontextmanager
async def lifespan(app: FastAPI):
    """PG 就绪时把会话/审批单/用量记录/知识文档载回内存（重启不丢）；不可达时静默用内存。"""
    session_store.load_from_pg()
    approval_store.load_from_pg()
    usage_store.load_from_pg()   # M7：重启后账单不清零
    kb_store.load_from_pg()      # M8：试点灌进来的语料重启不清零
    pg_store._connect()  # 触发建表/探活（一次性）
    yield


app = FastAPI(title="企业 AI 智能体中台", version="0.8.0", lifespan=lifespan)

# 审计 trace 中间件：每个请求生成 trace_id（须在网关之前，最先执行）
app.add_middleware(AuditTraceMiddleware)
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
app.include_router(audit.router, prefix=settings.API_PREFIX)  # M13：审计日志查询
app.include_router(eval_router.router, prefix=settings.API_PREFIX)  # M8：基线评测最近报告


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "env": settings.APP_ENV}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8080, reload=True)
