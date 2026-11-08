"""M5 审批流（第35章红线落地：注册后待评审，审批通过才发布）。

内存版审批单据库：注册智能体自动开单 → 管理员决策（approve→published / reject→rejected）
→ 智能体状态迁移由 approval 路由落账。生产替换为工作流引擎/DB，接口不变。
"""
import time
import uuid
from typing import Any, Dict, List, Optional

from app.services import pg_store, tenant_store

_TICKETS: Dict[str, Dict[str, Any]] = {}

# 默认单级审批（与历史行为一致）：未配置租户模板时退化为单步。
DEFAULT_STAGES = [{"name": "默认审批", "approver_role": None}]


def resolve_stages(tenant_id: str) -> List[Dict[str, Any]]:
    """按租户审批模板解析多级链路；无模板则单级（保持历史单步行为）。

    tenant.config.approval = {"stages": [{"name": ..., "approver_role": "dept_admin"|...}, ...]}
    末级 approve 才落 approved；任意级 reject 即时终态。
    """
    if tenant_id:
        cfg = tenant_store.get_config(tenant_id) or {}
        stages = (cfg.get("approval") or {}).get("stages")
        if stages:
            return [dict(s) for s in stages]
    return [dict(s) for s in DEFAULT_STAGES]


class TicketNotFound(Exception):
    pass


class DuplicateDecision(Exception):
    """重复决策：单据已终态，不可再改。"""


class ForbiddenDecider(Exception):
    """决策人无权审批该单据（如跨部门）。"""


def _persist(t: Dict[str, Any]) -> None:
    pg_store.upsert("approval_tickets", t["approval_id"], t)


def _can_decide_at_stage(ticket: Dict[str, Any], role: str, dept: str, tenant: str = "") -> bool:
    """当前级是否可由该角色/部门审批（用于待审列表裁剪，不含终态判断）。

    与 decide() 的授权规则对称：platform_admin 可审任何级；否则须满足
    当前级 approver_role 要求，且 dept_admin 不能跨部、也不能跨租户审。
    """
    if role == "platform_admin":
        return True
    # H4（安全加固）：跨租户强边界——dept_admin 不能审别的租户的单（即便部门名相同）。
    if ticket.get("tenant_id") and tenant and ticket["tenant_id"] != tenant:
        return False
    stages = ticket.get("stages") or DEFAULT_STAGES
    idx = ticket.get("current_stage", 0)
    stage = stages[idx] if idx < len(stages) else stages[-1]
    req = stage.get("approver_role")
    if req and req != role:
        return False
    if role == "dept_admin" and ticket["dept_id"] != dept:
        return False
    return True


