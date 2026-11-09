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

import os
import sys
import time
from typing import Any, Dict, List, Optional

from app.services import pg_store, public_skills, role_map, hard_rules, tenant_store

_TABLE = "agents"

# 平台级共享资产（115 业务包 / 28 公共库 / 内置助手）使用的租户占位：空串 = 对所有租户可见。
PLATFORM_TENANT = tenant_store.PLATFORM_TENANT

# 兼容旧引用：routers/approval.py、routers/invoke.py 仍可直接读写该 dict。
# 所有写入必须走 save()，以保证 PG 写穿；直接改 dict 只影响内存（测试可用）。
_AGENTS: Dict[str, Dict[str, Any]] = {}

VALID_STATUS = {"pending_review", "published", "rejected"}

# 平台内置通用助手：不属于任何业务技能包，不走评审（它是平台自带能力，不是交付物）。
# 作用：对外界面打开即可直接提问，不必先挑一个技能包——否则用户第一句"你好"都发不出去。
BUILTIN_ASSISTANT_ID = "ag_builtin_assistant"

# M46 平台级「审批助手」：辅助层技能（填单/摘要/合规校验），明确只建议不落账。
# 与内置通用助手同属 builtin，不进 115 业务口径、不进 catalog.total。
APPROVAL_ASSISTANT_ID = "ag_approval_assistant"


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


