"""身份与网关安全：X-Caller-Context 的签发/校验（第18章单一过滤点前置）。

前端不可伪造：调用者身份由 Bearer Token 在网关层解析，以 HMAC 签名注入
X-Caller-Context 头；下游服务只信任此上下文，不再各自判权。

注意：本文件中的令牌实现为本地开发 stub（HMAC-SHA256 + base64），
生产须替换为正式 OIDC/JWT 校验（第18章对接企业 IM SSO）。
"""
import base64
import hashlib
import hmac
import json
import time

from app.core.config import settings
from app.models.caller import CallerContext

_SECRET = settings.CALLER_SIGNING_SECRET.encode("utf-8")


def create_dev_token(user_id: str, dept_id: str, role: str, tenant_id: str = "") -> str:
    """生成本地开发用 Bearer Token（明文 JSON + HMAC 签名）。仅用于 dev/test。"""
    tenant_id = tenant_id or settings.DEFAULT_TENANT
    payload = {"u": user_id, "d": dept_id, "r": role, "t": tenant_id, "exp": int(time.time()) + 7200}
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    sig = hmac.new(_SECRET, raw.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"dev {raw}.{sig}"


def _verify_dev_token(token: str) -> CallerContext | None:
    if not token.startswith("dev "):
        return None
    try:
        raw, sig = token[4:].split(".", 1)
        expect = hmac.new(_SECRET, raw.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expect, sig):
            return None
        payload = json.loads(base64.urlsafe_b64decode(raw.encode("ascii")).decode("utf-8"))
        if payload.get("exp", 0) < int(time.time()):
            return None
        return CallerContext(
            userId=payload["u"], deptId=payload["d"],
            role=payload["r"], tenantId=payload.get("t", settings.DEFAULT_TENANT),
        )
    except Exception:
        return None


def decode_token(authorization: str | None) -> CallerContext | None:
    """网关层：由 Authorization 解析调用者。生产替换为正式 SSO/JWT 校验。"""
    if not authorization:
        return None
    return _verify_dev_token(authorization.strip())


def encode_caller_context(caller: CallerContext) -> str:
    """网关层：把调用者上下文签名后写入 X-Caller-Context（下游信任它）。"""
    payload = {"u": caller.userId, "d": caller.deptId, "r": caller.role, "t": caller.tenantId}
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    sig = hmac.new(_SECRET, raw.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def decode_caller_context(header: str | None) -> CallerContext | None:
    """下游服务：校验并解析 X-Caller-Context（前端不可伪造）。"""
    if not header:
        return None
    try:
        raw, sig = header.split(".", 1)
        expect = hmac.new(_SECRET, raw.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expect, sig):
            return None
        p = json.loads(base64.urlsafe_b64decode(raw.encode("ascii")).decode("utf-8"))
        return CallerContext(userId=p["u"], deptId=p["d"], role=p["r"], tenantId=p["t"])
    except Exception:
        return None
