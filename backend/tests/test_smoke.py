"""后端 smoke 测试：验证骨架可导入、契约路由可达、第18章过滤点逻辑正确。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # type: ignore

from app.core.security import create_dev_token
from app.core.filter import visible, single_filter
from app.main import app
from app.models.caller import CallerContext

client = TestClient(app)
DEV_TOKEN = create_dev_token(user_id="u_1024", dept_id="d_sales", role="employee")
AUTH = {"Authorization": DEV_TOKEN}
GUEST_TOKEN = create_dev_token(user_id="u_9999", dept_id="d_sales", role="guest")
GUEST_AUTH = {"Authorization": GUEST_TOKEN}


def test_health():
    assert client.get("/healthz").json()["status"] == "ok"


def test_models_list():
    r = client.get("/api/v1/models", headers=AUTH)
    assert r.status_code == 200
    assert len(r.json()["data"]["models"]) >= 2


def test_rag_filter_demonstrates_scope():
    # 张三(销售部) 查「活动方案」：应可见本部门+global，被过滤掉设计部/其他租户
    r = client.post("/api/v1/rag/retrieve",
                    json={"query": "五一活动方案", "kb_scope": "all", "top_k": 6, "tenant_id": "t_demo"},
                    headers=AUTH)
    body = r.json()["data"]
    scopes = {h["scope"] for h in body["hits"]}
    assert "department" in scopes or "global" in scopes
    assert body["filtered_out"] >= 1  # 设计部(doc_7002)/友商租户(doc_8008)被过滤


def test_kb_forbids_cross_dept_write():
    r = client.post("/api/v1/kb/documents",
                    json={"title": "x", "content": "y", "scope": "department", "dept_id": "d_design"},
                    headers=AUTH)
    assert r.status_code == 403  # 越权写被拦截


def test_agents_requires_selection():
    r = client.post("/api/v1/agents",
                    json={"name": "测试助手", "adapter_type": "", "description": "x", "knowledge_scope": "department"},
                    headers=AUTH)
    assert r.status_code == 400  # 缺选型不可发布（第35章红线）


def test_filter_unit():
    caller = CallerContext(userId="u_1024", deptId="d_sales", role="employee", tenantId="t_demo")
    items = [
        {"meta": {"tenant_id": "t_demo", "scope": "department", "dept": "d_sales", "owner": "u_1024"}},
        {"meta": {"tenant_id": "t_demo", "scope": "department", "dept": "d_design", "owner": "u_3000"}},
        {"meta": {"tenant_id": "t_other", "scope": "global", "dept": "x", "owner": "x"}},
    ]
    vis, filtered = single_filter(items, caller)
    assert len(vis) == 1 and filtered == 2
    assert visible({"tenant_id": "t_demo", "scope": "global", "dept": "x", "owner": "x"}, caller)


# ───────────────────────────────────────────────────────────
# M1（第35章选型服务化 + 注册校验 + selection 端点）
# ───────────────────────────────────────────────────────────
def test_selection_recommend_knowledge():
    r = client.post("/api/v1/selection/recommend",
                    json={"input_type": "text", "deterministic": False, "need": "knowledge"},
                    headers=AUTH)
    assert r.status_code == 200
    assert r.json()["data"]["adapter_type"] == "llm_retrieval"


def test_selection_recommend_creative():
    r = client.post("/api/v1/selection/recommend",
                    json={"input_type": "text", "deterministic": False, "need": "creative"},
                    headers=AUTH)
    assert r.json()["data"]["adapter_type"] == "llm"


def test_selection_recommend_rule():
    r = client.post("/api/v1/selection/recommend",
                    json={"input_type": "text", "deterministic": True, "need": "explain"},
                    headers=AUTH)
    assert r.json()["data"]["adapter_type"] == "rule"


def test_selection_recommend_ml_serving():
    r = client.post("/api/v1/selection/recommend",
                    json={"input_type": "table", "deterministic": True, "need": "accuracy"},
                    headers=AUTH)
    assert r.json()["data"]["adapter_type"] == "ml_serving"


def test_selection_forbidden_role():
    r = client.post("/api/v1/selection/recommend",
                    json={"input_type": "text", "deterministic": False, "need": "knowledge"},
                    headers=GUEST_AUTH)
    assert r.status_code == 403  # 非 employee/dept_admin/platform_admin 禁止选型


def test_agents_register_success():
    r = client.post("/api/v1/agents",
                    json={"name": "RAG客服助手", "adapter_type": "llm_retrieval",
                          "description": "企业知识问答", "knowledge_scope": "department",
                          "model_id": "m_gpt"},
                    headers=AUTH)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["status"] == "pending_review"
    assert d["adapter_type"] == "llm_retrieval"
    assert d["selection"]["recommended"] == "llm_retrieval"
    assert d["registered_by"] == "u_1024"
    assert "landing_chapters" in d["selection"]


def test_agents_reject_invalid_adapter():
    r = client.post("/api/v1/agents",
                    json={"name": "x", "adapter_type": "quantum", "description": "x", "knowledge_scope": "department"},
                    headers=AUTH)
    assert r.status_code == 400


def test_agents_reject_invalid_scope():
    r = client.post("/api/v1/agents",
                    json={"name": "x", "adapter_type": "llm", "description": "x", "knowledge_scope": "galaxy"},
                    headers=AUTH)
    assert r.status_code == 400


# ───────────────────────────────────────────────────────────
# M2（第36章 RAG：混合检索 + 父子切分 + 重排 + 评测集≥200）
# ───────────────────────────────────────────────────────────
def test_rag_retrieve_returns_hits():
    from app.services import knowledge as kb
    caller = CallerContext(userId="u_1024", deptId="d_sales", role="employee", tenantId="t_demo")
    hits, filtered = kb.retrieve("五一活动方案 满减", caller, top_k=6)
    assert len(hits) >= 1
    # 父子聚合：命中 doc_id 都是 parent 级（不以 _c 结尾）
    assert all(not h["doc_id"].endswith("_c") for h in hits)
    assert filtered >= 0


def test_rag_scope_filter_via_endpoint():
    # 张三(销售部) 查「设计部品牌规范」：应被过滤（filtered_out>=1，hits 不含 design 部）
    r = client.post("/api/v1/rag/retrieve",
                    json={"query": "设计部品牌规范", "kb_scope": "all", "top_k": 10, "tenant_id": "t_demo"},
                    headers=AUTH)
    body = r.json()["data"]
    assert body["filtered_out"] >= 1
    assert all(not (h["scope"] == "department" and h.get("dept") != "d_sales") for h in body["hits"])


def test_golden_size_ge_200():
    from app.services import eval as eval_svc
    golden = eval_svc.load_golden() or eval_svc.build_golden_entries()
    assert len(golden) >= 200


def test_recall_gate_passes():
    from app.services import eval as eval_svc
    golden = eval_svc.load_golden() or eval_svc.build_golden_entries()
    score = eval_svc.compute_recall_at_k(golden, 100)
    assert score >= eval_svc.RECALL_GATE_THRESHOLD


def test_rerank_returns_sorted_candidates():
    from app.services import knowledge as kb
    caller = CallerContext(userId="u_1024", deptId="d_sales", role="employee", tenantId="t_demo")
    hits, _ = kb.retrieve("报销 流程", caller, top_k=10)
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True)


def test_filter_accuracy_passes():
    from app.services import eval as eval_svc
    assert eval_svc.compute_filter_accuracy() >= 1.0


def test_eval_objective_gate():
    from app.services import eval as eval_svc
    res = eval_svc.run_objective_gate()
    assert res["status"] == "passed"


# ───────────────────────────────────────────────────────────
# M3（第37章评测：客观指标 + CI 门禁 + 演化阻断）
# ───────────────────────────────────────────────────────────
def test_recall_gate_blocks_on_bad_golden(tmp_path, monkeypatch):
    import json
    import pytest

    import app.services.eval as eval_svc
    bad = tmp_path / "bad_golden.jsonl"
    bad.write_text(
        json.dumps({"query": "随便问", "expected_doc_id": "doc_not_exist",
                    "viewer": {"userId": "u_1024", "deptId": "d_sales",
                               "role": "employee", "tenantId": "t_demo"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(eval_svc, "GOLDEN_PATH", bad)
    # 门禁：不达标 → 非零退出（CI 阻断，第20章演化联动）
    with pytest.raises(SystemExit):
        eval_svc.run_recall_gate(k=1)


def test_objective_gate_blocks_on_low_recall(monkeypatch):
    import app.services.eval as eval_svc
    monkeypatch.setattr(eval_svc, "compute_recall_at_k", lambda golden, k: 0.5)
    monkeypatch.setattr(eval_svc, "compute_filter_accuracy", lambda: 1.0)
    res = eval_svc.run_objective_gate()
    # recall@100 < 0.95 → status 非 passed → CI 阻断
    assert res["status"] == "review"
