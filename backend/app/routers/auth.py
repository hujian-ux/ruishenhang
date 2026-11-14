"""auth 路由：SSO 登录回调（第18章身份入口，对接企业 IM）+ OIDC 健康自检 +
后端代换码（避开浏览器 CORS）+ 可选一键写入 Casdoor（自助接入企业微信/钉钉）。"""
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from app.core.config import settings
from app.core.security import (
    _oidc_claims,
    _verify_oidc_token,
    create_dev_token,
    oidc_discovery_health,
    resolve_tenant_from_oidc_claims,
)
from app.deps import get_caller
from app.models.caller import CallerContext
from app.models.schemas import SsoCallbackRequest, SsoCallbackResponse
from app.services import feishu_auth, tenant_store, wx_auth

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/sso/callback", response_model=SsoCallbackResponse)
async def sso_callback(req: SsoCallbackRequest):
    # P1-173 身份收口路径：前端已拿到 IdP 下发的 id_token，后端只验签、不落 dev token。
    # 验签通过即把 id_token 原样回传作 Bearer（网关层走 OIDC 验签），实现「统一身份收口」。
    if req.id_token:
        caller = _verify_oidc_token(req.id_token)
        if caller is None:
            raise HTTPException(401, "id_token 验签失败（issuer / 签名 / 过期 / audience 不匹配）")
        # 多组织：尽力从 organization claim 解析租户（命中租户表才返回，否则 None 不误导）
        tid = resolve_tenant_from_oidc_claims(_oidc_claims(req.id_token))
        return {
            "code": 0,
            "data": {
                "token": req.id_token,
                "expires_in": settings.DEV_TOKEN_TTL_S,
                "tenant_id": tid,
                "user": {"userId": caller.userId, "name": caller.userId,
                         "deptId": caller.deptId, "role": caller.role},
                "device_bound": False,
                "need_stepup": False,
            },
        }

    # 收口态或生产环境：禁止 dev_admin 捷径，必须走真实 SSO（携带 id_token）。
    # C3（安全加固）：生产环境即便未开 AUTH_ENFORCE_OIDC，也绝不允许 dev 后门换平台管理员令牌。
    if settings.AUTH_ENFORCE_OIDC or settings.APP_ENV == "prod":
        raise HTTPException(
            403,
            "身份收口已开启或处于生产环境：禁止 dev 捷径，请走真实企业 SSO（携带 id_token）",
        )

    # 演示/开发路径（默认）：由 code 换演示身份。生产应关闭 OIDC 之外的此捷径。
    if req.code == "dev_admin":
        token = create_dev_token(user_id="u_9000", dept_id="d_it", role="platform_admin")
        user = {"userId": "u_9000", "name": "王总", "deptId": "d_it", "role": "platform_admin"}
    else:
        # 默认演示账号：张三 / 销售部 / 普通员工
        token = create_dev_token(user_id="u_1024", dept_id="d_sales", role="employee")
        user = {"userId": "u_1024", "name": "张三", "deptId": "d_sales", "role": "employee"}
    return {
        "code": 0,
        "data": {
            "token": token,
            "expires_in": settings.DEV_TOKEN_TTL_S,
            "user": user,
            "device_bound": True,
            "need_stepup": False,
        },
    }


@router.get("/oidc/health")
async def oidc_health():
    """OIDC 配置健康度自检：确认 issuer 可达、JWKS 能拉到（M12 联调排错用）。"""
    return oidc_discovery_health()


def _mask(v: str) -> str:
    if not v:
        return ""
    if len(v) <= 8:
        return "*" * len(v)
    return v[:4] + "*" * max(4, len(v) - 8) + v[-4:]


