"""M27 · 自助建部门（蓝图第 19 章）。

员工/部门管理员零代码发起一个部门，系统按所选模板自动初始化
scope / RBAC / 继承关系 / 知识边界；部门需平台管理员审批后才 active。

边界：
- 状态只存进程内 _m27_store.Store（重启即丢，与项目「无 PG 全内存」口径一致）。
- 默认路径不触碰 agent_store._AGENTS / public_skills / role_map，绝不影响 115/28 口径。
- DEPT_SELF_SERVICE 为运行时开关（monkeypatch 友好），关闭时整体拒绝自助创建。
"""
from __future__ import annotations

import copy
from typing import Any, Dict

from fastapi import HTTPException

from app.core.config import settings
from app.services._m27_store import (
    Store, new_id, now, iso, audit_event, caller_id, caller_role,
)

_DEPTS = Store("dept")

# 6 类部门模板：每类带 name / 示例智能体名列表 / 默认 scope / 默认可见边界。
_DEPT_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "general": {
        "name": "综合管理",
        "example_agents": ["经营分析助手", "制度问答助手", "会议纪要助手", "公文起草助手"],
        "default_scope": "department",
        "visible_boundary": {
            "knowledge_scope": "department",
            "cross_dept": False,
            "inherit_parent": True,
            "note": "默认只见本部门文档，可继承上级公共制度",
        },
    },
    "business": {
        "name": "业务",
        "example_agents": ["客户画像助手", "报价测算助手", "商机跟进助手", "合同初审助手"],
        "default_scope": "department",
        "visible_boundary": {
            "knowledge_scope": "department",
            "cross_dept": False,
            "inherit_parent": True,
            "note": "客户/商机数据限本部门，跨部门需显式授权",
        },
    },
    "rnd": {
        "name": "产研",
        "example_agents": ["需求评审助手", "技术方案助手", "代码规范助手", "测试用例助手"],
        "default_scope": "department",
        "visible_boundary": {
            "knowledge_scope": "department",
            "cross_dept": True,
            "inherit_parent": True,
            "note": "研发文档在项目组间可共享，跨租户仍禁止",
        },
    },
    "manufacturing": {
        "name": "制造",
        "example_agents": ["排产建议助手", "质检问答助手", "设备维保助手", "工艺核查助手"],
        "default_scope": "department",
        "visible_boundary": {
            "knowledge_scope": "department",
            "cross_dept": False,
            "inherit_parent": True,
            "note": "产线数据按车间隔离，默认不跨部门",
        },
    },
    "special": {
        "name": "特殊",
        "example_agents": ["合规预审助手", "法务条款助手", "审计取证助手", "风控预警助手"],
        "default_scope": "department",
        "visible_boundary": {
            "knowledge_scope": "department",
            "cross_dept": False,
            "inherit_parent": False,
            "note": "独立隔离域：不继承上级，最小可见、全程留痕",
        },
    },
    "blank": {
        "name": "空白",
        "example_agents": [],
        "default_scope": "department",
        "visible_boundary": {
            "knowledge_scope": "department",
            "cross_dept": False,
            "inherit_parent": True,
            "note": "不预置任何智能体，从零开始，边界取平台默认",
        },
    },
}


def templates() -> dict:
    """返回 6 类部门模板（深拷贝，外部改动不污染内置定义）。"""
    return copy.deepcopy(_DEPT_TEMPLATES)


def create_department(name: str, parent: str, domain: str, description: str,
                      template: str, caller) -> dict:
    if not settings.DEPT_SELF_SERVICE:
        raise HTTPException(status_code=403, detail="部门自助创建已关闭（DEPT_SELF_SERVICE=0）")
    if not name or not str(name).strip():
        raise HTTPException(status_code=400, detail="部门名称必填")
    if template not in _DEPT_TEMPLATES:
        raise HTTPException(status_code=400,
                            detail=f"非法部门模板：{template}，应为 {sorted(_DEPT_TEMPLATES)}")
    tpl = _DEPT_TEMPLATES[template]
    did = new_id("dept")
    row = {
        "id": did,
        "name": str(name).strip(),
        "parent": parent or "",
        "domain": domain or "",
        "description": description or "",
        "template": template,
        "template_name": tpl["name"],
        "status": "pending",
        # 自动初始化：scope / rbac / 继承 / 知识边界
        "scope": f"department:{did}",
        "rbac": ["employee", "dept_admin", "platform_admin"],
        "inherits_from": parent or "",
        "knowledge_boundary": {
            "scope": f"department:{did}",
            "default_scope": tpl["default_scope"],
            "visible_boundary": copy.deepcopy(tpl["visible_boundary"]),
            "inherits_from": parent or "",
            "domain": domain or "",
        },
        "created_by": caller_id(caller),
        "created_by_role": caller_role(caller),
        "created_at": now(),
        "created_iso": iso(),
    }
    _DEPTS.put(row)
    audit_event("dept.create", caller, target=did,
                meta={"name": row["name"], "template": template, "parent": parent})
    return dict(row)


def approve(did: str, caller) -> dict:
    """仅 platform_admin 可审批；active 后写入 approved_by / approved_at。"""
    if caller_role(caller) != "platform_admin":
        raise HTTPException(status_code=403, detail="仅平台管理员可审批部门")
    row = _DEPTS.get(did)
    if row is None:
        raise HTTPException(status_code=404, detail=f"部门不存在：{did}")
    updated = _DEPTS.update(did, {
        "status": "active",
        "approved_by": caller_id(caller),
        "approved_at": iso(),
        "approved_at_ts": now(),
    })
    audit_event("dept.approve", caller, target=did, meta={"status": "active"})
    return dict(updated)


def list_departments(caller=None, status: str | None = None) -> list:
    filters: Dict[str, Any] = {}
    if status:
        filters["status"] = status
    return _DEPTS.list(filters=filters)


def reset_state() -> None:
    _DEPTS.clear()
