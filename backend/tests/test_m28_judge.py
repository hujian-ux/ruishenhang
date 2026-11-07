"""M28 LLM-as-Judge + 提示词回归门禁 单元测试（不依赖真模型，mock 网关调用）。"""
import importlib

import pytest

from app.services import judge as judge_svc


def _fake_judge(resp_text: str, real: bool = True):
    """把 model_gateway.chat_sync_ex 替换成固定返回，避免真调模型。"""
    import app.services.judge as j

    def _fake(model_id, messages, **kw):
        return {"text": resp_text, "real": real, "model_id": model_id,
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                "latency_ms": 1, "error": ""}

    orig = j.model_gateway.chat_sync_ex
    j.model_gateway.chat_sync_ex = _fake
    return orig


def test_parse_judge_plain_and_fenced():
    assert judge_svc.parse_judge('{"verdict":"pass","correctness":0.9}')["verdict"] == "pass"
    fenced = '```json\n{"verdict":"fail","correctness":0.4}\n```'
    assert judge_svc.parse_judge(fenced)["correctness"] == 0.4
    with pytest.raises(ValueError):
        judge_svc.parse_judge("没有任何 json 在这里")


def test_score_from_obj_verdict_fallback():
    s = judge_svc.score_from_obj({"correctness": 0.5, "groundedness": 0.9,
                                  "completeness": 0.8, "reason": "x"}, True)
    assert s.verdict == "fail"          # correctness<0.6 → fail
    assert s.overall() == pytest.approx((0.5 + 0.9 + 0.8) / 3, abs=1e-3)
    s2 = judge_svc.score_from_obj({"correctness": 0.9, "groundedness": 0.9,
                                   "completeness": 0.9, "verdict": "pass"}, True)
    assert s2.verdict == "pass"


def test_run_judge_aggregation_and_coverage(monkeypatch):
    orig = _fake_judge('{"verdict":"pass","correctness":0.9,"groundedness":0.9,"completeness":0.8,"reason":"ok"}')
    try:
        golden = [
            {"query": f"q{i}", "reference": f"ref{i}", "dept": "finance"} for i in range(6)
        ]
        report = judge_svc.run_judge(golden, answerer=lambda q: "answer")
    finally:
        judge_svc.model_gateway.chat_sync_ex = orig

    assert report["golden_size"] == 6
    assert report["llm_real"] is True
    assert report["coverage_ok"] is False          # 每部门 <50 条 → 如实标未覆盖
    assert report["by_dept"]["finance"]["n"] == 6
    assert report["gate"]["passed"] is False        # 覆盖不足 → 门禁不通过（诚实）


def test_run_judge_coverage_pass_with_enough_golden(monkeypatch):
    orig = _fake_judge('{"verdict":"pass","correctness":0.95,"groundedness":0.95,"completeness":0.9,"reason":"ok"}')
    try:
        golden = [
            {"query": f"q{i}", "reference": f"ref{i}", "dept": "hr"} for i in range(60)
        ]
        report = judge_svc.run_judge(golden, answerer=lambda q: "answer")
    finally:
        judge_svc.model_gateway.chat_sync_ex = orig

    assert report["coverage_ok"] is True
    assert report["gate"]["passed"] is True
    assert report["avg_overall"] > 0.9


def test_regression_gate_detects_drop():
    base = {"avg_overall": 0.90, "by_dept": {"finance": {"correctness": 0.9}}}
    cur = {"avg_overall": 0.80, "by_dept": {"finance": {"correctness": 0.78}}}
    reg = judge_svc.regression_gate(cur, base)
    assert reg["status"] == "regression"
    assert reg["dept_drops"]["finance"] == pytest.approx(0.12, abs=1e-3)

    cur2 = {"avg_overall": 0.88, "by_dept": {"finance": {"correctness": 0.89}}}
    reg2 = judge_svc.regression_gate(cur2, base)
    assert reg2["status"] == "ok"


def test_regression_gate_no_baseline():
    reg = judge_svc.regression_gate({"avg_overall": 0.8, "by_dept": {}})
    assert reg["status"] == "baseline"
