"""P1-9 · 子代理 / 专家执行（#304）。

为什么要有这一层：平台里 203 个业务技能包 + 28 个公共库，每个都是一份「带人设、
带硬规则、带输入输出契约」的专家干法。此前它们只能被**用户直接点选**后整包执行
（invoke），主智能体无法把一个复杂任务拆开、把其中某个环节交给更对口的专家。

本模块把「专家」变成可被主智能体/管理员**委派**的执行单元：
- ``dispatch(agent_id, task, caller)``：用目标技能包的 system_prompt 作为人设，
  独立构造 messages 直接调模型网关产出该子任务的结论。
- ``dispatch_batch``：一次并发委派多个专家，结果按任务顺序聚合。
- ``list_experts``：列出当前调用者可见的专家（业务包 + 公共库，排除内置助手）。
- ``_RUNS``：执行台账（成功/降级/被拒都留痕），按 caller 的 RBAC 隔离。

设计红线（对齐平台既有口径）：
1. **独立上下文**：子代理不写主会话 ``session_store.turns``、不占用 ``q_index``、
   不落产出物、不触发 P1-5 会话锁——它是「一次无状态推理」，不是一轮对话。
   因此主会话的轮次与制品版本链完全不受委派影响。
2. **不伪造执行**：模型网关 ``real=False``（未配置密钥 / 全军覆没）时，如实返回
   ``ok=False`` + ``error``，绝不把 stub 文本包装成专家结论（M55「缺料必拒」红线）。
3. **防递归**：子代理执行不再进入工具循环；``depth`` 显式限深，达到上限直接拒绝，
   避免主智能体委派 → 子智能体再委派形成环。
4. **可见域复用 agent_store._visible**：跨租户/跨部门不可委派，内置助手不是交付专家。
5. **只读 + 台账**：不修改任何技能包字段；台账走 ``_m27_store.Store``（内存+RLock，
   与 M27 系列一致，重启即丢），审计动作仍走 ``audit`` 单一出口。
"""
from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.core.config import settings
from app.services import (agent_store, fewshot, model_gateway,
                          solution_materials, role_map)
from app.services import _m27_store

# 委派工具名：主智能体 enable_tools 时由 manifest_prompt 自动带上。
TOOL_NAME = "delegate_expert"
# 台账（进程内，RLock 保护，_batch 并发写安全）
_RUNS = _m27_store.Store("subagent_run")

# 平台默认：允许 1 层委派（depth=0 顶层 / 1 工具内），最多 6 个专家并发。
MAX_DEPTH = int(getattr(settings, "SUBAGENT_MAX_DEPTH", 2))
MAX_BATCH = int(getattr(settings, "SUBAGENT_MAX_BATCH", 6))
MAX_TASK_CHARS = int(getattr(settings, "SUBAGENT_MAX_TASK_CHARS", 4000))
MAX_CONTEXT_CHARS = int(getattr(settings, "SUBAGENT_MAX_CONTEXT_CHARS", 6000))

# 子代理专属约束：接在专家人设之后，明确「只做这一个子任务」。
_SUBAGENT_RULES = """

---
【当前运行模式：子代理 / 专家执行】
你是被主任务委派来独立完成**一个子任务**的专家。请严格遵守：
1. 只产出该子任务的结论本身，不复述任务、不寒暄、不输出 JSON 包裹或 Markdown 代码块围栏。
2. 结论要能被主智能体直接拼装进更大的交付物：结构清晰、要点明确、可独立成立。
3. 不得调用任何工具，不得再委派给其它专家（本模式无工具通道）。
4. 上游上下文仅供参考；其中没有提供的关键事实一律标注「缺料」，禁止编造数字、出处或结论。
5. 若你自身的人设/硬规则与上游上下文冲突，以你的人设与硬规则为准，并在结论里点明冲突。
"""


def _expert_persona(agent: Dict[str, Any]) -> str:
    """组装专家人设：技能包 system_prompt + opt-in few-shot / 材料 + 子代理约束。

    与 agent_runtime.run_agent 同源：compose_* 内部自己判 settings 开关（唯一真源），
    这里不重复判断——重复判断会因 tests 里 patch 实例属性 vs 类属性的差异产生分叉。
    两个开关默认关，未启用时 compose_* 原样返回 system_prompt，零行为变更。
    """
    sp = agent.get("system_prompt") or ""
    if not sp.strip():
        sp = "你是企业智能体平台的领域专家，请依据任务要求与给定上下文产出可靠的结论。"
    sp = fewshot.compose_with_fewshot(sp, agent)
    sp = solution_materials.compose_materials(sp, agent)
    return sp + _SUBAGENT_RULES


