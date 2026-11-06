"""M27 · 节点式工作流编排（蓝图第 21 章 L3）。

顺序执行节点、把上一步输出塞进 context。明确「不冒充」的四条：
- rag → 真调 knowledge.retrieve(query, caller, top_k=3)，必须带 caller 过第 18 章可见性过滤；
         失败降级为空片段并标记 degraded；
- tool → 调 guardrails.guard_tool_call(tool_name, args, caller)；模块/函数不存在时
         标记 unavailable，不假装执行、不产出伪造结果；
- approval → status=pending 并停止，等人工审核，不自动跳过；
- llm → 纯占位（不调模型），把 context 摘要写进输出并明确 "mode":"dry_run"。
"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import HTTPException

from app.core.config import settings
from app.services._m27_store import (
    Store, new_id, now, iso, audit_event, caller_id,
)

_WF = Store("workflow")

VALID_NODE_TYPES = {"llm", "rag", "tool", "approval"}


def create_workflow(name: str, nodes: list, caller) -> dict:
    if not isinstance(nodes, list) or len(nodes) == 0:
        raise HTTPException(status_code=400, detail="工作流至少需要一个节点")
    max_nodes = settings.WORKFLOW_MAX_NODES  # 运行时读，便于 monkeypatch
    if len(nodes) > max_nodes:
        raise HTTPException(status_code=400,
                            detail=f"节点数 {len(nodes)} 超过上限 WORKFLOW_MAX_NODES={max_nodes}")
    for i, n in enumerate(nodes):
        if not isinstance(n, dict) or n.get("type") not in VALID_NODE_TYPES:
            bad = n.get("type") if isinstance(n, dict) else n
            raise HTTPException(status_code=400,
                                detail=f"第 {i + 1} 个节点类型非法：{bad}，应为 {sorted(VALID_NODE_TYPES)}")
    wid = new_id("wf")
    normalized = [{
        "type": n["type"],
        "name": n.get("name") or n["type"],
        "config": n.get("config") or {},
    } for n in nodes]
    row = {
        "id": wid, "name": name or "", "nodes": normalized,
        "status": "draft", "created_by": caller_id(caller),
        "created_at": now(), "created_iso": iso(),
    }
    _WF.put(row)
    audit_event("workflow.create", caller, target=wid,
                meta={"name": name, "node_count": len(normalized)})
    return dict(row)


def list_workflows(caller=None) -> list:
    return _WF.list()


def _rag_node(node: dict, input_text: str, caller):
    cfg = node.get("config") or {}
    query = cfg.get("query") or input_text
    try:
        from app.services import knowledge
        # caller 必须一路带上：retrieve 内部按身份做单一过滤，不带就是越权。
        hits, filtered_out = knowledge.retrieve(query, caller, top_k=3)
        snippets = [str(h.get("snippet", "") or "") for h in (hits or [])]
        return {"snippets": snippets, "count": len(snippets),
                "filtered_out": filtered_out, "degraded": False}, True
    except Exception as exc:
        return {"snippets": [], "count": 0, "filtered_out": 0,
                "degraded": True, "error": str(exc)[:160]}, False


def _tool_node(node: dict, caller):
    cfg = node.get("config") or {}
    tool_name = cfg.get("tool_name") or node.get("name") or "unknown_tool"
    args = cfg.get("args") or {}
    if not settings.GUARDRAILS_TOOL_AUTH:
        return {"ok": False, "unavailable": True, "tool": tool_name,
                "reason": "GUARDRAILS_TOOL_AUTH=0，工具鉴权通道未开启"}
    try:
        from app.services import guardrails  # 当前项目未提供该模块
        fn = getattr(guardrails, "guard_tool_call", None)
        if fn is None:
            raise RuntimeError("guardrails.guard_tool_call 不存在")
        result = fn(tool_name, args, caller)
        if not result.get("allowed"):
            # 第44章：工具调用被同一过滤器拒绝时，节点必须失败，
            # 不能把「鉴权拒绝」包装成一次成功的工具执行。
            return {"ok": False, "unavailable": False, "denied": True, "tool": tool_name,
                    "reason": result.get("reason") or "工具调用未通过权限校验",
                    "guard": result}
        return {"ok": True, "unavailable": False, "tool": tool_name, "result": result}
    except Exception as exc:
        # 不假装执行：明确标记 unavailable，不产出「伪造的工具结果」。
        return {"ok": False, "unavailable": True, "tool": tool_name,
                "reason": f"guardrails 模块不可用：{str(exc)[:120]}"}


def _approval_node(node: dict) -> dict:
    return {"status": "pending", "message": "等待人工审核，流程已停止，未自动跳过后续节点"}


def _llm_node(node: dict, context: Dict[str, Any]) -> dict:
    # 纯占位：只看 context 摘要，不调用任何模型，不冒充真实生成。
    summary = {k: (len(v) if isinstance(v, (list, dict, str)) else 1)
               for k, v in context.items()}
    return {
        "mode": "dry_run",
        "message": "LLM 节点为纯占位，未调用模型（dry_run），不冒充真实生成结果",
        "context_summary": summary,
        "context_keys": sorted(context.keys()),
    }


def run_workflow(wid: str, input_text: str, caller) -> dict:
    wf = _WF.get(wid)
    if wf is None:
        raise HTTPException(status_code=404, detail=f"工作流不存在：{wid}")
    run_id = new_id("run")
    context: Dict[str, Any] = {"input": input_text}
    steps: List[Dict[str, Any]] = []
    status = "completed"
    for node in wf["nodes"]:
        t = node["type"]
        name = node.get("name") or t
        if t == "rag":
            out, ok = _rag_node(node, input_text, caller)
            context[f"rag:{name}"] = out["snippets"]
            steps.append({"type": t, "name": name, "output": out, "ok": ok})
        elif t == "tool":
            out = _tool_node(node, caller)
            context[f"tool:{name}"] = out
            steps.append({"type": t, "name": name, "output": out, "ok": bool(out.get("ok"))})
        elif t == "approval":
            status = "pending"
            out = _approval_node(node)
            context[f"approval:{name}"] = out
            steps.append({"type": t, "name": name, "output": out, "ok": True})
            break  # 停止，不自动跳过，后续节点一律不执行
        elif t == "llm":
            out = _llm_node(node, context)
            context[f"llm:{name}"] = out
            steps.append({"type": t, "name": name, "output": out, "ok": True})
    audit_event("workflow.run", caller, target=wid,
                meta={"run_id": run_id, "status": status, "steps": len(steps)})
    return {
        "run_id": run_id,
        "status": status,
        "steps": steps,
        "context_keys": sorted(context.keys()),
        "dry_run": True,
    }


def reset_state() -> None:
    _WF.clear()
