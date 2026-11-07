"""tenants 路由：M43 多企业开通（platform_admin 仅可操作）。

开通新企业 = 生成 tenant_id + 建空私有语料空间；115 平台资产自动共享、不克隆。
租户级 config 随租户一并写入，供 LLM/品牌/SSO 覆盖回落使用。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.deps import get_caller
from app.models.caller import CallerContext
from app.services import tenant_store

# 审批多级模板允许的审批人角色（employee 不能做审批节点，故不在此列）
_APPROVER_ROLES = ("dept_admin", "platform_admin")

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


class ApprovalStageModel(BaseModel):
    """多级审批模板的一级节点。"""
    name: str = Field(..., min_length=1, max_length=40, description="节点名称，如「部门经理」")
    approver_role: str = Field(..., description="该级审批人角色：dept_admin | platform_admin")


class ApprovalConfigRequest(BaseModel):
    """租户级审批多级模板（管理员在租户配置页可视化编辑后提交）。"""
    stages: list[ApprovalStageModel] = Field(default_factory=list,
        description="审批链路；空数组=退化为单级默认（兼容现有行为）")
    allow_global_scope: bool = Field(default=False,
        description="是否允许该租户提交「全局知识域」智能体（需平台管理员特别批准）")
    notifiers: dict[str, list[str]] = Field(default_factory=dict,
        description=("审批待办自动推的接收人（IM user_id 列表）。键支持："
                     "角色 `dept_admin`/`platform_admin`、部门 `dept:<dept_id>`、通配 `*`。"
                     "未配置 → 新单/流转不自动回推。"))


@router.put("/{tenant_id}/approval-config")
async def set_tenant_approval_config(req: ApprovalConfigRequest, tenant_id: str,
                                    caller: CallerContext = Depends(get_caller)):
    """配置租户审批多级模板（platform_admin）。空 stages 退化为单级默认。

    校验：每个 stage 的 approver_role 必须是 dept_admin / platform_admin。
    notifiers 写入后，开新单/多级流转会自动回推 OA 卡片给对应审批人（M49）。
    """
    _require_platform_admin(caller)
    t = tenant_store.get_tenant(tenant_id)
    if not t:
        raise HTTPException(status_code=404, detail=f"租户不存在：{tenant_id}")
    for st in req.stages:
        if st.approver_role not in _APPROVER_ROLES:
            raise HTTPException(
                status_code=400,
                detail=f"审批人角色非法：{st.approver_role}（仅支持 {_APPROVER_ROLES}）")
    cfg = dict(t.get("config") or {})
    cfg["approval"] = {
        "stages": [{"name": s.name, "approver_role": s.approver_role} for s in req.stages],
        "allow_global_scope": req.allow_global_scope,
        "notifiers": {k: list(v) for k, v in (req.notifiers or {}).items()},
    }
    tenant_store.update_config(tenant_id, cfg)
    return {"code": 0, "data": tenant_store.get_tenant(tenant_id)}


class CopyApprovalConfigRequest(BaseModel):
    """复制某租户审批模板到目标租户（快捷动作）。"""
    target_tenant_id: str = Field(..., description="接收模板的目标租户 tenant_id")


@router.post("/{tenant_id}/copy-approval-config")
async def copy_tenant_approval_config(req: CopyApprovalConfigRequest, tenant_id: str,
                                     caller: CallerContext = Depends(get_caller)):
    """把源租户的审批多级模板（stages + allow_global_scope + notifiers）整体复制到目标租户。

    platform_admin 专属。目标租户原审批配置被整体覆盖；其余 config（brand/llm/sso/im）不动。
    """
    _require_platform_admin(caller)
    src = tenant_store.get_tenant(tenant_id)
    tgt = tenant_store.get_tenant(req.target_tenant_id)
    if not src:
        raise HTTPException(status_code=404, detail=f"源租户不存在：{tenant_id}")
    if not tgt:
        raise HTTPException(status_code=404, detail=f"目标租户不存在：{req.target_tenant_id}")
    if src["tenant_id"] == tgt["tenant_id"]:
        raise HTTPException(status_code=400, detail="源租户与目标租户不能相同")
    src_ap = (src.get("config") or {}).get("approval") or {}
    for st in (src_ap.get("stages") or []):
        if st.get("approver_role") not in _APPROVER_ROLES:
            raise HTTPException(
                status_code=400,
                detail=f"源模板审批人角色非法：{st.get('approver_role')}（仅支持 {_APPROVER_ROLES}）")
    tgt_cfg = dict(tgt.get("config") or {})
    tgt_cfg["approval"] = {
        "stages": [dict(s) for s in (src_ap.get("stages") or [])],
        "allow_global_scope": bool(src_ap.get("allow_global_scope")),
        "notifiers": {k: list(v) for k, v in (src_ap.get("notifiers") or {}).items()},
    }
    tenant_store.update_config(tgt["tenant_id"], tgt_cfg)
    return {"code": 0, "data": {
        "source_tenant_id": src["tenant_id"],
        "source_tenant_name": src.get("name"),
        "target_tenant_id": tgt["tenant_id"],
        "target_tenant_name": tgt.get("name"),
        "stages_copied": len(src_ap.get("stages") or []),
        "notifiers_copied": len(src_ap.get("notifiers") or {}),
    }}