@router.get("/settings")
async def auth_settings():
    """公开（无需登录）：暴露 OIDC 收口状态与脱敏 issuer/client_id，
    供登录页决定是否隐藏演示入口、以及「身份与集成」页展示状态。不含任何密钥。
    附带 platform_branding 供登录页渲染平台默认品牌（租户级品牌需登录后取 /auth/branding）。"""
    issuer = settings.OIDC_ISSUER
    client_id = settings.OIDC_CLIENT_ID or settings.OIDC_AUDIENCE
    return {
        "code": 0,
        "data": {
            "oidc_enabled": settings.OIDC_ENABLED,
            "enforce_oidc": settings.AUTH_ENFORCE_OIDC,
            "feishu_enabled": settings.FEISHU_ENABLED
            and bool(settings.FEISHU_APP_ID and settings.FEISHU_APP_SECRET),
            "wx_miniapp_enabled": settings.WX_MINI_LOGIN_ENABLED
            and bool(settings.WX_MINI_APP_ID and settings.WX_MINI_APP_SECRET),
            "issuer_masked": _mask(issuer) if issuer else "",
            "client_id_masked": _mask(client_id) if client_id else "",
            "platform_branding": tenant_store.get_branding(None),
            "note": "AUTH_ENFORCE_OIDC=1 时禁止 dev 捷径，必须走真实企业 SSO。",
        },
    }


# ── P1-1 飞书身份源（授权码 → access_token → user_info → 平台自签 sso 令牌）──────
class FeishuCallbackRequest(BaseModel):
    code: str
    state: Optional[str] = None
    redirect_uri: str = ""


@router.get("/feishu/authorize")
async def feishu_authorize(state: str = "", redirect_uri: str = ""):
    """前端「使用飞书登录」按钮预留：返回飞书授权页 URL（无需登录）。

    飞书不发 OIDC id_token，故授权码流由前端跳转到飞书授权页、回跳本地后
    把 code 交给 POST /auth/feishu/callback 后端换身份。本端点只负责吐 URL。
    redirect_uri 由前端传入当前站点回调地址（须与飞书应用登记的回跳一致）。
    """
    if not settings.FEISHU_ENABLED or not (settings.FEISHU_APP_ID and settings.FEISHU_APP_SECRET):
        raise HTTPException(503, "飞书身份源未启用（FEISHU_ENABLED=1 且配置 APP_ID/APP_SECRET）")
    return {
        "code": 0,
        "data": {
            "enabled": True,
            "authorize_url": feishu_auth.build_authorize_url(state=state, redirect_uri=redirect_uri),
            "redirect_uri": redirect_uri or settings.FEISHU_REDIRECT_URI,
        },
    }


@router.post("/feishu/callback", response_model=SsoCallbackResponse)
async def feishu_callback(req: FeishuCallbackRequest):
    """飞书授权码回调：换码 → user_info 验明身份 → 平台自签 sso 会话令牌。

    飞书不发 OIDC id_token，故不走 JWKS 验签；身份真实性由「后端持有 app_secret
    换码 + 取 user_info」保证（飞书担保该 access_token 对应用户）。
    """
    if not settings.FEISHU_ENABLED or not (settings.FEISHU_APP_ID and settings.FEISHU_APP_SECRET):
        raise HTTPException(503, "飞书身份源未启用（FEISHU_ENABLED=1 且配置 APP_ID/APP_SECRET）")
    try:
        caller, sso_token = feishu_auth.authenticate(req.code, req.redirect_uri)
    except RuntimeError as e:
        raise HTTPException(502, str(e)) from e
    return {
        "code": 0,
        "data": {
            "token": sso_token,
            "expires_in": settings.DEV_TOKEN_TTL_S,
            "tenant_id": caller.tenantId,
            "user": {"userId": caller.userId, "name": caller.userId,
                     "deptId": caller.deptId, "role": caller.role},
            "device_bound": False,
            "need_stepup": False,
            "provider": "feishu",
        },
    }


# ── 微信小程序身份源（wx.login code → code2Session → 平台自签 sso 令牌）──────
class WxLoginRequest(BaseModel):
    code: str
    # 可选：小程序可将加密的用户信息（encrypted_data/iv）一并上送，后端用
    # session_key 解密。本期仅签发登录态，不强制解密，故为可选占位（待接入）。
    encrypted_data: Optional[str] = ""
    iv: Optional[str] = ""