def _build_messages(task: str, context: str, persona: str) -> List[Dict[str, str]]:
    """三段式：上游原始任务 → 本子任务 → 上游上下文（参考 workflows._build_workflow_user_prompt）。"""
    parts = [f"【委派的子任务】\n{task}\n"]
    if context and context.strip():
        parts.append(f"【上游上下文（主任务背景 / 前序专家结论，仅供参考）】\n{context.strip()}\n")
    parts.append("请基于你的专业方法直接产出该子任务的结论；缺料处明确标注，不要泛泛而谈。")
    return [
        {"role": "system", "content": persona},
        {"role": "user", "content": "\n".join(parts)},
    ]


def _load_expert(agent_id: str, caller: Any) -> Dict[str, Any]:
    """加载专家并做可见域校验；不可见 / 内置助手 / 不存在一律 404（不区分，避免泄露）。"""
    agent = agent_store.get(agent_id)
    if not agent or agent.get("builtin"):
        raise HTTPException(status_code=404, detail=f"专家不存在或无权限：{agent_id}")
    if not agent_store._visible(agent, caller):
        raise HTTPException(status_code=404, detail=f"专家不存在或无权限：{agent_id}")
    return agent


def _caller_meta(caller: Any) -> Dict[str, str]:
    return {
        "user_id": str(getattr(caller, "userId", "") or ""),
        "dept_id": str(getattr(caller, "deptId", "") or ""),
        "tenant_id": str(getattr(caller, "tenantId", "") or ""),
        "role": str(getattr(caller, "role", "") or "employee"),
    }


def _run_visible(row: Dict[str, Any], caller: Any) -> bool:
    """台账 RBAC：platform_admin 全量 / dept_admin 本部门+本租户 / 其余本人+本租户。"""
    if getattr(caller, "role", "") == "platform_admin":
        return True
    m = _caller_meta(caller)
    if row.get("tenant_id") and row["tenant_id"] != m["tenant_id"]:
        return False
    if m["role"] == "dept_admin":
        return (row.get("dept_id") or "") == m["dept_id"]
    return (row.get("user_id") or "") == m["user_id"]


def _new_run_id() -> str:
    return f"sub_{uuid.uuid4().hex[:12]}"


