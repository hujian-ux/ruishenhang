"""agents 路由：注册智能体（第35章选型结论落库，红线：缺选型不可发布）+ M15 技能包融合。

M15 相比 M0–M14 的增量：
- 落库改走 `agent_store`（内存 + PG 写穿），重启不再丢；保留描述/提示词/分类等技能包字段。
- `POST /agents/import` 批量导入技能包，默认合并成**一张批次审批单**（115 个包逐个开单不可治理）。
- `GET  /agents/catalog` 按「分类 → 子职能」分组，供前端树形导航。
- 红线不变：未 published 不可调用；批量导入同样要过审批。
"""
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.config import settings
from app.deps import get_caller
from app.models.caller import CallerContext
from app.models.schemas import (
    AgentImportRequest,
    AgentRegisterRequest,
    AgentRegisterResponse,
)
from app.selection import framework
from app.services import agent_store, approval_store, im_gateway, role_map, skill_pack

router = APIRouter(prefix="/agents", tags=["agent"])

VALID_ADAPTERS = set(framework.ADAPTER_META.keys())
VALID_SCOPES = {"personal", "owner", "department", "global"}


def org_role_for(caller: CallerContext) -> str:
    """第31章 / M22 遗留：岗位由组织（deptId）下发，而不是员工浏览器里自选。

    组织侧真实映射到位后改 `ORG_DEPT_ROLE_MAP` 即可；未配置的部门返回空串，
    此时保持原有「先自选一次」的体验，不把没数据的部门全拦在门外。
    """
    pairs = (p.strip() for p in settings.ORG_DEPT_ROLE_MAP.split(",") if ":" in p)
    mapping = dict(p.split(":", 1) for p in pairs)
    return mapping.get(caller.deptId, "")


def _effective_role(requested: str, caller: CallerContext) -> str:
    if not settings.ROLE_FROM_ORG or not requested or caller.role == "platform_admin":
        return requested
    org = org_role_for(caller)
    if not org:
        return requested
    resolved = (role_map.resolve(requested) or {}).get("id", requested)
    if resolved == org:
        return org
    raise HTTPException(
        status_code=403,
        detail=(f"岗位由组织分配：部门 {caller.deptId} 对应岗位 {org}，"
                f"不接受自选 {requested}（第31章组织身份收口）"),
    )


@router.get("/my-role")
async def my_role(caller: CallerContext = Depends(get_caller)):
    """前端启动时取一次：ROLE_FROM_ORG=1 时应以这里返回的岗位为准。"""
    return {"code": 0, "data": {
        "role": org_role_for(caller), "dept_id": caller.deptId,
        "source": "org" if settings.ROLE_FROM_ORG else "self",
        "enforced": bool(settings.ROLE_FROM_ORG and org_role_for(caller)),
    }}

# 兼容旧引用（approval.py / invoke.py 直接读该 dict）；真实写入统一走 agent_store.save
_AGENTS = agent_store._AGENTS


def _new_agent_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]", "", name.lower())
    return "ag_" + (slug[:16] or uuid.uuid4().hex[:8])


def _validate(adapter_type: str, knowledge_scope: str) -> None:
    if adapter_type not in VALID_ADAPTERS:
        raise HTTPException(
            status_code=400,
            detail=f"非法 adapter_type：{adapter_type}，应为 {sorted(VALID_ADAPTERS)}",
        )
    if knowledge_scope not in VALID_SCOPES:
        raise HTTPException(status_code=400, detail=f"非法 knowledge_scope：{knowledge_scope}")


