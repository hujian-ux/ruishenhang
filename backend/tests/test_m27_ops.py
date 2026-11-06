"""M27 · 运维可观测 / 就绪度 / 审计留存 / 事件总线 测试。

自建 FastAPI app（不 import app.main），两个中间件 + 两个 router，完全对齐公共契约。
审计用 monkeypatch 屏蔽 get_logs，避免触碰 sqlite 造成竞争；autouse fixture 调 reset_state
保证用例之间状态隔离。
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.middleware import AuditTraceMiddleware, GatewayMiddleware
from app.core.security import create_dev_token
from app.models.caller import CallerContext
from app.routers import events as events_router, ops
from app.services import audit as audit_svc
from app.services import events as events_svc
from app.services import knowledge, observability

ROOT = Path(__file__).resolve().parents[1]  # backend

app = FastAPI()
app.add_middleware(AuditTraceMiddleware)
app.add_middleware(GatewayMiddleware)
app.include_router(ops.router, prefix="/api/v1")
app.include_router(events_router.router, prefix="/api/v1")
client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_state():
    observability.reset_state()
    events_svc.reset_state()
    yield
    observability.reset_state()
    events_svc.reset_state()


def _caller(user="u_a", dept="d_a", role="employee") -> CallerContext:
    return CallerContext(userId=user, deptId=dept, role=role, tenantId="t_demo")


def _headers(user="u_a", dept="d_a", role="employee") -> dict:
    # 裸 dev token 直接放 Authorization（网关 decode_token 验签）
    return {"Authorization": create_dev_token(user, dept, role)}


@pytest.fixture
def fake_audit(monkeypatch):
    """屏蔽 audit.get_logs，返回可控的 RBAC 数据，避免 sqlite 竞争。"""
    rows = [
        {"trace_id": "t1", "user_id": "u_a", "dept_id": "d_a", "role": "employee",
         "action": "retrieve", "target": "q_a"},
        {"trace_id": "t2", "user_id": "u_b", "dept_id": "d_a", "role": "employee",
         "action": "retrieve", "target": "q_b"},
        {"trace_id": "t3", "user_id": "u_c", "dept_id": "d_b", "role": "employee",
         "action": "download", "target": "q_c"},
    ]

    def fake_get_logs(caller, action=None, limit=100):
        if caller.role == "employee":
            out = [r for r in rows if r["user_id"] == caller.userId]
        elif caller.role == "dept_admin":
            out = [r for r in rows if r["dept_id"] == caller.deptId]
        else:
            out = list(rows)
        if action:
            out = [r for r in out if r["action"] == action]
        return out[:limit]

    monkeypatch.setattr(audit_svc, "get_logs", fake_get_logs)
    return rows


# ── 1. observe_request 累计与 p95 ─────────────────────────────────────────────

def test_observe_accumulate_and_p95():
    for ms in (10, 20, 30, 40, 50, 60, 70, 80, 90, 100):
        observability.observe_request("GET", "/api/v1/rag", 200, ms, trace_id="tr")
    observability.observe_request("GET", "/api/v1/rag", 500, 5)
    observability.observe_request("GET", "/api/v1/rag", 429, 6)
    text = observability.prometheus_text()
    assert "requests_total 12" in text
    assert "errors_total 2" in text          # 500 + 429 均 >= 400
    assert "model_429_total 1" in text
    assert "latency_p95_ms 100" in text      # 12 样本 nearest-rank p95 = 100
    h = observability.health()
    bucket = h["components"]["metrics"]["by_path"]["/api/v1/rag"]
    assert bucket["requests"] == 12 and bucket["errors"] == 2 and bucket["429"] == 1


# ── 2. prometheus 文本合法 ────────────────────────────────────────────────────

def test_prometheus_text_valid():
    observability.observe_request("GET", '/api/v1/a"b', 200, 12.5)  # 含双引号的 path，验转义
    text = observability.prometheus_text()
    assert text.startswith("# HELP ")
    for name in ("requests_total", "errors_total", "latency_p95_ms", "model_429_total"):
        assert f"# HELP {name} " in text
        assert f"# TYPE {name} " in text
    sample_re = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*(\{.*\})?\s+[-\d.eE+]+$")
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        assert sample_re.match(line), f"非法 Prometheus 行: {line}"
    assert '\\"' in text  # label 值中的双引号已转义，未破坏 exposition


# ── 3. 错误率告警阈值 ─────────────────────────────────────────────────────────

def test_error_rate_alert_threshold(monkeypatch):
    monkeypatch.setattr(settings, "OPS_ALERT_ERROR_RATE", 0.5)
    monkeypatch.setattr(settings, "OPS_ALERT_P95_MS", 10 ** 9)
    monkeypatch.setattr(settings, "OPS_ALERT_MODEL_429_SPIKE", 10 ** 9)
    for i in range(30):
        observability.observe_request("GET", "/x", 500 if i < 20 else 200, 10)
    items = observability.alerts()
    er = next(a for a in items if a["type"] == "error_rate")
    assert er["value"] > er["threshold"]
    assert er["value"] > 0.5
    # 错误率降到阈值以下，不再报该项
    observability.reset_state()
    for i in range(30):
        observability.observe_request("GET", "/x", 500 if i < 3 else 200, 10)
    assert not any(a["type"] == "error_rate" for a in observability.alerts())


# ── 4. 样本不足不误报 ─────────────────────────────────────────────────────────

def test_insufficient_samples_no_false_alarm():
    # 5 条样本：100% 错误 + 超高延迟，若无样本保护会同时误报两类告警
    for _ in range(5):
        observability.observe_request("GET", "/x", 500, 9999)
    assert observability.alerts() == []
    h = observability.health()
    assert h["insufficient_data"]["insufficient"] is True
    assert "样本不足" in h["insufficient_data"]["reason"]
    r = client.get("/api/v1/ops/alerts", headers=_headers())
    assert r.json()["code"] == 0
    assert r.json()["data"]["alerts"] == []
    assert r.json()["data"]["insufficient_data"]["insufficient"] is True


# ── 5. 429 / 延迟告警 ─────────────────────────────────────────────────────────

def test_429_and_latency_alerts(monkeypatch):
    monkeypatch.setattr(settings, "OPS_ALERT_ERROR_RATE", 2.0)   # 关掉错误率
    monkeypatch.setattr(settings, "OPS_ALERT_P95_MS", 50)
    monkeypatch.setattr(settings, "OPS_ALERT_MODEL_429_SPIKE", 3)
    for i in range(25):
        observability.observe_request("GET", "/m", 429 if i < 5 else 200, 100)
    types = {a["type"] for a in observability.alerts()}
    assert "model_429_spike" in types
    assert "latency_p95" in types
    assert "error_rate" not in types


# ── 6. readiness 不伪造 key ───────────────────────────────────────────────────

def test_readiness_does_not_forge_keys(monkeypatch):
    for k in ("LLM_API_KEY", "ZHIPU_API_KEY", "OPENAI_API_KEY", "BOCHA_API_KEY",
              "SERPER_API_KEY", "TAVILY_API_KEY", "BING_SEARCH_KEY",
              "OIDC_ENABLED", "OIDC_ISSUER", "OIDC_AUDIENCE", "SEED_DEMO_CORPUS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SEED_DEMO_CORPUS", "1")  # 演示语料开 = 未切真实语料
    rd = observability.readiness()
    for name in ("llm_key", "search_key", "oidc_idp", "real_corpus"):
        assert rd[name]["configured"] is False, name
        assert rd[name]["status"] == "not_configured", name
        assert rd[name]["verify_command"].startswith(".venv/Scripts/python.exe scripts/")
    # 配了 key 也只说「待验收」，绝不写 PASS
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    rd2 = observability.readiness()
    assert rd2["llm_key"]["configured"] is True
    assert rd2["llm_key"]["status"] == "configured_unverified"
    assert "PASS" not in json.dumps(rd2, ensure_ascii=False)


# ── 7. 免费网页源不算就绪 ─────────────────────────────────────────────────────

def test_free_web_source_not_ready(monkeypatch):
    for k in ("BOCHA_API_KEY", "SERPER_API_KEY", "TAVILY_API_KEY", "BING_SEARCH_KEY"):
        monkeypatch.delenv(k, raising=False)
    # 免费网页抓取通道全开，仍不算生产就绪
    monkeypatch.setenv("WEB_SEARCH_ENABLED", "1")
    monkeypatch.setenv("WEB_SEARCH_PROVIDER", "auto")
    monkeypatch.setenv("WEB_SEARCH_ALLOW_FETCH", "1")
    rd = observability.readiness()
    assert rd["search_key"]["configured"] is False
    assert "免费" in rd["search_key"]["note"]
    assert "不算生产就绪" in rd["search_key"]["note"]


# ── 8. audit_export RBAC 收敛 ─────────────────────────────────────────────────

def test_audit_export_rbac_scope(fake_audit):
    emp = observability.audit_export(_caller("u_a", "d_a", "employee"))
    assert emp["count"] == 1
    assert all(r["user_id"] == "u_a" for r in emp["logs"])
    assert emp["scope"] == "self" and emp["read_only"] is True
    assert emp["retention_days"] == settings.AUDIT_RETENTION_DAYS
    assert "employee" in emp["rbac_note"] and "platform_admin" in emp["rbac_note"]

    dept = observability.audit_export(_caller("root", "d_a", "dept_admin"))
    assert dept["scope"] == "department" and dept["count"] == 2
    assert all(r["dept_id"] == "d_a" for r in dept["logs"])

    admin = observability.audit_export(_caller("root", "d_z", "platform_admin"))
    assert admin["scope"] == "all" and admin["count"] == 3


# ── 9. delete_my_data 返回计数且不谎称全删 ───────────────────────────────────

def test_delete_my_data_counts_and_no_fake_full_delete(monkeypatch):
    parents = [
        {"doc_id": "d1", "owner": "u_a"},
        {"doc_id": "d2", "owner": "u_a"},
        {"doc_id": "d3", "owner": "u_b"},
    ]
    monkeypatch.setattr(knowledge, "get_parents", lambda: parents)
    seen = {}

    def fake_remove(ids):
        seen["ids"] = list(ids)
        return len(ids)

    monkeypatch.setattr(knowledge, "remove_documents", fake_remove)

    a, b = _caller("u_a"), _caller("u_b")
    observability.add_feedback(a, "mine")
    observability.add_feedback(b, "other")
    events_svc.subscribe("https://example.com/hook", ["x"], a)
    events_svc.publish("x", {"n": 1}, a)
    events_svc.publish("x", {"n": 2}, b)

    d = observability.delete_my_data(a)
    assert d["documents"]["removed"] == 2
    assert set(seen["ids"]) == {"d1", "d2"}       # 只删 owner==u_a，没碰 d3
    assert d["feedback"]["removed"] == 1
    assert d["events"]["removed"] == 1
    assert d["subscriptions"]["removed"] == 1
    assert d["subject"] == "u_a"
    mr = d["manual_review"]
    assert mr["items"] and "未删除" in mr["note"]   # 明确列出其它 store 待人工确认
    assert "会话" in "".join(mr["items"]) and "产出物" in "".join(mr["items"])
    # 别人的数据仍在
    assert len(events_svc.list_events()) == 1
    assert events_svc.list_events()[0]["publisher"] == "u_b"


# ── 10. 订阅拒绝私网 URL ──────────────────────────────────────────────────────

def test_subscribe_rejects_private_url():
    caller = _caller()
    bad = [
        "http://127.0.0.1/hook", "http://10.0.0.5/hook", "http://192.168.1.1/hook",
        "http://169.254.169.254/latest/meta-data", "http://localhost/hook",
        "http://[::1]/hook", "http://0.0.0.0/hook", "ftp://example.com/hook", "https://",
    ]
    for url in bad:
        with pytest.raises(ValueError):
            events_svc.subscribe(url, ["x"], caller)
    # HTTP 层同样拒绝，且不伪装成成功
    r = client.post("/api/v1/events/subscribe",
                    json={"url": "http://127.0.0.1/hook", "event_types": ["x"]},
                    headers=_headers())
    assert r.json()["code"] != 0
    assert "私网" in r.json()["message"] or "SSRF" in r.json()["message"]


# ── 11. token 不明文回显（含 HMAC + 可逆混淆）────────────────────────────────

def test_subscribe_token_not_plaintext_and_obfuscation():
    caller = _caller()
    view = events_svc.subscribe("https://example.com/hook", ["order.created"], caller)
    raw = json.dumps(view, ensure_ascii=False)
    assert view["token_prefix"].endswith("...")
    assert not re.search(r"whsec_[0-9a-f]{48}", raw)   # 无完整 token
    assert "token_enc" not in view and "token_hmac" not in view

    rows = events_svc.list_subscriptions(caller)
    joined = json.dumps(rows, ensure_ascii=False)
    assert not re.search(r"whsec_[0-9a-f]{48}", joined)

    # 库里存的是可逆混淆密文 + HMAC 校验值，不是明文
    stored = events_svc.snapshot()["subscriptions"][0]
    token = events_svc._deobfuscate(stored["token_enc"])
    assert token.startswith("whsec_") and len(token) == len("whsec_") + 48
    assert stored["token_enc"] != token
    assert events_svc._token_hmac(token) == stored["token_hmac"]
    assert events_svc._obfuscate(token) == stored["token_enc"]
    assert "KMS" in view["protection"]


# ── 12. publish 默认不发网络 ──────────────────────────────────────────────────

def test_publish_default_no_network(monkeypatch):
    import httpx
    calls = []
    monkeypatch.setattr(httpx, "post", lambda *a, **k: calls.append((a, k)))
    events_svc.subscribe("https://example.com/hook", ["x"], _caller())
    r = events_svc.publish("x", {"k": 1}, _caller())  # deliver 默认 False
    assert r["deliver"] is False and r["matched_subscriptions"] == 1
    assert r["delivered"] == 0 and r["failed"] == 0
    r2 = client.post("/api/v1/events/publish",
                     json={"event_type": "x", "payload": {"k": 2}}, headers=_headers())
    assert r2.json()["code"] == 0 and r2.json()["data"]["deliver"] is False
    assert calls == [], "deliver=False 绝不发网络请求"


# ── 13. 事件台账可查 ──────────────────────────────────────────────────────────

def test_event_ledger_queryable():
    caller = _caller()
    events_svc.subscribe("https://example.com/hook", ["a", "b"], caller)
    events_svc.publish("a", {"n": 1}, caller)
    events_svc.publish("b", {"n": 2}, caller)
    rows = events_svc.list_events()
    assert len(rows) == 2
    assert {r["event_type"] for r in rows} == {"a", "b"}
    assert rows[0]["event_type"] == "b"            # 最新在前
    assert all(r["status"] == "recorded" for r in rows)
    r = client.get("/api/v1/events", headers=_headers())
    assert r.json()["code"] == 0 and r.json()["data"]["count"] == 2


# ── 14. HTTP 端点：text/plain、统一 code:0、需登录 ────────────────────────────

def test_http_endpoints_login_and_content_type(fake_audit):
    assert client.get("/api/v1/ops/health").status_code == 401
    assert client.get("/api/v1/events").status_code == 401
    h = _headers()
    m = client.get("/api/v1/ops/metrics", headers=h)
    assert m.status_code == 200
    assert m.headers["content-type"].startswith("text/plain")
    assert "# HELP requests_total" in m.text
    for p in ("/api/v1/ops/health", "/api/v1/ops/alerts", "/api/v1/ops/readiness",
              "/api/v1/ops/audit-export", "/api/v1/events/subscriptions"):
        r = client.get(p, headers=h)
        assert r.status_code == 200 and r.json()["code"] == 0, p
    s = client.post("/api/v1/events/subscribe",
                    json={"url": "https://hooks.example.com/x", "event_types": ["x"]}, headers=h)
    assert s.json()["code"] == 0
    assert "token_enc" not in json.dumps(s.json())
    p = client.post("/api/v1/events/publish",
                    json={"event_type": "x", "payload": {"n": 1}}, headers=h)
    assert p.json()["code"] == 0 and p.json()["data"]["deliver"] is False
    assert client.get("/api/v1/events", headers=h).json()["data"]["count"] == 1


# ── 15. audit-export / me/data 的 HTTP 结果按 caller 收敛 ─────────────────────

def test_http_audit_export_and_delete_scoped(monkeypatch, fake_audit):
    emp = client.get("/api/v1/ops/audit-export", headers=_headers("u_a", "d_a", "employee"))
    admin = client.get("/api/v1/ops/audit-export", headers=_headers("root", "d_z", "platform_admin"))
    assert emp.json()["data"]["scope"] == "self" and emp.json()["data"]["count"] == 1
    assert admin.json()["data"]["scope"] == "all" and admin.json()["data"]["count"] == 3

    monkeypatch.setattr(knowledge, "get_parents",
                        lambda: [{"doc_id": "d1", "owner": "u_a"},
                                 {"doc_id": "d2", "owner": "u_b"}])
    seen = {}

    def fake_remove(ids):
        seen["ids"] = list(ids)
        return len(ids)

    monkeypatch.setattr(knowledge, "remove_documents", fake_remove)
    r = client.delete("/api/v1/ops/me/data", headers=_headers("u_a", "d_a", "employee"))
    assert r.json()["code"] == 0
    assert seen["ids"] == ["d1"]                  # 只收敛到本人
    assert r.json()["data"]["subject"] == "u_a"
    assert "manual_review" in r.json()["data"]


# ── 16. 备份 / 恢复演练脚本 ───────────────────────────────────────────────────

def test_backup_restore_script(tmp_path):
    script = str(ROOT / "scripts" / "backup_restore.py")
    out = tmp_path / "m27.json"
    env = {**os.environ, "PYTHONPATH": str(ROOT)}

    def run(*args):
        return subprocess.run([sys.executable, script, *args],
                              capture_output=True, text=True, env=env)

    b = run("--backup", str(out))
    assert b.returncode == 0, b.stdout + b.stderr
    assert out.is_file()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema"] == "m27-backup/v1"
    assert "checksum" in data and "config_summary" in data and "stores" in data
    assert "observability" in data["stores"] and "events" in data["stores"]

    v = run("--verify", str(out))
    assert v.returncode == 0, v.stdout + v.stderr
    assert "重算一致" in v.stdout and "未写生产" in v.stdout

    # 没有 --confirm 明确拒绝
    r = run("--restore", str(out))
    assert r.returncode != 0
    assert "拒绝" in r.stdout and "--confirm" in r.stdout

    # 篡改后 sha256 校验失败
    data["counts"]["events"] = 999
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    v2 = run("--verify", str(out))
    assert v2.returncode != 0
    assert "sha256" in v2.stdout
