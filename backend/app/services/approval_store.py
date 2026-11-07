"""M5 审批流（第35章红线落地：注册后待评审，审批通过才发布）。

内存版审批单据库：注册智能体自动开单 → 管理员决策（approve→published / reject→rejected）
→ 智能体状态迁移由 approval 路由落账。生产替换为工作流引擎/DB，接口不变。
"""
import time
import uuid
from typing import Any, Dict, List, Optional

from app.services import pg_store

_TICKETS: Dict[str, Dict[str, Any]] = {}


class TicketNotFound(Exception):
    pass


class DuplicateDecision(Exception):
    """重复决策：单据已终态，不可再改。"""


class ForbiddenDecider(Exception):
    """决策人无权审批该单据（如跨部门）。"""


def _persist(t: Dict[str, Any]) -> None:
    pg_store.upsert("approval_tickets", t["approval_id"], t)


def create_ticket(agent_id: str, requester_id: str, dept_id: str,
                  tenant_id: str = "") -> Dict[str, Any]:
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
                        tenant_id: str = "") -> Dict[str, Any]:
    """M15：批量导入合并为一张批次单。

    115 个技能包逐个开单会把审批台压垮且人工不可能逐条看；合并成一张
    批次单，一次决策整批发布，决策时仍可整批拒绝。
    """
    if not agent_ids:
        raise ValueError("批次单至少需要一个 agent_id")
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
           decider_dept: str, comment: str | None = None) -> Dict[str, Any]:
    """决策审批单；返回单据（含终态）。状态迁移在此落账，智能体状态由路由同步。"""
    t = get_ticket(approval_id)
    if t["status"] != "pending":
        raise DuplicateDecision(approval_id)
    # dept_admin 只能审本部门发起的单；platform_admin 全局可审
    if decider_role == "dept_admin" and t["dept_id"] != decider_dept:
        raise ForbiddenDecider(approval_id)
    t["status"] = "approved" if decision == "approve" else "rejected"
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


def pending_list(dept_id: str | None = None, tenant_id: str | None = None) -> List[Dict[str, Any]]:
    """待审列表：platform_admin 看全部，dept_admin 只看本部门 + 本租户。"""
    rows = [t for t in _TICKETS.values() if t["status"] == "pending"]
    if dept_id:
        rows = [t for t in rows if t["dept_id"] == dept_id]
    # M43：租户隔离（tenant_id 为 None 表示平台管理员看全量，不按租户裁剪）
    if tenant_id:
        rows = [t for t in rows if t.get("tenant_id") == tenant_id]
    return sorted(rows, key=lambda t: t["created_at"])