@router.post("", response_model=AgentRegisterResponse)
async def register_agent(req: AgentRegisterRequest, caller: CallerContext = Depends(get_caller)):
    # 红线：adapter_type 来自选型向导，缺失即拒绝发布
    if not req.adapter_type:
        raise HTTPException(status_code=400, detail="缺选型不可发布：adapter_type 必填（第35章）")
    _validate(req.adapter_type, req.knowledge_scope)

    agent_id = req.agent_id or _new_agent_id(req.name)
    sel = framework.ADAPTER_META.get(req.adapter_type, {})
    agent_store.save({
        "agent_id": agent_id,
        "name": req.name,
        "adapter_type": req.adapter_type,
        "knowledge_scope": req.knowledge_scope,
        "model_id": req.model_id,
        "registered_by": caller.userId,
        "dept_id": caller.deptId,
        "tenant_id": caller.tenantId,  # M43：租户自建 → 仅本租户可见
        "status": "pending_review",
        # M15：技能包字段（旧调用方不传则为 None，行为与旧版一致）
        "description": req.description,
        "system_prompt": req.system_prompt,
        "category": req.category,
        "category_cn": req.category_cn,
        "sub_function": req.sub_function,
        "tags": req.tags or [],
        "source_path": req.source_path,
        "inputs_contract": req.inputs_contract,
        "outputs_contract": req.outputs_contract,
    })
    # M5：注册即开审批单（第35章红线：评审通过才可发布/调用）
    ticket = approval_store.create_ticket(agent_id, caller.userId, caller.deptId, caller.tenantId)
    # M49：新单落入即自动回推 OA 卡片给当前级审批人（best-effort，非阻塞）
    im_gateway.auto_notify_pending_async(caller.tenantId, ticket["approval_id"])
    return {
        "code": 0,
        "data": {
            "agent_id": agent_id,
            "status": "pending_review",  # 进入待发布评审（第35章红线）
            "approval_id": ticket["approval_id"],
            "adapter_type": req.adapter_type,
            "registered_by": caller.userId,
            "selection": {"recommended": req.adapter_type, "landing_chapters": sel.get("chapters", "")},
        },
    }


@router.post("/import")
async def import_agents(req: AgentImportRequest, caller: CallerContext = Depends(get_caller)):
    """批量导入技能包（M15）。

    默认 `batch_approval=True`：全部进 pending_review 并合并成一张批次单，
    审批一次整批发布。审批台不会被 115 张单淹没。
    """
    if not req.items:
        raise HTTPException(status_code=400, detail="items 不能为空")
    created, skipped = [], []
    for it in req.items:
        if it.adapter_type not in VALID_ADAPTERS or it.knowledge_scope not in VALID_SCOPES:
            skipped.append({"name": it.name, "reason": "adapter_type/knowledge_scope 非法"})
            continue
        # 幂等：同一 source_path 重复导入不生成第二个智能体
        agent_id = _new_agent_id(it.name)
        exist = agent_store.get(agent_id)
        if exist and exist.get("source_path") == it.source_path:
            skipped.append({"name": it.name, "reason": "已存在（同来源）", "agent_id": agent_id})
            continue
        agent_store.save({
            "agent_id": agent_id,
            "name": it.name,
            "adapter_type": it.adapter_type,
            "knowledge_scope": it.knowledge_scope,
            "model_id": it.model_id,
            "registered_by": caller.userId,
            "dept_id": caller.deptId,
            "tenant_id": caller.tenantId,  # H2（安全加固）：导入即归属当前租户，避免被越权当成平台级（全租户可见）
            "status": "pending_review",
            "description": it.description,
            "system_prompt": it.system_prompt,
            "category": it.category,
            "category_cn": it.category_cn,
            "sub_function": it.sub_function,
            "tags": it.tags or [],
            "source_path": it.source_path,
            "inputs_contract": it.inputs_contract,
            "outputs_contract": it.outputs_contract,
        })
        created.append({"agent_id": agent_id, "name": it.name})

    if not created:
        return {"code": 0, "data": {"created": 0, "skipped": skipped,
                                    "message": "无新增（全部已存在或非法）"}}

    ids = [c["agent_id"] for c in created]
    if req.batch_approval:
        ticket = approval_store.create_batch_ticket(ids, caller.userId, caller.deptId, req.batch_name, tenant_id=caller.tenantId)
        approvals = [{"approval_id": ticket["approval_id"], "agent_ids": ids, "batch": True}]
    else:
        approvals = [{"approval_id": approval_store.create_ticket(i, caller.userId, caller.deptId, caller.tenantId)["approval_id"],
                      "agent_ids": [i], "batch": False} for i in ids]

    # M49：批量导入同样自动回推当前级审批人（best-effort，非阻塞）
    for a in approvals:
        im_gateway.auto_notify_pending_async(caller.tenantId, a["approval_id"])

    return {
        "code": 0,
        "data": {
            "created": len(created),
            "skipped": skipped,
            "agents": created,
            "approvals": approvals,
            "status": "pending_review",
            "note": "批量导入同样受第35章红线约束：审批通过前不可调用",
        },
    }


