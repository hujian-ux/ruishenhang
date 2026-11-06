"""M27 · 反馈闭环 / 演化建议台 / 运营与质量度量 单元测试。

自建 FastAPI app（不 import app.main），按蓝图模板挂中间件与路由。
autouse fixture 负责：屏蔽审计写入（避免 sqlite 竞争）+ reset_state 隔离。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.middleware import AuditTraceMiddleware, GatewayMiddleware  # noqa: E402
from app.core.security import create_dev_token  # noqa: E402
from app.routers import feedback as fb_router  # noqa: E402
from app.services import feedback as fb_service  # noqa: E402

app = FastAPI()
app.add_middleware(AuditTraceMiddleware)
app.add_middleware(GatewayMiddleware)
app.include_router(fb_router.router, prefix="/api/v1")
client = TestClient(app)

ADMIN = {"Authorization": create_dev_token("p_admin", "d_platform", "platform_admin")}
EMP = {"Authorization": create_dev_token("u_zhang", "d_sales", "employee")}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    # 审计后端走 PG/SQLite，测试间并发写会互相干扰；这里整体屏蔽。
    monkeypatch.setattr("app.services.audit.record", lambda *a, **k: None)
    fb_service.reset_state()
    yield
    fb_service.reset_state()


# ---- 辅助 ------------------------------------------------------------------
def _post_feedback(vote, agent_id="a1", **extra):
    payload = {"run_id": "r_1", "session_id": "s_1", "agent_id": agent_id, "vote": vote}
    payload.update(extra)
    return client.post("/api/v1/feedback", json=payload, headers=EMP)


def _mk_proposal(kind="knowledge", payload=None, headers=None):
    r = client.post(
        "/api/v1/evolution/proposals",
        json={"kind": kind, "title": "补充价格知识",
              "payload": payload if payload is not None else {"doc": "x"},
              "source": "gap:g1"},
        headers=headers or EMP,
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _approve(pid):
    r = client.post(f"/api/v1/evolution/proposals/{pid}/decide",
                    json={"decision": "approve"}, headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _stats():
    return client.get("/api/v1/feedback/stats", headers=EMP).json()["data"]


def _proposals(status=None):
    url = "/api/v1/evolution/proposals"
    if status:
        url += f"?status={status}"
    return client.get(url, headers=ADMIN).json()["data"]


def _versions():
    return client.get("/api/v1/evolution/versions", headers=ADMIN).json()["data"]


# ---- 1. 赞 / 踩记录 ---------------------------------------------------------
def test_up_and_down_recorded():
    up = _post_feedback("up")
    assert up.status_code == 200, up.text
    d = up.json()["data"]
    assert d["vote"] == "up"
    assert d["userId"] == "u_zhang" and d["deptId"] == "d_sales"
    assert d["created_at"] and d["created_iso"] and d["id"].startswith("fb_")

    down = _post_feedback("down", reason="答案过时")
    assert down.status_code == 200, down.text
    assert down.json()["data"]["vote"] == "down"
    assert down.json()["data"]["reason"] == "答案过时"


# ---- 2. 非法 vote 拒绝 ------------------------------------------------------
def test_invalid_vote_rejected():
    r = _post_feedback("sideways")
    assert r.status_code in (400, 422), r.text
    assert _stats()["total"] == 0


# ---- 3. 纠错触发缺口 --------------------------------------------------------
def test_correction_triggers_gap():
    r = _post_feedback("up", agent_id="a_kb", correction="应引用 2026 版制度",
                       reason="制度版本")
    assert r.status_code == 200, r.text
    gaps = client.get("/api/v1/evolution/gaps", headers=EMP).json()["data"]["gaps"]
    assert len(gaps) == 1
    g = gaps[0]
    assert g["agent_id"] == "a_kb" and g["count"] == 1
    assert g["sample"]["correction"] == "应引用 2026 版制度"
    assert g["key"] == "a_kb|制度版本"
    assert g["suggestion"]


# ---- 4. 缺口按 count 倒序 ---------------------------------------------------
def test_gaps_sorted_by_count_desc():
    for i in range(3):
        assert _post_feedback("down", agent_id="a_hot", reason="价格错误").status_code == 200
    assert _post_feedback("down", agent_id="a_cold", reason="格式问题").status_code == 200
    gaps = client.get("/api/v1/evolution/gaps", headers=EMP).json()["data"]["gaps"]
    assert len(gaps) == 2
    counts = [g["count"] for g in gaps]
    assert counts == sorted(counts, reverse=True)
    assert gaps[0]["agent_id"] == "a_hot" and gaps[0]["count"] == 3
    assert gaps[1]["count"] == 1


# ---- 5. 建议默认 pending、绝不自动生效 --------------------------------------
def test_proposal_defaults_pending_never_auto_applied():
    pid = _mk_proposal()
    rows = _proposals()["proposals"]
    p = next(x for x in rows if x["id"] == pid)
    assert p["status"] == "pending"
    assert p["auto_applied"] is False
    assert p["require_approval"] is True
    assert p["applied_version_id"] is None
    # 没有任何自动落版本 / 自动生效
    assert _versions()["count"] == 0
    assert _proposals("applied")["count"] == 0


# ---- 6. 非管理员审批 403 ---------------------------------------------------
def test_non_admin_decide_returns_403():
    pid = _mk_proposal()
    r = client.post(f"/api/v1/evolution/proposals/{pid}/decide",
                    json={"decision": "approve"}, headers=EMP)
    assert r.status_code == 403
    assert _proposals()["proposals"][0]["status"] == "pending"
    assert _versions()["count"] == 0


# ---- 7. 管理员审批产生版本快照 ---------------------------------------------
def test_admin_approve_creates_version_snapshot():
    pid = _mk_proposal(payload={"doc": "价格表v2"})
    decided = _approve(pid)
    assert decided["status"] == "applied"
    assert decided["decided_by"] == "p_admin"
    vs = _versions()["versions"]
    assert _versions()["count"] == 1
    v = vs[0]
    assert v["version_id"] == decided["applied_version_id"]
    assert v["proposal_id"] == pid
    assert v["payload"] == {"doc": "价格表v2"}
    assert v["kind"] == "knowledge"


# ---- 8. 回滚生成新 pending 建议，而非静默改库 -------------------------------
def test_rollback_creates_new_pending_proposal_not_silent_write():
    pid = _mk_proposal(payload={"doc": "x"})
    vid = _approve(pid)["applied_version_id"]
    before = _proposals()["count"]

    r = client.post("/api/v1/evolution/rollback", json={"version_id": vid}, headers=ADMIN)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    rp = data["restored_proposal"]
    assert rp["status"] == "pending"
    assert rp["id"] != pid                       # 是一条新建议
    assert rp["source"] == f"rollback:{vid}"
    assert rp["payload"] == {"doc": "x"}
    assert data["silent_db_write"] is False
    assert data["requires_approval"] is True
    assert "提议" in data["note"] and "不是静默改库" in data["note"]
    assert _proposals()["count"] == before + 1
    assert _versions()["count"] == 1            # 原快照未被改动
    assert _proposals("pending")["count"] == 1  # 回滚出来的仍需再次审批


# ---- 9. adoption 空数据不造假 ----------------------------------------------
def test_adoption_empty_returns_zeros_with_insufficient_data():
    d = client.get("/api/v1/metrics/adoption", headers=EMP).json()["data"]
    assert d["insufficient_data"] is True
    assert d["sample_size"] == 0
    for key in ("dau", "wau", "adoption_rate", "task_completion_rate",
                "dept_penetration", "roi"):
        assert d[key] == 0, key


# ---- 10. quality 埋点后 p95 可算 -------------------------------------------
def test_quality_percentiles_after_events():
    for i in range(10):
        r = client.post("/api/v1/metrics/quality",
                        json={"success": True, "real": True,
                              "latency_ms": 100 + i * 10, "confidence": 0.9,
                              "userId": f"u_{i}", "deptId": "d_sales",
                              "agent_id": "a1"},
                        headers=EMP)
        assert r.status_code == 200, r.text
    m = client.get("/api/v1/metrics/quality", headers=EMP).json()["data"]
    assert m["sample_size"] == 10
    assert m["insufficient_data"] is False
    assert m["latency_p95"] > 0
    assert m["latency_ms"]["p95"] == m["latency_p95"]
    assert m["latency_ms"]["samples"] == 10
    assert m["latency_p50"] <= m["latency_p95"] <= m["latency_p99"]
    assert m["success_rate"] == 1.0 and m["stub_ratio"] == 0.0


# ---- 11. stats 计数正确 -----------------------------------------------------
def test_feedback_stats_counts_correct():
    _post_feedback("up", agent_id="a1")
    _post_feedback("up", agent_id="a1")
    _post_feedback("up", agent_id="a2")
    _post_feedback("down", agent_id="a1", reason="x")
    _post_feedback("up", agent_id="a2", correction="改一下")
    s = _stats()
    assert s["total"] == 5
    assert s["up"] == 4 and s["down"] == 1 and s["corrections"] == 1
    assert s["by_agent"] == {"a1": 3, "a2": 2}
    assert s["satisfaction"] == 0.8
    assert set(s) == {"total", "up", "down", "corrections", "by_agent", "satisfaction"}


# ---- 12. reset_state 隔离 ---------------------------------------------------
def test_reset_state_clears_everything():
    _post_feedback("up")
    pid = _mk_proposal()
    _approve(pid)
    client.post("/api/v1/metrics/quality", json={"latency_ms": 10}, headers=EMP)
    assert _stats()["total"] == 1
    assert _versions()["count"] == 1

    fb_service.reset_state()
    assert _stats()["total"] == 0
    assert _proposals()["count"] == 0
    assert _versions()["count"] == 0
    assert client.get("/api/v1/evolution/gaps", headers=EMP).json()["data"]["count"] == 0
    assert client.get("/api/v1/metrics/quality", headers=EMP).json()["data"]["sample_size"] == 0


def test_autouse_fixture_isolates_between_tests():
    # 上一条用例写入的数据不应泄漏到这里
    assert _stats()["total"] == 0
    assert _proposals()["count"] == 0
    assert _versions()["count"] == 0


# ---- 13. 全部端点需登录 -----------------------------------------------------
def test_all_endpoints_require_login():
    checks = [
        ("get", "/api/v1/feedback/stats"),
        ("get", "/api/v1/evolution/gaps"),
        ("get", "/api/v1/evolution/proposals"),
        ("get", "/api/v1/evolution/versions"),
        ("get", "/api/v1/metrics/adoption"),
        ("get", "/api/v1/metrics/quality"),
        ("post", "/api/v1/feedback"),
        ("post", "/api/v1/evolution/proposals"),
        ("post", "/api/v1/evolution/proposals/x/decide"),
        ("post", "/api/v1/evolution/rollback"),
        ("post", "/api/v1/metrics/quality"),
    ]
    for method, path in checks:
        r = (client.post(path, json={}) if method == "post" else client.get(path))
        assert r.status_code == 401, (path, r.status_code, r.text)


# ---- 14. 质量比率与低置信率 -------------------------------------------------
def test_quality_rates_and_low_confidence():
    events = [
        {"success": True, "real": True, "latency_ms": 10, "confidence": 0.9},
        {"success": False, "real": False, "latency_ms": 20,
         "hallucination_flag": True, "confidence": 0.2},
        {"success": True, "real": True, "latency_ms": 30,
         "handoff": True, "confidence": 0.4},
        {"success": True, "real": True, "latency_ms": 40, "confidence": 0.8},
        {"success": True, "real": True, "latency_ms": 50, "confidence": 0.95},
    ]
    for e in events:
        assert client.post("/api/v1/metrics/quality", json=e, headers=EMP).status_code == 200
    m = client.get("/api/v1/metrics/quality", headers=EMP).json()["data"]
    assert m["sample_size"] == 5
    assert m["success_rate"] == 0.8
    assert m["stub_ratio"] == 0.2
    assert m["hallucination_rate"] == 0.2
    assert m["handoff_rate"] == 0.2
    thr = m["confidence_threshold"]
    expected_low = round(sum(1 for e in events if e["confidence"] < thr) / 5, 4)
    assert m["low_confidence_rate"] == expected_low


# ---- 15. reject 不产生版本；非法 kind / 未知版本 -----------------------------
def test_reject_creates_no_version_and_validation():
    pid = _mk_proposal()
    r = client.post(f"/api/v1/evolution/proposals/{pid}/decide",
                    json={"decision": "reject"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["data"]["status"] == "rejected"
    assert _versions()["count"] == 0

    bad = client.post("/api/v1/evolution/proposals",
                      json={"kind": "magic", "title": "t", "payload": {}}, headers=EMP)
    assert bad.status_code in (400, 422)

    missing = client.post("/api/v1/evolution/rollback",
                          json={"version_id": "ver_not_exist"}, headers=ADMIN)
    assert missing.status_code == 404


# ---- 16. 回滚同样需要管理员 -------------------------------------------------
def test_rollback_requires_admin():
    pid = _mk_proposal()
    vid = _approve(pid)["applied_version_id"]
    r = client.post("/api/v1/evolution/rollback",
                    json={"version_id": vid}, headers=EMP)
    assert r.status_code == 403
    assert _proposals()["count"] == 1  # 未生成新建议


# ---- 17. status 过滤与直接调用服务契约 --------------------------------------
def test_status_filter_and_direct_service_contract():
    p1 = _mk_proposal()
    p2 = _mk_proposal()
    _approve(p1)
    assert _proposals("pending")["count"] == 1
    assert _proposals("applied")["count"] == 1
    assert _proposals("rejected")["count"] == 0
    assert p2 in [x["id"] for x in fb_service.list_proposals(status="pending")]

    # 直接调用业务层签名（其它模块会这样用）
    fb_service.add_feedback("r", "s", "a_direct", None, "down", reason="直接调用")
    assert fb_service.feedback_stats()["total"] == 1
    assert fb_service.detect_gaps()[0]["agent_id"] == "a_direct"
    fb_service.record_quality({"success": True, "latency_ms": 12})
    assert fb_service.quality_metrics()["sample_size"] == 1
    assert fb_service.adoption_metrics()["sample_size"] == 1