def dispatch(agent_id: str, task: str, caller: Any, *, context: str = "",
             parent_agent_id: str = "", depth: int = 0, source: str = "api",
             batch_id: str = "", trace_id: str = "") -> Dict[str, Any]:
    """把 ``task`` 委派给 ``agent_id`` 对应的专家独立执行一次，返回结构化结论 + 台账记录。

    不写 session_store、不落产出物、不进工具循环。模型不可用时如实降级（ok=False），
    不伪造专家结论。越权 / 内置助手 / 超深委派抛 HTTPException（由路由层转 4xx）。
    """
    t0 = time.time()
    m = _caller_meta(caller)
    task = (task or "").strip()
    context = (context or "")[:MAX_CONTEXT_CHARS]
    run_id = _new_run_id()

    if not task:
        raise HTTPException(status_code=400, detail="task 不能为空")
    if len(task) > MAX_TASK_CHARS:
        raise HTTPException(status_code=400, detail=f"task 超过 {MAX_TASK_CHARS} 字上限")
    if int(depth or 0) >= MAX_DEPTH:
        raise HTTPException(status_code=400, detail=f"委派深度超限（max={MAX_DEPTH}），禁止递归委派")

    agent = _load_expert(agent_id, caller)   # 可见域 / builtin 校验
    persona = _expert_persona(agent)
    messages = _build_messages(task[:MAX_TASK_CHARS], context, persona)
    agent_meta = {"adapter_type": agent.get("adapter_type") or "", "require_citation": False}

    status, ok, real, answer, error, model_id, usage = "ok", True, True, "", "", "", {}
    try:
        res = model_gateway.routed_chat_sync_ex(messages, user_input=task, agent=agent_meta)
        real = bool(res.get("real"))
        model_id = res.get("model_id") or ""
        usage = res.get("usage") or {}
        answer = (res.get("text") or "").strip()
        if not real:
            # 诚实降级：stub / 模型全军覆没都不冒充专家结论
            status, ok = "unavailable", False
            error = res.get("error") or "模型通道不可用"
            answer = ""
        elif not answer:
            status, ok = "unavailable", False
            error = "专家返回空结论"
    except Exception as exc:  # 不伪造：异常也转结构化失败并入台账
        status, ok, real = "error", False, False
        error = f"{type(exc).__name__}: {exc}"[:300]
        answer = ""

    row = _RUNS.put({
        "run_id": run_id,
        "batch_id": batch_id,
        "parent_agent_id": parent_agent_id or "",
        "agent_id": agent_id,
        "agent_name": agent.get("name") or "",
        "category_cn": agent.get("category_cn") or "",
        "role_primary": agent.get("role_primary") or "",
        "task": task[:500],
        "context_len": len(context),
        "depth": int(depth or 0),
        "source": source,
        "status": status,
        "ok": ok,
        "real": real,
        "answer": answer,
        "answer_len": len(answer),
        "persona_len": len(persona),
        "model_id": model_id,
        "usage": usage,
        "error": error,
        "latency_ms": int((time.time() - t0) * 1000),
        "trace_id": trace_id or "",
        **m,
    }, key="run_id")

    # 审计：委派动作走 audit 单一出口（后端不可用时静默降级，不影响主链路）
    _m27_store.audit_event(
        "subagent_dispatch", caller, target=agent_id,
        meta={"run_id": run_id, "status": status, "real": real, "depth": int(depth or 0),
              "parent_agent_id": parent_agent_id, "source": source},
        result="ok" if ok else status,
    )
    return dict(row)


