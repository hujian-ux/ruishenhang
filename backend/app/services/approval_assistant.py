"""M46 · 审批助手（辅助层，只建议不落账）。

设计边界（与架构图一致）：
- 本模块是**辅助面**，帮助申请人填单 / 抽要点 / 做合规风险校验，帮审批人看进度与摘要。
- 审批的**最终 commit（approve/reject）永远走传统引擎 approval_store.decide()**，
  任何 LLM/技能都不能替代管理员落账——红线在 invoke.py 平台级强制。
- LLM 仅用于生成自然语言建议，且失败优雅降级（不影响结构化结论）。
"""
from typing import Any, Dict, List, Optional

from app.services import approval_store, agent_store, tenant_store

RISK_GLOBAL = "知识域为「全局」：对所有部门可见，建议提升一级审批。"
RISK_BATCH = "批量审批包较大：建议分批或抽样复核，避免一次性误发。"
RISK_NODESC = "智能体描述缺失：审批人无法判断用途，易被驳回。"
RISK_PENDING_MULTI = "处于多级流转中：当前仅某一级已批，尚未最终发布。"


def build_application_draft(agent: Dict[str, Any],
                           ticket: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """根据智能体信息生成申请单草稿（确定性，不依赖 LLM）。"""
    scope = agent.get("knowledge_scope") or agent.get("scope") or "department"
    t = ticket or {}
    return {
        "title": agent.get("name", "未命名智能体"),
        "dept_id": t.get("dept_id") or agent.get("dept_id", ""),
        "knowledge_scope": scope,
        "reason_template": (
            f"申请发布智能体「{agent.get('name', '')}」，"
            f"用途：{agent.get('description', '（待补充）')}；"
            f"知识域：{scope}；请审批。"
        ),
        "checklist": [
            "补充清晰的业务用途描述",
            "确认知识域（个人/本部门/全局）符合数据安全要求",
            "如为全局，请说明跨部可见的必要性",
        ],
    }


def summarize_ticket(ticket: Dict[str, Any],
                     agent: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """生成审批单摘要与风险提示。"""
    stages = ticket.get("stages") or [{"name": "默认审批"}]
    cur = ticket.get("current_stage", 0)
    total = len(stages)
    finished = ticket["status"] != "pending"
    risk: List[str] = []
    if agent:
        scope = agent.get("knowledge_scope") or agent.get("scope")
        if scope == "global":
            risk.append(RISK_GLOBAL)
        if not (agent.get("description") or "").strip():
            risk.append(RISK_NODESC)
    if ticket.get("batch") and len(ticket.get("agent_ids") or []) >= 20:
        risk.append(RISK_BATCH)
    if not finished and total > 1:
        risk.append(RISK_PENDING_MULTI)
    return {
        "ticket_id": ticket["approval_id"],
        "status": ticket["status"],
        "stage_progress": f"{min(cur + 1, total)}/{total}",
        "current_stage": stages[cur]["name"] if not finished else "已完成",
        "risk_flags": risk,
        "summary": (
            f"该审批单当前状态：{ticket['status']}；"
            f"进度 {min(cur + 1, total)}/{total} 级"
            + (f"；风险提示 {len(risk)} 项" if risk else "；暂无高风险项")
        ),
    }


def compliance_flags(agent: Optional[Dict[str, Any]],
                     tenant_config: Dict[str, Any]) -> List[str]:
    """合规校验：返回阻断/提醒级标记（确定性）。"""
    flags: List[str] = []
    if agent:
        if not (agent.get("description") or "").strip():
            flags.append("缺少描述：无法进入评审，请先补全。")
        scope = agent.get("knowledge_scope") or agent.get("scope")
        if scope == "global" and not (tenant_config.get("approval") or {}).get("allow_global_scope"):
            flags.append("全局知识域未获租户授权：需平台管理员特别批准。")
    return flags


def _enrich_with_llm(prompt: str) -> Optional[str]:
    """可选：用 LLM 生成自然语言建议；失败返回 None（不影响主结论）。"""
    try:
        from app.services import model_gateway
        res = model_gateway.chat_sync_ex(
            None, [{"role": "user", "content": prompt}], llm_real=False)
        return (res or {}).get("text")
    except Exception:  # noqa: BLE001
        return None


def assistant_advise(approval_id: str, enrich: bool = False) -> Dict[str, Any]:
    """汇总一张审批单的 AI 辅助建议（不落账）。"""
    ticket = approval_store.get_ticket(approval_id)
    agent = agent_store.get(ticket.get("agent_id"))
    tenant_cfg = tenant_store.get_config(ticket.get("tenant_id") or "") or {}
    draft = build_application_draft(agent or {}, ticket)
    summary = summarize_ticket(ticket, agent)
    compliance = compliance_flags(agent, tenant_cfg)
    advice = {
        "approval_id": approval_id,
        "draft": draft,
        "summary": summary,
        "compliance_flags": compliance,
        "note": "以上为 AI 辅助建议，最终审批由授权管理员在审批台 commit，助手不落账。",
    }
    if enrich:
        llm_text = _enrich_with_llm(
            f"请用一句话提示审批人关注这张审批单的重点：{summary['summary']}")
        if llm_text:
            advice["llm_hint"] = llm_text
    return advice
