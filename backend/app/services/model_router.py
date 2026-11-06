"""M27 · 成本路由（蓝图第 34 章）。

诚实边界：这是「按输入特征选大小模型档位」的规则路由，不是真模型能力评测。
分档只改写「建议使用的模型名 + 单价」，真正调哪个模型由调用链决定；
ROUTING_ENABLED=0 时明确返回 enabled=False 且保持平台默认模型不变（不改模型）。

规则（字面关键词 + 长度，可解释、可复算）：
  - 简单任务（短文本 / 分类 / 摘要 / 翻译 / 格式化 / 润色 / 提取…）→ small 档；
  - 复杂任务（分析 / 归因 / 方案 / 多步 / 合同 / 合规 / 推理 / 决策…）→ large 档；
  - agent 指定 adapter_type=llm_retrieval 且「需引用」→ 倾向 large（检索+引用更吃推理）；
  - 复杂信号优先于简单信号（宁可贵一点，也别用小模型漏掉合规/归因）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from app.core.config import settings

# 简单任务信号
_SMALL_KW = (
    "分类", "归类", "摘要", "总结", "翻译", "格式化", "润色", "改写", "提取",
    "列出", "列表", "一句话", "标题", "关键词", "拼写", "查错", "打招呼",
    "你好", "谢谢", "hi", "hello", "translate", "summarize", "format", "classify",
)
# 复杂任务信号
_LARGE_KW = (
    "分析", "归因", "根因", "方案", "规划", "多步", "合同", "合规", "审计",
    "评估", "推理", "论证", "决策", "预测", "对比", "综合", "策略", "设计",
    "详细", "深入", "为什么", "如何改进", "风险", "推演", "条款", "法务",
    "诉讼", "归因分析", "report", "analysis", "reasoning",
)
# 「需引用」信号
_CITE_KW = ("引用", "依据", "出处", "根据", "证据", "标注来源", "cite", "citation")

_SHORT_LEN = 14
_LONG_LEN = 60


def _needs_citation(user_input: str, agent: Optional[dict]) -> bool:
    a = agent or {}
    if a.get("require_citation") or a.get("need_citation") or a.get("citations_required"):
        return True
    low = str(user_input or "").lower()
    return any(k in low for k in _CITE_KW)


def route(user_input: str, agent: dict | None = None) -> dict:
    """按输入特征给出模型档位建议。

    返回 ``{"tier","model","unit_price","reason","enabled"}``。
    ``ROUTING_ENABLED=0`` → ``enabled=False`` 且 ``model`` 保持平台默认模型不变。
    """
    text = str(user_input or "")
    low = text.lower()

    # 路由关闭：不改写模型，如实标注
    if not settings.ROUTING_ENABLED:
        return {
            "tier": "large",
            "model": settings.LLM_DEFAULT_MODEL,
            "unit_price": float(settings.ROUTING_LARGE_TIER_PRICE),
            "reason": "ROUTING_ENABLED=0：路由关闭，保持平台默认模型不变",
            "enabled": False,
        }

    large_hits = [k for k in _LARGE_KW if k in low]
    small_hits = [k for k in _SMALL_KW if k in low]
    retrieval_cite = (
        str((agent or {}).get("adapter_type", "")) == "llm_retrieval"
        and _needs_citation(text, agent)
    )

    if large_hits:
        tier = "large"
        reason = f"命中复杂任务信号 {large_hits[:3]}，走大模型保推理/合规质量"
    elif retrieval_cite:
        tier = "large"
        reason = "agent=llm_retrieval 且需引用标注，检索+引用倾向大模型"
    elif small_hits:
        tier = "small"
        reason = f"命中简单任务信号 {small_hits[:3]}，走小模型省成本"
    elif len(text) <= _SHORT_LEN:
        tier = "small"
        reason = f"短文本（{len(text)}字）无复杂信号，走小模型省成本"
    elif len(text) > _LONG_LEN:
        tier = "large"
        reason = f"长文本（{len(text)}字）保守走大模型"
    else:
        tier = "small"
        reason = "无复杂信号，默认走小模型（成本优先）"

    if tier == "small":
        model = settings.ROUTING_SMALL_MODEL
        price = float(settings.ROUTING_SMALL_TIER_PRICE)
    else:
        model = settings.ROUTING_LARGE_MODEL
        price = float(settings.ROUTING_LARGE_TIER_PRICE)

    return {
        "tier": tier,
        "model": model,
        "unit_price": price,
        "reason": reason,
        "enabled": True,
    }


def tiers() -> dict:
    """两档模型名、单价与当前开关，供 /models/tiers 展示。"""
    return {
        "small": {
            "tier": "small",
            "model": settings.ROUTING_SMALL_MODEL,
            "unit_price": float(settings.ROUTING_SMALL_TIER_PRICE),
        },
        "large": {
            "tier": "large",
            "model": settings.ROUTING_LARGE_MODEL,
            "unit_price": float(settings.ROUTING_LARGE_TIER_PRICE),
        },
        "enabled": bool(settings.ROUTING_ENABLED),
        "default_model": settings.LLM_DEFAULT_MODEL,
        "note": "规则路由（关键词 + 长度），非模型能力评测；关闭时保持默认模型不变。",
    }


def reset_state() -> None:
    """无状态模块：保留统一接口，供测试 autouse fixture 调用。"""
    return None