@router.post("/wx/login", response_model=SsoCallbackResponse)
async def wx_login(req: WxLoginRequest):
    """微信小程序登录：code → code2Session → 平台自签 sso 会话令牌。

    与飞书同源：微信不发 OIDC id_token，由后端换码验明 openid 后自签 sso token。
    未启用或 AppID/AppSecret 缺失时返回 503（避免误放行）。
    """
    if not settings.WX_MINI_LOGIN_ENABLED or not (settings.WX_MINI_APP_ID and settings.WX_MINI_APP_SECRET):
        raise HTTPException(503, "微信小程序登录未启用（WX_MINI_LOGIN_ENABLED=1 且配置 APP_ID/APP_SECRET）")
    try:
        caller, sso_token = wx_auth.authenticate(req.code)
    except RuntimeError as e:
        raise HTTPException(502, str(e)) from e
    return {
        "code": 0,
        "data": {
            "token": sso_token,
            "expires_in": settings.DEV_TOKEN_TTL_S,
            "tenant_id": caller.tenantId,
            "user": {"userId": caller.userId, "name": caller.userId,
                     "deptId": caller.deptId, "role": caller.role},
            "device_bound": False,
            "need_stepup": False,
            "provider": "wechat",
        },
    }


@router.get("/branding")
async def current_branding(caller: CallerContext = Depends(get_caller)):
    """登录后取当前租户品牌（含平台默认回落）。供前端按租户渲染登录后界面。"""
    return {
        "code": 0,
        "data": {
            "tenant_id": caller.tenantId,
            "branding": tenant_store.get_branding(caller.tenantId),
        },
    }


# ── 后端代前端换码（标准 Authorization Code + PKCE）──────────────────────────
# 放在后端做的好处：Casdoor token 端点无需开放浏览器 CORS；client_secret（若有）不出后端。
class OidcExchangeRequest(BaseModel):
    issuer: str = ""
    client_id: str = ""
    redirect_uri: str = ""
    code: str
    code_verifier: str = ""


@router.post("/oidc/exchange")
async def oidc_exchange(req: OidcExchangeRequest):
    """用授权码换 id_token。前端拿到后作为 Authorization 头，由网关验签。"""
    issuer = (req.issuer or settings.OIDC_ISSUER).rstrip("/")
    client_id = req.client_id or settings.OIDC_CLIENT_ID or settings.OIDC_AUDIENCE
    redirect_uri = req.redirect_uri or settings.OIDC_REDIRECT_URI
    if not issuer or not client_id:
        raise HTTPException(400, "未配置 OIDC issuer / client_id（Setup 页填写或设 OIDC_ISSUER/OIDC_CLIENT_ID）")
    try:
        disc = httpx.get(
            f"{issuer}/.well-known/openid-configuration",
            timeout=settings.OIDC_DISCOVERY_TIMEOUT, follow_redirects=True,
        ).json()
        token_endpoint = disc.get("token_endpoint") or f"{issuer}/api/login/oauth/access_token"
        resp = httpx.post(
            token_endpoint,
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": req.code,
                "redirect_uri": redirect_uri,
                "code_verifier": req.code_verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=settings.OIDC_DISCOVERY_TIMEOUT, follow_redirects=True,
        )
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"OIDC 换码失败：{e}") from e

    id_token = data.get("id_token")
    if not id_token:
        raise HTTPException(400, f"IdP 未返回 id_token：{str(data)[:300]}")
    # 轻量验签（pin 到本次 issuer/audience，确认非伪造且未过期）
    caller = _verify_oidc_token(id_token, issuer=issuer, audience=client_id)
    if caller is None:
        raise HTTPException(401, "id_token 验签失败（issuer / 签名 / 过期 / audience 不匹配）")
    # 多组织：尽力从 organization claim 解析租户（命中租户表才返回，否则 None 不误导）
    tid = resolve_tenant_from_oidc_claims(_oidc_claims(id_token))
    return {
        "code": 0,
        "data": {
            "id_token": id_token,
            "expires_in": data.get("expires_in", 3600),
            "tenant_id": tid,
            "user": {
                "userId": caller.userId,
                "name": caller.userId,
                "deptId": caller.deptId,
                "role": caller.role,
            },
        },
    }


