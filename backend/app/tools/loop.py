"""P0-2 通用工具调用循环（think → tool → observe）。

模型用自由文本里的 工具调用块 表达调用意图（模型无关，适配 OpenAI 兼容端点，
无需依赖厂商 function-calling）：

    ```tool_call
    {"tool": "<名称>", "args": {<参数>}}
    ```

循环流程（同步，兼容 run_agent 与 run_agent_stream）：
  - 最多 max_rounds 轮（默认 6）；
  - 每个工具调用先过 sandbox.check（P0-3 硬边界）→ 再交 guardrails.guard_tool_call
    （白名单 / 私网拦截 / 审计单一过滤点）；
  - 高危工具（shell）直接转 /approval，返回 approval_required，绝不执行；
  - 被拒调用把原因作为 observation 回灌，让模型自我纠正；
  - 无工具调用块或达上限则取最后文本为最终答案。

get_llm(messages) -> {"text":..., "usage":...} 由调用方注入（依赖反转，避免
tools 包反向依赖 agent_runtime 造成循环导入）。
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional

from app.services import approval_store, guardrails
from app.tools import registry, sandbox

# 工具调用块：支持 ```tool_call ... ``` 与 <tool_call> ... </tool_call> 两种写法
_TOOL_BLOCK_RE = re.compile(
    r"```tool_call\s*(\{.*?\})\s*```|"
    r"<tool_call>\s*(\{.*?\})\s*</tool_call>",
    re.DOTALL,
)
_HIGH_RISK_TOOLS = {"shell"}
_OBS_TRUNCATE = 4000


def _parse_tool_call(text: str) -> Optional[dict]:
    """从模型文本里解析出第一个工具调用块；无则返回 None。"""
    if not text:
        return None
    m = _TOOL_BLOCK_RE.search(text)
    if not m:
        return None
    blob = m.group(1) or m.group(2)
    try:
        obj = json.loads(blob)
    except Exception:
        return None
    if not isinstance(obj, dict) or "tool" not in obj:
        return None
    obj.setdefault("args", {})
    return obj


def _obs_block(tool: str, content: str, ok: bool) -> str:
    tag = "工具结果" if ok else "工具执行失败"
    return f"[{tag}] {tool}:\n{(content or '')[:_OBS_TRUNCATE]}"


def run_tool_loop(initial_input: str, system_prompt: str, caller: Any,
                  get_llm: Callable[[List[dict]], dict], max_rounds: int = 6,
                  rules: Optional[dict] = None, agent_id: str = "",
                  session_id: str = "") -> dict:
    """执行工具循环，返回 {
        answer, tool_calls(执行/尝试记录), usage, rounds,
        approval_required, approval(待审批的工具调用)
    }。"""
    rules = rules or sandbox.load_rules()
    messages: List[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": initial_input},
    ]
    tool_calls: List[dict] = []
    final_text: Optional[str] = None
    last_usage: dict = {}
    approval: Optional[dict] = None

    for _ in range(max_rounds + 1):
        resp = get_llm(messages)
        last_usage = resp.get("usage") or last_usage
        text = resp.get("text", "")
        block = _parse_tool_call(text)
        if not block:
            final_text = text
            break

        tool = block["tool"]
        args = block.get("args", {}) or {}

        # 1) 沙箱硬边界（P0-3）
        sb = sandbox.check(tool, args, caller, rules=rules)
        if not sb["allowed"]:
            tool_calls.append({"tool": tool, "allowed": False, "reason": sb["reason"]})
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user",
                             "content": _obs_block(tool, f"调用被沙箱拒绝：{sb['reason']}", False)})
            continue

        # 2) 高危工具转审批（不执行，落单到审批流，审批通过后才真正执行）
        if tool in _HIGH_RISK_TOOLS:
            ticket = approval_store.create_tool_approval_ticket(
                caller, agent_id, tool, args, session_id=session_id)
            approval = {"tool": tool, "args": args, "ticket_id": ticket["approval_id"]}
            tool_calls.append({"tool": tool, "allowed": False,
                               "reason": "high_risk_escalated_to_approval",
                               "ticket_id": ticket["approval_id"]})
            final_text = (
                f"操作「{tool}」属于高危操作，已提交人工审批（审批单 {ticket['approval_id']}），"
                f"待管理员批准后才会执行；在审批通过前该操作不会被执行。"
            )
            break

        # 2.5) 外部 MCP 工具（P1-3）：授权 + 执行，或 requires_approval 挂起审批
        if tool.startswith("mcp__"):
            from app.services import mcp_client
            if mcp_client.mcp_requires_approval(tool):
                # 需审批：不执行，落 tool_execution 单，审批通过后才真正执行
                ticket = approval_store.create_tool_approval_ticket(
                    caller, agent_id, tool, args, session_id=session_id)
                approval = {"tool": tool, "args": args, "ticket_id": ticket["approval_id"]}
                tool_calls.append({"tool": tool, "allowed": False,
                                   "reason": "mcp_tool_escalated_to_approval",
                                   "ticket_id": ticket["approval_id"]})
                final_text = (
                    f"外部工具「{tool}」需要人工审批（审批单 {ticket['approval_id']}），"
                    f"待管理员批准后才会执行；在审批通过前该操作不会被执行。"
                )
                break
            # 免审：经 guardrails 单一过滤点（含授权）后由 mcp_client 单点执行
            g = guardrails.guard_tool_call(tool, args, caller)
            if not g["allowed"]:
                tool_calls.append({"tool": tool, "allowed": False,
                                   "reason": "guard:" + g["reason"]})
                messages.append({"role": "assistant", "content": text})
                messages.append({"role": "user",
                                 "content": _obs_block(tool, f"调用被护栏拒绝：{g['reason']}", False)})
                continue
            content = (g.get("result") or {}).get("content", "") or ""
            tool_calls.append({
                "tool": tool, "allowed": True, "ok": True,
                "content": content[:_OBS_TRUNCATE], "error": "",
            })
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user",
                             "content": _obs_block(tool, content, True)})
            continue

        # 3) 工具合法性 + 护栏单一过滤点（白名单 / 私网 / 审计）
        # 内置工具（guardrails._ALLOWED_TOOLS）走 guardrails 全量检查；
        # 自定义/已注册工具以注册表为准，guardrails 默认放行（其白名单不含自定义工具）。
        in_builtin = tool in guardrails._ALLOWED_TOOLS
        registered = registry.get(tool) is not None
        if not in_builtin and not registered:
            tool_calls.append({"tool": tool, "allowed": False, "reason": "tool_not_registered"})
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user",
                             "content": _obs_block(tool, "未知或已下线的工具", False)})
            continue
        tc = guardrails.guard_tool_call(tool, args, caller)
        if not tc["allowed"] and registered and not in_builtin:
            tc = {"allowed": True, "reason": "custom_tool_ok"}  # 自定义工具以注册表为准
        if not tc["allowed"]:
            tool_calls.append({"tool": tool, "allowed": False,
                               "reason": "guard:" + tc["reason"]})
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user",
                             "content": _obs_block(tool, f"调用被护栏拒绝：{tc['reason']}", False)})
            continue

        # 4) 执行并观察
        res = registry.execute(tool, args, caller)
        tool_calls.append({
            "tool": tool,
            "allowed": True,
            "ok": res.get("ok", False),
            "content": (res.get("content") or "")[:_OBS_TRUNCATE],
            "error": res.get("error", ""),
        })
        messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user",
                         "content": _obs_block(tool, res.get("content") or res.get("error") or "",
                                               res.get("ok", False))})

    if final_text is None:
        final_text = "（工具循环达到上限，未能生成最终答案，请简化任务或关闭工具调用后重试。）"

    return {
        "answer": final_text,
        "tool_calls": tool_calls,
        "usage": last_usage,
        "rounds": len(tool_calls),
        "approval_required": approval is not None,
        "approval": approval,
    }


async def run_tool_loop_stream(initial_input: str, system_prompt: str, caller: Any,
                               stream_llm: Callable[[List[dict]], Any], max_rounds: int = 6,
                               rules: Optional[dict] = None, agent_id: str = "",
                               session_id: str = "", should_cancel: Any = None):
    """P0 收尾1：工具循环的流式版本（逐 token 流 + 工具调用 / 结果中间事件）。

    stream_llm(messages) 是异步生成器，产出 model_gateway.chat_stream_ex 事件：
        {"t": "delta", "text": <增量>}
        {"t": "end",   "usage": ..., "real": ..., "model_id": ..., "error": ..., ...}
    依次 yield 事件（供 SSE 透传）：
        {"type": "token",          "text": <增量>}
        {"type": "tool_call",      "tool": ..., "args": ...}
        {"type": "tool_result",    "tool": ..., "ok": bool, "content": str}
        {"type": "tool_escalated", "tool": ..., "args": ..., "ticket_id": ...}
        {"type": "cancelled"}
        {"type": "done",           "result": <与 run_agent 工具分支同结构>}
    与同步 run_tool_loop 共享同一套沙箱 / 护栏 / 高危转审批逻辑，保证行为一致。
    """
    rules = rules or sandbox.load_rules()
    messages: List[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": initial_input},
    ]
    tool_calls: List[dict] = []
    final_text: Optional[str] = None
    last_usage: dict = {}
    approval: Optional[dict] = None

    for _ in range(max_rounds + 1):
        full: list = []
        async for ev in stream_llm(messages):
            if ev.get("t") == "delta":
                full.append(ev.get("text", ""))
                yield {"type": "token", "text": ev.get("text", "")}
            else:  # ev["t"] == "end"
                last_usage = {
                    "model_id": ev.get("model_id"), "real": ev.get("real"),
                    "usage": ev.get("usage") or {}, "error": ev.get("error"),
                    "routed_tier": ev.get("routed_tier"),
                    "routed_model": ev.get("routed_model"),
                    "latency_ms": ev.get("latency_ms"),
                    "throttled": ev.get("throttled", False),
                }
        text = "".join(full)
        # 每轮生成后先检查取消：流式中途点「停止」，下一个工具轮之前就停。
        if should_cancel and should_cancel():
            yield {"type": "cancelled"}
            return
        block = _parse_tool_call(text)
        if not block:
            final_text = text
            break

        tool = block["tool"]
        args = block.get("args", {}) or {}

        # 1) 沙箱硬边界（P0-3）
        sb = sandbox.check(tool, args, caller, rules=rules)
        if not sb["allowed"]:
            tool_calls.append({"tool": tool, "allowed": False, "reason": sb["reason"]})
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user",
                             "content": _obs_block(tool, f"调用被沙箱拒绝：{sb['reason']}", False)})
            yield {"type": "tool_result", "tool": tool, "ok": False,
                   "content": f"调用被沙箱拒绝：{sb['reason']}"}
            continue

        # 2) 高危工具转审批（不执行，落单到审批流，审批通过后才真正执行）
        if tool in _HIGH_RISK_TOOLS:
            ticket = approval_store.create_tool_approval_ticket(
                caller, agent_id, tool, args, session_id=session_id)
            approval = {"tool": tool, "args": args, "ticket_id": ticket["approval_id"]}
            tool_calls.append({"tool": tool, "allowed": False,
                               "reason": "high_risk_escalated_to_approval",
                               "ticket_id": ticket["approval_id"]})
            final_text = (
                f"操作「{tool}」属于高危操作，已提交人工审批（审批单 {ticket['approval_id']}），"
                f"待管理员批准后才会执行；在审批通过前该操作不会被执行。"
            )
            yield {"type": "tool_escalated", "tool": tool, "args": args,
                   "ticket_id": ticket["approval_id"]}
            break

        # 2.5) 外部 MCP 工具（P1-3）：授权 + 执行，或 requires_approval 挂起审批
        if tool.startswith("mcp__"):
            from app.services import mcp_client
            if mcp_client.mcp_requires_approval(tool):
                ticket = approval_store.create_tool_approval_ticket(
                    caller, agent_id, tool, args, session_id=session_id)
                approval = {"tool": tool, "args": args, "ticket_id": ticket["approval_id"]}
                tool_calls.append({"tool": tool, "allowed": False,
                                   "reason": "mcp_tool_escalated_to_approval",
                                   "ticket_id": ticket["approval_id"]})
                final_text = (
                    f"外部工具「{tool}」需要人工审批（审批单 {ticket['approval_id']}），"
                    f"待管理员批准后才会执行；在审批通过前该操作不会被执行。"
                )
                yield {"type": "tool_escalated", "tool": tool, "args": args,
                       "ticket_id": ticket["approval_id"]}
                break
            g = guardrails.guard_tool_call(tool, args, caller)
            if not g["allowed"]:
                tool_calls.append({"tool": tool, "allowed": False,
                                   "reason": "guard:" + g["reason"]})
                messages.append({"role": "assistant", "content": text})
                messages.append({"role": "user",
                                 "content": _obs_block(tool, f"调用被护栏拒绝：{g['reason']}", False)})
                yield {"type": "tool_result", "tool": tool, "ok": False,
                       "content": f"调用被护栏拒绝：{g['reason']}"}
                continue
            content = (g.get("result") or {}).get("content", "") or ""
            tool_calls.append({
                "tool": tool, "allowed": True, "ok": True,
                "content": content[:_OBS_TRUNCATE], "error": "",
            })
            yield {"type": "tool_call", "tool": tool, "args": args}
            yield {"type": "tool_result", "tool": tool, "ok": True,
                   "content": content[:_OBS_TRUNCATE]}
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user",
                             "content": _obs_block(tool, content, True)})
            continue

        # 3) 工具合法性 + 护栏单一过滤点（白名单 / 私网 / 审计）
        in_builtin = tool in guardrails._ALLOWED_TOOLS
        registered = registry.get(tool) is not None
        if not in_builtin and not registered:
            tool_calls.append({"tool": tool, "allowed": False, "reason": "tool_not_registered"})
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user",
                             "content": _obs_block(tool, "未知或已下线的工具", False)})
            yield {"type": "tool_result", "tool": tool, "ok": False,
                   "content": "未知或已下线的工具"}
            continue
        tc = guardrails.guard_tool_call(tool, args, caller)
        if not tc["allowed"] and registered and not in_builtin:
            tc = {"allowed": True, "reason": "custom_tool_ok"}
        if not tc["allowed"]:
            tool_calls.append({"tool": tool, "allowed": False,
                               "reason": "guard:" + tc["reason"]})
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user",
                             "content": _obs_block(tool, f"调用被护栏拒绝：{tc['reason']}", False)})
            yield {"type": "tool_result", "tool": tool, "ok": False,
                   "content": f"调用被护栏拒绝：{tc['reason']}"}
            continue

        # 4) 执行并观察
        res = registry.execute(tool, args, caller)
        tool_calls.append({
            "tool": tool,
            "allowed": True,
            "ok": res.get("ok", False),
            "content": (res.get("content") or "")[:_OBS_TRUNCATE],
            "error": res.get("error", ""),
        })
        yield {"type": "tool_call", "tool": tool, "args": args}
        yield {"type": "tool_result", "tool": tool,
               "ok": res.get("ok", False),
               "content": (res.get("content") or res.get("error") or "")[:_OBS_TRUNCATE]}
        messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user",
                         "content": _obs_block(tool, res.get("content") or res.get("error") or "",
                                              res.get("ok", False))})

    if final_text is None:
        final_text = "（工具循环达到上限，未能生成最终答案，请简化任务或关闭工具调用后重试。）"

    result = {
        "answer": final_text,
        "citations": [], "web_sources": [], "web_search": None,
        "query_rewritten": False, "turn_no": 1,
        "usage": last_usage, "routed_tier": None, "routed_model": None,
        "tool_calls": [{"tool": "session_memory", "authorized": True}],
        "tool_invocations": tool_calls,
        "approval_required": approval is not None,
        "approval": approval,
        "approval_ticket_id": (approval or {}).get("ticket_id"),
        "skill_applied": False,
    }
    yield {"type": "done", "result": result}
