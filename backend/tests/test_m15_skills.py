"""M15 · 技能包融合测试。

覆盖四件核心事：
1. 注册不再丢字段（description / system_prompt / 分类）—— M15 前的真缺口
2. 批量导入合并成**一张**批次审批单（115 个包逐个开单不可治理）
3. 批次审批一次整批发布；审批前不可调用（第35章红线不因批量而松）
4. 技能包提示词真的进了运行时（skill_applied），且目录按行业分组不串味
"""
import pytest
from fastapi.testclient import TestClient

from app.core.security import create_dev_token
from app.main import app
from app.services import agent_store

client = TestClient(app)

ADMIN = {"Authorization": create_dev_token("p_admin", "d_platform", "platform_admin")}
EMP = {"Authorization": create_dev_token("u_zhang", "d_platform", "employee")}
OTHER = {"Authorization": create_dev_token("u_li", "d_other", "employee")}

SKILL_PROMPT = (
    "你是「经营复盘」执行专家。\n\n## 工作流程（严格按序执行，不得跳步）\n"
    "1. 五条线判定 2. 矛盾检测 3. 四层归因下钻 4. 出简报\n\n"
    "## 硬规则（违反即视为失败）\n主指标达标但支撑指标反向必须改判存疑。"
)


@pytest.fixture
def clean_store():
    """清场：避免其它用例注册的智能体污染计数断言。"""
    saved = dict(agent_store._AGENTS)
    agent_store._AGENTS.clear()
    yield
    agent_store._AGENTS.clear()
    agent_store._AGENTS.update(saved)


def _import(n=2, scope="department", name_prefix="技能"):
    items = [{
        "name": f"{name_prefix}{i}",
        "description": "测试用技能包",
        "system_prompt": SKILL_PROMPT,
        "adapter_type": "llm",
        "knowledge_scope": scope,
        "category": "industry",
        "category_cn": "制造业",
        "sub_function": "",
        "source_path": f"test/skill{i}/skill.md",
    } for i in range(n)]
    return client.post("/api/v1/agents/import", headers=ADMIN,
                       json={"items": items, "batch_name": "测试批次"})


def test_register_keeps_skill_fields(clean_store):
    """M15 前的真 bug：注册后 description 丢失、system_prompt 无处安放。"""
    r = client.post("/api/v1/agents", headers=ADMIN, json={
        "name": "经营复盘", "adapter_type": "llm", "description": "月度经营复盘",
        "knowledge_scope": "department", "system_prompt": SKILL_PROMPT,
        "category": "industry", "category_cn": "制造业", "sub_function": "",
        "source_path": "01_制造业/02_经营复盘/skill.md",
    })
    assert r.status_code == 200
    aid = r.json()["data"]["agent_id"]
    a = agent_store.get(aid)
    assert a["description"] == "月度经营复盘"          # 不再丢
    assert a["system_prompt"] == SKILL_PROMPT          # 技能包灵魂落库
    assert a["category_cn"] == "制造业"
    assert a["status"] == "pending_review"


def test_batch_import_creates_single_ticket(clean_store):
    """批量导入必须合并为一张批次单，否则审批台被 115 张单淹没。"""
    r = _import(5)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["created"] == 5
    approvals = d["approvals"]
    assert len(approvals) == 1, "5 个技能包应只开 1 张批次单"
    assert approvals[0]["batch"] is True
    assert len(approvals[0]["agent_ids"]) == 5


def test_batch_approve_publishes_all_and_invoke_works(clean_store):
    """红线不放宽：审批前 403；批次审批后整批 published 且技能包提示词生效。"""
    r = _import(3)
    d = r.json()["data"]
    aid = d["agents"][0]["agent_id"]
    apr = d["approvals"][0]["approval_id"]

    # 审批前：不可调用
    assert client.post("/api/v1/agent/invoke", headers=EMP,
                       json={"agent_id": aid, "input": "六月复盘"}).status_code == 403

    dec = client.post(f"/api/v1/approval/{apr}/decide", headers=ADMIN,
                      json={"decision": "approve"})
    assert dec.status_code == 200
    assert dec.json()["data"]["migrated_count"] == 3   # 一次决策整批发布

    r = client.post("/api/v1/agent/invoke", headers=EMP,
                    json={"agent_id": aid, "input": "六月五条线数据"})
    assert r.status_code == 200
    data = r.json()["data"]
    # 技能包提示词必须真的进了运行时（M15 前所有 agent 共用硬编码 prompt）
    assert data["agent"]["skill_applied"] is True
    assert data["agent"]["adapter_type"] == "llm"
    # llm 类技能包不走检索，不应凭空产生引用
    assert data["citations"] == []


def test_catalog_groups_by_industry_not_merged(clean_store):
    """kind 都是 industry，但制造业/零售不能并成一个组（M15 修过的真 bug）。"""
    client.post("/api/v1/agents/import", headers=ADMIN, json={"items": [
        {"name": "复盘A", "description": "d", "system_prompt": SKILL_PROMPT,
         "category": "industry", "category_cn": "制造业", "source_path": "a/skill.md"},
        {"name": "零售B", "description": "d", "system_prompt": SKILL_PROMPT,
         "category": "industry", "category_cn": "零售", "source_path": "b/skill.md"},
        {"name": "用工C", "description": "d", "system_prompt": SKILL_PROMPT,
         "category": "office", "category_cn": "办公角色", "sub_function": "HR",
         "source_path": "c/skill.md"},
    ]})
    cat = client.get("/api/v1/agents/catalog", headers=ADMIN).json()["data"]
    names = {c["category_cn"]: c["count"] for c in cat["categories"]}
    assert names == {"制造业": 1, "零售": 1, "办公角色": 1}, names
    hr = [c for c in cat["categories"] if c["category_cn"] == "办公角色"][0]
    assert hr["groups"][0]["sub_function"] == "HR"


def test_import_is_idempotent(clean_store):
    """同来源重复导入不生成第二个智能体。"""
    _import(2)
    r = _import(2)
    d = r.json()["data"]
    assert d["created"] == 0
    assert len(d["skipped"]) == 2


def test_other_dept_cannot_see_department_agents(clean_store):
    """第18章权限语义延伸到智能体目录：跨部门不可见。"""
    _import(2, scope="department")
    mine = client.get("/api/v1/agents", headers=EMP).json()["data"]
    theirs = client.get("/api/v1/agents", headers=OTHER).json()["data"]
    assert mine["count"] == 2
    assert theirs["count"] == 0


def test_global_scope_visible_across_depts(clean_store):
    _import(1, scope="global")
    assert client.get("/api/v1/agents", headers=OTHER).json()["data"]["count"] == 1
