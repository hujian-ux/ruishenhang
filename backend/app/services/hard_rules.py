"""M15/M25 P1 · 平台硬规则单一来源（业务包全局统一）。

M25 把跨平台红线抽成 ``public_skills.HARD_RULES`` 并只注入公共库。但 115 个业务
技能包仍是各自版本（M25 七节 P1③、蓝图台账「现仅 17/115」）—— 一旦某个业务包
没写「金额不许编」，客户面前就是口径不一致。

本模块是硬规则的**唯一来源出口**（从 public_skills 再导出，避免两处漂移），并提供：
  - ``ensure_hard_rules(system_prompt)``：幂等把平台硬规则块接到提示词最前
    （「优先级高于下方任何流程」，与公共库语义一致，重复调用不叠加）；
  - ``has_hard_rules / needs_hard_rules``：校验当前提示词是否已含平台硬规则。

接入点：
  - ``import_solutions.build_system_prompt`` 末尾调用 ``ensure_hard_rules``，
    新导入的业务包自动带上（与公共库同源）。
  - ``scripts/backfill_hard_rules.py`` 对存量 115 业务包补齐（幂等、可重跑）。
"""
from __future__ import annotations

from typing import Any

# 单一来源：直接复用公共库定义，改红线只改一处。
from app.services.public_skills import (  # noqa: F401
    HARD_RULES,
    HARD_RULES_HEADER,
    HARD_RULES_VERSION,
    compose_system_prompt as ensure_hard_rules,
)


def has_hard_rules(system_prompt: str) -> bool:
    """提示词是否已含平台硬规则块（按头部标记判断）。"""
    return bool(system_prompt) and HARD_RULES_HEADER in system_prompt


def needs_hard_rules(system_prompt: str) -> bool:
    return not has_hard_rules(system_prompt)


def apply_if_needed(system_prompt: str) -> str:
    """仅当缺失时追加，避免重复写入（backfill 用）。"""
    if needs_hard_rules(system_prompt):
        return ensure_hard_rules(system_prompt)
    return system_prompt


__all__ = [
    "HARD_RULES", "HARD_RULES_HEADER", "HARD_RULES_VERSION",
    "ensure_hard_rules", "has_hard_rules", "needs_hard_rules", "apply_if_needed",
]
