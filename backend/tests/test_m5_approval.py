"""M5 审批流测试：注册开单→红线拦截→决策发布→可调用；RBAC/重复决策/404。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # type: ignore

from app.core.security import create_dev_token
from app.main import app

client = TestClient(app)
# 张三：销售部员工（注册智能体）
AUTH = {"Authorization": create_dev_token(user_id="u_1024", dept_id="d_sales", role="employee")}
# 平台管理员：全局可审
ADMIN = {"Authorization": create_dev_token(user_id="u_9000", dept_id="d_it", role="platform_admin")}
# 销售部部门管理员：只能审本部门单
DEPT_ADMIN = {"Authorization": create_dev_token(user_id="u_8001", dept_id="d_sales", role="dept_admin")}
# 设计部部门管理员：跨部门单不可审
OTHER_DEPT_ADMIN = {"Authorization": create_dev_token(user_id="u_8002", dept_id="d_design", role="dept_admin")}


def _register(name="审批流测试助手") -> tuple[str, str]:
    r = client.post("/api/v1/agents",
                    json={"name": name, "adapter_type": "llm_retrieval",
                          "description": "M5", "knowledge_scope": "department"},
                    headers=AUTH)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    return d["agent_id"], d["approval_id"]


def test_register_creates_pending_ticket():
    agent_id, approval_id = _register("开单测试助手")
    assert approval_id.startswith("apr_")
    agents = client.get("/api/v1/agents", headers=AUTH).json()["data"]["agents"]
    agent = next(a for a in agents if a["agent_id"] == agent_id)
    assert agent["status"] == "pending_review"
    assert agent["dept_id"] == "d_sales"


def test_invoke_blocked_before_publish():
    agent_id, _ = _register("未发布拦截测试")
    r = client.post("/api/v1/agent/invoke",
                    json={"agent_id": agent_id, "input": "hi"},
                    headers=AUTH)
    assert r.status_code == 403  # 第35章红线：未发布不可调用
    assert "未发布" in r.json()["detail"]


def test_employee_cannot_decide():
    _, approval_id = _register("员工越权审批测试")
    r = client.post(f"/api/v1/approval/{approval_id}/decide",
                    json={"decision": "approve"},
                    headers=AUTH)  # 普通员工
    assert r.status_code == 403


def test_approve_publishes_and_invoke_works():
    agent_id, approval_id = _register("审批通过测试")
    r = client.post(f"/api/v1/approval/{approval_id}/decide",
                    json={"decision": "approve", "comment": "评审通过"},
                    headers=ADMIN)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["status"] == "approved" and d["agent_status"] == "published"
    assert d["decided_by"] == "u_9000"
    # 发布后可调用
    inv = client.post("/api/v1/agent/invoke",
                      json={"agent_id": agent_id, "input": "查一下五一活动方案"},
                      headers=AUTH)
    assert inv.status_code == 200, inv.text


def test_reject_keeps_blocked():
    agent_id, approval_id = _register("驳回测试")
    r = client.post(f"/api/v1/approval/{approval_id}/decide",
                    json={"decision": "reject", "comment": "选型依据不足"},
                    headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["data"]["agent_status"] == "rejected"
    inv = client.post("/api/v1/agent/invoke",
                      json={"agent_id": agent_id, "input": "hi"},
                      headers=AUTH)
    assert inv.status_code == 403


def test_duplicate_decision_conflict():
    _, approval_id = _register("重复决策测试")
    r1 = client.post(f"/api/v1/approval/{approval_id}/decide",
                     json={"decision": "approve"}, headers=ADMIN)
    assert r1.status_code == 200
    r2 = client.post(f"/api/v1/approval/{approval_id}/decide",
                     json={"decision": "reject"}, headers=ADMIN)
    assert r2.status_code == 409  # 终态不可改


def test_dept_admin_scope():
    _, approval_id = _register("跨部门审批测试")
    # 设计部 dept_admin 审销售部单 → 403
    r = client.post(f"/api/v1/approval/{approval_id}/decide",
                    json={"decision": "approve"}, headers=OTHER_DEPT_ADMIN)
    assert r.status_code == 403
    # 本部门 dept_admin 可审 → 200
    r2 = client.post(f"/api/v1/approval/{approval_id}/decide",
                     json={"decision": "approve"}, headers=DEPT_ADMIN)
    assert r2.status_code == 200
    assert r2.json()["data"]["decided_by"] == "u_8001"


def test_unknown_ticket_404_and_bad_decision_400():
    r = client.post("/api/v1/approval/apr_nosuch/decide",
                    json={"decision": "approve"}, headers=ADMIN)
    assert r.status_code == 404
    _, approval_id = _register("非法决策测试")
    r2 = client.post(f"/api/v1/approval/{approval_id}/decide",
                     json={"decision": "maybe"}, headers=ADMIN)
    assert r2.status_code == 400


def test_pending_list_scoped():
    agent_id, _ = _register("待审列表测试")
    # 平台管理员能看到全部待审
    r = client.get("/api/v1/approval/pending", headers=ADMIN)
    assert r.status_code == 200
    assert any(t["agent_id"] == agent_id for t in r.json()["data"]["tickets"])
    # 员工不可看
    assert client.get("/api/v1/approval/pending", headers=AUTH).status_code == 403