# ── 可选（实验性）：把企业微信/钉钉资料一键写入 Casdoor ──────────────────────
# 需要 Casdoor 管理员账号。失败不影响手动在 Casdoor 后台填写（前端始终给出手动清单）。
class ProviderInput(BaseModel):
    corp_id: str = ""      # 企业微信：CorpID；钉钉：AppKey
    secret: str = ""       # 企业微信：CorpSecret；钉钉：AppSecret
    agent_id: str = ""     # 企业微信专用 AgentId


class OidcProvisionRequest(BaseModel):
    issuer: str
    admin_user: str
    admin_password: str
    owner: str = "built-in"
    app_name: str = "app-built-in"
    wecom: ProviderInput = ProviderInput()
    dingtalk: ProviderInput = ProviderInput()


@router.post("/oidc/provision")
async def oidc_provision(req: OidcProvisionRequest):
    """实验性自动化：登录 Casdoor 管理员 → 建 IdP → 关联到应用。返回逐步结果便于排错。"""
    base = req.issuer.rstrip("/")
    steps: list[str] = []
    try:
        login = httpx.post(
            f"{base}/api/login",
            json={"username": req.admin_user, "password": req.admin_password},
            timeout=settings.OIDC_DISCOVERY_TIMEOUT,
        ).json()
        adm_token = (login.get("data") or {}).get("token")
        if not adm_token:
            return {"code": 1, "msg": "Casdoor 管理员登录失败", "detail": login, "steps": steps}
        hdr = {"Authorization": f"Bearer {adm_token}"}
        steps.append("admin_login ok")

        created: list[dict] = []
        specs = [
            ("wecom-rsp", "企业微信", "WeCom", req.wecom, True),
            ("dingtalk-rsp", "钉钉", "DingTalk", req.dingtalk, False),
        ]
        for pid, dname, ptype, cred, is_wecom in specs:
            if not (cred.corp_id and cred.secret):
                continue
            body = {
                "id": pid, "owner": req.owner, "name": pid, "displayName": dname,
                "category": "OAuth", "type": ptype,
                "clientId": cred.corp_id, "clientSecret": cred.secret,
            }
            if is_wecom:
                body["customInfo"] = {"agentId": cred.agent_id, "subType": "internal",
                                      "method": "normal", "scope": "snsapi_userinfo"}
            else:
                body["customInfo"] = {"subType": "internal"}
            rp = httpx.post(f"{base}/api/add-provider", json=body, headers=hdr,
                            timeout=settings.OIDC_DISCOVERY_TIMEOUT)
            created.append({"id": pid, "type": ptype, "status": rp.status_code,
                            "resp": _safe_json(rp)})
            steps.append(f"{ptype} add-provider {rp.status_code}")

        if created:
            ga = httpx.get(f"{base}/api/get-application/{req.owner}/{req.app_name}",
                           headers=hdr, timeout=settings.OIDC_DISCOVERY_TIMEOUT)
            app = (_safe_json(ga) or {}).get("data") or {}
            providers = app.get("providers") or []
            have = {p.get("name") for p in providers}
            for c in created:
                if c["id"] not in have:
                    providers.append({"name": c["id"], "displayName": c["type"], "type": c["type"]})
            app["providers"] = providers
            ua = httpx.post(f"{base}/api/update-application", json=app, headers=hdr,
                            timeout=settings.OIDC_DISCOVERY_TIMEOUT)
            steps.append(f"update-application {ua.status_code}")

        return {"code": 0, "msg": "已尝试写入 Casdoor", "steps": steps, "providers": created}
    except Exception as e:  # noqa: BLE001
        return {"code": 1, "msg": f"写入异常：{e}", "steps": steps}


def _safe_json(resp) -> dict | None:
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return {"status_code": getattr(resp, "status_code", None), "text": getattr(resp, "text", "")[:300]}
