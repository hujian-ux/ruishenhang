"""M22 · 公共库与岗位边界。

验收三件事：
1. 公共库是平台预置的（10 条办公基础技能），重启即有，内容随代码刷新；
2. 公共库**不进 115 口径**（categories / total / role_total 都不含它）；
3. 选完岗位后，接口按岗位收窄：只回本岗业务包 + 公共库 + 通用助手。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_dev_token
from app.main import app
from app.services import agent_store, public_skills

client = TestClient(app)
ADMIN = {"Authorization": create_dev_token("p_admin", "d_platform", "platform_admin")}
EMP = {"Authorization": create_dev_token("u_1024", "d_sales", "employee")}


@pytest.fixture(scope="module")
def seeded():
    """预置公共库 + 造 6 个业务包（财务 3 / 销售 3）。

    不扫 115 个真实包：这里验的是**口径与边界**，不是包内容，快一点更稳。
    结束后把公共库摘掉，避免污染其它用例的 115 断言。
    """
    before = set(agent_store._AGENTS)
    ids = [a["agent_id"] for a in public_skills.build_agents()]
    agent_store.ensure_public_skills()
    agent_store.ensure_builtin_assistant()

    packs = [
        ("ag_t_fin_1", "费用异常波动分析", "finance"),
        ("ag_t_fin_2", "报销单合规预审", "finance"),
        ("ag_t_fin_3", "应收账款账龄分析", "finance"),
        ("ag_t_sal_1", "客户信用速查卡", "sales"),
        ("ag_t_sal_2", "报价单生成", "sales"),
        ("ag_t_man_1", "经营复盘", "manager"),
    ]
    for aid, name, role in packs:
        agent_store.save({
            "agent_id": aid, "name": name, "description": name,
            "adapter_type": "llm", "knowledge_scope": "global",
            "status": "published", "category": "office", "category_cn": "办公角色",
            "sub_function": "财务" if role == "finance" else "销售",
        })
        agent_store._AGENTS[aid]["role_primary"] = role
        agent_store._AGENTS[aid]["role_tags"] = [role]

    yield {"public_ids": ids, "pack_ids": [p[0] for p in packs]}

    for aid in list(agent_store._AGENTS):
        if aid not in before:
            agent_store._AGENTS.pop(aid, None)


def test_public_library_is_preset():
    """公共库 10 条，形态正确：global / published / 有真提示词 / 带建议产出格式。"""
    assert len(public_skills.PUBLIC_SKILLS) == 10
    names = [d["name"] for d in public_skills.PUBLIC_SKILLS]
    for must in ("文案生成", "PPT 制作", "表格制作"):
        assert must in names, f"缺基础技能：{must}"

    for a in public_skills.build_agents():
        assert a["knowledge_scope"] == "global"
        assert a["status"] == "published"
        assert a["category"] == "public"
        # 提示词不能是空壳：至少要说清输出结构
        assert len(a["system_prompt"]) > 200
        assert a["default_format"] in {"docx", "pptx", "xlsx", "pdf", "html"}


def test_public_skills_idempotent(seeded):
    """重复预置不产生重复条目（启动会反复调）。"""
    n_before = len([a for a in agent_store._AGENTS.values() if agent_store.is_public(a)])
    agent_store.ensure_public_skills()
    agent_store.ensure_public_skills()
    n_after = len([a for a in agent_store._AGENTS.values() if agent_store.is_public(a)])
    assert n_before == n_after == 10


def test_public_not_counted_in_business_total(seeded):
    """口径红线：公共库不能把 115 变成 125。

    不用写死 115：_AGENTS 是进程级全局，别的用例可能已灌了包。
    换算法——total 必须恰好等于"库里非公共库、非内置助手的业务包数量"。
    """
    business = [a for a in agent_store._AGENTS.values()
                if not agent_store.is_public(a) and not a.get("builtin")]
    tagged = [a for a in business if a.get("role_primary")]

    d = client.get("/api/v1/agents/catalog", headers=ADMIN).json()["data"]
    assert d["total"] == len(business)
    assert d["public_total"] == 10
    assert d["role_total"] == len(tagged)
    # 行业目录里不许出现"公共库"这一格
    assert "公共库" not in [c["category_cn"] for c in d["categories"]]


def test_public_does_not_get_role_tag(seeded):
    """公共库不挂岗位标签，否则 11 个岗位的计数会互相叠加。"""
    for a in agent_store._AGENTS.values():
        if agent_store.is_public(a):
            assert not a.get("role_tags"), f"{a['name']} 不该挂职业标签"


def test_employee_sees_public_library(seeded):
    """普通员工必须能看到公共库（scope=global + 字段名不能写错）。"""
    ids = [a["agent_id"] for a in
           client.get("/api/v1/agents?category=public", headers=EMP).json()["data"]["agents"]]
    for pid in seeded["public_ids"]:
        assert pid in ids


def test_role_narrows_agents_endpoint(seeded):
    """?role= 只回本岗业务包 + 公共库 + 通用助手。"""
    d = client.get("/api/v1/agents?role=finance", headers=EMP).json()["data"]["agents"]
    got = {a["name"] for a in d}
    assert "费用异常波动分析" in got
    assert "公共库" not in got
    assert "文案生成" in got, "公共库必须跟着岗位一起给"
    assert "报销单合规预审" in got
    assert "客户信用速查卡" not in got, "别的岗位的包不该出现"
    assert "经营复盘" not in got
    assert any(a.get("builtin") for a in d), "通用助手不能被岗位过滤掉"

    # 中文名与 id 都收
    d2 = client.get("/api/v1/agents?role=财务", headers=EMP).json()["data"]["agents"]
    assert {a["agent_id"] for a in d} == {a["agent_id"] for a in d2}


def test_role_workspace_includes_public(seeded):
    d = client.get("/api/v1/agents/roles?role=finance", headers=ADMIN).json()["data"]
    assert d["count"] == 3
    assert len(d["public"]) == 10
    assert {a["name"] for a in d["core"]} | {a["name"] for a in d["rest"]} == {
        "费用异常波动分析", "报销单合规预审", "应收账款账龄分析"}


def test_catalog_with_role_filter(seeded):
    """catalog?role= 也不许漏出别的岗位。"""
    d = client.get("/api/v1/agents/catalog?role=sales", headers=ADMIN).json()["data"]
    names = {a["name"] for c in d["categories"] for g in c["groups"] for a in g["agents"]}
    assert names == {"客户信用速查卡", "报价单生成"}
    assert d["public_total"] == 10
    assert [r["id"] for r in d["roles"]] == ["sales"]


def test_public_skill_is_invocable_shape(seeded):
    """公共库条目能走 invoke 的前置条件：published + 有提示词 + 不依赖输入文件。"""
    a = agent_store.get("ag_pub_sheet")
    assert a and a["status"] == "published"
    assert a["name"] == "表格制作"
    assert "Markdown" in a["system_prompt"]
    # 表格类默认出 Excel，PPT 类默认出 PPTX
    assert a["default_format"] == "xlsx"
    assert agent_store.get("ag_pub_ppt")["default_format"] == "pptx"
