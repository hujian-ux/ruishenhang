"""M28 LLM-as-Judge + 提示词回归门禁 单元测试（不依赖真模型，mock 网关调用）。"""
import importlib

import pytest
from fastapi.testclient import TestClient

from app.core import security
from app.main import app
from app.services import judge as judge_svc

_ADMIN = security.create_dev_token(user_id="u_admin", dept_id="d_it",
                                    role="platform_admin", tenant_id="t_demo")
_EMP = security.create_dev_token(user_id="u_emp", dept_id="d_it",
                                  role="employee", tenant_id="t_demo")
_H_ADMIN = {"Authorization": _ADMIN}
_H_EMP = {"Authorization": _EMP}


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


def test_regression_gate_no_baseline(tmp_path):
    # 隔离基线文件路径，避免读取到其它测试/运行留下的基线
    judge_svc.JUDGE_BASELINE = tmp_path / "judge_baseline.json"
    reg = judge_svc.regression_gate({"avg_overall": 0.8, "by_dept": {}})
    assert reg["status"] == "baseline"


# ── REST 端点覆盖（真判官经 API 暴露）──────────────────────────────────────
def test_eval_judge_one_honest_without_model():
    c = TestClient(app)
    r = c.post("/api/v1/eval/judge-one", headers=_H_EMP,
               json={"query": "q", "reference": "ref", "answer": "a"})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["llm_real"] is False          # 无真模型：诚实降级，不冒充真判分
    assert d["verdict"] in ("pass", "partial", "fail")
    assert "correctness" in d and "overall" in d


def test_eval_judge_run_and_regression_admin(monkeypatch, tmp_path):
    orig = _fake_judge('{"verdict":"pass","correctness":0.95,"groundedness":0.95,'
                       '"completeness":0.9,"reason":"ok"}')
    monkeypatch.setattr(judge_svc, "default_answerer", lambda q, model_id=None: "answer")
    # 隔离基线文件路径，避免污染仓库内 reports/ 的基线
    monkeypatch.setattr(judge_svc, "JUDGE_BASELINE", tmp_path / "judge_baseline.json")
    try:
        c = TestClient(app)
        golden = [{"query": f"q{i}", "reference": f"ref{i}", "dept": "finance"}
                  for i in range(6)]
        r = c.post("/api/v1/eval/judge-run", headers=_H_ADMIN, json={"golden": golden})
        assert r.status_code == 200
        rep = r.json()["data"]
        assert rep["golden_size"] == 6
        assert rep["llm_real"] is True
        assert rep["by_dept"]["finance"]["n"] == 6
        # 首次跑无基线 → regression 标 baseline，并保存
        r2 = c.post("/api/v1/eval/judge-regression", headers=_H_ADMIN,
                    json={"golden": golden, "save_baseline": True})
        assert r2.status_code == 200
        reg = r2.json()["data"]["regression"]
        assert reg["status"] == "baseline"
        assert reg.get("baseline_saved") is True
    finally:
        judge_svc.model_gateway.chat_sync_ex = orig


def test_eval_judge_run_forbidden_for_employee():
    c = TestClient(app)
    r = c.post("/api/v1/eval/judge-run", headers=_H_EMP,
               json={"golden": [{"query": "q", "reference": "r", "dept": "x"}]})
    assert r.json()["code"] == 403          # 越权：员工不可跑判分批处理


def test_eval_judge_save_baseline_forbidden_for_employee():
    c = TestClient(app)
    r = c.post("/api/v1/eval/judge-save-baseline", headers=_H_EMP,
               json={"report": {"avg_overall": 0.8}})
    assert r.json()["code"] == 403
