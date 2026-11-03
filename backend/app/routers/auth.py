"""auth 路由：SSO 登录回调（第18章身份入口，对接企业 IM）+ OIDC 健康自检 +
后端代换码（避开浏览器 CORS）+ 可选一键写入 Casdoor（自助接入企业微信/钉钉）。"""
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.config import settings
from app.core.security import (
    _verify_oidc_token,
    create_dev_token,
    oidc_discovery_health,
)
from app.models.schemas import SsoCallbackRequest, SsoCallbackResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/sso/callback", response_model=SsoCallbackResponse)
async def sso_callback(req: SsoCallbackRequest):
    # 骨架：演示用——由 provider/code 换身份。生产对接企微/钉钉/飞书 OAuth。
    # code=dev_admin 演示管理员（王总/平台管理员），其余为默认员工账号
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
            "expires_in": 7200,
            "user": user,
            "device_bound": True,
            "need_stepup": False,
        },
    }


@router.get("/oidc/health")
async def oidc_health():
    """OIDC 配置健康度自检：确认 issuer 可达、JWKS 能拉到（M12 联调排错用）。"""
    return oidc_discovery_health()


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
    return {
        "code": 0,
        "data": {
            "id_token": id_token,
            "expires_in": data.get("expires_in", 3600),
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
