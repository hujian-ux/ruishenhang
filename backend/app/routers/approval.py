"""approval 路由：M5 审批流真实化（第35章红线：评审通过才发布）。

- POST /approval/{approval_id}/decide：决策（仅 platform_admin / 本部门 dept_admin）
- GET  /approval/pending：待审列表（管理员）
决策通过 → 智能体 pending_review→published（可被 invoke）；驳回 → rejected。
"""
from fastapi import APIRouter, Depends, HTTPException, Path as PathParam

from app.deps import get_caller, get_trace_id
from app.models.caller import CallerContext
from app.models.schemas import ApprovalDecisionRequest, ApprovalDecisionResponse
from app.services import agent_store, approval_store, audit, approval_assistant, im_gateway, session_store

router = APIRouter(prefix="/approval", tags=["approval"])


def _require_admin(caller: CallerContext) -> None:
    if caller.role not in ("platform_admin", "dept_admin"):
        raise HTTPException(status_code=403, detail="forbidden: 无审批权限")


def _backfill_session(session_id: str, ticket: dict, execution: dict, decider_id: str,
                      status: str = "approved") -> None:
    """P0 收尾2 / P1-3：工具审批落定后，把结果作为 assistant 轮次回灌原会话；
    用户在对话历史里即可看到「已审批执行 + 结果」或「已驳回未执行」，不必另查审批台。
    会话不存在 / 已删则安全跳过（append_turn 静默返回）。任何异常不阻断审批返回。"""
    if not session_id:
        return
    try:
        tool = (ticket.get("payload") or {}).get("tool")
        if status == "approved":
            ok = bool((execution or {}).get("ok"))
            content = (execution or {}).get("content") or (execution or {}).get("error") or ""
            text = (
                f"【操作已审批执行】工具：{tool}\n"
                f"审批结果：已通过（审批人 {decider_id}）\n"
                f"执行结果：{'成功' if ok else '失败'}\n"
                f"{content[:4000]}"
            )
        else:
            text = (
                f"【操作已驳回】工具：{tool}\n"
                f"审批结果：已驳回（审批人 {decider_id}），该操作未被执行。"
            )
        session_store.append_turn(session_id, "assistant", text)
    except Exception:
        pass


@router.post("/{approval_id}/decide", response_model=ApprovalDecisionResponse)
async def decide(
    approval_id: str = PathParam(...),
    req: ApprovalDecisionRequest = None,
    caller: CallerContext = Depends(get_caller),
    trace_id: str = Depends(get_trace_id),
):
    _require_admin(caller)
    if req is None or req.decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="decision 必须为 approve|reject")
    try:
        ticket = approval_store.decide(
            approval_id, req.decision, caller.userId, caller.role, caller.deptId, req.comment, caller.tenantId,
        )
    except approval_store.TicketNotFound:
        audit.record("approval", caller, target=approval_id, result="error:not_found", trace_id=trace_id)
        raise HTTPException(status_code=404, detail=f"审批单不存在：{approval_id}")
    except approval_store.DuplicateDecision:
        audit.record("approval", caller, target=approval_id, result="error:duplicate", trace_id=trace_id)
        raise HTTPException(status_code=409, detail="该审批单已决策（终态不可改）")
    except approval_store.ForbiddenDecider:
        audit.record("approval", caller, target=approval_id, result="error:forbidden", trace_id=trace_id)
        raise HTTPException(status_code=403, detail="dept_admin 仅可审批本部门发起的单据")
    # ── 工具执行审批（P0-2 收尾 / P1-3）：通过即真正执行工具，不迁移智能体状态 ──
    # 执行入口按工具类型分发：内置/注册工具走 registry，外部 MCP 工具（mcp__ 前缀）
    # 走 mcp_client.call_mcp_tool（经沙箱硬闸 + 护栏授权）。驳回则不执行。
    if ticket.get("ticket_type") == "tool_execution":
        payload = ticket.get("payload") or {}
        tool = payload.get("tool", "")
        targs = payload.get("args", {}) or {}
        execution = None
        if ticket["status"] == "approved":
            tcaller = CallerContext(userId=ticket["requester_id"], deptId=ticket["dept_id"],
                                    role="user", tenantId=ticket.get("tenant_id") or "")
            try:
                if tool.startswith("mcp__"):
                    from app.services import mcp_client
                    res = mcp_client.call_mcp_tool(tool, targs)
                    execution = {
                        "ok": not bool(res.get("is_error")),
                        "content": res.get("content", ""),
                        "error": "" if not res.get("is_error") else res.get("content", ""),
                    }
                else:
                    from app.tools import registry as tool_registry
                    execution = tool_registry.execute(tool, targs, tcaller)
            except Exception as exc:
                execution = {"ok": False, "content": "", "error": f"exec_error:{exc}"}
            approval_store.record_execution(approval_id, execution)
        # P0 收尾2 / P1-3：审批落定后把执行结果（或驳回说明）回灌原会话轮次
        _backfill_session(payload.get("session_id"), ticket, execution, caller.userId,
                          status=ticket["status"])
        audit.record("approval", caller, target=approval_id, result=ticket["status"],
                     meta={"ticket_type": "tool_execution", "tool": tool,
                           "executed": ticket["status"] == "approved"},
                     trace_id=trace_id)
        return {
            "code": 0,
            "data": {
                "approval_id": approval_id,
                "ticket_type": "tool_execution",
                "status": ticket["status"],
                "tool": tool,
                "execution": execution,
            },
        }

    # ── 智能体发布审批（M5/M15/M46）：评审通过才发布 ──
    # M15：批次单一次决策整批迁移，避免 115 个技能包逐个点。
    target_ids = ticket.get("agent_ids") or [ticket["agent_id"]]
    new_status = "published" if ticket["status"] == "approved" else "rejected"
    migrated = []
    for aid in target_ids:
        if agent_store.set_status(aid, new_status):
            migrated.append(aid)
    agent = agent_store.get(ticket["agent_id"])
    # M46：多级进度信息
    stages = ticket.get("stages") or [{"name": "默认审批"}]
    total = len(stages)
    cur = ticket.get("current_stage", 0)
    finished = ticket["status"] != "pending"
    # M49：流转到下级（仍 pending）→ 自动回推 OA 卡片给下一级审批人；终态（通过/驳回）不推。
    if ticket["status"] == "pending":
        im_gateway.auto_notify_pending_async(ticket.get("tenant_id") or "", approval_id)
    stage_name = stages[cur]["name"] if not finished else None
    next_stage_name = stages[cur + 1]["name"] if (not finished and cur + 1 < total) else None
    audit.record("approval", caller, target=approval_id, result=ticket["status"],
                 meta={"agent_id": ticket["agent_id"], "agent_ids": target_ids,
                       "batch": bool(ticket.get("batch")), "decision": req.decision,
                       "migrated": len(migrated), "current_stage": cur,
                       "total_stages": total}, trace_id=trace_id)
    return {
        "code": 0,
        "data": {
            "approval_id": approval_id,
            "status": ticket["status"],
            "agent_id": ticket["agent_id"],
            "agent_status": agent["status"] if agent else None,
            "batch": bool(ticket.get("batch")),
            "migrated_count": len(migrated),
            "decided_by": caller.userId,
            "comment": ticket["comment"],
            # M46 多级进度
            "current_stage": cur,
            "total_stages": total,
            "stage_name": stage_name,
            "next_stage_name": next_stage_name,
            "finished": finished,
        },
    }


