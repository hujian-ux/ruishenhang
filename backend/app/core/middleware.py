"""网关中间件：单一入口解析身份并注入 X-Caller-Context（第18章过滤点前置）。

单体阶段，网关逻辑即本中间件；拆容器后，本逻辑整体迁到独立 api-gateway 服务，
下游契约（X-Caller-Context 头）保持不变。
"""
import uuid

from starlette.middleware.base import BaseHTTPMiddleware

from app.core.security import decode_token, encode_caller_context


class AuditTraceMiddleware(BaseHTTPMiddleware):
    """M13：为每个请求生成 trace_id，供审计与日志串联（检索/生成/下载/审批）。"""

    async def dispatch(self, request, call_next):
        request.state.trace_id = uuid.uuid4().hex[:16]
        response = await call_next(request)
        response.headers["X-Trace-Id"] = request.state.trace_id
        return response


class GatewayMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        authorization = request.headers.get("Authorization")
        caller = decode_token(authorization) if authorization else None
        request.state.caller = caller
        # 将签名上下文写入下游信任的头（即便后续拆服务，此头即契约）
        if caller is not None:
            ctx = encode_caller_context(caller).encode("utf-8")
            request.scope["headers"] = list(request.scope.get("headers", [])) + [(b"x-caller-context", ctx)]
        response = await call_next(request)
        return response
