"""中文特化 · 中文任务内部基准集（蓝图第 43 章，C-Eval/CMMLU 风格）。

诚实边界（务必看清）：
  - 这是**内部替代基准**，不是官方 C-Eval / CMMLU（那些需各自授权数据集，不能随仓库打包）。
  - 作用是「在自家场景里跟踪中文任务是否退化」（常识/数学/法律/合规/中文理解），
    不是去刷公开榜。题目与答案均为项目组自编、可核验，不声称覆盖官方题面。
  - 基础评分走「关键词命中」（deterministic，无需联网）；可选接 LLM-as-Judge（judge.py）
    做语义判分，无真模型时如实标 llm_real=False。

每题字段：id / category / question / expected_keywords（任一命中即算对该点）/ rubric。
"""
from __future__ import annotations

from typing import Any, Dict, List

# 内部自编基准（小集，生产应扩到 50+ 并按部门补齐；此处为可运行的最小骨架 + 真实样例）。
BENCHMARK: List[Dict[str, Any]] = [
    {"id": "zh_c1", "category": "常识", "question": "中国的首都是哪座城市？",
     "expected_keywords": ["北京"], "rubric": "答出北京即可"},
    {"id": "zh_m1", "category": "数学", "question": "一项采购预算 12000 元，分 4 个月均摊，每月多少元？",
     "expected_keywords": ["3000"], "rubric": "计算结果为 3000 元/月"},
    {"id": "zh_l1", "category": "法律", "question": "劳动合同试用期最长不得超过几个月？",
     "expected_keywords": ["6", "六"], "rubric": "《劳动合同法》规定试用期最长 6 个月"},
    {"id": "zh_k1", "category": "合规", "question": "员工报销差旅费，缺少发票时应如何处理？",
     "expected_keywords": ["发票", "不予报销", "补", "说明"], "rubric": "应要求补开发票或说明，缺发票通常不予报销"},
    {"id": "zh_u1", "category": "中文理解", "question": "「他差一点没赶上火车」的意思是？",
     "expected_keywords": ["赶上", "赶上了"], "rubric": "「差一点没」在中文里表示「最终赶上了」"},
    {"id": "zh_u2", "category": "中文理解", "question": "把「并非所有鸟类都会飞」换个说法，要求语义不变。",
     "expected_keywords": ["有的", "存在", "不会飞"], "rubric": "等价于「有的鸟不会飞」"},
    {"id": "zh_c2", "category": "常识", "question": "一斤等于多少克？",
     "expected_keywords": ["500"], "rubric": "1 斤 = 500 克"},
    {"id": "zh_m2", "category": "数学", "question": "某部门季度目标 100 万，已达成 73 万，完成率百分之多少？",
     "expected_keywords": ["73"], "rubric": "完成率 73%"},
]


def list_items(category: str = "") -> List[Dict[str, Any]]:
    if not category:
        return [dict(i) for i in BENCHMARK]
    return [dict(i) for i in BENCHMARK if i["category"] == category]


def score_keyword(item: Dict[str, Any], answer: str) -> Dict[str, Any]:
    """关键词命中评分（deterministic，无需联网）。任一 expected_keyword 出现即计该点命中。"""
    ans = answer or ""
    hits = [k for k in item["expected_keywords"] if k in ans]
    return {
        "id": item["id"],
        "category": item["category"],
        "hit": bool(hits),
        "hits": hits,
        "expected_keywords": item["expected_keywords"],
        "method": "keyword",
    }


def run(items: List[Dict[str, Any]], answers: Dict[str, str]) -> Dict[str, Any]:
    """跑一遍基准：对每题用关键词评分，汇总通过率（按类别）。

    answers: {item_id: model_answer}。缺答的题计为未命中。
    """
    per_item = []
    by_cat: Dict[str, Dict[str, int]] = {}
    for it in items:
        ans = answers.get(it["id"], "")
        r = score_keyword(it, ans)
        per_item.append(r)
        c = r["category"]
        by_cat.setdefault(c, {"total": 0, "hit": 0})
        by_cat[c]["total"] += 1
        if r["hit"]:
            by_cat[c]["hit"] += 1
    total = len(per_item)
    hit = sum(1 for r in per_item if r["hit"])
    cat_rate = {c: (v["hit"] / v["total"] if v["total"] else 0.0)
                for c, v in by_cat.items()}
    return {
        "total": total,
        "hit": hit,
        "pass_rate": round(hit / total, 4) if total else 0.0,
        "by_category": cat_rate,
        "items": per_item,
        "source": "internal_zh_bench",
        "note": "内部替代基准（非官方 C-Eval/CMMLU）；关键词命中为粗筛，精确语义判分需接 LLM-as-Judge。",
    }
