"""网关中间件：单一入口解析身份并注入 X-Caller-Context（第18章过滤点前置）。

单体阶段，网关逻辑即本中间件；拆容器后，本逻辑整体迁到独立 api-gateway 服务，
下游契约（X-Caller-Context 头）保持不变。
"""
import threading
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.config import settings
from app.core.security import decode_token, encode_caller_context


class _IpTokenBucket:
    """按客户端 IP 的令牌桶；rps<=0 时全放行（默认关）。限额实时读 settings，便于运维热配。"""

    def __init__(self, rps_attr: str, burst_attr: str):
        self.rps_attr = rps_attr
        self.burst_attr = burst_attr
        self._buckets: dict = {}
        self._lock = threading.Lock()

    def allow(self, ip: str) -> bool:
        rps = float(getattr(settings, self.rps_attr, 0) or 0)
        if rps <= 0:
            return True
        burst = float(getattr(settings, self.burst_attr, 0) or rps)
        now = time.time()
        with self._lock:
            b = self._buckets.get(ip)
            if b is None:
                b = [burst, now]
                self._buckets[ip] = b
            elapsed = now - b[1]
            b[0] = min(burst, b[0] + elapsed * rps)
            b[1] = now
            if b[0] >= 1.0:
                b[0] -= 1.0
                return True
            return False

    def prune(self) -> None:
        now = time.time()
        with self._lock:
            expired = [k for k, v in self._buckets.items() if now - v[1] > 120]
            for k in expired:
                self._buckets.pop(k, None)


# 通用桶 + 认证/集成端点更严格桶（A 层安全收尾）
_GENERAL_BUCKET = _IpTokenBucket("HTTP_RATE_LIMIT_RPS", "HTTP_RATE_LIMIT_BURST")
_AUTH_BUCKET = _IpTokenBucket("HTTP_AUTH_RATE_LIMIT_RPS", "HTTP_AUTH_RATE_LIMIT_BURST")


def _client_ip(request) -> str:
    fwd = request.headers.get("X-Forwarded-For")
    if fwd:
        return fwd.split(",")[0].strip()
    return (request.client.host if request.client else "unknown")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """A 层安全收尾：HTTP 层按 IP 令牌桶限流，保护认证/Webhook 端点免暴力与资源耗尽。

    默认全关（rps<=0）；生产经 _fail_if_insecure_for_prod 强制开启。
    OPTIONS 预检、/ops/metrics、/ops/health 豁免（监控抓取与跨域预检不被限）。
    """

    _last_prune = 0.0

    async def dispatch(self, request, call_next):
        path = request.url.path
        if request.method == "OPTIONS" or path in ("/ops/metrics", "/ops/health", "/health"):
            return await call_next(request)
        ip = _client_ip(request)
        bucket = _AUTH_BUCKET if path.startswith(("/auth", "/integrations")) else _GENERAL_BUCKET
        now = time.time()
        if now - RateLimitMiddleware._last_prune > 60:
            RateLimitMiddleware._last_prune = now
            bucket.prune()
        if not bucket.allow(ip):
            return JSONResponse(
                status_code=429,
                content={"code": 1, "msg": "请求过于频繁，请稍后再试"},
                headers={"Retry-After": "1"},
            )
        return await call_next(request)


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