def create_ticket(agent_id: str, requester_id: str, dept_id: str,
                  tenant_id: str = "", stages: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    stages = stages or resolve_stages(tenant_id)
    approval_id = "apr_" + uuid.uuid4().hex[:12]
    ticket = {
        "approval_id": approval_id,
        "agent_id": agent_id,
        "agent_ids": [agent_id],   # 统一字段：单张单也当长度 1 的批次，审批路径共用
        "batch": False,
        "batch_name": None,
        "requester_id": requester_id,
        "dept_id": dept_id,
        "tenant_id": tenant_id,    # M43：租户隔离；平台级 115 导入留空
        "stages": stages,          # M46：多级审批链路（默认单级）
        "current_stage": 0,
        "decisions": [],           # 每级决策记录
        "status": "pending",  # pending | approved | rejected
        "decided_by": None,
        "comment": None,
        "created_at": time.time(),
        "decided_at": None,
    }
    _TICKETS[approval_id] = ticket
    _persist(ticket)
    return ticket


def create_batch_ticket(agent_ids: List[str], requester_id: str, dept_id: str,
                        batch_name: str | None = None,
                        tenant_id: str = "",
                        stages: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    """M15：批量导入合并为一张批次单。

    115 个技能包逐个开单会把审批台压垮且人工不可能逐条看；合并成一张
    批次单，一次决策整批发布，决策时仍可整批拒绝。
    """
    if not agent_ids:
        raise ValueError("批次单至少需要一个 agent_id")
    stages = stages or resolve_stages(tenant_id)
    approval_id = "apr_" + uuid.uuid4().hex[:12]
    ticket = {
        "approval_id": approval_id,
        "agent_id": agent_ids[0],       # 兼容旧字段：取首个
        "agent_ids": list(agent_ids),
        "batch": True,
        "batch_name": batch_name or f"批量导入 {len(agent_ids)} 个技能包",
        "requester_id": requester_id,
        "dept_id": dept_id,
        "tenant_id": tenant_id,    # M43：租户隔离；平台级 115 导入留空
        "stages": stages,          # M46：多级审批链路（默认单级）
        "current_stage": 0,
        "decisions": [],
        "status": "pending",
        "decided_by": None,
        "comment": None,
        "created_at": time.time(),
        "decided_at": None,
    }
    _TICKETS[approval_id] = ticket
    _persist(ticket)
    return ticket


def get_ticket(approval_id: str) -> Dict[str, Any]:
    t = _TICKETS.get(approval_id)
    if not t:
        raise TicketNotFound(approval_id)
    return t


def ticket_for_agent(agent_id: str) -> Dict[str, Any] | None:
    """取某智能体最近一张审批单（注册重试会开多张，取最新；批次单按 agent_ids 命中）。"""
    mine = [t for t in _TICKETS.values()
            if agent_id in (t.get("agent_ids") or [t.get("agent_id")])]
    return max(mine, key=lambda t: t["created_at"]) if mine else None


def decide(approval_id: str, decision: str, decider_id: str, decider_role: str,
           decider_dept: str, comment: str | None = None, decider_tenant: str = "") -> Dict[str, Any]:
    """决策审批单；返回单据（含终态）。状态迁移在此落账，智能体状态由路由同步。

    M46 多级：当前级 approve 且非末级 → 推进 current_stage（仍 pending）；
    末级 approve → approved；任意级 reject → rejected（即时终态）。
    """
    t = get_ticket(approval_id)
    if t["status"] != "pending":
        raise DuplicateDecision(approval_id)
    stages = t.get("stages") or DEFAULT_STAGES
    idx = t.get("current_stage", 0)
    stage = stages[idx] if idx < len(stages) else stages[-1]
    # 按级角色校验：该级要求某角色，且决策人非 platform_admin 且不符 → 无权
    req_role = stage.get("approver_role")
    if req_role and decider_role != "platform_admin" and decider_role != req_role:
        raise ForbiddenDecider(approval_id)
    # H4（安全加固）：跨租户强边界——非平台管理员不得决策其它租户的单据。
    if decider_role != "platform_admin" and t.get("tenant_id") and decider_tenant and t["tenant_id"] != decider_tenant:
        raise ForbiddenDecider(approval_id)
    # 保留历史跨部门规则：dept_admin 只能审本部门发起的单
    if decider_role == "dept_admin" and t["dept_id"] != decider_dept:
        raise ForbiddenDecider(approval_id)
    t.setdefault("decisions", []).append({
        "stage": idx,
        "stage_name": stage.get("name"),
        "decider_id": decider_id,
        "decision": decision,
        "comment": comment,
        "at": time.time(),
    })
    if decision == "reject":
        t["status"] = "rejected"
        t["decided_by"] = decider_id
        t["comment"] = comment
        t["decided_at"] = time.time()
    else:
        if idx < len(stages) - 1:
            t["current_stage"] = idx + 1  # 流转到下级，仍是 pending
        else:
            t["status"] = "approved"
            t["decided_by"] = decider_id
            t["comment"] = comment
            t["decided_at"] = time.time()
    _persist(t)
    return t


def load_from_pg() -> int:
    """启动时从 PG 载回审批单；PG 不可达时为空操作。"""
    n = 0
    for payload in pg_store.load_all("approval_tickets"):
        try:
            _TICKETS[payload["approval_id"]] = payload
            n += 1
        except Exception:
            continue
    return n


def pending_list(dept_id: str | None = None, tenant_id: str | None = None,
                 caller_role: str | None = None, caller_dept: str | None = None,
                 only_my_stage: bool = False) -> List[Dict[str, Any]]:
    """待审列表：platform_admin 看全部，dept_admin 只看本部门 + 本租户。

    only_my_stage=True 时再按调用人角色/部门裁剪：只返回当前级可由其审批的单据
    （含租户/部门/级别约束）。用于审批台「仅显示当前级我能审的单」。
    """
    rows = [t for t in _TICKETS.values() if t["status"] == "pending"]
    if dept_id:
        rows = [t for t in rows if t["dept_id"] == dept_id]
    # M43：租户隔离（tenant_id 为 None 表示平台管理员看全量，不按租户裁剪）
    if tenant_id:
        rows = [t for t in rows if t.get("tenant_id") == tenant_id]
    if only_my_stage and caller_role:
        rows = [t for t in rows if _can_decide_at_stage(t, caller_role, caller_dept, tenant_id or "")]
    return sorted(rows, key=lambda t: t["created_at"])
