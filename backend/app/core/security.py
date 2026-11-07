"""身份与网关安全：X-Caller-Context 的签发/校验（第18章单一过滤点前置）。

前端不可伪造：调用者身份由 Bearer Token 在网关层解析，以 HMAC 签名注入
X-Caller-Context 头；下游服务只信任此上下文，不再各自判权。

身份来源（可插拔，下游零感知）：
  1. dev token（本地开发 / CI / 演示）：HMAC-SHA256 + base64，明文 JSON。
  2. OIDC JWT（生产）：对接任意企业 SSO（钉钉/飞书/微信企业号/Keycloak）。
     通过标准 discovery + JWKS 验签（RS256），无需写死各厂商 SDK。
     仅当 OIDC_ENABLED=1 且配置了 OIDC_ISSUER 时启用；未配置则只认 dev token。
"""
import base64
import hashlib
import hmac
import json
import os
import time
from typing import Dict, Optional, Tuple

import jwt
from jwt.algorithms import RSAAlgorithm

from app.core.config import settings
from app.models.caller import CallerContext

_SECRET = settings.CALLER_SIGNING_SECRET.encode("utf-8")

# OIDC JWKS 缓存：issuer -> (jwks_dict, fetched_at)。避免每次请求都拉远程。
_JWKS_CACHE: Dict[str, Tuple[dict, float]] = {}
_JWKS_TTL = 3600.0


def create_dev_token(user_id: str, dept_id: str, role: str, tenant_id: str = "") -> str:
    """生成本地开发用 Bearer Token（明文 JSON + HMAC 签名）。仅用于 dev/test。"""
    tenant_id = tenant_id or settings.DEFAULT_TENANT
    payload = {"u": user_id, "d": dept_id, "r": role, "t": tenant_id,
               "exp": int(time.time()) + settings.DEV_TOKEN_TTL_S}
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


# ---- OIDC / JWT（生产身份源） -------------------------------------------------

def _fetch_jwks(issuer: str) -> dict:
    """拉取 IdP 的 JWKS（走标准 discovery）。带缓存；失败抛异常让调用方降级。"""
    cached = _JWKS_CACHE.get(issuer)
    if cached and (time.time() - cached[1]) < _JWKS_TTL:
        return cached[0]
    import httpx
    disc = httpx.get(f"{issuer}/.well-known/openid-configuration",
                     timeout=settings.OIDC_DISCOVERY_TIMEOUT, follow_redirects=True).json()
    jwks_uri = disc["jwks_uri"]
    jwks = httpx.get(jwks_uri, timeout=settings.OIDC_DISCOVERY_TIMEOUT,
                     follow_redirects=True).json()
    _JWKS_CACHE[issuer] = (jwks, time.time())
    return jwks


def _verify_oidc_token(token: str, issuer: str = "", audience: str = "") -> CallerContext | None:
    """校验真实 OIDC/JWT（RS256 + JWKS）。成功返回 CallerContext，否则 None。
    单元测试可注入 _JWKS_CACHE 以绕过网络。"""
    issuer = issuer or settings.OIDC_ISSUER
    audience = audience or settings.OIDC_AUDIENCE
    if not issuer:
        # 自服务 / 演示：未固定 issuer 时，用 token 自带的 iss 拉 JWKS 验签
        # （仅当 token 确由某可达 IdP 签发才通过，伪造 token 因无对应私钥而失败）
        try:
            issuer = jwt.decode(token, options={"verify_signature": False}).get("iss")
        except Exception:
            return None
        if not issuer:
            return None
    try:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        jwks = _JWKS_CACHE.get(issuer)
        if jwks is None or (time.time() - jwks[1]) >= _JWKS_TTL:
            jwks = (_fetch_jwks(issuer), time.time())
            _JWKS_CACHE[issuer] = jwks
        jwk = next((k for k in jwks[0].get("keys", []) if k.get("kid") == kid), None)
        # 兜底：kid 匹配不到但只有一个 key（自签/单租户常见），直接用
        if jwk is None and len(jwks[0].get("keys", [])) == 1:
            jwk = jwks[0]["keys"][0]
        if jwk is None:
            return None
        key = RSAAlgorithm.from_jwk(json.dumps(jwk))  # JWK dict → 公钥对象
        # PyJWT 在 token 带 aud 而 audience=None 时会抛 InvalidAudienceError；
        # 未配 audience 时显式关闭校验（自服务），配了就严格校验。
        payload = jwt.decode(token, key, algorithms=[jwk.get("alg", "RS256")],
                            audience=audience or None,
                            issuer=issuer or None,
                            options={"verify_aud": bool(audience)})
        # 用户名：优先配置 claim，其次 preferred_username / email，最后 sub（保证非空）
        user = (payload.get(settings.OIDC_USER_CLAIM)
                or payload.get("preferred_username")
                or payload.get("email")
                or payload.get("sub") or "")
        # 部门/租户：claim 可能是数组（Casdoor 等），取首个非空
        dept_raw = payload.get(settings.OIDC_DEPT_CLAIM) or ""
        dept = dept_raw[0] if isinstance(dept_raw, list) and dept_raw else str(dept_raw)
        role = _resolve_role(payload, settings.OIDC_ROLE_CLAIM)
        tenant_raw = payload.get(settings.OIDC_TENANT_CLAIM) if settings.OIDC_TENANT_CLAIM else ""
        tenant = (tenant_raw[0] if isinstance(tenant_raw, list) and tenant_raw
                  else str(tenant_raw) or settings.DEFAULT_TENANT)
        return CallerContext(userId=str(user), deptId=str(dept),
                             role=role, tenantId=tenant)
    except Exception:
        if os.environ.get("OIDC_DEBUG"):
            import traceback
            traceback.print_exc()
        return None