@router.get("/pending")
async def pending(caller: CallerContext = Depends(get_caller),
                 only_my_stage: bool = False):
    """待审列表：platform_admin 全量，dept_admin 本部门 + 本租户。

    only_my_stage=true 时仅返回当前级可由该调用人审批的单据（含租户/部门/级别约束）。
    """
    _require_admin(caller)
    dept = caller.deptId if caller.role == "dept_admin" else None
    # M43：dept_admin 仅看本租户；platform_admin 传 None 看全量租户。
    tenant_id = None if caller.role == "platform_admin" else caller.tenantId
    rows = approval_store.pending_list(
        dept, tenant_id,
        caller_role=caller.role if only_my_stage else None,
        caller_dept=caller.deptId if only_my_stage else None,
        only_my_stage=only_my_stage,
    )
    return {"code": 0, "data": {"count": len(rows), "tickets": rows}}


@router.get("/{approval_id}")
async def ticket_detail(approval_id: str = PathParam(...),
                        caller: CallerContext = Depends(get_caller)):
    """审批单详情（含多级进度）；申请人本人或管理员可读。"""
    try:
        t = approval_store.get_ticket(approval_id)
    except approval_store.TicketNotFound:
        raise HTTPException(status_code=404, detail=f"审批单不存在：{approval_id}")
    if caller.role == "platform_admin":
        pass  # 平台管理员看全域
    elif t.get("tenant_id") and t["tenant_id"] != caller.tenantId:
        raise HTTPException(status_code=403, detail="forbidden: 非本租户审批单")
    elif caller.role == "dept_admin":
        pass  # 本部门管理员可读本租户单据
    elif t["requester_id"] != caller.userId:
        raise HTTPException(status_code=403, detail="forbidden: 仅申请人或管理员可查看")
    stages = t.get("stages") or [{"name": "默认审批"}]
    total = len(stages)
    cur = t.get("current_stage", 0)
    finished = t["status"] != "pending"
    return {
        "code": 0,
        "data": {
            "approval_id": t["approval_id"],
            "agent_id": t["agent_id"],
            "agent_ids": t.get("agent_ids"),
            "batch": bool(t.get("batch")),
            "requester_id": t["requester_id"],
            "dept_id": t["dept_id"],
            "tenant_id": t.get("tenant_id"),
            "status": t["status"],
            "stages": stages,
            "current_stage": cur,
            "total_stages": total,
            "stage_name": stages[cur]["name"] if not finished else "已完成",
            "finished": finished,
            "decisions": t.get("decisions") or [],
            "created_at": t.get("created_at"),
            "decided_at": t.get("decided_at"),
        },
    }


@router.post("/{approval_id}/assist")
async def assist(approval_id: str = PathParam(...),
                 enrich: bool = False,
                 caller: CallerContext = Depends(get_caller)):
    """审批助手（辅助层）：返回申请草稿/摘要/合规风险建议，不落账。

    enrich=true 时额外调用真模型通道生成一句话自然语言提示（llm_hint），
    真模型不可用则优雅降级（不出 llm_hint，不影响结构化结论）。
    权限：申请人本人或管理员可读；最终 approve/reject 仍由审批台 decide() 落账。
    """
    try:
        t = approval_store.get_ticket(approval_id)
    except approval_store.TicketNotFound:
        raise HTTPException(status_code=404, detail=f"审批单不存在：{approval_id}")
    if caller.role == "platform_admin":
        pass
    elif t.get("tenant_id") and t["tenant_id"] != caller.tenantId:
        raise HTTPException(status_code=403, detail="forbidden: 非本租户审批单")
    elif caller.role == "dept_admin":
        pass
    elif t["requester_id"] != caller.userId:
        raise HTTPException(status_code=403, detail="forbidden: 仅申请人或管理员可使用")
    return {"code": 0, "data": approval_assistant.assistant_advise(approval_id, enrich=enrich)}