def ensure_approval_assistant() -> Dict[str, Any]:
    """幂等注册平台级「审批助手」；已存在则原样返回（不覆盖用户改过的配置）。

    定位：辅助层技能——帮申请人填单、抽要点、做合规/风险校验，帮审批人看进度。
    硬约束：只建议不落账；最终 approve/reject 由授权管理员在审批台 decide() 落账。
    builtin=True 使其不进 115 业务口径、不进 catalog.total（与内置通用助手同待遇）。
    """
    cur = _AGENTS.get(APPROVAL_ASSISTANT_ID)
    if cur:
        if not cur.get("knowledge_scope"):
            cur["knowledge_scope"] = "global"
            save(cur)
        return cur
    return save({
        "agent_id": APPROVAL_ASSISTANT_ID,
        "name": "审批助手",
        "description": "帮申请人自动填单、抽取待批要点、做合规与风险校验；帮审批人生成摘要与催办建议。注意：本助手只出建议，不替你批准或驳回。",
        "adapter_type": "llm",
        "scope": "global",
        "knowledge_scope": "global",
        "status": "published",
        "category": "approval",
        "category_cn": "审批",
        "sub_function": "",
        "builtin": True,
        "system_prompt": (
            "你是平台的「审批助手」，只做辅助、不落账。\n"
            "可做的事：帮用户补全审批申请单（用途、知识域、理由）；"
            "解释某张审批单当前走到第几级、还卡在哪；给出合规与风险提示。\n"
            "绝对不做：调用或模拟审批的批准/驳回——那是授权管理员在审批台的操作。"
            "若用户让你「帮我批了」，明确告知他审批必须由授权管理员 commit，你只能准备材料与提醒。"
        ),
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
                      "when_to_use", "inputs_contract", "outputs_contract", "adapter_type",
                      "hard_rules_version", "web_fetch_top"):
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


def has_business_agents() -> bool:
    """是否存在业务技能包（非公共库、非内置助手）。用于判断是否需要首次自动灌库。"""
    return any(
        (not a.get("builtin")) and (not is_public(a))
        for a in _AGENTS.values()
    )


def bootstrap_solutions(root: str = "") -> int:
    """首次启动自修复：把仓库内置的 115 包源直接注册为 published（幂等）。

    解决「客户本地装/重启后业务包为空 → 岗位选择器无选项」的交付硬伤：
    - 仅当业务包为空时由 lifespan 调用，已存在（同 source_path）则跳过，可重复安全运行；
    - 平台自带资产 tenant_id 留空（PLATFORM_TENANT），全租户可见；
    - 直接 published（vendor 随产品交付的种子包，非用户提交，无需走审批）。

    返回新建数量。
    """
    if has_business_agents():
        return 0
    try:
        from scripts.import_solutions import SOLUTIONS_ROOT, scan  # 延迟导入，避免循环依赖
        from app.routers.agents import _new_agent_id
    except Exception:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
        from import_solutions import SOLUTIONS_ROOT, scan  # type: ignore
        from app.routers.agents import _new_agent_id  # type: ignore
    root = root or SOLUTIONS_ROOT
    if not root or not os.path.isdir(root):
        return 0
    defs = scan(root)
    if not defs:
        return 0
    created = 0
    for d in defs:
        agent_id = _new_agent_id(d["name"])  # 与 HTTP 导入同源（含中文名 uuid 兜底），保证幂等一致
        exist = _AGENTS.get(agent_id)
        if exist and exist.get("source_path") == d["source"]:
            continue  # 幂等：同来源已存在
        save({
            "agent_id": agent_id,
            "name": d["name"],
            "adapter_type": d["adapter_type"],
            "knowledge_scope": "global",
            "model_id": None,
            "registered_by": "bootstrap",
            "dept_id": "d_platform",
            "tenant_id": PLATFORM_TENANT,
            "status": "published",
            "description": d["description"],
            "system_prompt": d["system_prompt"],
            "category": d["kind"],
            "category_cn": d["board_cn"],
            "sub_function": d["sub_function"],
            "tags": d["tags"],
            "source_path": d["source"],
            "inputs_contract": d["inputs_contract"],
            "outputs_contract": d["outputs_contract"],
        })
        created += 1
    return created


# 技能包字段（会被原样保留并在列表接口回显）
PACK_FIELDS = (
    "description", "system_prompt", "category", "category_cn",
    "sub_function", "tags", "source_path", "inputs_contract", "outputs_contract",
    "when_to_use", "role_tags", "role_primary", "role_core",
    "hard_rules_version", "web_fetch_top", "fewshot", "fewshot_version",
)


def save(agent: Dict[str, Any]) -> Dict[str, Any]:
    """写入内存并写穿 PG。PG 不可达时仅内存生效（不抛错）。

    M19：保存前补职业标签（已有值不覆盖），使所有入口自动带 role_* 字段。
    """
    agent.setdefault("created_at", time.time())
    agent["updated_at"] = time.time()
    # M43：租户隔离底座。平台共享资产（115/公共库/内置助手）tenant_id 留空 → 全租户可见；
    # 租户自建智能体由路由层写入真实 tenant_id → 仅本租户可见。
    agent.setdefault("tenant_id", PLATFORM_TENANT)
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
    # M43：租户私有智能体，跨租户不可见（平台共享资产 tenant_id 留空，对所有租户可见）。
    tid = agent.get("tenant_id")
    if tid and tid != caller.tenantId:
        return False
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


def backfill_hard_rules() -> Dict[str, Any]:
    """M15/M25 P1 · 对存量业务包补齐平台硬规则（幂等、可重跑）。

    仅处理业务技能包（非内置助手、非公共库）；已含平台硬规则头的不动，
    避免重复叠加。返回 {total, already, patched, by_category}。
    """
    total = already = patched = 0
    by_category: Dict[str, Dict[str, int]] = {}
    for a in list(_AGENTS.values()):
        if a.get("builtin") or a.get("category") == "public":
            continue
        total += 1
        sp = a.get("system_prompt") or ""
        cat = a.get("category_cn") or a.get("category") or "unknown"
        slot = by_category.setdefault(cat, {"total": 0, "already": 0, "patched": 0})
        slot["total"] += 1
        if hard_rules.has_hard_rules(sp):
            already += 1
            slot["already"] += 1
            continue
        a["system_prompt"] = hard_rules.ensure_hard_rules(sp)
        a["hard_rules_version"] = hard_rules.HARD_RULES_VERSION
        save(a)
        patched += 1
        slot["patched"] += 1
    return {
        "total": total, "already": already, "patched": patched,
        "by_category": by_category,
        "hard_rules_version": hard_rules.HARD_RULES_VERSION,
    }


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
        "web_fetch_top": int(a.get("web_fetch_top") or 0),
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


# ---------------------------------------------------------------- 行业元数据
# 行业展示名 + 一句话说明。key 用 category_cn（技能包自带的行业名），
# 未登记的行业走兜底文案，不报错也不隐藏——客户加了新行业立刻能看见。
INDUSTRY_NOTE: Dict[str, str] = {
    "制造业": "图纸报价、排产交期、来料品质、BOM 基线",
    "零售": "门店运营、会员分层、爆品选品、社群触达",
    "跨境电商": "选品验证、多平台上架、PPC 利润、FBA 申诉",
    "办公角色": "不分行业：HR、行政、财务、销售的日常活",
}
# 展示名：category_cn 是数据真值（筛选/接口都用它），这里只换前端看到的字。
INDUSTRY_LABEL: Dict[str, str] = {
    "办公角色": "通用办公",
}
# 「通用」行业：不绑定具体行业的岗位包（现在就是 62 个办公角色包）。
# 前端把它排在行业列表最后，并标注「任何行业都能用」。
UNIVERSAL_INDUSTRIES = {"办公角色"}


def _role_nodes(agents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把一批包按岗位（role_primary）聚成岗位节点，顺序跟 role_map.ROLES 一致。

    行业视角下的第二层就是岗位——"制造业 → 品质工程 5 个"，而不是把 21 个包平铺。
    没有 role_primary 的包（历史数据/手工建的）不丢，落到 unassigned 计数里。
    """
    order = {r["id"]: i for i, r in enumerate(role_map.ROLES)}
    meta = {r["id"]: r for r in role_map.ROLES}
    bucket: Dict[str, List[Dict[str, Any]]] = {}
    unassigned = 0
    for a in agents:
        rid = a.get("role_primary") or ""
        if not rid or rid not in meta:
            unassigned += 1
            continue
        bucket.setdefault(rid, []).append(a)
    out = []
    for rid in sorted(bucket, key=lambda k: order.get(k, 99)):
        g = bucket[rid]
        core_list = role_map.ROLE_CORE.get(rid, [])
        out.append({
            "id": rid,
            "name": meta[rid]["name"],
            "initial": meta[rid]["initial"],
            "color": meta[rid]["color"],
            "note": meta[rid].get("desc", ""),
            "count": len(g),
            "core_count": sum(1 for a in g if a.get("name") in core_list),
            "agents": g,
        })
    return out


def catalog(caller, role: str = "") -> Dict[str, Any]:
    """三层目录：行业（categories） → 岗位（categories[].roles） → 技能包；外加公共库。

    M19 起前端默认走职业视角（员工按岗位找工具），行业视角保留给老板/售前。
    注意：kind（industry/office）粒度太粗，制造业/零售/跨境电商共用 industry，
    若只按 kind 分组会把三个行业并成一个。故分组键用 category_cn（行业名），
    无 category_cn 时回退 kind。

    M22：公共库单独成块，**不进 categories 也不进 total**——否则 115 口径会被
    办公基础技能污染，客户对不上数。传 role 时只回本岗 + 公共库。

    M49：行业节点下补 roles（岗位分层）。「先选行业、再看这个行业有哪些岗位」是
    客户的真实心智；groups 保留旧字段给老消费方，不删。
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
            "agents": [],
        })
        node["agents"].append(a)
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
        role_nodes = []
        for rn in _role_nodes(v["agents"]):
            role_nodes.append({
                "id": rn["id"], "name": rn["name"], "initial": rn["initial"],
                "color": rn["color"], "note": rn["note"],
                "count": rn["count"], "core_count": rn["core_count"],
                "agents": [{
                    "agent_id": a["agent_id"], "name": a.get("name"),
                    "status": a.get("status"), "adapter_type": a.get("adapter_type"),
                    "description": (a.get("description") or "")[:120],
                    "tags": a.get("tags") or [],
                    "role_primary": rn["id"],
                    "role_name": rn["name"],
                    "core": a.get("name") in role_map.ROLE_CORE.get(rn["id"], []),
                } for a in rn["agents"]],
            })
        cats.append({
            "category": v["category"],
            "category_cn": v["category_cn"],
            "name": INDUSTRY_LABEL.get(v["category_cn"], v["category_cn"]),
            "note": INDUSTRY_NOTE.get(v["category_cn"], ""),
            "universal": v["category_cn"] in UNIVERSAL_INDUSTRIES,
            "count": sum(len(g) for g in v["groups"].values()),
            "roles": role_nodes,
            "groups": [
                {"sub_function": s or "", "count": len(g), "agents": g}
                for s, g in sorted(v["groups"].items())
            ],
        })
    # 行业排序：具体行业在前、通用在后；同组按名称稳定排序
    cats.sort(key=lambda c: (1 if c["universal"] else 0, c["category_cn"]))
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