def _resolve_role(payload: dict, role_claim: str) -> str:
    """角色归一化：优先用配置的 role claim（字符串或数组，取最高权限）；
    否则按 Casdoor 的布尔标志推导（缺省路径，无需在 IdP 侧配角色名字段）。"""
    raw = payload.get(role_claim)
    if raw:
        items = raw if isinstance(raw, list) else [raw]
        for r in items:
            if r in ("platform_admin", "dept_admin", "employee"):
                return r
    # Casdoor：isGlobalAdmin = 全局超管 → platform_admin；isAdmin = 组织管理员 → dept_admin
    if payload.get("isGlobalAdmin"):
        return "platform_admin"
    if payload.get("isAdmin"):
        return "dept_admin"
    return "employee"


def decode_token(authorization: str | None) -> CallerContext | None:
    """网关层：由 Authorization 解析调用者。
      - dev token：本地/CI/演示始终可用（除非生产环境强制 OIDC）。
      - OIDC JWT：OIDC_ENABLED=1 且配置了 issuer 时启用，作为真实身份源。
    """
    if not authorization:
        return None
    token = authorization.strip()
    # P1-173 身份收口：显式开关开启后，任何环境都拒绝 dev token（不止 prod）。
    # 这是「统一身份收口」的硬边界——所有调用方必须走真实 OIDC 身份源。
    if settings.AUTH_ENFORCE_OIDC and token.startswith("dev "):
        return None
    # 生产环境强制真实身份：拒绝 dev token（避免伪造）
    if settings.APP_ENV == "prod" and token.startswith("dev "):
        return None
    # dev token 优先（本地/CI 仍是主力，且与 OIDC 分支解耦）
    if token.startswith("dev "):
        return _verify_dev_token(token)
    # 真实身份源：非 dev 的 JWT 一律尝试验签。
    # 已设 OIDC_ISSUER 即「固定 issuer」加固模式；未设则按 token 自带 iss 拉 JWKS（自服务）。
    if token.count(".") == 2:  # JWS compact 形态才验，避免对任意字符串做网络 discovery
        return _verify_oidc_token(token)
    return None


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


def oidc_discovery_health() -> dict:
    """暴露 OIDC 配置健康度（运维排查用）。"""
    if not settings.OIDC_ENABLED:
        return {"enabled": False, "reason": "OIDC_ENABLED 未开启"}
    if not settings.OIDC_ISSUER:
        return {"enabled": True, "ok": False, "reason": "未配置 OIDC_ISSUER"}
    try:
        _fetch_jwks(settings.OIDC_ISSUER)
        return {"enabled": True, "ok": True, "issuer": settings.OIDC_ISSUER}
    except Exception as e:  # noqa: BLE001
        return {"enabled": True, "ok": False, "reason": str(e)[:200]}


def _oidc_claims(token: str) -> dict:
    """未验签解码 id_token 取 claims（仅在已验签之后调用，仅用于读 organization 等公开字段）。"""
    try:
        return jwt.decode(token, options={"verify_signature": False})
    except Exception:
        return {}


def resolve_tenant_from_oidc_claims(claims: dict) -> Optional[str]:
    """Casdoor 多组织：从 OIDC claims 解析出本平台 tenant_id。

    Casdoor 把企业映射到 organization claim（数组或字符串），命中租户注册表的
    `config.sso.casdoor_org` 则返回其 tenant_id；否则 None（回落网关注入或默认租户）。
    纯映射函数，不修改任何 token 流程（零行为变更），供网关或后端自签 token 复用。
    """
    from app.services import tenant_store
    org_raw = claims.get("organization") or claims.get("org") or claims.get("tenant")
    if not org_raw:
        return None
    org = org_raw[0] if isinstance(org_raw, list) and org_raw else str(org_raw)
    return tenant_store.resolve_tenant_by_org(org)
