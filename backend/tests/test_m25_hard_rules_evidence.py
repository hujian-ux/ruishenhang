"""M25 · 三件不依赖真模型也必须成立的事。

1. 平台硬规则**单一来源**：全部公共技能全量注入，改一处全生效，启动刷新能自愈；
   硬规则不许带 `## `（下游按它切页，多一个标题就多一张空白页）。
2. 「已有表格清洗与改写」补齐 M16 上传链路的技能引导，
   数量仍卡在上限内（M26 后为 28），表格组 5 条且同组连续。
3. RAG 证据**带正文进 prompt**：M24 之前只拼 title，模型看不到原文，
   「逐字引用 / 冲突并列 / 查不到说没查到」全是空话。这里用 monkeypatch
   截获真实 prompt 验证，不需要真模型。

答案质量本身（模型会不会照做）由 scripts/verify_rag_answers.py 在真模型下验收，
stub 下明确 SKIP，不许在这里假装通过。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models.caller import CallerContext
from app.services import agent_runtime, agent_store, knowledge, public_skills

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures" / "rag_conflicts"


# ---------------------------------------------------------------- 1. 硬规则
def test_hard_rules_single_source_and_full_coverage():
    """所有公共技能共享同一段硬规则；不是各写一遍，也不许漏一条。"""
    agents = public_skills.build_agents()
    assert len(agents) == len(public_skills.PUBLIC_SKILLS)
    for a in agents:
        p = a["system_prompt"]
        assert p.startswith(public_skills.HARD_RULES_HEADER), f"{a['name']} 没注入硬规则"
        assert public_skills.HARD_RULES in p
        assert a["hard_rules_version"] == public_skills.HARD_RULES_VERSION


def test_hard_rules_have_no_markdown_h2():
    """硬规则里出现 `## ` 会让渲染器多切一页，产出物首页变空。"""
    assert "## " not in public_skills.HARD_RULES
    assert "\n# " not in public_skills.HARD_RULES


def test_hard_rules_cover_the_three_red_lines():
    for word in ("不许编造", "不替审批人拍板", "依据可追溯", "缺料先问", "越权不猜"):
        assert word in public_skills.HARD_RULES, f"红线漏了：{word}"
    # 查不到要老实说，不能拿常识补
    assert "未查到" in public_skills.HARD_RULES


def test_compose_system_prompt_is_idempotent():
    body = "## 执行结论\n正文"
    once = public_skills.compose_system_prompt(body)
    twice = public_skills.compose_system_prompt(once)
    assert once == twice
    assert once.count(public_skills.HARD_RULES_HEADER) == 1
    assert once.endswith(body), "业务提示词必须原样保留在硬规则之后"


def test_business_rules_are_not_absorbed():
    """抽的是跨平台红线，不是各技能的业务干法；后者仍留在原提示词里。"""
    plan = next(s for s in public_skills.PUBLIC_SKILLS if s["key"] == "pub_plan")
    assert "每条任务必须有具体负责人" in plan["system_prompt"]
    a = next(x for x in public_skills.build_agents() if x["agent_id"] == "ag_pub_plan")
    assert "每条任务必须有具体负责人" in a["system_prompt"]


def test_startup_refresh_repairs_tampered_rules():
    """管理员/旧代码把提示词改坏，启动刷新要按代码自愈（含版本字段）。"""
    before = set(agent_store._AGENTS)
    try:
        agent_store.ensure_public_skills()
        rec = agent_store.get("ag_pub_sheet")
        assert rec
        rec["system_prompt"] = "被改坏的空提示词"
        rec["hard_rules_version"] = "ancient"
        agent_store.ensure_public_skills()
        rec = agent_store.get("ag_pub_sheet")
        assert rec["system_prompt"].startswith(public_skills.HARD_RULES_HEADER)
        assert rec["hard_rules_version"] == public_skills.HARD_RULES_VERSION
    finally:
        for aid in list(agent_store._AGENTS):
            if aid not in before:
                agent_store._AGENTS.pop(aid, None)


# ---------------------------------------------------------------- 2. 第 25 条
def test_file_rework_skill_is_complete():
    s = next((x for x in public_skills.PUBLIC_SKILLS if x["key"] == "pub_file_rework"), None)
    assert s, "缺「上传已有表再加工」技能"
    assert s["sub_function"] == "表格" and s["default_format"] == "xlsx"
    for f in ("when_to_use", "inputs_contract", "outputs_contract"):
        assert s.get(f), f"第 25 条缺 {f}"
    assert "附件" in s["inputs_contract"]
    p = s["system_prompt"]
    assert p.count("## ") >= 3
    assert len(p) >= 350
    # 没有附件时必须停下来要文件，不许凭空造表
    assert "未收到附件，暂不执行" in p
    assert "改动逐条说明" in p and "需人工确认" in p


def test_cap_still_holds_at_28():
    assert 20 <= len(public_skills.PUBLIC_SKILLS) <= 28
    seq = [s["sub_function"] for s in public_skills.PUBLIC_SKILLS]
    compact = [g for i, g in enumerate(seq) if i == 0 or g != seq[i - 1]]
    assert not [g for g in compact if compact.count(g) > 1], "分组被拆散了"
    from collections import Counter
    c = Counter(seq)
    assert all(3 <= n <= 5 for n in c.values()), c
    assert c["表格"] == 5


# ---------------------------------------------------------------- 3. 证据进 prompt
def test_compose_evidence_carries_snippet_and_numbers():
    hits = [
        {"title": "差旅住宿标准（2023 版）", "snippet": "部门经理一类城市 600 元/晚，超标经分管副总审批"},
        {"title": "差旅住宿标准（2026 修订版）", "snippet": "部门经理一类城市 550 元/晚，超标一律报 CEO 审批"},
    ]
    ev = agent_runtime.compose_evidence(hits)
    assert "[1]" in ev and "[2]" in ev
    assert "600 元" in ev and "550 元" in ev
    assert "分管副总" in ev and "CEO" in ev, "只拼标题就丢了这两个关键事实"


def test_compose_evidence_prefers_multi_snippets():
    """同 parent 的多句证据要一起进 prompt，否则条款冲突看不出来。"""
    hits = [{
        "title": "差旅住宿标准（2026 修订版）",
        "snippet": "部门经理一类城市 550 元/晚",
        "snippets": ["部门经理一类城市 550 元/晚",
                     "超标住宿一律报 CEO 审批，分管副总不再具有超标审批权限",
                     "本版自 2026 年 3 月 1 日起执行，原 2023 版同日废止"],
    }]
    ev = agent_runtime.compose_evidence(hits)
    for token in ("550 元", "CEO", "废止"):
        assert token in ev, f"多句证据里丢了「{token}」"


def test_compose_evidence_signals_empty_result():
    ev = agent_runtime.compose_evidence([])
    assert "未查到" in ev and "没有任何" in ev


def test_grounded_prompt_differs_from_reference_prompt():
    ev = "[1] 《制度》：金额 500 元"
    grounded = agent_runtime._compose_prompt("限额多少", ev, "", True, grounded=True)
    plain = agent_runtime._compose_prompt("限额多少", ev, "", True, grounded=False)
    assert "授权检索依据" in grounded and "[编号]" in grounded
    assert "冲突" in grounded and "未查到" in grounded
    assert "可参考的企业授权片段" in plain
    assert "授权检索依据" not in plain, "非 RAG 技能不该被改成严格依据口径"


def test_rag_skill_prompt_really_contains_both_conflicting_versions(monkeypatch):
    """截获真实 prompt：不靠读代码，直接看模型会收到什么。"""
    hits = [
        {"doc_id": "d_v1", "title": "差旅住宿标准（2023 版）", "scope": "global",
         "snippet": "部门经理一类城市 600 元/晚，超标经分管副总审批"},
        {"doc_id": "d_v2", "title": "差旅住宿标准（2026 修订版）", "scope": "global",
         "snippet": "部门经理一类城市 550 元/晚，超标一律报 CEO 审批"},
    ]
    monkeypatch.setattr(knowledge, "retrieve", lambda *a, **kw: (hits, 0))
    captured = {}

    def fake_llm(prompt, system_prompt=None, user_input="", agent=None, tenant_id=None):
        captured["prompt"] = prompt
        captured["system"] = system_prompt or ""
        return {"text": "（测试占位）", "real": False}

    monkeypatch.setattr(agent_runtime, "_call_llm", fake_llm)
    # 直接从定义构造，不动进程级 _AGENTS，避免污染其它用例的 115 口径
    rec = next(a for a in public_skills.build_agents() if a["agent_id"] == "ag_pub_kb_qa")
    caller = CallerContext(userId="u_1", deptId="d_sales", role="employee", tenantId="t_demo")
    agent_runtime.run_agent("ag_pub_kb_qa", "部门经理去上海出差上限多少？", caller,
                            system_prompt=rec["system_prompt"],
                            adapter_type="llm_retrieval")

    p = captured["prompt"]
    for token in ("600 元", "550 元", "分管副总", "CEO", "[1]", "[2]"):
        assert token in p, f"模型收不到「{token}」，冲突并列无从谈起"
    assert "授权检索依据" in p and "冲突" in p
    assert public_skills.HARD_RULES_HEADER in captured["system"], "硬规则没进 system prompt"


def test_plain_skill_is_not_forced_into_grounded_mode(monkeypatch):
    """115 个业务包不该因为查证类技能改了口径就集体变脸。"""
    monkeypatch.setattr(knowledge, "retrieve", lambda *a, **kw: ([], 0))
    captured = {}

    def fake_llm(prompt, system_prompt=None, user_input="", agent=None, tenant_id=None):
        captured["prompt"] = prompt
        return {"text": "ok", "real": False}

    monkeypatch.setattr(agent_runtime, "_call_llm", fake_llm)
    caller = CallerContext(userId="u_1", deptId="d_sales", role="employee", tenantId="t_demo")
    agent_runtime.run_agent("x", "做个表", caller,
                            system_prompt="你是做表的人。## 执行结论\n…",
                            adapter_type="llm")
    assert "授权检索依据" not in captured["prompt"]


# ---------------------------------------------------------------- 4. 验收夹具
def test_conflict_fixtures_are_really_conflicting():
    files = sorted(FIXTURES.glob("*.md"))
    assert len(files) == 3
    texts = [p.read_text(encoding="utf-8") for p in files]
    all_text = "\n".join(texts)
    for token in ("600", "550", "分管副总", "CEO", "废止", "XZ-2023-017"):
        assert token in all_text, f"夹具里缺关键冲突因子：{token}"
    assert "海外" not in all_text, "第三问要验「查不到」，夹具里不能真有海外条款"


def test_verify_script_cases_are_json_serializable():
    """脚本报告要能落 JSON；别忘了 case 里的 lambda 不能混进去。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("verify_rag_answers",
                                                  ROOT / "scripts" / "verify_rag_answers.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert {c["id"] for c in mod.CASES} == {"conflict", "missing", "citation"}
    assert all(c["checks"] for c in mod.CASES)
    # 模拟脚本里落盘的字段集合
    payload = [{"id": c["id"], "name": c["name"], "question": c["question"],
                "checks": [{"item": label, "status": "SKIP"} for label, _ in c["checks"]]}
               for c in mod.CASES]
    json.dumps(payload, ensure_ascii=False)
