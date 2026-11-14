"""中文特化 · 中文任务提示词模板库（蓝图第 43 章）。

模板已外置到 ``app/prompts/``（Jinja2，运营可改 ``.tpl`` 即生效，无需发版）。
本模块保留旧公开符号（``render`` / ``available`` / ``TEMPLATES``）作为薄封装，
对调用方零行为变更：``render`` 未知任务 / 缺变量仍抛 ``KeyError``，``available``
仍返回全部 7 个场景。

诚实边界：这是「工程模板库」，不是「任务效果保证」；具体某技能包仍可覆盖 system_prompt。
"""
from __future__ import annotations

from app.prompts.loader import (
    DEFAULT_SCENES,
    available_scenes,
    render as _render,
    template_source,
)

# 场景 → 默认模板源码视图（保留旧符号，供检视 / 调试）。
# 注意：这是 Jinja2 源码（含 ``{{ var }}`` 与 ``{% include %}``），不再是可直接 .format 的字符串。
TEMPLATES = {scene: template_source(scene, "default") for scene in DEFAULT_SCENES}


def render(task: str, **vars) -> str:
    """渲染指定任务模板；未知任务抛 KeyError，缺变量抛 KeyError（兼容旧契约）。"""
    return _render(task, "default", **vars)


def available() -> list[str]:
    """返回全部可用场景（7 个）。"""
    return available_scenes()