def dispatch_batch(caller: Any, tasks: List[Dict[str, Any]], *, context: str = "",
                   parent_agent_id: str = "", depth: int = 0) -> Dict[str, Any]:
    """并发委派多个专家。单条失败不影响其它专家；结果按输入顺序返回。

    tasks: [{"agent_id": str, "task": str, "context"?: str}, ...]
    """
    if not isinstance(tasks, list) or not tasks:
        raise HTTPException(status_code=400, detail="tasks 不能为空")
    if len(tasks) > MAX_BATCH:
        raise HTTPException(status_code=400, detail=f"一次最多并发 {MAX_BATCH} 个专家")

    batch_id = f"bat_{uuid.uuid4().hex[:12]}"
    t0 = time.time()

    def _one(item: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return dispatch(
                str(item.get("agent_id") or ""), str(item.get("task") or ""), caller,
                context=str(item.get("context") or context or ""),
                parent_agent_id=parent_agent_id, depth=depth,
                source="batch", batch_id=batch_id,
            )
        except HTTPException as exc:
            return {"ok": False, "status": "denied", "real": False, "answer": "",
                    "error": str(exc.detail), "agent_id": str(item.get("agent_id") or ""),
                    "agent_name": "", "task": str(item.get("task") or "")[:500]}
        except Exception as exc:
            return {"ok": False, "status": "error", "real": False, "answer": "",
                    "error": f"{type(exc).__name__}: {exc}"[:300],
                    "agent_id": str(item.get("agent_id") or ""), "agent_name": "",
                    "task": str(item.get("task") or "")[:500]}

    with ThreadPoolExecutor(max_workers=min(MAX_BATCH, len(tasks))) as pool:
        items = list(pool.map(_one, tasks))

    usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
    for r in items:
        u = r.get("usage") or {}
        usage["prompt_tokens"] += int(u.get("prompt_tokens", 0) or 0)
        usage["completion_tokens"] += int(u.get("completion_tokens", 0) or 0)
        usage["calls"] += 1
    return {
        "batch_id": batch_id,
        "items": items,
        "total": len(items),
        "ok_count": sum(1 for r in items if r.get("ok")),
        "real_count": sum(1 for r in items if r.get("real")),
        "failed_count": sum(1 for r in items if not r.get("ok")),
        "usage": usage,
        "latency_ms": int((time.time() - t0) * 1000),
    }


def list_experts(caller: Any, *, industry: str = "", role: str = "", q: str = "",
                 kind: str = "", limit: int = 200) -> Dict[str, Any]:
    """可见专家目录：业务包 + 公共库，排除内置助手（复用 agent_store._visible）。"""
    rows = [a for a in agent_store.list_all(caller) if not a.get("builtin")]
    if role:
        rid = (role_map.resolve(role) or {}).get("id") or role
        rows = agent_store.filter_role(rows, rid)
    if industry:
        rows = [a for a in rows if (a.get("category_cn") or a.get("category") or "") == industry]
    if kind:
        want_public = (kind == "public")
        rows = [a for a in rows if agent_store.is_public(a) == want_public]
    if q:
        ql = q.lower()
        rows = [a for a in rows if (
            (a.get("name") or "").lower().find(ql) >= 0
            or (a.get("description") or "").lower().find(ql) >= 0
            or (a.get("sub_function") or "").lower().find(ql) >= 0
        )]
    items = [{
        "agent_id": a["agent_id"],
        "name": a.get("name") or "",
        "description": (a.get("description") or "")[:160],
        "kind": "public" if agent_store.is_public(a) else "business",
        "category_cn": a.get("category_cn") or a.get("category") or "",
        "sub_function": a.get("sub_function") or "",
        "role_primary": a.get("role_primary") or "",
        "role_name": role_map.ROLE_NAME.get(a.get("role_primary") or "", ""),
        "adapter_type": a.get("adapter_type") or "",
        "status": a.get("status") or "",
        "persona_len": len(a.get("system_prompt") or ""),
        "has_fewshot": bool(a.get("fewshot")),
        "has_materials": bool(a.get("reference_specs") or a.get("acceptance")),
    } for a in rows]
    total = len(items)
    items = items[:limit]
    return {"items": items, "total": total, "count": len(items)}


def list_runs(caller: Any, *, agent_id: str = "", status: str = "",
              limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    """执行台账（RBAC 隔离，按时间倒序）。"""
    rows = _RUNS.list(limit=10 ** 6)
    rows = [r for r in rows if _run_visible(r, caller)]
    if agent_id:
        rows = [r for r in rows if r.get("agent_id") == agent_id]
    if status:
        rows = [r for r in rows if r.get("status") == status]
    total = len(rows)
    return {"items": rows[offset:offset + limit], "total": total,
            "count": len(rows[offset:offset + limit])}


def get_run(caller: Any, run_id: str) -> Optional[Dict[str, Any]]:
    row = _RUNS.get(run_id)
    if row is None or not _run_visible(row, caller):
        return None
    return row


def stats(caller: Any) -> Dict[str, Any]:
    """执行台账看板：总量 / 成功与降级 / 按专家 TOP / 累计用量。"""
    rows = [r for r in _RUNS.list(limit=10 ** 6) if _run_visible(r, caller)]
    by_agent: Dict[str, Dict[str, Any]] = {}
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    latencies: List[int] = []
    for r in rows:
        k = r.get("agent_id") or ""
        slot = by_agent.setdefault(k, {"agent_id": k, "agent_name": r.get("agent_name") or "",
                                      "count": 0, "ok": 0, "real": 0})
        slot["count"] += 1
        slot["ok"] += 1 if r.get("ok") else 0
        slot["real"] += 1 if r.get("real") else 0
        u = r.get("usage") or {}
        usage["prompt_tokens"] += int(u.get("prompt_tokens", 0) or 0)
        usage["completion_tokens"] += int(u.get("completion_tokens", 0) or 0)
        latencies.append(int(r.get("latency_ms") or 0))
    by_agent_list = sorted(by_agent.values(), key=lambda x: (-x["count"], x["agent_name"]))
    return {
        "total": len(rows),
        "ok": sum(1 for r in rows if r.get("ok")),
        "unavailable": sum(1 for r in rows if r.get("status") == "unavailable"),
        "denied": sum(1 for r in rows if r.get("status") == "denied"),
        "error": sum(1 for r in rows if r.get("status") == "error"),
        "real_rate": round(sum(1 for r in rows if r.get("real")) / len(rows), 4) if rows else 0.0,
        "avg_latency_ms": int(sum(latencies) / len(latencies)) if latencies else 0,
        "usage": usage,
        "by_agent": by_agent_list,
    }


def reset_state() -> None:
    """测试用：清空执行台账（不影响技能包）。"""
    _RUNS.clear()
