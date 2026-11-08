"""企业 AI 智能体中台 · 后端单体入口（先单体后拆容器）。

挂载 openapi.yaml 的 10 个 path 路由；网关中间件统一注入 X-Caller-Context
并承载第18章单一过滤点；所有下游路由信任 caller 上下文。
"""
from contextlib import asynccontextmanager
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.core.middleware import AuditTraceMiddleware, GatewayMiddleware
from app.routers import (agents, approval, audit, auth, builder, eval as eval_router,
                        events, feedback, files, guardrails, integrations, invoke, kb,
                        models, ops, quality, rag, search, selection, tenants, usage)
from app.services import (agent_store, approval_store, file_store, kb_store,
                          observability, pg_store, session_store, usage_store)


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """第26章可观测性：每请求耗时 / 状态码 / trace_id 进指标层。

    只观测、不拦截、不改写响应；`X-Trace-Id` 由 AuditTraceMiddleware 在最内层
    写好响应头，这里从响应头取，避免打乱既有中间件顺序。
    """

    async def dispatch(self, request, call_next):
        t0 = time.time()
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            observability.observe_request(request.method, request.url.path, 500,
                                          (time.time() - t0) * 1000, "")
            raise
        observability.observe_request(
            request.method, request.url.path, status,
            (time.time() - t0) * 1000,
            response.headers.get("X-Trace-Id", ""),
        )
        return response


def _fail_if_insecure_for_prod() -> None:
    """C1（安全加固）：生产环境安全自检。

    网关与下游之间用 CALLER_SIGNING_SECRET 作 HMAC 签名信任边界；若仍是默认弱密钥，
    任何知道默认值的人都能伪造 X-Caller-Context，身份体系形同虚设。故生产启动即拒绝。
    测试默认 APP_ENV=dev，不受影响。
    """
    if settings.APP_ENV != "prod":
        return
    weak = {"dev-only-change-me"}
    if settings.CALLER_SIGNING_SECRET in weak:
        raise RuntimeError(
            "生产环境 CALLER_SIGNING_SECRET 仍为默认弱密钥（dev-only-change-me），"
            "拒绝启动。请设置高强度随机值并通过环境变量注入。")
    if settings.WEBHOOK_TOKEN_SECRET in weak:
        raise RuntimeError(
            "生产环境 WEBHOOK_TOKEN_SECRET 仍为默认弱密钥，拒绝启动。")
    if not settings.OIDC_ISSUER:
        raise RuntimeError(
            "生产环境未配置 OIDC_ISSUER（统一身份收口未生效），拒绝启动。"
            "请配置企业 SSO 的 issuer。")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """PG 就绪时把会话/审批单/用量记录/知识文档载回内存（重启不丢）；不可达时静默用内存。"""
    _fail_if_insecure_for_prod()
    session_store.load_from_pg()
    approval_store.load_from_pg()
    usage_store.load_from_pg()   # M7：重启后账单不清零
    kb_store.load_from_pg()      # M8：试点灌进来的语料重启不清零
    agent_store.load_from_pg()   # M15：注册的智能体/技能包重启不清零
    agent_store.ensure_builtin_assistant()  # 内置通用助手：新用户开口即可提问
    agent_store.ensure_approval_assistant()  # M46 平台级审批助手（辅助层，只建议不落账）
    agent_store.ensure_public_skills()      # 公共库：文案/PPT/表格等办公基础技能（9 条，不进 115 口径）
    file_store.load_from_pg()    # M16：上传件与产出物重启后仍可下载
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
if settings.OBSERVABILITY_ENABLED:
    app.add_middleware(ObservabilityMiddleware)  # M27：最外层，记录所有请求含 4xx/5xx

app.include_router(auth.router, prefix=settings.API_PREFIX)
app.include_router(rag.router, prefix=settings.API_PREFIX)
app.include_router(agents.router, prefix=settings.API_PREFIX)
app.include_router(invoke.router, prefix=settings.API_PREFIX)  # M4: /agent/invoke + 会话记忆
app.include_router(files.router, prefix=settings.API_PREFIX)   # M16: 文件进出（上传/下载/产出物）
app.include_router(selection.router, prefix=settings.API_PREFIX)
app.include_router(kb.router, prefix=settings.API_PREFIX)
app.include_router(models.router, prefix=settings.API_PREFIX)
app.include_router(usage.router, prefix=settings.API_PREFIX)
app.include_router(approval.router, prefix=settings.API_PREFIX)
app.include_router(audit.router, prefix=settings.API_PREFIX)  # M13：审计日志查询
app.include_router(eval_router.router, prefix=settings.API_PREFIX)  # M8：基线评测最近报告
app.include_router(search.router, prefix=settings.API_PREFIX)       # M26：联网查资料 / 竞品调研
# ── M27：蓝图缺口收口（第 19/20/21/26/27/28/29/30/31/34/38/39/40/41/42/43/44 章）──
app.include_router(guardrails.router, prefix=settings.API_PREFIX)   # 输入/输出/工具三层安全
app.include_router(feedback.router, prefix=settings.API_PREFIX)     # 反馈、演化建议台、度量
app.include_router(ops.router, prefix=settings.API_PREFIX)          # 指标 / 告警 / 就绪度 / 数据主体权利
app.include_router(events.router, prefix=settings.API_PREFIX)       # webhook 事件总线
app.include_router(quality.router, prefix=settings.API_PREFIX)      # 评测 / 路由 / 缓存 / 降级 / MLOps
app.include_router(builder.router, prefix=settings.API_PREFIX)      # 自助建部门 / L2 建包 / L3 编排
app.include_router(integrations.router, prefix=settings.API_PREFIX)  # P1-173：企业微信/钉钉 集成与审批回推
app.include_router(tenants.router, prefix=settings.API_PREFIX)        # M43：多企业租户开通


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "env": settings.APP_ENV}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8080, reload=True)
