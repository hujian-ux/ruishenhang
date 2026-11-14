"""微信小程序身份源：wx.login code → code2Session → openid → 平台身份（CallerContext）。

微信小程序不发 OIDC id_token，故不走 JWKS 验签，改为后端用登录 code 调微信
code2Session 换出 openid（微信担保该 openid 对应用户），再自签平台会话令牌
（前缀 sso，与飞书同源，生产/收口态放行，区别于本地 dev 后门）。

网络调用沿用同步 httpx（与 feishu_auth / security._fetch_jwks 一致），
便于单测用 monkeypatch 注入 httpx.get。
"""
import httpx

from app.core.config import settings
from app.core.security import create_sso_token
from app.models.caller import CallerContext


def _base() -> str:
    return settings.WX_MINI_BASE_URL.rstrip("/")


def code2session(code: str) -> dict:
    """用小程序登录 code 换微信侧 openid / session_key。返回微信原始 JSON。"""
    resp = httpx.get(
        f"{_base()}/sns/jscode2session",
        params={
            "appid": settings.WX_MINI_APP_ID,
            "secret": settings.WX_MINI_APP_SECRET,
            "js_code": code,
            "grant_type": "authorization_code",
        },
        timeout=settings.OIDC_DISCOVERY_TIMEOUT,
        follow_redirects=True,
    )
    return resp.json()


def authenticate(code: str) -> tuple[CallerContext, str]:
    """端到端：code → code2Session → 解析 openid → 自签平台 sso 令牌。

    返回 (caller, sso_token)。任何环节失败抛 RuntimeError（由路由层转 401/502）。
    session_key 仅在需要解密小程序加密数据（如手机号）时使用，本期只签发登录态，
    故不把它落库/回传前端（避免密钥泄露面）。
    """
    data = code2session(code)
    # 微信成功时返回 openid（无 errcode 或 errcode=0）；失败返回 errcode/errmsg。
    errcode = data.get("errcode")
    if errcode not in (None, 0):
        raise RuntimeError(f"微信 code2Session 失败：{str(data)[:300]}")
    openid = data.get("openid")
    if not openid:
        raise RuntimeError(f"微信 code2Session 未返回 openid：{str(data)[:300]}")
    # unionid 作跨应用统一标识，缺省回落 openid，保证非空。
    name = data.get("unionid") or openid
    tenant = settings.WX_MINI_TENANT_ID or settings.DEFAULT_TENANT
    caller = CallerContext(
        userId=f"wx_{openid}",
        deptId=settings.WX_MINI_DEFAULT_DEPT,
        role=settings.WX_MINI_DEFAULT_ROLE,
        tenantId=tenant,
    )
    sso_token = create_sso_token(
        user_id=caller.userId, dept_id=caller.deptId,
        role=caller.role, tenant_id=caller.tenantId,
    )
    return caller, sso_token
