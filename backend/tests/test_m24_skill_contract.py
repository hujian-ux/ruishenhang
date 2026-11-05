"""M24 · 参考 WorkBuddy 内置技能：给公共库补「技能契约」和「先检索再答」。

WorkBuddy 的内置技能有两件我们没有的事：
1. 每条技能都写清「什么时候用 / 要给什么 / 会拿到什么」，用户不用靠名字猜；
2. 技能是「能力 + 工具链」，查资料类会先检索再答，不是凭记忆编。

对应到本平台：
- 每条公共技能带 when_to_use / inputs_contract / outputs_contract，且接口要回显；
- 至少一条挂 adapter_type=llm_retrieval，走 /rag/retrieve，补上公共库不走 RAG 的短板。

另外守：字段是平台资产，启动时按代码刷新，管理员改过也不会口径漂移。
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
    before = set(agent_store._AGENTS)
    agent_store.ensure_public_skills()
    agent_store.ensure_builtin_assistant()
    yield
    for aid in list(agent_store._AGENTS):
        if aid not in before:
            agent_store._AGENTS.pop(aid, None)


def test_every_skill_has_usage_contract():
    """24 条，条条写清三件事；空一条就是又回到「不知道该点哪个」。"""
    for s in public_skills.PUBLIC_SKILLS:
        for f in ("when_to_use", "inputs_contract", "outputs_contract"):
            assert s.get(f), f"{s['name']} 缺 {f}"


def test_contract_reaches_agent_record():
    """定义要真的落到 agent 记录里，不能只停在源码。"""
    for a in public_skills.build_agents():
        assert a["when_to_use"]
        assert a["inputs_contract"]
        assert a["outputs_contract"]


def test_retrieval_skill_exists():
    """公共库必须有一条「先检索再答」的技能，且提示词不许凭空编。"""
    hits = [s for s in public_skills.PUBLIC_SKILLS
            if s.get("adapter_type") == "llm_retrieval"]
    assert len(hits) >= 1, "公共库全是纯生成，查公司制度只能靠模型瞎编"
    for s in hits:
        p = s["system_prompt"]
        assert "检索" in p and "出处" in p
        assert "不许" in p or "不写" in p or "一律不写" in p
    ids = {a["agent_id"] for a in public_skills.build_agents()
           if a["adapter_type"] == "llm_retrieval"}
    assert ids


def test_retrieval_adapter_is_wired_to_runtime(seeded):
    """挂了 llm_retrieval 就得真被运行时认出来（RETRIEVAL_ADAPTERS）。"""
    from app.services.agent_runtime import RETRIEVAL_ADAPTERS
    a = agent_store.get("ag_pub_kb_qa")
    assert a and a["adapter_type"] in RETRIEVAL_ADAPTERS


def test_brief_exposes_contract(seeded):
    """catalog / roles 走 brief()，前端搜「请个假」要靠这个字段。"""
    b = agent_store.brief(agent_store.get("ag_pub_request"))
    assert "要钱" in b["when_to_use"]
    assert b["inputs_contract"] and b["outputs_contract"]


def test_contract_in_http_responses(seeded):
    """两个前端入口都要带契约：/agents 列表 与 /agents/catalog。"""
    rows = client.get("/api/v1/agents?category=public", headers=EMP).json()["data"]["agents"]
    assert len(rows) == len(public_skills.PUBLIC_SKILLS)
    assert all(r.get("when_to_use") for r in rows)

    pub = client.get("/api/v1/agents/catalog", headers=ADMIN).json()["data"]["public"]
    assert all(p.get("inputs_contract") and p.get("outputs_contract") for p in pub)


def test_startup_refreshes_contract(seeded):
    """公共库是平台资产：内存里被改过的契约，启动刷新后必须回到代码值。"""
    aid = "ag_pub_notice"
    code_value = next(s["when_to_use"] for s in public_skills.PUBLIC_SKILLS
                      if s["key"] == "pub_notice")
    agent_store._AGENTS[aid]["when_to_use"] = "被手工改坏了"
    agent_store.ensure_public_skills()
    assert agent_store.get(aid)["when_to_use"] == code_value


def test_contract_does_not_touch_115(seeded):
    """加字段、加 RAG 条目，都不能把口径搅乱。"""
    business = [a for a in agent_store._AGENTS.values()
                if not agent_store.is_public(a) and not a.get("builtin")]
    d = client.get("/api/v1/agents/catalog", headers=ADMIN).json()["data"]
    assert d["total"] == len(business)
    assert d["public_total"] == len(public_skills.PUBLIC_SKILLS)
    for a in agent_store._AGENTS.values():
        if agent_store.is_public(a):
            assert not a.get("role_tags")
