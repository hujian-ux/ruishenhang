"""P1-8 · 技能索引台账路由（#303）。

管理后台「技能台账」tab 的数据接口。纯只读聚合：覆盖度台账列表 / 聚合看板 / 单包明细。
可见域与权限完全复用 agent_store（RBAC），本路由不新增任何写操作。

路由顺序注意：`/skills/stats` 必须在 `/skills/{agent_id}` 之前注册，否则 "stats"
会被路径参数吞掉（同 P1-7 的 `/artifacts` 处理）。
"""
from fastapi import APIRouter, Depends, HTTPException, Query

from app.deps import get_caller
from app.models.caller import CallerContext
from app.services import skill_ledger

router = APIRouter(prefix="/skills", tags=["skill-ledger"])


@router.get("/stats")
async def ledger_stats(caller: CallerContext = Depends(get_caller)):
    """聚合覆盖看板：总数 / 按行业 / 按岗位 / 维度覆盖 / 评分分布 / 缺口 TOP。"""
    return {"code": 0, "data": skill_ledger.stats(caller)}


@router.get("")
async def ledger_index(
    industry: str = Query("", description="按行业过滤（category_cn）"),
    role: str = Query("", description="岗位 id 或中文名；传了只回本岗 + 公共库"),
    status: str = Query("", description="按状态过滤：pending_review|published|rejected"),
    q: str = Query("", description="搜索：名称/描述/子职能/岗位名"),
    kind: str = Query("", description="类型：business|public|空(全部)"),
    flag: str = Query("", description="缺口筛选：传维度 key 仅返回缺失该维度的包"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    caller: CallerContext = Depends(get_caller),
):
    """覆盖度台账列表（带过滤）。"""
    d = skill_ledger.index(caller, industry=industry, role=role, status=status,
                           q=q, kind=kind, flag=flag, limit=limit, offset=offset)
    return {"code": 0, "data": d}


@router.get("/{agent_id}")
async def ledger_detail(agent_id: str, caller: CallerContext = Depends(get_caller)):
    """单技能覆盖度明细：含输入清单、样例输入、材料/规范摘要与版本号。"""
    rec = skill_ledger.detail(caller, agent_id)
    if rec is None:
        # 统一口径：不可见 / 不存在 / 内置助手都返回 404，不区分以避免信息泄露
        raise HTTPException(status_code=404, detail=f"技能不存在或无权限：{agent_id}")
    return {"code": 0, "data": rec}
