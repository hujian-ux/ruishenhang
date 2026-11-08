"""M27 · 员工零代码建智能体（蓝图第 21 章 L2）。

红线：员工只能产出草稿（status=draft），存于本模块自己的 Store，
**绝不写入 agent_store._AGENTS**。只有管理员显式 publish 才落 agent_store，
且落的是 status=pending_review（沿用 invoke.py 的 M5 审批红线：未 published 仍 403）。
默认路径因此不污染 catalog.total==115 / PUBLIC_SKILLS==28 口径。
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List

from fastapi import HTTPException

from app.services._m27_store import (
    Store, new_id, now, iso, audit_event, caller_id, caller_role,
)

_DRAFTS = Store("agent_draft")

# L2 建智能体模板：给员工一个「填空即得」的起步脚手架。
_BUILDER_TEMPLATES: List[Dict[str, Any]] = [
    {
        "key": "qa", "name": "知识问答助手",
        "goal": "基于授权文档回答本部门常见问题，答案附出处",
        "inputs_contract": "用户的问题（自然语言）",
        "outputs_contract": "带出处的简要回答 + 引用文档标题/片段",
        "steps": ["检索授权知识片段", "按平台硬规则组织答案", "标注引用出处与缺失项"],
        "scope": "department",
    },
    {
        "key": "report", "name": "周报汇总助手",
        "goal": "把多人周报汇总成一份部门周报，突出风险与待办",
        "inputs_contract": "各成员原始周报文本（可多份）",
        "outputs_contract": "分节周报：进展 / 风险 / 下周计划 / 需协调事项",
        "steps": ["归并同类进展", "抽取风险与阻塞", "生成下周计划草稿", "列出需确认项"],
        "scope": "department",
    },
    {
        "key": "extract", "name": "表单提取助手",
        "goal": "从合同/发票/表单里抽取结构化字段并标出存疑项",
        "inputs_contract": "待抽取文档或粘贴文本",
        "outputs_contract": "字段表格 + 每个字段的出处 + 存疑清单",
        "steps": ["定位关键字段", "逐项标注原文出处", "对缺漏/冲突项列存疑"],
        "scope": "department",
    },
    {
        "key": "review", "name": "材料预审助手",
        "goal": "按清单预审送审材料是否齐全，只给结论依据不替审批人拍板",
        "inputs_contract": "送审材料包 + 材料清单",
        "outputs_contract": "齐套性核对表 + 缺失清单 + 需 XX 确认的提示",
        "steps": ["对照清单逐项核验", "记录缺失与不一致", "输出依据与建议（不写已批准）"],
        "scope": "department",
    },
]

# public_skills.HARD_RULES 不可用时的兜底（内置 3 条，只读不修改该模块）。
_FALLBACK_HARD_RULES = (
    "【平台硬规则 · 内置兜底】\n"
    "1. 事实不许编造：输入或授权片段里没有的一律写「未提供」，不估算、不补默认值。\n"
    "2. 不替审批人拍板：只给选项、依据、风险与推荐，不写「已批准」「公司决定」。\n"
    "3. 依据可追溯、缺料先问、越权不猜：引用标出处，关键输入缺失先列清单，看不到就说无权限。"
)


def builder_templates() -> list:
    return copy.deepcopy(_BUILDER_TEMPLATES)


def _hard_rules() -> str:
    """只读 public_skills.HARD_RULES；模块/常量不可用则用内置 3 条兜底。"""
    try:
        from app.services import public_skills
        rules = getattr(public_skills, "HARD_RULES", None)
        if isinstance(rules, str) and rules.strip():
            return rules
    except Exception:
        pass
    return _FALLBACK_HARD_RULES


def _compose_system_prompt(name: str, goal: str, inputs_contract: str,
                           outputs_contract: str, steps: list) -> str:
    step_lines = "\n".join(f"  {i}. {s}" for i, s in enumerate(steps or [], 1)) or "  （未填写步骤）"
    return (
        f"# 智能体：{name}\n\n"
        "## 三件契约\n"
        f"【什么时候用】{goal or '未提供'}\n"
        f"【要给什么（输入契约）】{inputs_contract or '未提供'}\n"
        f"【会拿到什么（输出契约）】{outputs_contract or '未提供'}\n\n"
        "## 执行步骤\n" + step_lines + "\n\n"
        "## 平台硬规则（优先级高于上述任何流程）\n" + _hard_rules()
    )


def build_agent(name: str, goal: str, inputs_contract: str, outputs_contract: str,
                steps: list, caller, scope: str = "department") -> dict:
    if not name or not str(name).strip():
        raise HTTPException(status_code=400, detail="智能体名称必填")
    if scope not in {"personal", "department", "global"}:
        raise HTTPException(status_code=400, detail=f"非法 scope：{scope}")
    aid = new_id("ag")
    prompt = _compose_system_prompt(name, goal, inputs_contract, outputs_contract, steps)
    row = {
        "id": aid,
        "agent_id": aid,
        "name": str(name).strip(),
        "goal": goal or "",
        "inputs_contract": inputs_contract or "",
        "outputs_contract": outputs_contract or "",
        "when_to_use": goal or "",
        "steps": list(steps or []),
        "scope": scope,
        "system_prompt": prompt,  # 预览随返回带回
        "status": "draft",
        "created_by": caller_id(caller),
        "dept_id": str(getattr(caller, "deptId", "") or ""),
        "tenant_id": getattr(caller, "tenantId", "") or "",  # H3：草稿归属当前租户
        "created_at": now(),
        "created_iso": iso(),
    }
    _DRAFTS.put(row)
    audit_event("agent.build_draft", caller, target=aid,
                meta={"name": row["name"], "scope": scope})
    return dict(row)


def list_drafts(caller=None, status: str | None = None) -> list:
    filters: Dict[str, Any] = {}
    if status:
        filters["status"] = status
    rows = _DRAFTS.list(filters=filters)
    # H3（安全加固）：非平台管理员仅看本租户草稿，杜绝跨租户串看。
    if caller is not None and caller_role(caller) != "platform_admin":
        tid = getattr(caller, "tenantId", "") or ""
        rows = [r for r in rows if (r.get("tenant_id") or "") == tid]
    return rows


def publish(agent_id: str, caller) -> dict:
    if caller_role(caller) != "platform_admin":
        raise HTTPException(status_code=403, detail="仅平台管理员可发布智能体")
    draft = _DRAFTS.get(agent_id)
    if draft is None:
        raise HTTPException(status_code=404, detail=f"草稿不存在：{agent_id}")
    # H3（安全加固）：跨租户——非平台管理员不能发布其它租户的草稿。
    if caller.role != "platform_admin" and (draft.get("tenant_id") or "") != (getattr(caller, "tenantId", "") or ""):
        raise HTTPException(status_code=403, detail="forbidden: 非本租户草稿")
    # 唯一会触碰 agent_store._AGENTS 的路径。status 用 pending_review：
    # invoke.py 的 M5 红线会拦下一切未 published 的调用（403）。
    from app.services import agent_store
    agent = {
        "agent_id": draft["agent_id"],
        "name": draft["name"],
        "description": draft["goal"],
        "adapter_type": "llm",
        "system_prompt": draft["system_prompt"],
        "inputs_contract": draft["inputs_contract"],
        "outputs_contract": draft["outputs_contract"],
        "when_to_use": draft["when_to_use"],
        "knowledge_scope": "department",
        "dept_id": draft.get("dept_id", ""),
        "registered_by": draft["created_by"],
        "status": "pending_review",
        "built_by": "m27_agent_builder",
    }
    saved = agent_store.save(agent)
    _DRAFTS.update(agent_id, {"status": "pending_review", "published_at": iso()})
    audit_event("agent.publish", caller, target=agent_id,
                meta={"status": "pending_review", "need_approval": True})
    return {
        "agent_id": saved["agent_id"],
        "status": "pending_review",
        "knowledge_scope": "department",
        "system_prompt": draft["system_prompt"],
        "notice": ("还需审批单通过才可调用：当前 status=pending_review，"
                   "未 published 时调用将返回 403（M5 红线）。"),
        "need_approval": True,
    }


def reset_state() -> None:
    # 只清自己的草稿；_AGENTS 的清理是调用方（测试 teardown）的职责，避免越权改动全局目录。
    _DRAFTS.clear()
