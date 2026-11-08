"""M27 · 自助建部门 / L2 员工零代码建智能体 / L3 节点式工作流编排（蓝图 19/21 章）。

测试自建 FastAPI app（不 import app.main），挂 GatewayMiddleware + AuditTraceMiddleware。
红线：默认路径绝不污染 agent_store._AGENTS（catalog.total==115 / PUBLIC_SKILLS==28）；
publish 落库后由 autouse teardown 精确删除该 agent_id 并恢复 _AGENTS 快照。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.middleware import AuditTraceMiddleware, GatewayMiddleware
from app.core.security import create_dev_token
from app.routers import builder
from app.services import (agent_builder, agent_store, dept_onboarding,
                          knowledge, workflows)

app = FastAPI()
app.add_middleware(AuditTraceMiddleware)
app.add_middleware(GatewayMiddleware)
app.include_router(builder.router, prefix="/api/v1")

client = TestClient(app)
ADMIN = {"Authorization": create_dev_token("p_admin", "d_platform", "platform_admin")}
EMP = {"Authorization": create_dev_token("u_zhang", "d_sales", "employee")}

# publish 用例登记落的 agent_id，供 autouse teardown 精确删除。
_PUBLISHED_IDS: list = []
# 跨用例验证 teardown 是否清理干净。
_LAST_PUBLISHED = [""]
# 动态基线：全量跑时别的测试文件会往 _AGENTS 写注册过的包，
# 用「模块导入时的长度」做断言会误判；改为每个用例开始时取快照长度。
_DYNAMIC_BASELINE = [len(agent_store._AGENTS)]


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """每用例前：屏蔽审计 + 重置三个模块状态 + 快照 _AGENTS；
    teardown：精确删除 publish 落的 id + 恢复 _AGENTS 快照 + 再次 reset。"""
    from app.services import audit
    monkeypatch.setattr(audit, "record", lambda *a, **k: "trace-test")
    snap = dict(agent_store._AGENTS)
    _DYNAMIC_BASELINE[0] = len(agent_store._AGENTS)
    dept_onboarding.reset_state()
    agent_builder.reset_state()
    workflows.reset_state()
    _PUBLISHED_IDS.clear()
    yield
    for aid in _PUBLISHED_IDS:
        agent_store._AGENTS.pop(aid, None)      # 精确删除该 agent_id
    agent_store._AGENTS.clear()
    agent_store._AGENTS.update(snap)           # 恢复 _AGENTS 快照
    dept_onboarding.reset_state()
    agent_builder.reset_state()
    workflows.reset_state()
    _PUBLISHED_IDS.clear()


def _new_dept(template="business", name="华东销售部"):
    return client.post("/api/v1/builder/departments", json={
        "name": name, "parent": "dept_sales", "domain": "sales",
        "description": "华东区销售", "template": template,
    }, headers=EMP).json()["data"]


def _new_draft(name="销售问答助手"):
    return client.post("/api/v1/builder/agents", json={
        "name": name, "goal": "回答售前常见问题并附出处",
        "inputs_contract": "客户问题（自然语言）",
        "outputs_contract": "带出处的简要回答",
        "steps": ["检索授权片段", "按硬规则作答", "标注出处与缺失项"],
        "scope": "department",
    }, headers=EMP).json()["data"]


def _new_workflow(nodes, name="测试流"):
    return client.post("/api/v1/builder/workflows",
                       json={"name": name, "nodes": nodes},
                       headers=EMP).json()["data"]


# ── 部门模板 & 端点 ────────────────────────────────────────────────────────

def test_department_templates_six_categories():
    r = client.get("/api/v1/builder/departments/templates", headers=EMP)
    assert r.status_code == 200
    d = r.json()["data"]
    assert len(d) == 6
    assert {v["name"] for v in d.values()} == {"综合管理", "业务", "产研", "制造", "特殊", "空白"}
    for v in d.values():
        for key in ("name", "example_agents", "default_scope", "visible_boundary"):
            assert key in v, key
        assert isinstance(v["example_agents"], list)
        assert v["default_scope"]
        assert isinstance(v["visible_boundary"], dict)
    # 直接调用 service 函数，确认签名可用
    assert len(dept_onboarding.templates()) == 6


def test_builder_templates_endpoint_returns_both():
    r = client.get("/api/v1/builder/templates", headers=ADMIN)
    assert r.status_code == 200
    d = r.json()["data"]
    assert len(d["department_templates"]) == 6
    assert isinstance(d["agent_templates"], list) and d["agent_templates"]
    assert len(agent_builder.builder_templates()) == len(d["agent_templates"])


def test_all_endpoints_require_login():
    assert client.get("/api/v1/builder/templates").status_code == 401
    assert client.post("/api/v1/builder/agents", json={"name": "x"}).status_code == 401
    assert client.get("/api/v1/builder/departments").status_code == 401
    assert client.get("/api/v1/builder/workflows").status_code == 401


# ── 部门创建 / 审批 / 开关 ──────────────────────────────────────────────────

def test_create_department_auto_initializes():
    r = client.post("/api/v1/builder/departments", json={
        "name": "华东销售部", "parent": "dept_sales", "domain": "sales",
        "description": "华东区", "template": "business",
    }, headers=EMP)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["status"] == "pending"
    assert d["scope"] == f"department:{d['id']}"
    assert d["rbac"] == ["employee", "dept_admin", "platform_admin"]
    assert d["inherits_from"] == "dept_sales"
    assert d["template"] == "business"
    kb = d["knowledge_boundary"]
    assert kb["scope"] == d["scope"]
    assert kb["inherits_from"] == "dept_sales"
    assert kb["default_scope"] == "department"
    assert isinstance(kb["visible_boundary"], dict)


def test_non_admin_approve_403():
    did = _new_dept()["id"]
    r = client.post(f"/api/v1/builder/departments/{did}/approve", headers=EMP)
    assert r.status_code == 403
    # 普通 employee 不能审批，部门仍是 pending
    rows = client.get("/api/v1/builder/departments?status=pending",
                      headers=ADMIN).json()["data"]["departments"]
    assert any(x["id"] == did for x in rows)


def test_admin_approve_active():
    did = _new_dept()["id"]
    r = client.post(f"/api/v1/builder/departments/{did}/approve", headers=ADMIN)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["status"] == "active"
    assert d["approved_by"] == "p_admin"
    assert d["approved_at"]
    act = client.get("/api/v1/builder/departments?status=active",
                     headers=ADMIN).json()["data"]["departments"]
    assert any(x["id"] == did for x in act)
    pend = client.get("/api/v1/builder/departments?status=pending",
                      headers=ADMIN).json()["data"]["departments"]
    assert not any(x["id"] == did for x in pend)


def test_dept_self_service_off_rejects(monkeypatch):
    monkeypatch.setattr(settings, "DEPT_SELF_SERVICE", False)
    r = client.post("/api/v1/builder/departments", json={
        "name": "不该建成", "parent": "", "domain": "sales",
        "description": "", "template": "blank",
    }, headers=EMP)
    assert r.status_code == 403
    assert dept_onboarding.list_departments() == []


# ── L2 草稿 / system_prompt / publish ───────────────────────────────────────

def test_l2_draft_does_not_touch_agent_store():
    before = len(agent_store._AGENTS)
    r = client.post("/api/v1/builder/agents", json={
        "name": "销售问答", "goal": "回答售前问题",
        "inputs_contract": "客户问题", "outputs_contract": "带出处回答",
        "steps": ["检索", "作答"], "scope": "department",
    }, headers=EMP)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["status"] == "draft"
    assert d["system_prompt"]                       # 带 system_prompt 预览
    assert len(agent_store._AGENTS) == before        # _AGENTS 数量不变
    assert agent_store.get(d["agent_id"]) is None     # 没落 agent_store
    lst = client.get("/api/v1/builder/agents", headers=EMP).json()["data"]["agents"]
    assert any(x["agent_id"] == d["agent_id"] for x in lst)


def test_system_prompt_contains_contracts_and_hard_rules():
    d = _new_draft()
    p = d["system_prompt"]
    for kw in ("什么时候用", "要给什么", "会拿到什么", "执行步骤", "平台硬规则",
               "事实不许编造", "回答售前常见问题并附出处"):
        assert kw in p, kw
    # 硬规则取自 public_skills 单一来源（只读，不修改该模块）
    from app.services.public_skills import HARD_RULES
    assert HARD_RULES in p


def test_publish_is_admin_only():
    d = _new_draft()
    r = client.post(f"/api/v1/builder/agents/{d['agent_id']}/publish", headers=EMP)
    assert r.status_code == 403
    assert agent_store.get(d["agent_id"]) is None


def test_publish_pending_review():
    before = len(agent_store._AGENTS)
    d = _new_draft()
    aid = d["agent_id"]
    r = client.post(f"/api/v1/builder/agents/{aid}/publish", headers=ADMIN)
    assert r.status_code == 200
    out = r.json()["data"]
    assert out["status"] == "pending_review"
    assert out["knowledge_scope"] == "department"
    assert "审批" in out["notice"] and out["need_approval"] is True
    saved = agent_store.get(aid)
    assert saved is not None and saved["status"] == "pending_review"
    assert len(agent_store._AGENTS) == before + 1
    # 登记给 autouse teardown 精确删除，并给下一条用例验证清理
    _PUBLISHED_IDS.append(aid)
    _LAST_PUBLISHED[0] = aid


def test_publish_teardown_left_no_residue():
    """上一条 publish 用例的 teardown 必须已精确删除 + 恢复快照。"""
    aid = _LAST_PUBLISHED[0]
    assert aid, "上一条 publish 用例应记录 agent_id"
    assert aid not in agent_store._AGENTS
    assert agent_store.get(aid) is None
    assert not [a for a in agent_store._AGENTS.values()
                if a.get("built_by") == "m27_agent_builder"]
    assert len(agent_store._AGENTS) == _DYNAMIC_BASELINE[0]


# ── L3 工作流 ──────────────────────────────────────────────────────────────

def test_workflow_invalid_node_type_and_empty():
    bad = client.post("/api/v1/builder/workflows", json={
        "name": "bad", "nodes": [{"type": "magic", "name": "x", "config": {}}],
    }, headers=EMP)
    assert bad.status_code == 400
    empty = client.post("/api/v1/builder/workflows",
                        json={"name": "empty", "nodes": []}, headers=EMP)
    assert empty.status_code == 400
    assert workflows.list_workflows() == []


def test_workflow_node_count_cap(monkeypatch):
    monkeypatch.setattr(settings, "WORKFLOW_MAX_NODES", 2)
    nodes = [{"type": "llm", "name": f"n{i}", "config": {}} for i in range(3)]
    over = client.post("/api/v1/builder/workflows",
                       json={"name": "over", "nodes": nodes}, headers=EMP)
    assert over.status_code == 400
    ok = client.post("/api/v1/builder/workflows",
                     json={"name": "ok", "nodes": nodes[:2]}, headers=EMP)
    assert ok.status_code == 200
    assert ok.json()["data"]["status"] == "draft"
    assert len(workflows.list_workflows()) == 1


def test_workflow_rag_passes_caller(monkeypatch):
    calls = []

    def fake_retrieve(query, caller, top_k=6):
        calls.append({"query": query, "caller": caller, "top_k": top_k})
        return ([{"snippet": "片段A", "title": "文档1"}], 2)

    monkeypatch.setattr(knowledge, "retrieve", fake_retrieve)
    wf = _new_workflow([
        {"type": "rag", "name": "查知识", "config": {"query": "报销限额"}},
    ])
    d = client.post(f"/api/v1/builder/workflows/{wf['id']}/run",
                    json={"input": "备用问题"}, headers=EMP).json()["data"]
    assert len(calls) == 1
    assert calls[0]["top_k"] == 3                       # 固定 top_k=3
    assert calls[0]["caller"].userId == "u_zhang"       # caller 确实带过去了
    assert calls[0]["query"] == "报销限额"
    step = d["steps"][0]
    assert step["type"] == "rag" and step["ok"] is True
    assert step["output"]["snippets"] == ["片段A"]
    assert step["output"]["degraded"] is False
    assert "rag:查知识" in d["context_keys"]
    assert d["dry_run"] is True


def test_workflow_rag_failure_degrades(monkeypatch):
    def boom(query, caller, top_k=6):
        raise RuntimeError("kb down")

    monkeypatch.setattr(knowledge, "retrieve", boom)
    wf = _new_workflow([{"type": "rag", "name": "查知识", "config": {}}])
    d = client.post(f"/api/v1/builder/workflows/{wf['id']}/run",
                    json={"input": "任意"}, headers=EMP).json()["data"]
    step = d["steps"][0]
    assert step["ok"] is False
    assert step["output"]["degraded"] is True
    assert step["output"]["snippets"] == []


def test_workflow_approval_stops_and_does_not_skip():
    wf = _new_workflow([
        {"type": "llm", "name": "整理", "config": {}},
        {"type": "approval", "name": "人工审", "config": {}},
        {"type": "llm", "name": "生成", "config": {}},
    ])
    d = client.post(f"/api/v1/builder/workflows/{wf['id']}/run",
                    json={"input": "资料"}, headers=EMP).json()["data"]
    assert d["status"] == "pending"
    assert [s["type"] for s in d["steps"]] == ["llm", "approval"]  # 第二个 llm 未执行
    assert d["steps"][-1]["output"]["status"] == "pending"
    assert "llm:生成" not in d["context_keys"]


def test_workflow_llm_dry_run():
    wf = _new_workflow([{"type": "llm", "name": "起草", "config": {}}])
    d = client.post(f"/api/v1/builder/workflows/{wf['id']}/run",
                    json={"input": "写个摘要"}, headers=EMP).json()["data"]
    assert d["dry_run"] is True and d["status"] == "completed"
    out = d["steps"][0]["output"]
    assert out["mode"] == "dry_run"
    assert "未调用模型" in out["message"]
    assert "input" in out["context_summary"]


def test_workflow_tool_unavailable_does_not_fake_execution():
    wf = _new_workflow([
        {"type": "tool", "name": "查库存",
         "config": {"tool_name": "erp.query", "args": {"sku": "A1"}}},
    ])
    d = client.post(f"/api/v1/builder/workflows/{wf['id']}/run",
                    json={"input": ""}, headers=EMP).json()["data"]
    out = d["steps"][0]["output"]
    # guardrails 模块在集成后已存在：未知工具会被白名单拒绝（denied），
    # 模块不存在时则是 unavailable。两种都必须「不假装执行」。
    assert out["ok"] is False
    assert out.get("unavailable") is True or out.get("denied") is True
    assert "erp.query" in out.get("tool", "")
    assert "result" not in out or out.get("denied") is True


def test_reset_state_isolation():
    _new_dept()
    _new_draft()
    _new_workflow([{"type": "llm", "name": "n", "config": {}}])
    assert len(dept_onboarding.list_departments()) == 1
    assert len(agent_builder.list_drafts()) == 1
    assert len(workflows.list_workflows()) == 1
    dept_onboarding.reset_state()
    agent_builder.reset_state()
    workflows.reset_state()
    assert dept_onboarding.list_departments() == []
    assert agent_builder.list_drafts() == []
    assert workflows.list_workflows() == []
    # 默认路径从未写 agent_store
    assert len(agent_store._AGENTS) == _DYNAMIC_BASELINE[0]


# ── L3 工作流图结构（坐标 + 边 + DAG）─────────────────────────────────────

def _post_wf(nodes, edges=None, name="G"):
    body = {"name": name, "nodes": nodes}
    if edges is not None:
        body["edges"] = edges
    return client.post("/api/v1/builder/workflows", json=body, headers=EMP).json()["data"]


def test_workflow_graph_roundtrip_positions_and_edges():
    nodes = [
        {"id": "a", "type": "rag", "name": "A", "config": {"query": "q"}, "position": {"x": 10, "y": 20}},
        {"id": "b", "type": "llm", "name": "B", "config": {}, "position": {"x": 10, "y": 160}},
    ]
    wf = _post_wf(nodes, edges=[{"from": "a", "to": "b"}])
    got = client.get(f"/api/v1/builder/workflows/{wf['id']}", headers=EMP).json()["data"]
    assert got["name"] == "G"
    assert got["edges"] == [{"from": "a", "to": "b"}]
    assert got["nodes"][0]["position"] == {"x": 10, "y": 20}
    # 更新：改名称 + 加节点 + 加边，验证原地更新（不再新建副本）
    nodes2 = nodes + [{"id": "c", "type": "tool", "name": "C", "config": {}, "position": {"x": 300, "y": 20}}]
    upd = client.put(f"/api/v1/builder/workflows/{wf['id']}", json={
        "name": "G2", "nodes": nodes2,
        "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}],
    }, headers=EMP).json()["data"]
    assert upd["name"] == "G2"
    assert {e["from"] for e in upd["edges"]} == {"a", "b"}
    assert len(upd["nodes"]) == 3
    # 列表里仍只有 1 条（更新而非新建）
    assert len(workflows.list_workflows()) == 1


def test_workflow_update_rejects_cycle():
    wf = _post_wf([{"id": "a", "type": "llm", "name": "A", "config": {}},
                   {"id": "b", "type": "llm", "name": "B", "config": {}}])
    r = client.put(f"/api/v1/builder/workflows/{wf['id']}", json={
        "name": "cyc",
        "nodes": [{"id": "a", "type": "llm", "name": "A", "config": {}},
                  {"id": "b", "type": "llm", "name": "B", "config": {}}],
        "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}],
    }, headers=EMP)
    assert r.status_code == 400


def test_workflow_runs_in_edge_topo_order():
    nodes = [{"id": "a", "type": "llm", "name": "A", "config": {}},
             {"id": "b", "type": "rag", "name": "B", "config": {}},
             {"id": "c", "type": "llm", "name": "C", "config": {}}]
    # 数组顺序 A,B,C；但边定义 c→a→b，拓扑序应覆盖数组序
    wf = _post_wf(nodes, edges=[{"from": "c", "to": "a"}, {"from": "a", "to": "b"}])
    d = client.post(f"/api/v1/builder/workflows/{wf['id']}/run",
                    json={"input": "x"}, headers=EMP).json()["data"]
    assert [s["name"] for s in d["steps"]] == ["C", "A", "B"]


def test_workflow_get_and_update_missing_404():
    assert client.get("/api/v1/builder/workflows/wf_not_exist", headers=EMP).status_code == 404
    r = client.put("/api/v1/builder/workflows/wf_not_exist", json={
        "name": "x", "nodes": [{"id": "a", "type": "llm", "name": "A", "config": {}}], "edges": [],
    }, headers=EMP)
    assert r.status_code == 404


def test_workflow_auto_assigns_id_and_sequential_edges():
    # 不传 id / edges 时，后端补 id 并默认生成顺序边（保持线性语义）
    wf = _new_workflow([{"type": "rag", "name": "R", "config": {}},
                        {"type": "llm", "name": "L", "config": {}}])
    got = client.get(f"/api/v1/builder/workflows/{wf['id']}", headers=EMP).json()["data"]
    assert len(got["nodes"]) == 2 and all(n.get("id") for n in got["nodes"])
    assert len(got["edges"]) == 1 and got["edges"][0]["from"] == got["nodes"][0]["id"]