@router.get("/catalog")
async def agent_catalog(
    role: str = Query("", description="岗位 id 或中文名；传了只回本岗 + 公共库"),
    caller: CallerContext = Depends(get_caller),
):
    """三块目录：categories（按行业）+ roles（按职业）+ public（公共库）。

    前端默认展示 roles——员工按岗位找工具，不按行业找。
    """
    rid = ""
    if role:
        hit = role_map.resolve(_effective_role(role, caller))
        rid = hit["id"] if hit else role
    return {"code": 0, "data": agent_store.catalog(caller, rid)}


@router.get("/roles")
async def list_roles(
    role: str = Query("", description="职业 id 或中文名，如 finance / 财务"),
    caller: CallerContext = Depends(get_caller),
):
    """职业工作台：不传 role 返回全部职业目录；传了返回该职业的完整技能包清单。

    这是「选职业 → 相应的 AI 先配进去」的落地接口：
    core=True 的 4 个是该岗开箱即用的默认配置，其余是该岗可选扩展。
    M22 起两份响应都带 public（公共库），因为「本岗技能 + 公共库」才是完整工作台。
    """
    rows = agent_store.list_all(caller)
    public = [agent_store.brief(a) for a in sorted(
        (a for a in rows if agent_store.is_public(a)), key=lambda a: a.get("sort", 99))]
    roles = role_map.catalog(rows)
    if not role:
        return {"code": 0, "data": {"roles": roles,
                                    "public": public,
                                    "total": sum(r["count"] for r in roles)}}
    hit = role_map.resolve(_effective_role(role, caller))
    if not hit:
        raise HTTPException(status_code=404, detail=f"没有这个职业：{role}")
    node = next((r for r in roles if r["id"] == hit["id"]), None)
    if node is None:
        return {"code": 0, "data": {"role": hit, "count": 0, "core": [], "rest": [],
                                    "public": public}}
    core = [a for a in node["agents"] if a["core"]]
    rest = [a for a in node["agents"] if not a["core"]]
    return {"code": 0, "data": {"role": hit, "count": node["count"],
                                "core": core, "rest": rest, "public": public}}


@router.get("/{agent_id}/inputs")
async def agent_inputs(agent_id: str, caller: CallerContext = Depends(get_caller)):
    """M52 · 这个技能「要给什么」+「包里自带什么」。

    试跑时用户最常卡在两件事：不知道要准备哪些材料，以及包作者明明放了样例
    却拿不到。这里一次把两件事答了：
    - checklist：从 skill.md 输入章节解析出的条目（不是自由文本，前端可直接摆成清单）；
    - samples：包目录里的文件及分类，input 类可直接一键挂载（invoke 的 use_samples）。
    """
    agent = agent_store.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"智能体不存在：{agent_id}")
    tid = agent.get("tenant_id")
    if tid and tid != caller.tenantId and caller.role != "platform_admin":
        raise HTTPException(status_code=404, detail=f"智能体不存在：{agent_id}")

    src = agent.get("source_path") or ""
    items = skill_pack.checklist(agent)
    files = skill_pack.samples(src) if src else []
    inputs = [f for f in files if f["kind"] == "input"]
    return {"code": 0, "data": {
        "agent_id": agent_id,
        "name": agent.get("name"),
        "checklist": items,
        "source_path": src,
        "pack_found": bool(skill_pack.pack_dir(src)) if src else False,
        "samples": [{"filename": f["filename"], "display_name": f.get("display_name") or f["filename"],
                     "kind": f["kind"], "size": f["size"],
                     "ext": f["ext"], "from_readme": f["from_readme"]} for f in files],
        "input_sample_count": len(inputs),
        "can_run_with_samples": bool(inputs),
        "hint": ("" if inputs else
                 "这个包没带原始输入样例，试跑要自己上传文件；输入清单见 checklist。"),
    }}


@router.get("")
async def list_agents(
    category: str = Query("", description="按分类过滤：industry|office|public"),
    role: str = Query("", description="岗位 id 或中文名；传了只回本岗 + 公共库 + 通用助手"),
    status: str = Query("", description="按状态过滤：pending_review|published|rejected"),
    all_tenants: bool = Query(False, alias="all"),
    caller: CallerContext = Depends(get_caller),
):
    rows = agent_store.list_all(caller, include_all=all_tenants)
    if role:
        rid = (role_map.resolve(_effective_role(role, caller)) or {}).get("id", role)
        rows = agent_store.filter_role(rows, rid)
    if category:
        rows = [a for a in rows if a.get("category") == category]
    if status:
        rows = [a for a in rows if a.get("status") == status]
    return {"code": 0, "data": {"agents": rows, "count": len(rows),
                                "status_stats": agent_store.stats()}}
