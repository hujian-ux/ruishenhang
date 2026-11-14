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

import json
from collections import deque
from typing import Any, Dict, List

from fastapi import HTTPException

from app.core.config import settings
from app.services import model_gateway
from app.services._m27_store import (
    Store, new_id, now, iso, audit_event, caller_id,
)

_WF = Store("workflow")

VALID_NODE_TYPES = {"llm", "rag", "tool", "approval"}

_NG = 94   # 节点高度（与前端 WfNode 的 H 对齐）
_GAP = 30  # 节点垂直间距


def _normalize_nodes(nodes: list, edges: list | None):
    """给每个节点补 id / position（坐标），并把边规整为 {from,to}。

    不传 edges 时，默认生成顺序链（n0→n1→…），保证无图结构时仍保持线性执行语义。
    """
    norm = []
    for i, n in enumerate(nodes):
        nid = n.get("id") or new_id("nd")
        pos = n.get("position") or {"x": 40, "y": 30 + i * (_NG + _GAP)}
        norm.append({
            "id": nid,
            "type": n["type"],
            "name": n.get("name") or n["type"],
            "config": n.get("config") or {},
            "position": pos,
        })
    ids = [n["id"] for n in norm]
    if edges:
        clean = []
        for e in edges:
            f = e.get("from") or e.get("source")
            t = e.get("to") or e.get("target")
            if f in ids and t in ids and f != t:
                clean.append({"from": f, "to": t})
        edges = clean
    else:
        edges = [{"from": ids[i], "to": ids[i + 1]} for i in range(len(ids) - 1)]
    return norm, edges


