"""M42 置信降级 / 转人工 单元测试（降级链真执行 + 低置信自动转人工）。"""
import pytest

from app.models.caller import CallerContext
from app.services import degrade as degrade_svc
from app.core.config import settings


@pytest.fixture(autouse=True)
def _reset():
    degrade_svc.reset_state()
    yield
    degrade_svc.reset_state()


def _caller(role="employee", uid="u_x"):
    return CallerContext(userId=uid, deptId="d_it", role=role, tenantId="t_demo")


def _primary_good():
    return {"answer": "依据第一章第三条，差旅费需主管审批。", "evidence": "第一章第三条",
            "llm_real": True, "guard": {}}


def _primary_bad():
    return {"answer": "可能吧", "evidence": "", "llm_real": False, "guard": {}}


def _small_model_better():
    return {"answer": "依据规定，需审批。", "evidence": "制度正文", "llm_real": True, "guard": {}}


def test_confidence_caps_stub_at_half():
    assert degrade_svc.confidence("有答案", "有证据", llm_real=False) <= 0.5
    assert degrade_svc.confidence("有答案", "有证据", llm_real=True) > 0.5


def test_exec_chain_stops_when_conf_high():
    out = degrade_svc.exec_chain("ag1", _primary_good, _caller())
    assert out["tried"] == ["primary"]
    assert out["confidence"] >= settings.CONFIDENCE_THRESHOLD
    assert "handoff" not in out


def test_exec_chain_falls_back_to_small_model():
    out = degrade_svc.exec_chain(
        "ag1", _primary_bad, _caller(),
        fallbacks={"small_model": _small_model_better})
    assert "small_model" in out["tried"]
    assert out["confidence"] >= settings.CONFIDENCE_THRESHOLD


def test_exec_chain_human_handoff_when_all_low():
    out = degrade_svc.exec_chain("ag1", _primary_bad, _caller())
    assert out["confidence"] < settings.CONFIDENCE_THRESHOLD
    assert out.get("handoff") is not None
    assert out["handoff"]["status"] == "open"


def test_maybe_handoff_gated_by_enabled(monkeypatch):
    monkeypatch.setattr(settings, "HANDOFF_ENABLED", True)
    # 低置信 → 建单
    ho = degrade_svc.maybe_handoff("r1", "ag1",
        {"answer": "可能", "evidence": "", "llm_real": False, "guard": {}}, _caller())
    assert ho is not None and ho["status"] == "open"
    # 高置信 → 不建单
    ho2 = degrade_svc.maybe_handoff("r2", "ag1",
        {"answer": "依据第一章第三条。", "evidence": "第一章", "llm_real": True, "guard": {}},
        _caller())
    assert ho2 is None
    # 总开关关闭 → 即便低置信也不建单
    monkeypatch.setattr(settings, "HANDOFF_ENABLED", False)
    ho3 = degrade_svc.maybe_handoff("r3", "ag1",
        {"answer": "可能", "evidence": "", "llm_real": False, "guard": {}}, _caller())
    assert ho3 is None
