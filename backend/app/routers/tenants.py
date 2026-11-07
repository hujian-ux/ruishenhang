"""tenants 路由：M43 多企业开通（platform_admin 仅可操作）。

开通新企业 = 生成 tenant_id + 建空私有语料空间；115 平台资产自动共享、不克隆。
租户级 config 随租户一并写入，供 LLM/品牌/SSO 覆盖回落使用。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.deps import get_caller
from app.models.caller import CallerContext
from app.services import tenant_store

router = APIRouter(prefix="/tenants", tags=["tenant"])


def _require_platform_admin(caller: CallerContext) -> None:
    if caller.role != "platform_admin":
        raise HTTPException(status_code=403, detail="forbidden: 仅平台管理员可管理租户")


class TenantCreateRequest(BaseModel):
    name: str
    config: dict = {}


@router.post("")
async def create_tenant(req: TenantCreateRequest, caller: CallerContext = Depends(get_caller)):
    _require_platform_admin(caller)
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="租户名称必填")
    t = tenant_store.create_tenant(name, req.config or {})
    return {"code": 0, "data": t}


@router.get("")
async def list_tenants(caller: CallerContext = Depends(get_caller)):
    _require_platform_admin(caller)
    rows = tenant_store.list_tenants()
    return {"code": 0, "data": {"tenants": rows, "count": len(rows)}}


@router.get("/{tenant_id}")
async def get_tenant(tenant_id: str, caller: CallerContext = Depends(get_caller)):
    _require_platform_admin(caller)
    t = tenant_store.get_tenant(tenant_id)
    if not t:
        raise HTTPException(status_code=404, detail=f"租户不存在：{tenant_id}")
    return {"code": 0, "data": t}


@router.post("/{tenant_id}/activate")
async def activate_tenant(tenant_id: str, caller: CallerContext = Depends(get_caller)):
    _require_platform_admin(caller)
    t = tenant_store.activate(tenant_id)
    if not t:
        raise HTTPException(status_code=404, detail=f"租户不存在：{tenant_id}")
    return {"code": 0, "data": t}


class TenantBrandingRequest(BaseModel):
    """租户品牌与 SSO 组织绑定（platform_admin 配置）。

    - brand：登录页 / 界面主色、Logo、名称，供前端按租户渲染。
    - casdoor_org：该企业在本平台对应的 Casdoor organization 名（多组织映射键）。
    """
    name: str = ""
    logo_url: str = ""
    primary_color: str = ""
    casdoor_org: str = ""


@router.get("/{tenant_id}/branding")
async def get_tenant_branding(tenant_id: str, caller: CallerContext = Depends(get_caller)):
    _require_platform_admin(caller)
    if not tenant_store.get_tenant(tenant_id):
        raise HTTPException(status_code=404, detail=f"租户不存在：{tenant_id}")
    return {"code": 0, "data": {
        "tenant_id": tenant_id,
        "branding": tenant_store.get_branding(tenant_id),
    }}


@router.put("/{tenant_id}/branding")
async def set_tenant_branding(req: TenantBrandingRequest, tenant_id: str,
                              caller: CallerContext = Depends(get_caller)):
    _require_platform_admin(caller)
    t = tenant_store.get_tenant(tenant_id)
    if not t:
        raise HTTPException(status_code=404, detail=f"租户不存在：{tenant_id}")
    cfg = dict(t.get("config") or {})
    brand = dict(cfg.get("brand") or {})
    if req.name:
        brand["name"] = req.name
    if req.logo_url:
        brand["logo_url"] = req.logo_url
    if req.primary_color:
        brand["primary_color"] = req.primary_color
    cfg["brand"] = brand
    sso = dict(cfg.get("sso") or {})
    if req.casdoor_org:
        sso["casdoor_org"] = req.casdoor_org
    cfg["sso"] = sso
    tenant_store.update_config(tenant_id, cfg)
    return {"code": 0, "data": tenant_store.get_tenant(tenant_id)}
