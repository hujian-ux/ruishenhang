"""飞书身份源：授权码 → access_token → user_info → 平台身份（CallerContext）。

飞书不发 OIDC id_token，故不走 JWKS 验签，改为后端换码后直接取 user_info
验明身份，再由调用方用 security.create_sso_token 自签平台会话令牌（前缀 sso）。

本期仅身份源；连接器抽象（send/notify/回调验签/重试、双区域、token 落库刷新）
待身份源落地后纳入 P1-1 连接器部分。

网络调用沿用既有约定：同步 httpx（与 security._fetch_jwks / router.oidc_exchange 一致），
便于单测用 monkeypatch 注入 httpx.get / httpx.post。
"""
import httpx
from urllib.parse import urlencode

from app.core.config import settings
from app.core.security import create_sso_token
from app.models.caller import CallerContext


def _base() -> str:
    return settings.FEISHU_BASE_URL.rstrip("/")


def exchange_code(code: str, redirect_uri: str = "") -> dict:
    """用授权码换 access_token（飞书 user_access_token）。返回原始 JSON。"""
    resp = httpx.post(
        f"{_base()}/open-apis/authen/v1/oidc/access_token",
        json={
            "grant_type": "authorization_code",
            "code": code,
            "app_id": settings.FEISHU_APP_ID,
            "app_secret": settings.FEISHU_APP_SECRET,
            "redirect_uri": redirect_uri or settings.FEISHU_REDIRECT_URI,
        },
        timeout=settings.OIDC_DISCOVERY_TIMEOUT,
        headers={"Content-Type": "application/json"},
    )
    return resp.json()


def fetch_user_info(access_token: str) -> dict:
    """用 access_token 取飞书用户信息（open_id / name / email / tenant_key 等）。"""
    resp = httpx.get(
        f"{_base()}/open-apis/authen/v1/user_info",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=settings.OIDC_DISCOVERY_TIMEOUT,
    )
    return resp.json()


def authenticate(code: str, redirect_uri: str = "") -> tuple[CallerContext, str]:
    """端到端：换码 → 取 user_info → 解析出 CallerContext 并自签平台 sso 令牌。

    返回 (caller, sso_token)。任何环节失败抛 RuntimeError（由路由层转 401/502）。
    """
    tok = exchange_code(code, redirect_uri)
    data = tok.get("data") or {}
    access_token = data.get("access_token")
    if not access_token:
        raise RuntimeError(f"飞书换码失败：{str(tok)[:300]}")
    info = fetch_user_info(access_token)
    udata = info.get("data") or {}
    # open_id 是用户在企业内的稳定唯一标识；union_id/name 作为兜底，保证非空。
    open_id = udata.get("open_id") or udata.get("union_id") or udata.get("name") or ""
    if not open_id:
        raise RuntimeError(f"飞书 user_info 缺失 open_id：{str(info)[:300]}")
    name = udata.get("name") or open_id
    # 租户：优先飞书 tenant_key（企业唯一标识），回落配置 FEISHU_TENANT_ID，再回落默认。
    tenant = udata.get("tenant_key") or settings.FEISHU_TENANT_ID or settings.DEFAULT_TENANT
    caller = CallerContext(
        userId=f"feishu_{open_id}",
        deptId=settings.FEISHU_DEFAULT_DEPT,
        role=settings.FEISHU_DEFAULT_ROLE,
        tenantId=tenant,
    )
    sso_token = create_sso_token(
        user_id=caller.userId, dept_id=caller.deptId,
        role=caller.role, tenant_id=caller.tenantId,
    )
    return caller, sso_token


def build_authorize_url(state: str = "", redirect_uri: str = "") -> str:
    """构造飞书授权页 URL，供前端「使用飞书登录」按钮跳转（预留前端 authorize）。"""
    params = {
        "app_id": settings.FEISHU_APP_ID,
        "redirect_uri": redirect_uri or settings.FEISHU_REDIRECT_URI,
        "response_type": "code",
        "state": state or "",
    }
    return f"{_base()}/open-apis/authen/v1/authorize?{urlencode(params)}"
