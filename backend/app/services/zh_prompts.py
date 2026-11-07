"""中文特化 · 中文任务提示词模板库（蓝图第 43 章）。

把常见中文任务（摘要 / 提取 / 分类 / 问答 / 改写 / 合规审查 / 表格生成）固化成模板，
减少「同一类问题每次重写提示词」的漂移，也便于统一中文表达与输出格式约束。
模板用 ``{var}`` 占位，``render(task, **vars)`` 填充；缺变量抛 KeyError（不静默填空）。

诚实边界：这是「工程模板库」，不是「任务效果保证」；具体某技能包仍可覆盖 system_prompt。
"""
from __future__ import annotations

from typing import Dict

# 通用中文输出约束（追加在每个模板末尾，统一可读性与格式）
_ZH_OUT_CONSTRAINT = (
    "输出要求：使用简体中文；只给结论与依据，不重复问题；"
    "涉及数字/专名/政策术语必须原文照录，不得改写或估算；"
    "不确定处明确说「资料未提及」，不要编造。"
)

TEMPLATES: Dict[str, str] = {
    "summarize": (
        "你是一个严谨的中文摘要助手。请基于下方【资料】对用户的【任务】做摘要。\n"
        "摘要控制在 3-5 句话，先给结论后给要点；保留关键数字与专名。\n"
        "【资料】\n{context}\n【任务】\n{input}\n" + _ZH_OUT_CONSTRAINT
    ),
    "extract": (
        "请从下方【资料】中提取与【任务】相关的结构化信息，按要点列出，"
        "每条附原文摘录（带书名号/引号）。\n"
        "【资料】\n{context}\n【任务】\n{input}\n" + _ZH_OUT_CONSTRAINT
    ),
    "classify": (
        "请对【输入】做分类，只输出类别标签与一句话理由，不要展开。\n"
        "可选类别：{labels}\n【输入】\n{input}\n" + _ZH_OUT_CONSTRAINT
    ),
    "qa": (
        "请基于【资料】回答【问题】。若资料不足以回答，明确说「资料未提及」。\n"
        "【资料】\n{context}\n【问题】\n{input}\n" + _ZH_OUT_CONSTRAINT
    ),
    "rewrite": (
        "请将【原文】改写为【要求】。保持原意与专名不变，只调整表达。\n"
        "【原文】\n{input}\n【要求】\n{instruction}\n" + _ZH_OUT_CONSTRAINT
    ),
    "compliance": (
        "请基于【制度】审查【事项】是否合规。输出：结论（合规/不合规/需补充材料）、"
        "依据条款（原文摘录）、风险提示。\n"
        "【制度】\n{context}\n【事项】\n{input}\n" + _ZH_OUT_CONSTRAINT
    ),
    "table": (
        "请将【资料】整理为表格，列名简洁，行对应要点；涉及数字必须原文照录。\n"
        "【资料】\n{context}\n【任务】\n{input}\n" + _ZH_OUT_CONSTRAINT
    ),
}


def render(task: str, **vars) -> str:
    """渲染指定任务模板；未知任务抛 KeyError，缺变量抛 KeyError。"""
    tpl = TEMPLATES.get(task)
    if tpl is None:
        raise KeyError(f"未知中文提示词模板：{task}（可选：{', '.join(TEMPLATES)}）")
    return tpl.format(**vars)


def available() -> list[str]:
    return sorted(TEMPLATES.keys())
