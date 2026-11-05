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

from app.services import pg_store, public_skills, role_map

_TABLE = "agents"

# 兼容旧引用：routers/approval.py、routers/invoke.py 仍可直接读写该 dict。
# 所有写入必须走 save()，以保证 PG 写穿；直接改 dict 只影响内存（测试可用）。
_AGENTS: Dict[str, Dict[str, Any]] = {}

VALID_STATUS = {"pending_review", "published", "rejected"}

# 平台内置通用助手：不属于任何业务技能包，不走评审（它是平台自带能力，不是交付物）。
# 作用：对外界面打开即可直接提问，不必先挑一个技能包——否则用户第一句"你好"都发不出去。
BUILTIN_ASSISTANT_ID = "ag_builtin_assistant"


def ensure_builtin_assistant() -> Dict[str, Any]:
    """幂等注册内置通用助手；已存在则原样返回（不覆盖用户改过的配置）。"""
    cur = _AGENTS.get(BUILTIN_ASSISTANT_ID)
    if cur:
        # 存量修补：早期版本只写了 scope，_visible 读的是 knowledge_scope，
        # 于是内置助手对非管理员不可见，前端默认落到第一个业务技能包（选品/排产之类）。
        if not cur.get("knowledge_scope"):
            cur["knowledge_scope"] = "global"
            save(cur)
        return cur
    return save({
        "agent_id": BUILTIN_ASSISTANT_ID,
        "name": "通用助手",
        "description": "不套用具体技能包，直接回答、整理、起草。需要专业输出时再选技能包。",
        "adapter_type": "llm",
        "scope": "global",
        # 可见性判据认这个字段；漏了它对员工就是隐身（e2e 用员工身份才发现）
        "knowledge_scope": "global",
        "status": "published",
        "category": "builtin",
        "category_cn": "内置",
        "sub_function": "",
        "builtin": True,
        # 留空表示不覆盖运行时默认提示词（agent_runtime.SYSTEM_PROMPT）
        "system_prompt": "",
    })


def ensure_public_skills() -> int:
    """幂等预置公共库（办公基础技能）。返回本次写入条数。

    和内置通用助手的区别：
    - 通用助手 = 空白页，什么都能聊，没有干法约束；
    - 公共库 = 有明确干法和产出格式的手册（文案/PPT/表格/纪要…），跟岗位无关。

    每次启动都按代码刷新内容：公共库是**平台资产**，改了提示词就该全租户一致。
    只刷新内容字段，不动 status（管理员临时下架不该被启动流程覆盖）。
    """
    n = 0
    for a in public_skills.build_agents():
        cur = _AGENTS.get(a["agent_id"])
        if cur:
            for k in ("name", "description", "system_prompt", "tags", "sub_function",
                      "default_format", "sort", "category", "category_cn",
                      "when_to_use", "inputs_contract", "outputs_contract", "adapter_type"):
                cur[k] = a[k]
            cur.setdefault("status", "published")
            cur.setdefault("knowledge_scope", "global")
            save(cur)
        else:
            save(a)
        n += 1
    return n


def is_public(agent: Dict[str, Any]) -> bool:
    """公共库条目：不是业务技能包，不进 115 口径，也不挂职业标签。"""
    return bool(agent.get("public")) or agent.get("category") == "public"


# 技能包字段（会被原样保留并在列表接口回显）
PACK_FIELDS = (
    "description", "system_prompt", "category", "category_cn",
    "sub_function", "tags", "source_path", "inputs_contract", "outputs_contract",
    "when_to_use", "role_tags", "role_primary", "role_core",
)


def save(agent: Dict[str, Any]) -> Dict[str, Any]:
    """写入内存并写穿 PG。PG 不可达时仅内存生效（不抛错）。

    M19：保存前补职业标签（已有值不覆盖），使所有入口自动带 role_* 字段。
    """
    agent.setdefault("created_at", time.time())
    agent["updated_at"] = time.time()
    role_map.annotate(agent)
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
    # 历史写法并存：早期数据用 scope，现在统一 knowledge_scope。两者都认，
    # 否则字段名差一个词就会让整批资产对员工隐身（M19 踩过）。
    scope = agent.get("knowledge_scope") or agent.get("scope")
    if scope == "global":
        return True
    if scope == "department" and agent.get("dept_id") == caller.deptId:
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


def brief(a: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "agent_id": a["agent_id"], "name": a.get("name"),
        "status": a.get("status"), "adapter_type": a.get("adapter_type"),
        "description": (a.get("description") or "")[:120],
        "tags": a.get("tags") or [],
        "sub_function": a.get("sub_function") or "",
        "default_format": a.get("default_format") or "",
        # M24：技能使用契约。缺省空串，业务包不受影响；公共库有了之后，
        # 前端可以按「什么时候用」搜到技能，卡片上也能说清要给什么、会拿到什么。
        "when_to_use": a.get("when_to_use") or "",
        "inputs_contract": a.get("inputs_contract") or "",
        "outputs_contract": a.get("outputs_contract") or "",
    }


def filter_role(rows: List[Dict[str, Any]], role: str) -> List[Dict[str, Any]]:
    """按岗位收窄可见清单：本岗业务包 + 公共库 + 通用助手。

    「选完岗位只看到自己的技能和公共库」的落点。注意这是**视图过滤不是权限**：
    权限仍由 _visible（RBAC）决定，岗位只决定在这个人面前先摆哪些。
    主职业唯一，避免一个包在多个岗位重复出现、各岗数字加起来大于 115。
    """
    if not role:
        return rows
    return [a for a in rows
            if is_public(a) or a.get("builtin") or a.get("role_primary") == role]


def catalog(caller, role: str = "") -> Dict[str, Any]:
    """双视角目录：按 行业（categories） + 按 职业（roles） + 公共库（public）。

    M19 起前端默认走职业视角（员工按岗位找工具），行业视角保留给老板/售前。
    注意：kind（industry/office）粒度太粗，制造业/零售/跨境电商共用 industry，
    若只按 kind 分组会把三个行业并成一个。故分组键用 category_cn（行业名），
    无 category_cn 时回退 kind。

    M22：公共库单独成块，**不进 categories 也不进 total**——否则 115 口径会被
    9 个办公基础技能污染，客户对不上数。传 role 时只回本岗 + 公共库。
    """
    rows = filter_role(list_all(caller), role)
    tree: Dict[str, Dict[str, Any]] = {}
    for a in rows:
        # 内置通用助手与公共库都不是业务技能包，不进行业目录（否则 115 → 125，口径乱）
        if a.get("builtin") or is_public(a):
            continue
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
            "role_primary": a.get("role_primary") or "",
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
    public = [ brief(a) for a in sorted(
        (a for a in rows if is_public(a)), key=lambda a: a.get("sort", 99)) ]
    roles = role_map.catalog(rows)
    return {
        "categories": cats,
        "roles": roles,
        "public": public,
        "public_groups": public_skills.groups(),
        "total": sum(c["count"] for c in cats),
        "role_total": sum(r["count"] for r in roles),
        "public_total": len(public),
    }


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
