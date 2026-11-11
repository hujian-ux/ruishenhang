"""M19 · 职业工作台。

背景：技能包原来只按行业分（制造业/零售/跨境/办公角色）。M53 起扩到 203 个、
11 个行业，但员工是按岗位找工具的——财务不会先想"我属于哪个行业"。故职业维度
作为技能库的一级入口。

这一层要守住的四件事：
1. 全部包都必须有职业归属，不允许出现"其他"黑洞（映射表漏了会静默丢包）
2. 职业是**横切标签**，不替代行业：一个包可挂多个职业，主职业唯一
3. 选中职业要先给 core（4 个开箱即用），再给全部，不能一上来摊 20 个
4. 行业视角与包数口径不能变（老接口、老看板还在用）
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


def _scan():
    """包总数以导入器扫描为准，不在测试里写死（M53：115 → 203）。"""
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts"))
    from import_solutions import scan
    return scan()


def _total() -> int:
    return len(_scan())


def _seed_all(scope: str = "global"):
    """把全部包按真实解析结果灌进 store（模拟 M15 导入后的状态）。"""
    for i, d in enumerate(_scan()):
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
    """全部包都有职业；核心包归属正确（validate 会把错配直接点出来）。"""
    assert role_map.validate() == []
    # 映射表条数必须等于实际包数：漏登记会静默落到兜底岗，数字对不上就该红
    assert len(role_map.PACK_ROLES) == _total()
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
    assert data["total"] == _total()
    # 每个包只归一个主职业，故职业计数之和必须等于总包数（不能重复计数）
    assert data["role_total"] == _total()
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
    看到「0 个业务技能包 · 0 个岗位」，而管理员能看到全部。之前所有演示都用管理员
    身份，所以这个问题藏了很久。技能库是公司资产，不该是导入人所在部门的私产。
    """
    _seed_all()
    r = client.get("/api/v1/agents/roles", headers=EMP)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["total"] == _total(), "员工不该因为部门不同而看不到技能库"
    assert len(d["roles"]) == len(role_map.ROLES)
    # 行业视角同样要有，不能只修了一个入口
    assert client.get("/api/v1/agents/catalog", headers=EMP).json()["data"]["total"] == _total()


def test_department_scope_is_still_respected():
    """修 bug 不能顺手把 RBAC 放开：department 域的包对外部门仍然不可见。"""
    _seed_all(scope="department")
    assert client.get("/api/v1/agents/roles", headers=EMP).json()["data"]["total"] == 0
    assert client.get("/api/v1/agents/roles", headers=ADMIN).json()["data"]["total"] == _total()


def test_roles_list_without_param():
    _seed_all()
    r = client.get("/api/v1/agents/roles", headers=ADMIN)
    assert r.status_code == 200
    d = r.json()["data"]
    assert len(d["roles"]) == len(role_map.ROLES)
    assert sum(x["count"] for x in d["roles"]) == _total()
    # 空职业不出现在目录里（前端不该渲染 0 个卡的岗位）
    assert all(x["count"] > 0 for x in d["roles"])


def test_builtin_assistant_visible_to_employee():
    """内置「通用助手」必须对员工可见。

    回归用例：ensure_builtin_assistant 写的是 scope=global，而 _visible 判的是
    knowledge_scope，字段名差一个词 → 员工列表里没有通用助手，前端把默认技能
    落到排序第一个的业务包（AI选品机会发现），用户随便问一句就走错技能。
    """
    agent_store.ensure_builtin_assistant()
    rows = client.get("/api/v1/agents", headers=EMP).json()["data"]["agents"]
    builtin = [a for a in rows if a.get("builtin")]
    assert len(builtin) == 1
    assert builtin[0]["agent_id"] == agent_store.BUILTIN_ASSISTANT_ID
    assert builtin[0].get("knowledge_scope") == "global"
    # 前端默认选中它，所以它必须排在最前
    assert rows[0]["agent_id"] == agent_store.BUILTIN_ASSISTANT_ID


def test_visible_accepts_legacy_scope_field():
    """老数据只有 scope 字段时也不能隐身（存量迁移期兼容）。"""
    agent_store.save({
        "agent_id": "ag_legacy_scope", "name": "老字段包",
        "adapter_type": "llm", "status": "published",
        "scope": "global",  # 故意不写 knowledge_scope
    })
    ids = [a["agent_id"] for a in client.get("/api/v1/agents", headers=EMP).json()["data"]["agents"]]
    assert "ag_legacy_scope" in ids


# ---------------------------------------------------------------- M49：行业 → 岗位两层
def test_industry_node_has_role_layer():
    """行业是第一层，岗位是行业下面的第二层。

    客户心智是「先选行业，再看这个行业有哪些岗位」，而不是一上来把 21 个制造业包
    平铺。这里锁住：每个行业节点都带 roles，且 roles 计数之和 == 行业包数
    （不允许有包既不在任何岗位里、又还被算进总数）。
    """
    _seed_all()
    data = client.get("/api/v1/agents/catalog", headers=ADMIN).json()["data"]
    for c in data["categories"]:
        assert c["roles"], f"{c['category_cn']} 缺岗位分层"
        assert sum(r["count"] for r in c["roles"]) == c["count"]
        for r in c["roles"]:
            assert r["count"] == len(r["agents"])
            assert r["name"] and r["initial"]
            # 岗位节点里的包必须回填岗位名，前端卡片直接显示，不用再查表
            assert all(a["role_name"] == r["name"] for a in r["agents"])


def test_industry_roles_are_real_roles():
    """制造业下确实是品质/计划/采购这些岗，不是把行业名当岗位。"""
    _seed_all()
    data = client.get("/api/v1/agents/catalog", headers=ADMIN).json()["data"]
    mfg = next(c for c in data["categories"] if c["category_cn"] == "制造业")
    names = [r["name"] for r in mfg["roles"]]
    assert "品质工程" in names and "生产计划" in names
    assert mfg["count"] == 21


def test_universal_industry_is_marked_and_sorted_last():
    """「通用」（办公角色 62 个）不绑行业：打标 universal 并排在具体行业之后。"""
    _seed_all()
    data = client.get("/api/v1/agents/catalog", headers=ADMIN).json()["data"]
    cats = data["categories"]
    uni = [c for c in cats if c["universal"]]
    assert [c["category_cn"] for c in uni] == ["办公角色"]
    assert uni[0]["name"] == "通用办公"
    assert cats[-1]["universal"] is True, "通用行业必须排最后，具体行业在前"
    # 公共库仍然不进行业树，也不进 total（包数口径不能变）
    assert all(c["category_cn"] != "公共库" for c in cats)
    assert data["total"] == _total()
