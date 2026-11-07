"""第28章 LLM-as-Judge + 提示词回归门禁。

与 eval.py 的「客观指标」（Recall/MRR/nDCG/过滤正确率，纯确定性、不依赖 LLM）
互补：本模块用**真模型**对「生成答案」做质量判分——
正确性 correctness / 依据度 groundedness / 完整度 completeness / 总体裁决 verdict。

设计要点：
- 判官复用 ``model_gateway`` 的真模型通道（与业务同一 key，无需新增依赖）；
  没有真模型时 ``chat_sync_ex`` 走 stub 并返回 ``real=False``，判官会如实标记
  ``llm_real=False``，**绝不把 stub 当真判分**。
- 答案由「answerer」可注入函数产出：默认是轻量 RAG（检索 top 段落 + 基于证据作答），
  避免每次都把全量 agent 编排跑一遍；生产可换成真实 ``run_agent``。
- 回归门禁：每次判分落基线快照；提示词/技能包变更后重跑，若平均分相对基线
  跌幅超过阈值则标 ``regression``，可接 CI 阻断（与 M20 演化联动）。
- 覆盖度：按部门统计 golden 条数，达标线 ``JUDGE_COVERAGE_MIN_PER_DEPT``（蓝图要求 ≥50 条/部门）。

golden 格式（services/qa_golden.jsonl，每行一条 JSON）：
  {"query": "...", "reference": "标准答案/依据", "dept": "finance",
   "scope": "global", "accept": ["也可接受的另一种表述"]}
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from app.core.config import settings
from app.services import model_gateway

QA_GOLDEN_PATH = Path(__file__).parent / "qa_golden.jsonl"
REPORT_DIR = Path(__file__).resolve().parent.parent.parent / "reports"
JUDGE_COVERAGE_MIN_PER_DEPT = 50          # 蓝图：每部门 ≥50 条 golden
JUDGE_REGRESSION_DROP = 0.05              # 平均分跌幅 >5pp 即标回归
JUDGE_BASELINE = REPORT_DIR / "judge_baseline.json"

_JUDGE_PROMPT = """你是一名严格的中文企业知识库答案质量评审员。给定【用户问题】、【标准依据】和【待评审答案】，请只输出一个 JSON 对象，不要输出任何额外文字或代码块标记。
JSON 字段说明：
- correctness（0到1浮点数）：答案事实是否与标准依据一致，有无编造或事实错误。
- groundedness（0到1浮点数）：答案是否基于标准依据/可追溯来源，有无凭空杜撰。
- completeness（0到1浮点数）：答案是否覆盖了问题要点。
- verdict：只能是 pass / partial / fail 三者之一（correctness<0.6 或 groundedness<0.6 应判 fail）。
- reason：一句话中文说明扣分点（如无误则写「无明显问题」）。

【用户问题】
{query}

【标准依据】
{reference}

