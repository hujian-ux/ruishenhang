"""第35章 · 模型选型决策框架（骨架 + 真实规则）。

与原型「选型向导」同源：前端 wizard 调用本模块得到 adapter_type 与落地章节，
导出 JSON 直接对齐 openapi /agents 的 adapter_type 枚举。
"""
from dataclasses import dataclass

# 决策树问题（前端 wizard 顺序）
QUESTIONS = [
    {"key": "input_type", "label": "输入类型", "options": [
        ("text", "文本/自然语言"), ("image", "图像/视觉"), ("table", "表格/结构化数据"), ("structured", "API/数据库")]},
    {"key": "deterministic", "label": "是否需要确定性 / 可解释", "options": [
        (True, "是（结果可复现、可解释）"), (False, "否（可接受概率性）")]},
    {"key": "need", "label": "核心需求", "options": [
        ("knowledge", "基于企业知识问答"), ("creative", "开放内容生成"), ("accuracy", "精度优先预测"), ("explain", "强可解释推理")]},
]

ADAPTER_META = {
    "rule": {"name": "规则引擎 / 传统 ML", "tag": "RULE", "cls": "b-ok",
             "why": "结果确定、可解释、零推理成本，适合审批规则、表单校验、固定流程。", "link": "传统 ML 服务化即可，无需大模型。",
             "chapters": "第35章选型 + 第37章评测"},
    "ml_serving": {"name": "传统 ML 服务", "tag": "ML", "cls": "b-ok",
                   "why": "表格/结构化数据精度优先，XGBoost/逻辑回归等，成本低、可解释。", "link": "模型训练后服务化，经 /models 注册计量。",
                   "chapters": "第35章选型 + 第37章评测"},
    "llm": {"name": "大模型（LLM）", "tag": "LLM", "cls": "b-p1",
            "why": "开放生成、对话、改写；但不可直接用于企业知识问答（须 RAG）。", "link": "经第25章模型网关路由，注意成本与配额。",
            "chapters": "第35章选型 + 第25章模型网关"},
    "llm_retrieval": {"name": "RAG（检索增强）", "tag": "RAG", "cls": "b-p0",
                      "why": "企业知识问答必须 RAG：先混合检索+重排(Recall@100>=0.95)，再生成。", "link": "第36章质量工程 + 第37章评测门禁。",
                      "chapters": "第35章 + 第36章 RAG + 第37章评测"},
    "ml_llm_retrieval": {"name": "数据分析 + RAG 混合", "tag": "HYBRID", "cls": "b-p0",
                         "why": "表格预测结合知识检索，精度与上下文兼顾。", "link": "ML 服务与 RAG 并联，统一经过滤点。",
                         "chapters": "第35/36/37/43 章"},
}


def recommend(input_type: str, deterministic: bool, need: str) -> dict:
    """决策树：返回选型结论（与 openapi /agents.adapter_type 对齐）。"""
    if deterministic and need in ("accuracy", "explain"):
        adapter = "ml_serving" if input_type in ("table", "structured") else "rule"
    elif need == "knowledge":
        adapter = "llm_retrieval"
    elif need == "creative":
        adapter = "llm"
    elif input_type in ("table", "structured") and need == "accuracy":
        adapter = "ml_llm_retrieval"
    else:
        adapter = "llm"
    meta = ADAPTER_META[adapter]
    return {
        "adapter_type": adapter,
        "name": meta["name"],
        "tag": meta["tag"],
        "why": meta["why"],
        "link": meta["link"],
        "landing_chapters": meta["chapters"],
    }
