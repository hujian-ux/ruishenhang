"""M15 · 智能体注册表（技能包融合的落库载体）。

M15 之前：`_AGENTS` 只是 routers/agents.py 里的内存 dict —— 重启即丢，
且只存 name/adapter_type/scope/model_id/status，技能包真正的灵魂
（system_prompt、输入产出契约、分类）无处安放。本模块把它升级为：

- **内存 + PG 写穿**（对齐 approval_store 范式，PG 不可达时静默降级为内存）
- **保留 description / system_prompt / category / inputs_contract** 等技能包字段
- **RBAC 可见域收敛**：platform_admin 全量 / dept_admin 本部门+global / employee 自建+global
- **分类目录**（category + sub_function）供前端分组导航

设计取舍：不做向量化/不做版本管理，先解决「装得下、查得到、重启不丢」。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from app.services import pg_store

_TABLE = "agents"

# 兼容旧引用：routers/approval.py、routers/invoke.py 仍可直接读写该 dict。
# 所有写入必须走 save()，以保证 PG 写穿；直接改 dict 只影响内存（测试可用）。
_AGENTS: Dict[str, Dict[str, Any]] = {}

VALID_STATUS = {"pending_review", "published", "rejected"}

# 技能包字段（会被原样保留并在列表接口回显）
PACK_FIELDS = (
    "description", "system_prompt", "category", "category_cn",
    "sub_function", "tags", "source_path", "inputs_contract", "outputs_contract",
)


def save(agent: Dict[str, Any]) -> Dict[str, Any]:
    """写入内存并写穿 PG。PG 不可达时仅内存生效（不抛错）。"""
    agent.setdefault("created_at", time.time())
    agent["updated_at"] = time.time()
    _AGENTS[agent["agent_id"]] = agent
    pg_store.upsert(_TABLE, agent["agent_id"], agent)
    return agent


def get(agent_id: str) -> Optional[Dict[str, Any]]:
    return _AGENTS.get(agent_id)


def set_status(agent_id: str, status: str) -> Optional[Dict[str, Any]]:
    if status not in VALID_STATUS:
        raise ValueError(f"非法状态：{status}")
    a = _AGENTS.get(agent_id)
    if not a:
        return None
    a["status"] = status
    return save(a)


def _visible(agent: Dict[str, Any], caller) -> bool:
    """第18章权限语义在「智能体目录」上的延伸。"""
    if caller.role == "platform_admin":
        return True
    if agent.get("registered_by") == caller.userId:
        return True
    if agent.get("knowledge_scope") == "global":
        return True
    if agent.get("knowledge_scope") == "department" and agent.get("dept_id") == caller.deptId:
        return True
    if caller.role == "dept_admin" and agent.get("dept_id") == caller.deptId:
        return True
    return False


def list_all(caller, include_all: bool = False) -> List[Dict[str, Any]]:
    """按可见域列出；platform_admin 可 include_all 跨租户排查。"""
    rows = list(_AGENTS.values())
    if not (include_all and caller.role == "platform_admin"):
        rows = [a for a in rows if _visible(a, caller)]
    return sorted(rows, key=lambda a: (a.get("category") or "", a.get("name") or ""))


def catalog(caller) -> Dict[str, Any]:
    """按 行业/角色 → 子职能 分组，供前端树形导航（只统计可见项）。

    注意：kind（industry/office）粒度太粗，制造业/零售/跨境电商共用 industry，
    若只按 kind 分组会把三个行业并成一个。故分组键用 category_cn（行业名），
    无 category_cn 时回退 kind。
    """
    tree: Dict[str, Dict[str, Any]] = {}
    for a in list_all(caller):
        cat_cn = a.get("category_cn") or a.get("category") or "未分类"
        key = cat_cn
        node = tree.setdefault(key, {
            "category": a.get("category") or "", "category_cn": cat_cn, "groups": {},
        })
        sub = a.get("sub_function") or ""
        node["groups"].setdefault(sub, []).append({
            "agent_id": a["agent_id"], "name": a.get("name"),
            "status": a.get("status"), "adapter_type": a.get("adapter_type"),
            "description": (a.get("description") or "")[:120],
            "tags": a.get("tags") or [],
        })
    cats = []
    for k in sorted(tree):
        v = tree[k]
        cats.append({
            "category": v["category"],
            "category_cn": v["category_cn"],
            "count": sum(len(g) for g in v["groups"].values()),
            "groups": [
                {"sub_function": s or "", "count": len(g), "agents": g}
                for s, g in sorted(v["groups"].items())
            ],
        })
    return {"categories": cats, "total": sum(c["count"] for c in cats)}


def load_from_pg() -> int:
    """启动时载回，避免重启后 115 个技能包蒸发。"""
    n = 0
    for payload in pg_store.load_all(_TABLE):
        try:
            _AGENTS[payload["agent_id"]] = payload
            n += 1
        except Exception:
            continue
    return n


def stats() -> Dict[str, int]:
    out: Dict[str, int] = {}
    for a in _AGENTS.values():
        out[a.get("status", "unknown")] = out.get(a.get("status", "unknown"), 0) + 1
    return out
