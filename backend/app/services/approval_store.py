"""M5 审批流（第35章红线落地：注册后待评审，审批通过才发布）。

内存版审批单据库：注册智能体自动开单 → 管理员决策（approve→published / reject→rejected）
→ 智能体状态迁移由 approval 路由落账。生产替换为工作流引擎/DB，接口不变。
"""
import time
import uuid
from typing import Any, Dict, List

_TICKETS: Dict[str, Dict[str, Any]] = {}


class TicketNotFound(Exception):
    pass


class DuplicateDecision(Exception):
    """重复决策：单据已终态，不可再改。"""


class ForbiddenDecider(Exception):
    """决策人无权审批该单据（如跨部门）。"""


def create_ticket(agent_id: str, requester_id: str, dept_id: str) -> Dict[str, Any]:
    approval_id = "apr_" + uuid.uuid4().hex[:12]
    ticket = {
        "approval_id": approval_id,
        "agent_id": agent_id,
        "requester_id": requester_id,
        "dept_id": dept_id,
        "status": "pending",  # pending | approved | rejected
        "decided_by": None,
        "comment": None,
        "created_at": time.time(),
        "decided_at": None,
    }
    _TICKETS[approval_id] = ticket
    return ticket


def get_ticket(approval_id: str) -> Dict[str, Any]:
    t = _TICKETS.get(approval_id)
    if not t:
        raise TicketNotFound(approval_id)
    return t


def ticket_for_agent(agent_id: str) -> Dict[str, Any] | None:
    """取某智能体最近一张审批单（注册重试会开多张，取最新）。"""
    mine = [t for t in _TICKETS.values() if t["agent_id"] == agent_id]
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
    return t


def pending_list(dept_id: str | None = None) -> List[Dict[str, Any]]:
    """待审列表：platform_admin 看全部，dept_admin 只看本部门。"""
    rows = [t for t in _TICKETS.values() if t["status"] == "pending"]
    if dept_id:
        rows = [t for t in rows if t["dept_id"] == dept_id]
    return sorted(rows, key=lambda t: t["created_at"])
