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
        raise HTTPException(status_code=401, detail="unauthorized: missing caller context")
    return caller
