"""M19 · 职业工作台。

背景：115 个技能包原来只按行业分（制造业/零售/跨境/办公角色）。员工是按岗位找工具的
——财务不会先想"我属于哪个行业"。故新增职业维度，作为技能库的一级入口。

这一层要守住的四件事：
1. 115 个包必须全部有职业归属，不允许出现"其他"黑洞（映射表漏了会静默丢包）
2. 职业是**横切标签**，不替代行业：一个包可挂多个职业，主职业唯一
3. 选中职业要先给 core（4 个开箱即用），再给全部，不能一上来摊 20 个
4. 行业视角与 115 口径不能变（老接口、老看板还在用）
"""
from fastapi.testclient import TestClient
import pytest

from app.core.security import create_dev_token
from app.main import app
from app.services import agent_store, role_map

client = TestClient(app)
ADMIN = {"Authorization": create_dev_token("p_admin", "d_platform", "platform_admin")}
EMP = {"Authorization": create_dev_token("u_1024", "d_sales", "employee")}


@pytest.fixture(autouse=True)
def _isolate_store():
    """agent_store._AGENTS 是模块级全局，别的测试也会往里塞包。
    不隔离的话「总数必须 115」会被污染成 117，测的是别的用例的副作用而不是本功能。"""
    backup = dict(agent_store._AGENTS)
    agent_store._AGENTS.clear()
    yield
    agent_store._AGENTS.clear()
    agent_store._AGENTS.update(backup)


def _seed_all(scope: str = "global"):
    """把 115 个包按真实解析结果灌进 store（模拟 M15 导入后的状态）。"""
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts"))
    from import_solutions import scan
    for i, d in enumerate(scan()):
        agent_store.save({
            "agent_id": f"ag_r{i:03d}",
            "name": d["name"],
            "description": d["description"],
            "adapter_type": d["adapter_type"],
            "knowledge_scope": scope,
            "status": "published",
            "category": d["kind"],
            "category_cn": d["board_cn"],
            "sub_function": d["sub_function"],
            "source_path": d["source"],
        })


def test_mapping_covers_every_pack():
    """115 条全部有职业；核心包归属正确（validate 会把错配直接点出来）。"""
    assert role_map.validate() == []
    assert len(role_map.PACK_ROLES) == 115
    # 每个职业的 4 个 core 都是真实存在的包名
    for rid, packs in role_map.ROLE_CORE.items():
        assert len(packs) == 4, f"{rid} 的核心包应恰好 4 个"
        for p in packs:
            assert role_map.PACK_ROLES[p][0] == rid, f"{p} 的主职业不是 {rid}"


def test_role_tags_are_multi_valued_but_primary_is_single():
    """横切语义：制造业的对账包既是制造业的，也是财务岗的。"""
    tags = role_map.roles_for("对账与开票核对", "", "制造业")
    assert tags[0] == "finance"
    # 主职业唯一，且就是列表第一个
    assert role_map.primary_role("对账与开票核对") == "finance"
    # 未登记的新包走回退，不抛错也不丢
    assert role_map.roles_for("未来新增的某个包", "财务", "")[0] == "finance"


def test_catalog_exposes_roles_alongside_industry():
    """双视角：roles 与 categories 同时返回，行业口径不变。"""
    _seed_all()
    r = client.get("/api/v1/agents/catalog", headers=ADMIN)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["categories"], "行业视角不能因新维度而消失"
    assert data["roles"], "职业视角必须有内容"
    assert data["total"] == 115
    # 每个包只归一个主职业，故职业计数之和必须等于总包数（不能重复计数）
    assert data["role_total"] == 115
    names = [x["name"] for x in data["roles"]]
    assert "财务" in names and "人事" in names and "销售" in names


def test_role_workspace_puts_core_first():
    """选财务 → 先给 4 个 core，其余在 rest，且 core 排前面。"""
    _seed_all()
    r = client.get("/api/v1/agents/roles", params={"role": "财务"}, headers=ADMIN)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["role"]["id"] == "finance"
    assert len(d["core"]) == 4
    assert all(a["core"] for a in d["core"])
    assert d["count"] == len(d["core"]) + len(d["rest"])
    assert "报销单合规预审" in [a["name"] for a in d["core"]]


def test_role_accepts_cn_name_and_id():
    _seed_all()
    for q in ("finance", "财务"):
        r = client.get("/api/v1/agents/roles", params={"role": q}, headers=ADMIN)
        assert r.status_code == 200
        assert r.json()["data"]["role"]["id"] == "finance"


def test_unknown_role_404():
    r = client.get("/api/v1/agents/roles", params={"role": "法务"}, headers=ADMIN)
    assert r.status_code == 404


def test_employee_sees_published_skill_library():
    """普通员工必须能看到技能库。

    回归用例：M15 批量导入写的是 scope=department + 导入人部门，结果销售部员工
    看到「0 个业务技能包 · 0 个岗位」，而管理员看到 115 个。之前所有演示都用管理员
    身份，所以这个问题藏了很久。技能库是公司资产，不该是导入人所在部门的私产。
    """
    _seed_all()
    r = client.get("/api/v1/agents/roles", headers=EMP)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["total"] == 115, "员工不该因为部门不同而看不到技能库"
    assert len(d["roles"]) == len(role_map.ROLES)
    # 行业视角同样要有，不能只修了一个入口
    assert client.get("/api/v1/agents/catalog", headers=EMP).json()["data"]["total"] == 115


def test_department_scope_is_still_respected():
    """修 bug 不能顺手把 RBAC 放开：department 域的包对外部门仍然不可见。"""
    _seed_all(scope="department")
    assert client.get("/api/v1/agents/roles", headers=EMP).json()["data"]["total"] == 0
    assert client.get("/api/v1/agents/roles", headers=ADMIN).json()["data"]["total"] == 115


def test_roles_list_without_param():
    _seed_all()
    r = client.get("/api/v1/agents/roles", headers=ADMIN)
    assert r.status_code == 200
    d = r.json()["data"]
    assert len(d["roles"]) == len(role_map.ROLES)
    assert sum(x["count"] for x in d["roles"]) == 115
    # 空职业不出现在目录里（前端不该渲染 0 个卡的岗位）
    assert all(x["count"] > 0 for x in d["roles"])
