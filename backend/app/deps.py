"""FastAPI 依赖：从网关注入的 X-Caller-Context 取出调用者（不可伪造）。"""
from fastapi import Header, HTTPException, Request

from app.core.security import decode_caller_context
from app.models.caller import CallerContext


def get_caller(request: Request, x_caller_context: str | None = Header(default=None)) -> CallerContext:
    """下游信任网关注入的上下文；缺失或签名失败即 401。"""
    # 优先取中间件已解析并存入 request.state 的 caller
    caller = getattr(request.state, "caller", None)
    if caller is None and x_caller_context:
        caller = decode_caller_context(x_caller_context)
    if caller is None:
        # 401 分两种原因，排障时差别很大：前端没带 token，还是 token 过期/验签失败。
        # 之前统一报 missing caller context，看到这行根本判断不出该重新登录还是查请求头。
        has_token = bool(request.headers.get("Authorization"))
        detail = ("unauthorized: invalid or expired token" if has_token
                  else "unauthorized: missing token (未携带 Authorization)")
        raise HTTPException(status_code=401, detail=detail)
    return caller


def get_trace_id(request: Request) -> str:
    """M13：取请求级 trace_id（AuditTraceMiddleware 注入），用于审计串联。"""
    return getattr(request.state, "trace_id", "no-trace")