【待评审答案】
{answer}
"""


@dataclass
class JudgeScore:
    correctness: float
    groundedness: float
    completeness: float
    verdict: str
    reason: str
    llm_real: bool

    def overall(self) -> float:
        return round((self.correctness + self.groundedness + self.completeness) / 3.0, 4)


def parse_judge(raw: str) -> dict:
    """从判官返回里抽取 JSON（容忍 ```json 围栏与前后多余文字）。解析失败抛 ValueError。"""
    if not raw:
        raise ValueError("判官返回为空")
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text).strip()
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError(f"判官返回中找不到 JSON 对象：{raw[:120]}")
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise ValueError(f"判官 JSON 解析失败：{e}") from e
    return obj


def _clamp(v) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, x))


def score_from_obj(obj: dict, llm_real: bool) -> JudgeScore:
    verdict = str(obj.get("verdict", "")).lower()
    if verdict not in ("pass", "partial", "fail"):
        verdict = "fail" if (_clamp(obj.get("correctness")) < 0.6
                            or _clamp(obj.get("groundedness")) < 0.6) else "partial"
    return JudgeScore(
        correctness=_clamp(obj.get("correctness")),
        groundedness=_clamp(obj.get("groundedness")),
        completeness=_clamp(obj.get("completeness")),
        verdict=verdict,
        reason=str(obj.get("reason", ""))[:300],
        llm_real=llm_real,
    )


def judge_one(query: str, reference: str, answer: str,
              model_id: str | None = None) -> JudgeScore:
    """对单条（问题/依据/答案）跑真模型判分。无真模型时返回 llm_real=False 的占位判分。"""
    mid = model_id or settings.LLM_DEFAULT_MODEL
    resp = model_gateway.chat_sync_ex(
        mid,
        [{"role": "system", "content": "你是严格的中文答案质量评审员，只输出 JSON。"},
         {"role": "user", "content": _JUDGE_PROMPT.format(query=query, reference=reference, answer=answer)}],
        max_tokens=512, temperature=0,
    )
    if not resp.get("real"):
        # 没有真模型：诚实降级，不冒充真判分（与 M17/M27 一致口径）
        return JudgeScore(0.0, 0.0, 0.0, "fail",
                         f"判官未接真模型（{resp.get('error', 'stub')}）", llm_real=False)
    return score_from_obj(parse_judge(resp["text"]), True)


def default_answerer(query: str, model_id: str | None = None) -> str:
    """轻量 RAG 作答（评测用，避免每次跑全量 agent 编排）：检索 top 段落 → 基于证据合成答案。"""
    from app.models.caller import CallerContext
    from app.services import knowledge

    caller = CallerContext(userId="u_judge", deptId="d_it", role="platform_admin", tenantId="t_demo")
    hits, _ = knowledge.retrieve(query, caller, top_k=5)
    evidence = "\n\n".join(
        f"[{i+1}] {h.get('title', '')}\n{(h.get('content') or '')[:600]}"
        for i, h in enumerate(hits)
    ) or "（无可用证据）"
    mid = model_id or settings.LLM_DEFAULT_MODEL
    resp = model_gateway.chat_sync_ex(
        mid,
        [{"role": "system", "content": "你是企业知识库助手，只能基于给定证据作答，证据不足就明说。"},
         {"role": "user", "content": f"证据：\n{evidence}\n\n问题：{query}\n请给出简洁、有依据的中文回答。"}],
        max_tokens=600, temperature=0,
    )
    return resp.get("text", "") if resp.get("real") else ""


def load_qa_golden(path: str | Path | None = None) -> list[dict]:
    p = Path(path) if path else QA_GOLDEN_PATH
    if not p.exists():
        return []
    rows: list[dict] = []
    for n, line in enumerate(p.read_text(encoding="utf-8-sig").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise ValueError(f"QA golden 第 {n} 行解析失败：{e}")
    return rows


def run_judge(qa_golden: list[dict] | None = None,
              answerer: Callable[[str], str] | None = None,
              model_id: str | None = None) -> dict:
    """跑一轮 QA 判分：逐条生成答案并判分，按部门聚合 + 算覆盖度。

    返回结构化报告（与 eval.evaluate 同构，便于面板统一消费）。
    """
    golden = qa_golden if qa_golden is not None else load_qa_golden()
    ans = answerer or default_answerer
    if not golden:
        raise ValueError("QA golden 为空：没有可用于判分的 query（检查 qa_golden.jsonl）")

    per_dept: dict[str, dict] = {}
    rows: list[dict] = []
    for g in golden:
        q = (g.get("query") or "").strip()
        ref = g.get("reference") or g.get("expected_answer") or ""
        dept = g.get("dept") or "unknown"
        if not q or not ref:
            continue
        answer = ans(q) or ""
        sc = judge_one(q, ref, answer, model_id=model_id)
        s = per_dept.setdefault(dept, {"n": 0, "correctness": 0.0, "groundedness": 0.0,
                                       "completeness": 0.0, "pass": 0, "fail": 0})
        s["n"] += 1
        s["correctness"] += sc.correctness
        s["groundedness"] += sc.groundedness
        s["completeness"] += sc.completeness
        if sc.verdict == "pass":
            s["pass"] += 1
        elif sc.verdict == "fail":
            s["fail"] += 1
        rows.append({"query": q, "dept": dept, "verdict": sc.verdict,
                     "overall": sc.overall(), "reason": sc.reason, "llm_real": sc.llm_real})

    total = len(rows)
    by_dept = {}
    for d, s in sorted(per_dept.items()):
        by_dept[d] = {
            "n": s["n"],
            "correctness": round(s["correctness"] / s["n"], 4),
            "groundedness": round(s["groundedness"] / s["n"], 4),
            "completeness": round(s["completeness"] / s["n"], 4),
            "pass_rate": round(s["pass"] / s["n"], 4),
            "fail_rate": round(s["fail"] / s["n"], 4),
            "covered": s["n"] >= JUDGE_COVERAGE_MIN_PER_DEPT,
        }
    avg = (sum(r["overall"] for r in rows) / total) if total else 0.0
    llm_real = all(r["llm_real"] for r in rows) if rows else False
    coverage_ok = all(v["covered"] for v in by_dept.values()) if by_dept else False
    return {
        "generated_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        "judge_model": model_id or settings.LLM_DEFAULT_MODEL,
        "llm_real": llm_real,
        "golden_size": total,
        "avg_overall": round(avg, 4),
        "coverage_min_per_dept": JUDGE_COVERAGE_MIN_PER_DEPT,
        "coverage_ok": coverage_ok,
        "by_dept": by_dept,
        "rows": rows,
        "gate": {
            "passed": bool(llm_real and coverage_ok and avg >= 0.6),
            "reason": ("通过" if (llm_real and coverage_ok and avg >= 0.6)
                       else ("判官未接真模型" if not llm_real
                             else ("部门覆盖不足（需 ≥%d 条/部门）" % JUDGE_COVERAGE_MIN_PER_DEPT
                                   if not coverage_ok else f"平均分 {avg:.3f} < 0.6"))),
        },
    }


def load_baseline(path: str | Path | None = None) -> dict | None:
    p = Path(path) if path else JUDGE_BASELINE
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_baseline(report: dict, path: str | Path | None = None) -> None:
    p = Path(path) if path else JUDGE_BASELINE
    p.parent.mkdir(parents=True, exist_ok=True)
    keep = {k: report[k] for k in ("avg_overall", "by_dept", "golden_size", "judge_model",
                                   "generated_at") if k in report}
    p.write_text(json.dumps(keep, ensure_ascii=False, indent=2), encoding="utf-8")


def regression_gate(report: dict, baseline: dict | None = None) -> dict:
    """对比当前报告与基线：平均分跌幅 > 阈值标 regression；首次跑无基线则标 baseline。"""
    base = baseline if baseline is not None else load_baseline()
    if not base:
        return {"status": "baseline", "drop": 0.0,
                "reason": "无历史基线，本次作为基线（保存后下次比对）"}
    base_avg = float(base.get("avg_overall", 0.0) or 0.0)
    cur_avg = float(report.get("avg_overall", 0.0) or 0.0)
    drop = round(base_avg - cur_avg, 4)
    regressed = drop > JUDGE_REGRESSION_DROP
    # 部门级回归明细
    dept_drops: dict[str, float] = {}
    base_dept = base.get("by_dept", {})
    for d, cur in (report.get("by_dept") or {}).items():
        b = base_dept.get(d, {}).get("correctness")
        if b is not None:
            ddrop = round(float(b) - float(cur.get("correctness", 0.0)), 4)
            if ddrop > JUDGE_REGRESSION_DROP:
                dept_drops[d] = ddrop
    return {
        "status": "regression" if regressed else "ok",
        "base_avg": base_avg,
        "cur_avg": cur_avg,
        "drop": drop,
        "threshold": JUDGE_REGRESSION_DROP,
        "dept_drops": dept_drops,
        "reason": ("检测到提示词/技能包回归：平均分跌幅 %.3f 超阈值 %.2f"
                   % (drop, JUDGE_REGRESSION_DROP)) if regressed else "无显著回归",
    }


def main():
    import argparse

    parser = argparse.ArgumentParser(description="企业 AI 中台 · LLM-as-Judge（第28章）")
    parser.add_argument("--golden", default="", help="QA golden 路径（默认 services/qa_golden.jsonl）")
    parser.add_argument("--model", default="", help="判官模型（默认 LLM_DEFAULT_MODEL）")
    parser.add_argument("--save-baseline", action="store_true", help="跑完把本次作为回归基线")
    parser.add_argument("--compare-baseline", action="store_true", help="与已存基线比对（回归门禁）")
    args = parser.parse_args()

    report = run_judge(golden_path := (args.golden or None), model_id=(args.model or None))
    if args.save_baseline:
        save_baseline(report)
    reg = regression_gate(report) if args.compare_baseline else None
    print(f"[JUDGE] golden={report['golden_size']} llm_real={report['llm_real']} "
          f"avg={report['avg_overall']} coverage_ok={report['coverage_ok']}")
    print(f"[JUDGE] 门禁：{'PASS' if report['gate']['passed'] else 'FAIL'} — {report['gate']['reason']}")
    if reg:
        print(f"[JUDGE] 回归：{reg['status']}（base={reg['base_avg']} cur={reg['cur_avg']} drop={reg['drop']}）")
    if not report["gate"]["passed"]:
        raise SystemExit(f"FAIL: judge gate — {report['gate']['reason']}")


if __name__ == "__main__":
    main()