def _check_dag(nodes: list, edges: list) -> None:
    """校验边引用的节点都存在、且无环（工作流必须是 DAG）。"""
    ids = [n["id"] for n in nodes]
    adj: Dict[str, List[str]] = {i: [] for i in ids}
    indeg = {i: 0 for i in ids}
    for e in edges:
        f, t = e["from"], e["to"]
        if f not in adj or t not in adj:
            raise HTTPException(status_code=400, detail=f"边引用了不存在的节点：{f}→{t}")
        adj[f].append(t)
        indeg[t] += 1
    q = deque(i for i in ids if indeg[i] == 0)
    seen = 0
    while q:
        u = q.popleft(); seen += 1
        for v in adj[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                q.append(v)
    if seen != len(ids):
        raise HTTPException(status_code=400, detail="工作流存在环路（DAG 不允许），请检查连线")


def _execution_order(wf: dict) -> list:
    """按 edges 拓扑排序得到执行顺序；无边或存在环（理论上已被拦）则退回数组顺序。"""
    nodes = wf["nodes"]
    edges = wf.get("edges") or []
    if not edges:
        return list(nodes)
    ids = [n["id"] for n in nodes]
    pos = {n["id"]: n for n in nodes}
    adj: Dict[str, List[str]] = {i: [] for i in ids}
    indeg = {i: 0 for i in ids}
    for e in edges:
        f, t = e["from"], e["to"]
        if f in adj and t in adj:
            adj[f].append(t); indeg[t] += 1
    q = deque(i for i in ids if indeg[i] == 0)
    order = []
    while q:
        u = q.popleft(); order.append(u)
        for v in adj[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                q.append(v)
    if len(order) != len(ids):
        return list(nodes)
    return [pos[i] for i in order]


def _validate_nodes(nodes: list) -> None:
    if not isinstance(nodes, list) or len(nodes) == 0:
        raise HTTPException(status_code=400, detail="工作流至少需要一个节点")
    if len(nodes) > settings.WORKFLOW_MAX_NODES:
        raise HTTPException(status_code=400,
                            detail=f"节点数 {len(nodes)} 超过上限 WORKFLOW_MAX_NODES={settings.WORKFLOW_MAX_NODES}")
    for i, n in enumerate(nodes):
        if not isinstance(n, dict) or n.get("type") not in VALID_NODE_TYPES:
            bad = n.get("type") if isinstance(n, dict) else n
            raise HTTPException(status_code=400,
                                detail=f"第 {i + 1} 个节点类型非法：{bad}，应为 {sorted(VALID_NODE_TYPES)}")


def create_workflow(name: str, nodes: list, caller, edges: list | None = None) -> dict:
    _validate_nodes(nodes)
    normalized, edges = _normalize_nodes(nodes, edges)
    wid = new_id("wf")
    row = {
        "id": wid, "name": name or "", "nodes": normalized, "edges": edges,
        "status": "draft", "created_by": caller_id(caller),
        "tenant_id": getattr(caller, "tenantId", "") or "",  # H3：工作流归属当前租户
        "created_at": now(), "created_iso": iso(),
    }
    _WF.put(row)
    audit_event("workflow.create", caller, target=wid,
                meta={"name": name, "node_count": len(normalized)})
    return dict(row)


def get_workflow(wid: str, caller=None) -> dict:
    wf = _WF.get(wid)
    if wf is None:
        raise HTTPException(status_code=404, detail=f"工作流不存在：{wid}")
    # H3：跨租户——非平台管理员不能看其它租户的工作流
    if caller is not None and caller.role != "platform_admin" and \
            (wf.get("tenant_id") or "") != (getattr(caller, "tenantId", "") or ""):
        raise HTTPException(status_code=403, detail="forbidden: 非本租户工作流")
    return dict(wf)


def update_workflow(wid: str, name: str, nodes: list, edges: list | None, caller) -> dict:
    wf = _WF.get(wid)
    if wf is None:
        raise HTTPException(status_code=404, detail=f"工作流不存在：{wid}")
    if caller is not None and caller.role != "platform_admin" and \
            (wf.get("tenant_id") or "") != (getattr(caller, "tenantId", "") or ""):
        raise HTTPException(status_code=403, detail="forbidden: 非本租户工作流")
    _validate_nodes(nodes)
    normalized, edges = _normalize_nodes(nodes, edges)
    _check_dag(normalized, edges)
    _WF.update(wid, {
        "name": name or wf.get("name", ""),
        "nodes": normalized,
        "edges": edges,
        "updated_at": now(),
    })
    audit_event("workflow.update", caller, target=wid,
                meta={"name": name, "node_count": len(normalized)})
    return get_workflow(wid, caller)


def list_workflows(caller=None) -> list:
    rows = _WF.list()
    # H3（安全加固）：非平台管理员仅看本租户工作流，杜绝跨租户串看。
    if caller is not None and caller.role != "platform_admin":
        tid = getattr(caller, "tenantId", "") or ""
        rows = [r for r in rows if (r.get("tenant_id") or "") == tid]
    return rows


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
    # H3（安全加固）：跨租户——非平台管理员不能运行其它租户的工作流。
    if caller is not None and caller.role != "platform_admin" and (wf.get("tenant_id") or "") != (getattr(caller, "tenantId", "") or ""):
        raise HTTPException(status_code=403, detail="forbidden: 非本租户工作流")
    run_id = new_id("run")
    context: Dict[str, Any] = {"input": input_text}
    steps: List[Dict[str, Any]] = []
    status = "completed"
    for node in _execution_order(wf):
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


# ── 真执行（P1-6）：llm 节点真正调用模型，上下文贯穿，可绑定智能体人设 ──────
def _load_agent(agent_id: str, caller):
    """工作流借某个已注册智能体的人设运行：加载并做租户隔离。"""
    from app.services import agent_store
    ag = agent_store.get(agent_id)
    if ag is None:
        raise HTTPException(status_code=404, detail=f"智能体不存在：{agent_id}")
    tid = ag.get("tenant_id") or ""
    if tid and tid != (getattr(caller, "tenantId", "") or "") and caller.role != "platform_admin":
        raise HTTPException(status_code=403, detail="forbidden: 非本租户智能体")
    return ag


def _fmt_context(context: Dict[str, Any]) -> str:
    """把前序节点累计的 context 渲染成可读文本，喂给后续 llm 节点当上下文。"""
    blocks: List[str] = []
    for k, v in context.items():
        if k == "input":
            continue
        if k.startswith("rag:"):
            name = k[4:]
            snips = v if isinstance(v, list) else []
            body = "\n".join(f"- {s}" for s in snips) if snips else "(无命中片段)"
            blocks.append(f"知识检索【{name}】:\n{body}")
        elif k.startswith("tool:"):
            name = k[5:]
            if isinstance(v, dict):
                if v.get("ok"):
                    body = f"执行成功：{json.dumps(v.get('result', ''), ensure_ascii=False)[:800]}"
                elif v.get("denied"):
                    body = f"被权限拒绝：{v.get('reason', '')}"
                elif v.get("unavailable"):
                    body = f"工具不可用：{v.get('reason', '')}"
                else:
                    body = f"执行异常：{v.get('reason', '')}"
            else:
                body = str(v)
            blocks.append(f"工具【{name}】:\n{body}")
        elif k.startswith("llm:"):
            name = k[4:]
            blocks.append(f"生成【{name}】:\n{str(v)}")
        elif k.startswith("approval:"):
            name = k[10:]
            blocks.append(f"审批【{name}】:\n{json.dumps(v, ensure_ascii=False)}")
    return "\n\n".join(blocks)


def _build_workflow_user_prompt(input_text: str, instruction: str, context: Dict[str, Any]) -> str:
    parts = [
        f"用户原始输入：\n{input_text}\n",
        f"【本节点任务】\n{instruction}\n",
    ]
    prior = _fmt_context(context)
    if prior:
        parts.append(f"【已完成步骤的上下文（前序节点输出，请基于它继续推进）】\n{prior}\n")
    parts.append("请直接产出本节点的结果，不要重复解释任务本身。")
    return "\n".join(parts)


def _llm_node_real(node: dict, input_text: str, context: Dict[str, Any], caller,
                   *, agent=None, system_prompt=None) -> dict:
    """llm 节点真实调用模型（取代 dry_run 占位）。"""
    cfg = node.get("config") or {}
    instruction = cfg.get("instruction") or node.get("name") or "按上下文完成本步骤"
    sp = system_prompt or (
        "你是企业智能体平台的工作流执行引擎。请严格按节点任务指令，"
        "结合前序步骤的上下文，产出可直接使用的结果；不要编造未经检索/"
        "工具校验的事实。"
    )
    messages = [
        {"role": "system", "content": sp},
        {"role": "user", "content": _build_workflow_user_prompt(input_text, instruction, context)},
    ]
    try:
        res = model_gateway.routed_chat_sync_ex(messages, user_input=input_text, agent=agent)
        return {
            "mode": "executed",
            "real": bool(res.get("real")),
            "text": res.get("text") or "",
            "model_id": res.get("model_id"),
            "usage": res.get("usage") or {},
            "error": res.get("error") or "",
            "rotated": res.get("rotated", False),
        }
    except Exception as exc:  # 不冒充执行：明确标记失败，不产出伪造文本
        return {"mode": "executed", "real": False, "text": "",
                "usage": {}, "error": str(exc)[:160]}


def _exec_node(node: dict, input_text: str, context: Dict[str, Any], caller, agent, persona):
    """按节点类型执行一个节点（同步），返回 (type, name, output, ok)。"""
    t = node["type"]
    name = node.get("name") or t
    if t == "rag":
        out, ok = _rag_node(node, input_text, caller)
    elif t == "tool":
        out = _tool_node(node, caller)
        ok = bool(out.get("ok"))
    elif t == "approval":
        out = _approval_node(node)
        ok = True
    elif t == "llm":
        out = _llm_node_real(node, input_text, context, caller, agent=agent, system_prompt=persona)
        ok = bool(out.get("real"))
    else:
        out, ok = {"error": f"未知节点类型：{t}"}, False
    return t, name, out, ok


def _acc_usage(total: Dict[str, Any], usage: dict | None) -> None:
    if not usage:
        return
    total["prompt_tokens"] = (total["prompt_tokens"] or 0) + int(usage.get("prompt_tokens", 0) or 0)
    total["completion_tokens"] = (total["completion_tokens"] or 0) + int(usage.get("completion_tokens", 0) or 0)
    total["calls"] = (total["calls"] or 0) + 1


def run_workflow_real(wid: str, input_text: str, caller, *,
                      agent_id: str | None = None, system_prompt: str | None = None) -> dict:
    """工作流**真执行**（P1-6）：llm 节点真正调用模型，前序节点输出贯穿后续节点。
    rag/tool 走既有真实通道（带身份过滤），approval 停下等人工审核。
    agent_id 可绑定已注册智能体的人设（system_prompt）作为 llm 节点的 system 角色。
    """
    wf = _WF.get(wid)
    if wf is None:
        raise HTTPException(status_code=404, detail=f"工作流不存在：{wid}")
    if caller is not None and caller.role != "platform_admin" and \
            (wf.get("tenant_id") or "") != (getattr(caller, "tenantId", "") or ""):
        raise HTTPException(status_code=403, detail="forbidden: 非本租户工作流")

    agent = None
    persona = system_prompt
    if agent_id:
        agent = _load_agent(agent_id, caller)
        persona = persona or agent.get("system_prompt")

    run_id = new_id("run")
    context: Dict[str, Any] = {"input": input_text}
    steps: List[Dict[str, Any]] = []
    status = "completed"
    answers: List[str] = []
    total_usage: Dict[str, Any] = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}

    for node in _execution_order(wf):
        t, name, out, ok = _exec_node(node, input_text, context, caller, agent, persona)
        if t == "llm":
            context[f"llm:{name}"] = out.get("text", "")
            if out.get("text"):
                answers.append(out["text"])
            _acc_usage(total_usage, out.get("usage"))
        elif t == "rag":
            context[f"rag:{name}"] = out.get("snippets", [])
        elif t == "tool":
            context[f"tool:{name}"] = out
        elif t == "approval":
            context[f"approval:{name}"] = out
        steps.append({"type": t, "name": name, "output": out, "ok": ok})
        if t == "approval":
            status = "pending"
            break  # 停下等人工，不自动跳过后续节点

    audit_event("workflow.execute", caller, target=wid,
                meta={"run_id": run_id, "status": status, "steps": len(steps), "agent_id": agent_id or ""})
    return {
        "run_id": run_id,
        "status": status,
        "steps": steps,
        "context_keys": sorted(context.keys()),
        "dry_run": False,
        "answer": answers[-1] if answers else "",
        "answers": answers,
        "usage": total_usage,
        "agent_id": agent_id or "",
    }


async def arun_workflow_stream(wid: str, input_text: str, caller, *,
                               agent_id: str | None = None, system_prompt: str | None = None):
    """工作流真执行的 SSE 事件流：meta → 每节点 node_start/node_result → done。
    重节点体（rag/model/tool 含阻塞 IO）用 to_thread 跑，避免阻塞事件循环。
    """
    import asyncio
    wf = _WF.get(wid)
    if wf is None:
        yield {"type": "error", "detail": f"工作流不存在：{wid}"}
        return
    if caller is not None and caller.role != "platform_admin" and \
            (wf.get("tenant_id") or "") != (getattr(caller, "tenantId", "") or ""):
        yield {"type": "error", "detail": "forbidden: 非本租户工作流"}
        return

    agent = None
    persona = system_prompt
    if agent_id:
        try:
            agent = _load_agent(agent_id, caller)
        except HTTPException as exc:
            yield {"type": "error", "detail": exc.detail}
            return
        persona = persona or agent.get("system_prompt")

    run_id = new_id("run")
    yield {"type": "meta", "run_id": run_id, "dry_run": False, "status": "running"}
    context: Dict[str, Any] = {"input": input_text}
    answers: List[str] = []
    total_usage: Dict[str, Any] = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
    status = "completed"

    for node in _execution_order(wf):
        t = node["type"]
        name = node.get("name") or t
        yield {"type": "node_start", "node_type": t, "name": name}
        t2, name2, out, ok = await asyncio.to_thread(
            _exec_node, node, input_text, context, caller, agent, persona)
        if t2 == "llm":
            context[f"llm:{name2}"] = out.get("text", "")
            if out.get("text"):
                answers.append(out["text"])
            _acc_usage(total_usage, out.get("usage"))
        elif t2 == "rag":
            context[f"rag:{name2}"] = out.get("snippets", [])
        elif t2 == "tool":
            context[f"tool:{name2}"] = out
        elif t2 == "approval":
            context[f"approval:{name2}"] = out
        yield {"type": "node_result", "node_type": t2, "name": name2, "output": out, "ok": ok}
        if t2 == "approval":
            status = "pending"
            break

    yield {"type": "done", "status": status, "answer": answers[-1] if answers else "",
           "answers": answers, "usage": total_usage, "dry_run": False, "agent_id": agent_id or ""}
