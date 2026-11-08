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
        # C1（安全加固）：网关闭权——客户端不得自报 X-Caller-Context，网关是唯一可信注入方。
        # 先剥离任何进来的 x-caller-context，再按 Authorization 解析后重新签名注入。
        # 业务层（deps.get_caller）只读取并验签此头，不再信任任何客户端传来的身份。
        raw_headers = list(request.scope.get("headers", []))
        stripped = [(k, v) for (k, v) in raw_headers if k.lower() != b"x-caller-context"]
        request.scope["headers"] = stripped
        # 直接读 scope（避免提前触发 request.headers 缓存，否则上面的剥离对下游不可见）。
        authorization = None
        for k, v in stripped:
            if k.lower() == b"authorization":
                authorization = v.decode("latin-1")
                break
        caller = decode_token(authorization) if authorization else None
        request.state.caller = caller
        # 仅当 Authorization 解析成功才注入签名上下文；否则下游既无 caller 也无伪造头。
        if caller is not None:
            ctx = encode_caller_context(caller).encode("utf-8")
            request.scope["headers"] = stripped + [(b"x-caller-context", ctx)]
        response = await call_next(request)
        return response
