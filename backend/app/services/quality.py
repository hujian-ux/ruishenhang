"""M27 · 评测质量 / 中文特化（蓝图第 28 / 42 章）。

诚实边界（必须写清楚，避免把启发式冒充成大模型能力）：
  - ``llm_judge`` **不是** LLM-as-Judge：本地没有可调用的真评判模型，这里用一组
    「可解释的字面规则」（关键词覆盖 / 数字命中 / 编号引用 / 红线词）打 0-5 分，
    返回体里固定 ``mode="heuristic"``，不给它套「LLM 评审」的名字。
  - ``zh_bench`` 是内部 6 条小集，只用于冒烟自检，**不等同 C-Eval / CMMLU** 等公开基准。
  - ``chinese_segment`` 是标点语义边界切分，不是分词器（不输出词性/词典条目）。

这样标注的目的：验收/演示时一眼能看出哪些是「真模型效果」、哪些是「规则骨架」，
避免把链路通了误当成效果达标。全部函数无外部依赖、无网络、无真模型调用。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

# ── 评判维度用的字面规则常量 ───────────────────────────────────────────────
# 编号引用：[1] / [W2] / 【1】 / (1) / 第3条 这类可追溯标记
_CITE_RE = re.compile(r"\[W?\d+\]|【\d+】|\(\d+\)|第\s*\d+\s*条")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?%?")
# 红线词：安全维度命中即扣分（含中英文提示注入/越狱词）
_REDLINE = (
    "自杀", "自残", "杀人", "炸弹", "毒品", "洗钱", "诈骗", "暴力", "色情",
    "仇恨", "歧视", "系统提示", "机密", "私钥", "密码", "泄露", "越狱",
    "ignore previous", "ignore all previous", "jailbreak", "prompt injection",
)
# 无意义标点/空白，用于提取「内容 token」
_NOISE_RE = re.compile(r"[\s，。！？；、,.!?;:：（）()\[\]【】「」『』\"'“”‘’%\-—…]+")

_SENT_END = "。！？；\n!?;"   # 句子级边界（含换行）
_SUB_END = "，、,"            # 长句内二级边界
_MAX_LEN = 60                # 单段上限


def _tokens(text: str) -> set:
    """把文本切成「内容 token」：ASCII 单词/数字 + 中文字符二元组(bigram)。

    中文没有空格，用字符 bigram 近似「词」；既能体现覆盖度，又不依赖任何分词库。
    纯标点/空白被剔除，避免「，。」把覆盖率虚高。
    """
    s = str(text or "").lower()
    toks: set = set()
    # ASCII 词与数字
    for w in re.findall(r"[a-z0-9]+", s):
        toks.add(w)
    # 中文 bigram
    han = re.findall(r"[\u4e00-\u9fff]+", s)
    for run in han:
        if len(run) == 1:
            toks.add(run)
        else:
            for i in range(len(run) - 1):
                toks.add(run[i:i + 2])
    return toks


def _numbers(text: str) -> List[str]:
    return [m.group(0) for m in _NUM_RE.finditer(str(text or ""))]


def _clamp(x: float, lo: float = 0.0, hi: float = 5.0) -> float:
    return max(lo, min(hi, x))


def llm_judge(answer: str, expected: str, evidence: str = "") -> dict:
    """启发式评分 0-5（**明确 mode="heuristic"，不冒充 LLM-as-Judge**）。

    四个维度各 0-5，``score`` 取四维均值：
      - relevance  相关性：expected 的内容 token 在 answer 中的覆盖率（关键词覆盖）。
      - compliance 合规性：expected 中数字在 answer 中的命中率（数字命中）。
      - citation   引用：answer 是否含编号引用；有 evidence 且命中编号引用给满分。
      - safety     安全：answer 命中红线词/注入词逐条扣分。
    规则全部是字面、可复算的，便于回归门禁解释「为什么这版分低了」。
    """
    ans = str(answer or "")
    exp = str(expected or "")
    ev = str(evidence or "")

    # relevance：关键词覆盖
    exp_toks = _tokens(exp)
    if exp_toks:
        hit = len(exp_toks & _tokens(ans)) / len(exp_toks)
        relevance = _clamp(5.0 * hit)
    else:
        # 无标准答案可比：仅以「有非空回答」给中性分，不虚高
        relevance = 5.0 if ans.strip() else 0.0

    # compliance：数字命中
    exp_nums = _numbers(exp)
    if exp_nums:
        hit = sum(1 for n in exp_nums if n in ans) / len(exp_nums)
        compliance = _clamp(5.0 * hit)
    else:
        compliance = 5.0 if ans.strip() else 0.0

    # citation：编号引用 + 证据
    has_cite = bool(_CITE_RE.search(ans))
    if has_cite and ev.strip():
        citation = 5.0
    elif has_cite:
        citation = 2.5
    else:
        citation = 0.0

    # safety：红线词/注入命中扣分
    low = ans.lower()
    red_hits = sum(1 for w in _REDLINE if w.lower() in low)
    safety = _clamp(5.0 - 2.0 * red_hits)

    dimensions = {
        "relevance": round(relevance, 2),
        "compliance": round(compliance, 2),
        "citation": round(citation, 2),
        "safety": round(safety, 2),
    }
    score = round(_clamp(sum(dimensions.values()) / len(dimensions)), 2)
    return {
        "score": score,
        "dimensions": dimensions,
        "mode": "heuristic",
        "note": (
            "字面规则评分（关键词覆盖/数字命中/编号引用/红线词），未调用真 LLM-as-Judge；"
            "仅用于相对回归比较，不等于人工或模型评审的绝对质量。"
        ),
    }


def prompt_regression(baseline: str, candidate: str, cases: list) -> dict:
    """提示词回归门禁：候选回答的均分不低于基线回答才放行。

    语义约定（骨架，便于离线复算）：
      - ``baseline`` / ``candidate`` 为两段「待比较的回答文本」（旧版 / 新版产出）。
      - ``cases`` 为评测用例列表，逐例提供 ``expected``（标准答案；也接受 ``answer``
        作为别名）、可选 ``evidence``。
      - 逐例分别对 baseline / candidate 调用 :func:`llm_judge`，得到该例两条分数。

    返回 ``{"pass","baseline_avg","candidate_avg","delta","cases","mode"}``；
    ``pass = candidate_avg >= baseline_avg``（不劣化即放行，delta 允许为 0）。
    """
    rows: List[Dict[str, Any]] = []
    b_scores: List[float] = []
    c_scores: List[float] = []
    for i, raw in enumerate(cases or []):
        c = raw if isinstance(raw, dict) else {}
        expected = str(c.get("expected") or c.get("answer") or "")
        evidence = str(c.get("evidence") or "")
        b = llm_judge(baseline, expected, evidence)
        cd = llm_judge(candidate, expected, evidence)
        b_scores.append(b["score"])
        c_scores.append(cd["score"])
        rows.append({
            "index": i,
            "expected": expected,
            "evidence": evidence,
            "baseline": b["score"],
            "candidate": cd["score"],
            "baseline_dimensions": b["dimensions"],
            "candidate_dimensions": cd["dimensions"],
        })

    baseline_avg = round(sum(b_scores) / len(b_scores), 4) if b_scores else 0.0
    candidate_avg = round(sum(c_scores) / len(c_scores), 4) if c_scores else 0.0
    delta = round(candidate_avg - baseline_avg, 4)
    return {
        "pass": bool(candidate_avg >= baseline_avg),
        "baseline_avg": baseline_avg,
        "candidate_avg": candidate_avg,
        "delta": delta,
        "cases": rows,
        "mode": "heuristic",
    }


def _split_keep(text: str, delims: str) -> List[str]:
    """按 delims 切分并把分隔符保留在段尾；纯空白段丢弃，纯分隔符段并入前段。"""
    segs: List[str] = []
    buf = ""
    for ch in str(text or ""):
        buf += ch
        if ch in delims:
            segs.append(buf)
            buf = ""
    if buf:
        segs.append(buf)

    merged: List[str] = []
    for s in segs:
        if not s.strip():                       # 纯空白（含单独换行）
            continue
        if merged and all(ch in delims or ch.isspace() for ch in s):
            merged[-1] += s                     # 纯标点碎片并回前段，保留标点
        else:
            merged.append(s)
    return merged


def _split_long(sentence: str) -> List[str]:
    """长句（>60 字）再按 ，、 切；任一子片仍 >60 时按 60 字兜底硬切。"""
    if len(sentence) <= _MAX_LEN:
        return [sentence]
    pieces = _split_keep(sentence, _SUB_END)
    out: List[str] = []
    for p in pieces:
        while len(p) > _MAX_LEN:
            out.append(p[:_MAX_LEN])
            p = p[_MAX_LEN:]
        if p:
            out.append(p)
    return out


def chinese_segment(text: str) -> list:
    """中文语义边界切分，保留标点。

    1. 先按 ``。！？；\\n``（含半角 ``!?;``）切句，分隔符保留在段尾；
    2. 长句（>60 字）再按 ``，、`` 切到每片 <=60 字（仍超长按 60 字兜底硬切）；
    3. 只在标点边界切，不在词中间切，故短句里的专名/成语/数字保持完整。

    空文本 / 纯空白返回 ``[]``。
    """
    s = str(text or "")
    if not s.strip():
        return []
    sentences = _split_keep(s, _SENT_END)
    out: List[str] = []
    for sent in sentences:
        out.extend(_split_long(sent))
    return [seg for seg in out if seg.strip()]


# ── 内置 6 条中文基准（数字 / 专名 / 政策术语 / 成语 / 长句 / 混合）────────
_BENCH: List[Dict[str, str]] = [
    {"id": "num", "type": "数字",
     "text": "2023年公司营收同比增长15.8%，净利润达到3.2亿元，详见[1]。"},
    {"id": "proper", "type": "专名",
     "text": "粤港澳大湾区的腾讯与华为在深圳、广州两地联合建设人工智能算力中心。"},
    {"id": "policy", "type": "政策术语",
     "text": "依据《新型储能实施方案》，落实容量补偿与现货市场交易机制，坚持绿色低碳发展理念[2]。"},
    {"id": "idiom", "type": "成语",
     "text": "面对复杂局面要未雨绸缪、循序渐进，切忌揠苗助长与缘木求鱼。"},
    {"id": "long", "type": "长句",
     "text": "本次专项评审首先梳理了过去三个季度的经营数据，随后对照年度预算逐项核对了差旅、"
             "采购与人力成本，接着邀请财务、法务与业务部门分别就合规口径发表意见，"
             "最终形成了一份覆盖收入确认、成本归集、税务处理以及内控留痕四个方面的综合整改方案，"
             "并要求各责任部门在下一次例会前提交书面反馈与时间表。"},
    {"id": "mixed", "type": "混合/编号引用",
     "text": "根据《数据安全法》第21条[1]与内部保密制度，2025年第四季度共拦截可疑外发请求128次，"
             "较上季度的96次上升33.3%，已按应急预案完成溯源与整改。"},
]


def zh_bench() -> dict:
    """内置 6 条中文基准 + 自检，**不等同 C-Eval / CMMLU**。

    self_check 用本模块的切分/评判函数对 6 条做一致性自检（非空、分数在 0-5、
    长句确实被切分），只证明骨架自洽，不代表模型中文能力达标。
    """
    items = []
    for b in _BENCH:
        segs = chinese_segment(b["text"])
        j = llm_judge(b["text"], b["text"], "[1] 内置证据")
        items.append({
            "id": b["id"], "type": b["type"], "chars": len(b["text"]),
            "segments": len(segs), "max_segment_chars": max((len(x) for x in segs), default=0),
            "judge_score": j["score"],
        })
    long_item = next(i for i in items if i["id"] == "long")
    self_check = {
        "size": len(items),
        "all_segmented_non_empty": all(i["segments"] >= 1 for i in items),
        "all_segments_le_60": all(i["max_segment_chars"] <= _MAX_LEN for i in items),
        "judge_scores_in_range": all(0 <= i["judge_score"] <= 5 for i in items),
        "long_sentence_split": long_item["segments"] > 1,
        "pass": (
            all(i["segments"] >= 1 for i in items)
            and all(i["max_segment_chars"] <= _MAX_LEN for i in items)
            and all(0 <= i["judge_score"] <= 5 for i in items)
            and long_item["segments"] > 1
        ),
        "items": items,
    }
    return {
        "size": 6,
        "self_check": self_check,
        "note": "内部小集，不等同 C-Eval/CMMLU",
    }


def reset_state() -> None:
    """无状态模块：保留统一接口，供测试 autouse fixture 调用（重置隔离用）。"""
    return None
