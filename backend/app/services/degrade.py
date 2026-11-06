"""M27 · 置信降级 / 转人工工单（蓝图第 40 章）。

诚实边界：
  - ``confidence`` 是启发式置信分，**stub（未接真模型）硬上限 0.5** —— 不冒充真模型
    的校准置信度；证据 + 编号引用加分，guard 泄密/注入命中扣分，空答案 0。
  - ``chain`` 只是「降级顺序」配置，不真的执行降级动作（执行链由调用方编排）。
  - ``handoff`` 建的是进程内工单（status=open），带完整上下文与置信理由，供人工兜底。

存储复用 ``_m27_store.Store``，审计动作走 ``_m27_store.audit_event``。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.services._m27_store import (
    Store, new_id, now, iso, audit_event, caller_id, caller_role, is_admin,
)

_CITE_RE = re.compile(r"\[W?\d+\]|【\d+】|\(\d+\)|第\s*\d+\s*条")
_LEAK_KEYS = ("leak", "leaked", "secret_leak", "leak_hit", "data_leak", "leaked_secret")
_INJ_KEYS = ("injection", "injection_hit", "prompt_injection", "jailbreak")

_HANDOFFS = Store("handoff")
_DEFAULT_CHAIN = ["small_model", "rules", "human"]
_CHAINS: Dict[str, List[str]] = {}          # agent_id -> 自定义降级顺序


def confidence(answer: str, evidence: str = "", llm_real: bool = False,
               guard: dict | None = None) -> float:
    """启发式置信分 0-1。

    - 空答案 → 0.0；
    - 起点：真模型 0.60 / stub 0.20；
    - 有证据 +0.12；有编号引用且证据齐全 +0.15（仅编号引用 +0.05）；
    - guard 泄密命中 -0.25、注入标志 -0.15、注入命中数 -0.05/次；
    - **stub（llm_real=False）结果硬上限 0.5**（min(raw, 0.5)），真模型上限 1.0。
    """
    ans = str(answer or "")
    if not ans.strip():
        return 0.0
    raw = 0.60 if llm_real else 0.20
    ev = str(evidence or "")
    has_cite = bool(_CITE_RE.search(ans))
    if ev.strip():
        raw += 0.12
    if has_cite and ev.strip():
        raw += 0.15
    elif has_cite:
        raw += 0.05

    g = guard or {}
    if any(g.get(k) for k in _LEAK_KEYS):
        raw -= 0.25
    if any(g.get(k) for k in _INJ_KEYS):
        raw -= 0.15
    hits = 0
    for k in ("injection_hits", "hits", "prompt_injection_hits"):
        try:
            hits = max(hits, int(g.get(k) or 0))
        except (TypeError, ValueError):
            pass
    raw -= 0.05 * hits

    cap = 1.0 if llm_real else 0.5          # stub 上限 0.5
    return round(max(0.0, min(cap, raw)), 4)


def chain(agent_id: str = "") -> list:
    """降级顺序，默认 ``["small_model","rules","human"]``；可按 agent_id 用 set_chain 覆盖。"""
    return list(_CHAINS.get(str(agent_id or ""), _DEFAULT_CHAIN))


def set_chain(agent_id: str, steps: list) -> list:
    """配置某 agent 的降级顺序（骨架辅助函数，非契约要求但供编排方使用）。"""
    value = [str(s) for s in (steps or [])]
    _CHAINS[str(agent_id or "")] = value
    return list(value)


def handoff(run_id: str, agent_id: str, reason: str, context: dict, caller) -> dict:
    """低置信转人工工单：status=open，带完整上下文与置信理由。"""
    ctx: Dict[str, Any] = dict(context or {})
    conf = confidence(
        ctx.get("answer", ""), ctx.get("evidence", ""),
        bool(ctx.get("llm_real", False)), ctx.get("guard"),
    )
    row: Dict[str, Any] = {
        "id": new_id("ho"),
        "run_id": str(run_id or ""),
        "agent_id": str(agent_id or ""),
        "reason": str(reason or ""),
        "confidence_reason": str(reason or ""),   # 置信理由（与 reason 同源，显式带出）
        "confidence": conf,
        "threshold": float(settings.CONFIDENCE_THRESHOLD),
        "context": ctx,                           # 完整上下文
        "chain": chain(agent_id),
        "status": "open",
        "created_by": caller_id(caller),
        "created_by_role": caller_role(caller),
        "handoff_enabled": bool(settings.HANDOFF_ENABLED),
    }
    stored = _HANDOFFS.put(row)
    audit_event("handoff.create", caller, target=stored["id"],
                meta={"run_id": row["run_id"], "agent_id": row["agent_id"],
                      "confidence": conf, "reason": row["reason"]})
    return dict(stored)


def list_handoffs(caller=None, status: str | None = None) -> list:
    """列工单；可按 status 过滤。非管理员（caller 非空时）只看自己创建的。"""
    filters = {"status": status} if status else None
    rows = _HANDOFFS.list(filters=filters, limit=10 ** 6)
    if caller is not None and not is_admin(caller):
        me = caller_id(caller)
        rows = [r for r in rows if r.get("created_by") == me]
    return rows


def resolve(handoff_id: str, resolution: str, caller) -> dict:
    """结单：status=resolved。不存在时抛 KeyError（路由层转 404）。"""
    patch = {
        "status": "resolved",
        "resolution": str(resolution or ""),
        "resolved_by": caller_id(caller),
        "resolved_by_role": caller_role(caller),
        "resolved_at": now(),
        "resolved_iso": iso(),
    }
    row = _HANDOFFS.update(handoff_id, patch)
    if row is None:
        raise KeyError(f"handoff 不存在：{handoff_id}")
    audit_event("handoff.resolve", caller, target=handoff_id,
                meta={"resolution": patch["resolution"]})
    return dict(row)


def reset_state() -> None:
    """清空工单与自定义降级链，供测试隔离。"""
    _HANDOFFS.clear()
    _CHAINS.clear()
