"""P1-8 · 技能索引台账（#303）契约测试。

覆盖：
1. 覆盖度计算 _record：9 个维度 flag（提示词长度阈值 / 硬规则头 / 岗位映射 /
   输入输出契约含 skill.md 章节解析兜底 / 源码目录 / 样例输入 / few-shot / 材料）、
   加权评分、gaps、complete、公共库豁免 role、opt_in 不算盲区。
2. index 过滤：industry / role / status / q / kind / flag（缺口筛选）、
   按完备度升序、分页 limit/offset、排除 builtin。
3. stats 聚合：total/business/public/avg/complete、by_industry、by_role、
   dimension_coverage、score_buckets、gap_top、空库零值结构。
4. detail：checklist / samples / reference_specs / 版本号 / pack_found，
   builtin / 不存在 / 不可见一律 None。
5. 可见域：employee 跨租户 / 跨部门不可见，platform_admin 全量，复用 agent_store._visible。
6. 路由层：/skills/stats 必须早于 /skills/{agent_id}（否则被路径参数吞）、
   code=0 包装、无鉴权 401、detail 404 不区分「不存在 / 无权限 / builtin」。

测试隔离：清空 agent_store._AGENTS 后手工灌入可控技能包；把 skill_pack 的
文件系统读取全部打桩，令 source / samples / checklist 由 agent 字段与
source_path 约定驱动，测试不依赖仓库真实 solutions 目录，可移植、稳定。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_dev_token
from app.main import app
from app.models.caller import CallerContext
from app.services import agent_store, hard_rules, role_map, skill_ledger, skill_pack

client = TestClient(app)
API = "/api/v1/skills"

ADMIN = {"Authorization": create_dev_token("p_admin", "d_platform", "platform_admin", "t_sys")}
EMP_A = {"Authorization": create_dev_token("u_a", "d_a", "employee", "t_a")}
EMP_B = {"Authorization": create_dev_token("u_x", "d_x", "employee", "t_b")}

# 提示词 ≥50 字且含平台硬规则头 → prompt / hard_rules 两个 flag 同时为真。
HARD = hard_rules.HARD_RULES_HEADER
LONG_PROMPT = "你是经营分析专家，负责量价分离与异常归因。" * 5 + "\n" + HARD + "\n不得编造任何数据。"


# ── 打桩：把 skill_pack 的磁盘读取换成内存约定 ──────────────────────────────
def _install_stubs(monkeypatch):
    """source_path 以 'pack://' 开头 = 有包目录；含 'nosample' = 无原始输入样例。

    其余路径一律视为「无源码目录」；_skill_md 恒返回 ''（inputs/outputs 契约
    只认显式字段，除非某个用例单独 monkeypatch skill_ledger._skill_md_text）。
    """
    def fake_pack_dir(src: str) -> str:
        return "/virtual/pack" if (src or "").startswith("pack://") else ""

    def fake_samples(src: str):
        if not (src or "").startswith("pack://") or "nosample" in src:
            return []
        return [
            {"filename": "input.csv", "display_name": "输入.csv", "kind": "input",
             "size": 128, "ext": "csv", "from_readme": True, "rel_path": "input.csv"},
            {"filename": "out.html", "display_name": "产出.html", "kind": "output",
             "size": 512, "ext": "html", "from_readme": True, "rel_path": "out.html"},
        ]

    def fake_checklist(agent):
        c = agent.get("inputs_contract") or ""
        return [x.strip() for x in c.split("\n") if x.strip()]

    monkeypatch.setattr(skill_pack, "pack_dir", fake_pack_dir)
    monkeypatch.setattr(skill_pack, "samples", fake_samples)
    monkeypatch.setattr(skill_pack, "checklist", fake_checklist)
    monkeypatch.setattr(skill_pack, "_skill_md", lambda src: "")


@pytest.fixture
def env(monkeypatch):
    """清场 + 打桩。yield 期间 _AGENTS 里只有本用例手工灌入的包。"""
    saved = dict(agent_store._AGENTS)
    agent_store._AGENTS.clear()
    _install_stubs(monkeypatch)
    yield
    agent_store._AGENTS.clear()
    agent_store._AGENTS.update(saved)


def _put(agent_id: str, **kw):
    """直接写内存 dict（绕过 save 的 annotate），精确控制 role_primary 等字段。"""
    a = {
        "agent_id": agent_id,
        "name": kw.pop("name", agent_id),
        "status": "published",
        "adapter_type": "llm",
        "category": "industry",
        "category_cn": "制造业",
        "sub_function": "经营分析",
        "description": "测试技能包",
        "system_prompt": "",
        "knowledge_scope": "global",
        "tenant_id": "",              # 平台共享资产：对所有租户可见
        "registered_by": "bootstrap",
        "dept_id": "d_platform",
        "role_primary": "finance",
        "role_core": False,
    }
    a.update(kw)
    agent_store._AGENTS[agent_id] = a
    return a


def _full(agent_id: str, **kw):
    """9 维全覆盖的业务包：score=1.0 / complete=True / gaps=[]。"""
    base = dict(
        system_prompt=LONG_PROMPT,
        role_primary="finance",
        inputs_contract="历史报价\n询价邮件",
        outputs_contract="报价单",
        source_path="pack://full",
        fewshot=[{"q": "示例", "a": "产出"}],
        fewshot_version="fs-1",
        hard_rules_version="hr-2",
        reference_specs=[{"name": "领域规范", "chars": 100}],
        reference_specs_version="rs-3",
        acceptance="验收标准",
    )
    base.update(kw)
    return _put(agent_id, **base)


def _empty(agent_id: str, **kw):
    """9 维全空的业务包：score=0 / complete=False / gaps=7 个必查维度。"""
    base = dict(
        system_prompt="", role_primary="", inputs_contract="", outputs_contract="",
        source_path="", fewshot=None, reference_specs=[], acceptance="",
    )
    base.update(kw)
    return _put(agent_id, **base)


ADMIN_CTX = CallerContext(userId="p_admin", deptId="d_platform", role="platform_admin", tenantId="t_sys")


# ══════════════════════════════════════════════════════════════════════════
# 1. 覆盖度计算 _record
# ══════════════════════════════════════════════════════════════════════════
def test_record_full_coverage(env):
    rec = skill_ledger._record(_full("b_full"))
    assert all(rec["flags"].values()), rec["flags"]
    assert set(rec["coverage"]) == {d["key"] for d in skill_ledger.DIMENSIONS}
    assert rec["score"] == 1.0 and rec["score_pct"] == 100
    assert rec["complete"] is True and rec["gaps"] == []
    assert rec["kind"] == "business"
    assert rec["prompt_len"] >= 50
    assert rec["sample_count"] == 2 and rec["input_sample_count"] == 1
    assert rec["checklist_count"] == 2
    assert rec["reference_spec_count"] == 1 and rec["has_acceptance"] is True
    assert rec["role_name"] == role_map.ROLE_NAME["finance"] == "财务"


def test_record_all_empty_flags_and_required_gaps(env):
    rec = skill_ledger._record(_empty("b_empty"))
    assert not any(rec["flags"].values()), rec["flags"]
    assert rec["score"] == 0.0 and rec["score_pct"] == 0
    assert rec["complete"] is False
    # 必查盲区 = 非 opt_in 维度（含 role），不含 few-shot / materials
    assert set(rec["gaps"]) == set(skill_ledger._BUSINESS_REQUIRED)
    assert "fewshot" not in rec["gaps"] and "materials" not in rec["gaps"]
    assert "role" in rec["gaps"]                       # 业务包缺岗位必须判盲区（修复点）


def test_record_prompt_length_threshold_and_hard_rules(env):
    # 短提示词（<50 字）→ prompt False；不含硬规则头 → hard_rules False
    short = skill_ledger._record(_empty("short", system_prompt="太短"))
    assert short["flags"]["prompt"] is False and short["flags"]["hard_rules"] is False
    # 长提示词但无硬规则头 → prompt True / hard_rules False
    long_no_hard = skill_ledger._record(_empty("long", system_prompt="你是专家。" * 12))
    assert long_no_hard["flags"]["prompt"] is True
    assert long_no_hard["flags"]["hard_rules"] is False
    # 含硬规则头 → hard_rules True
    with_hard = skill_ledger._record(_empty("hard", system_prompt="你是专家。" * 12 + HARD))
    assert with_hard["flags"]["hard_rules"] is True


def test_record_opt_in_missing_not_a_gap_but_lowers_score(env):
    """few-shot / 材料是 opt_in：缺失不算盲区，但拉低评分。"""
    rec = skill_ledger._record(_full("no_optin", fewshot=None,
                                     reference_specs=[], acceptance=""))
    assert rec["flags"]["fewshot"] is False and rec["flags"]["materials"] is False
    assert rec["complete"] is True and rec["gaps"] == []      # 仍无盲区
    assert rec["score"] == pytest.approx(13 / 15, abs=1e-3)  # 扣 2 分权重
    assert rec["score_pct"] == 87


def test_record_public_exempts_role_from_gaps(env):
    """公共库不强制挂主岗位：role flag 可为假，但不进必查盲区。"""
    pub = _full("p1", public=True, category="public", role_primary="")
    rec = skill_ledger._record(pub)
    assert rec["kind"] == "public"
    assert rec["flags"]["role"] is False
    assert rec["complete"] is True and "role" not in rec["gaps"]
    assert rec["gaps"] == []
    # role 缺失仍计入评分（权重 2）
    assert rec["score"] == pytest.approx(13 / 15, abs=1e-3)


def test_record_business_missing_role_is_gap(env):
    """对照：业务包缺 role 必须进 gaps（修复前两个常量都排除 role，会漏判）。"""
    rec = skill_ledger._record(_full("no_role", role_primary=""))
    assert rec["kind"] == "business"
    assert rec["flags"]["role"] is False
    assert "role" in rec["gaps"] and rec["complete"] is False


def test_record_contract_from_skill_md_sections(env, monkeypatch):
    """inputs/outputs 契约可由 skill.md 章节解析兜底（无显式 *_contract 字段）。"""
    monkeypatch.setattr(
        skill_ledger, "_skill_md_text",
        lambda src: "## 输入\n- 历史报价\n- 询价邮件\n\n## 产出\n- 报价单\n",
    )
    a = _empty("md_contract", source_path="pack://md")  # 无 inputs/outputs 字段
    assert skill_ledger._has_inputs_contract(a) is True
    assert skill_ledger._has_outputs_contract(a) is True
    rec = skill_ledger._record(a)
    assert rec["flags"]["inputs"] is True and rec["flags"]["outputs"] is True
    # 无 source_path 时不该去解析文件
    a_no_src = _empty("md_nosrc")
    assert skill_ledger._has_inputs_contract(a_no_src) is False


def test_record_source_and_samples_flags(env):
    # 有包目录但无原始输入样例 → source True / samples False
    r = skill_ledger._record(_full("no_sample", source_path="pack://x/nosample"))
    assert r["flags"]["source"] is True and r["flags"]["samples"] is False
    assert r["flags"]["source"] not in r["gaps"] and "samples" in r["gaps"]
    # 无源码目录 → source / samples 都 False，且 checklist 为空
    r2 = skill_ledger._record(_empty("no_src"))
    assert r2["flags"]["source"] is False and r2["flags"]["samples"] is False
    assert r2["checklist_count"] == 0


# ══════════════════════════════════════════════════════════════════════════
# 2. index 过滤 / 排序 / 分页
# ══════════════════════════════════════════════════════════════════════════
def _seed_mixed():
    """b1 满分 / b2 缺样例与源码 / b3 待审+零售+quality+缺 role / p1 公共库 / builtin。"""
    _full("b1", name="经营复盘", category_cn="制造业", role_primary="finance")
    _full("b2", name="报价核对", category_cn="制造业", role_primary="finance",
          source_path="pack://x/nosample")
    _empty("b3", name="Report ABC", category_cn="零售",
           status="pending_review")                    # 缺 role（业务包盲区）
    _full("p1", name="通用文案", public=True, category="public",
          category_cn="公共库", role_primary="")      # 真实公共库 category_cn=公共库
    _put("builtin1", name="通用助手", builtin=True)
    return ADMIN_CTX


def test_index_excludes_builtin(env):
    _seed_mixed()
    d = skill_ledger.index(ADMIN_CTX, limit=500)
    ids = {r["agent_id"] for r in d["items"]}
    assert "builtin1" not in ids
    assert d["total"] == 4 and d["count"] == 4          # b1,b2,b3,p1


def test_index_kind_filter(env):
    _seed_mixed()
    pub = skill_ledger.index(ADMIN_CTX, kind="public", limit=500)
    biz = skill_ledger.index(ADMIN_CTX, kind="business", limit=500)
    assert {r["agent_id"] for r in pub["items"]} == {"p1"}
    assert {r["agent_id"] for r in biz["items"]} == {"b1", "b2", "b3"}


def test_index_industry_and_status_filter(env):
    _seed_mixed()
    ind = skill_ledger.index(ADMIN_CTX, industry="制造业", limit=500)
    assert {r["agent_id"] for r in ind["items"]} == {"b1", "b2"}
    st = skill_ledger.index(ADMIN_CTX, status="pending_review", limit=500)
    assert {r["agent_id"] for r in st["items"]} == {"b3"}


def test_index_q_search_name_and_case_insensitive(env):
    _seed_mixed()
    assert {r["agent_id"] for r in skill_ledger.index(ADMIN_CTX, q="复盘", limit=500)["items"]} == {"b1"}
    # 英文大小写不敏感
    assert {r["agent_id"] for r in skill_ledger.index(ADMIN_CTX, q="abc", limit=500)["items"]} == {"b3"}


def test_index_q_search_by_role_name(env):
    _seed_mixed()
    d = skill_ledger.index(ADMIN_CTX, q="财务", limit=500)
    # b1/b2 的 role_name=财务；p1 无 role，不匹配
    assert {r["agent_id"] for r in d["items"]} == {"b1", "b2"}


def test_index_role_filter_keeps_public_and_matching_role(env):
    _seed_mixed()
    d = skill_ledger.index(ADMIN_CTX, role="财务", limit=500)
    # 本岗业务包(b1,b2,finance) + 公共库(p1)；b3(quality) 排除
    assert {r["agent_id"] for r in d["items"]} == {"b1", "b2", "p1"}
    # 中文名与 id 等价
    assert skill_ledger.index(ADMIN_CTX, role="finance", limit=500)["total"] == 3


def test_index_flag_gap_filter(env):
    _seed_mixed()
    miss_samples = skill_ledger.index(ADMIN_CTX, flag="samples", limit=500)
    assert {r["agent_id"] for r in miss_samples["items"]} == {"b2", "b3"}
    miss_role = skill_ledger.index(ADMIN_CTX, flag="role", limit=500)
    assert {r["agent_id"] for r in miss_role["items"]} == {"b3", "p1"}
    # 未知 flag 不筛选（放行全部）
    assert skill_ledger.index(ADMIN_CTX, flag="not_a_dim", limit=500)["total"] == 4


def test_index_sorted_by_score_ascending(env):
    _full("hi")                                     # score 1.0
    _empty("lo")                                    # score 0.0
    a = _empty("mid")
    a.update(system_prompt=LONG_PROMPT, role_primary="finance",
             inputs_contract="x", outputs_contract="y")   # 5 维 = 11/15
    d = skill_ledger.index(ADMIN_CTX, limit=500)
    scores = [r["score"] for r in d["items"]]
    assert scores == sorted(scores)
    assert d["items"][0]["agent_id"] == "lo" and d["items"][-1]["agent_id"] == "hi"


def test_index_pagination(env):
    for i in range(5):
        _empty(f"p{i}")
    d = skill_ledger.index(ADMIN_CTX, limit=2, offset=1)
    assert d["total"] == 5 and d["count"] == 2
    assert len(skill_ledger.index(ADMIN_CTX, limit=2, offset=4)["items"]) == 1


# ══════════════════════════════════════════════════════════════════════════
# 3. stats 聚合
# ══════════════════════════════════════════════════════════════════════════
def test_stats_empty_store_zero_structure(env):
    s = skill_ledger.stats(ADMIN_CTX)
    assert s["total"] == 0 and s["business"] == 0 and s["public"] == 0
    assert s["avg_score_pct"] == 0 and s["complete_count"] == 0
    assert s["by_industry"] == [] and s["by_role"] == [] and s["gap_top"] == []
    assert set(s["dimension_coverage"]) == {d["key"] for d in skill_ledger.DIMENSIONS}
    assert set(s["score_buckets"]) == {"0-40", "40-60", "60-80", "80-100"}
    assert all(v["covered"] == 0 for v in s["dimension_coverage"].values())


def test_stats_counts_industry_role_and_buckets(env):
    _full("f1", category_cn="制造业", role_primary="finance")     # 100 分（全覆盖）
    _empty("e1", category_cn="制造业", role_primary="finance")    # 13 分（仅 role 命中）
    _full("f2", category_cn="零售", role_primary="quality")       # 100 分（全覆盖）
    _full("p1", public=True, category="public", role_primary="")  # 公共库 87 分（缺 role）
    s = skill_ledger.stats(ADMIN_CTX)
    assert s["total"] == 4 and s["business"] == 3 and s["public"] == 1
    assert s["complete_count"] == 3                 # f1/f2 + 公共库 p1（豁免 role）
    assert s["avg_score_pct"] == int(round((100 + 13 + 100 + 87) / 4)) == 75
    # by_industry / by_role 只统计业务包（公共库不计入）
    inds = {r["category_cn"]: r["count"] for r in s["by_industry"]}
    assert inds == {"制造业": 2, "零售": 1}
    roles = {r["role_primary"]: r["count"] for r in s["by_role"]}
    assert roles == {"finance": 2, "quality": 1}
    # 评分分布求和 = 总数
    assert sum(s["score_buckets"].values()) == s["total"]
    assert s["score_buckets"]["0-40"] == 1 and s["score_buckets"]["80-100"] == 3
    # 维度覆盖：4 个包里 role 覆盖 3 个（p1 无 role）
    assert s["dimension_coverage"]["role"]["covered"] == 3
    assert s["dimension_coverage"]["prompt"]["covered"] == 3
    assert s["dimension_coverage"]["prompt"]["ratio"] == 0.75
    assert s["dimension_coverage"]["fewshot"]["opt_in"] is True


def test_stats_gap_top_sorted_and_counts(env):
    _empty("e1")           # 7 个必查盲区
    _empty("e2")           # 7 个必查盲区
    _full("f1")            # 0 盲区
    s = skill_ledger.stats(ADMIN_CTX)
    counter = {g["key"]: g["count"] for g in s["gap_top"]}
    # 两个空包 → 每个必查维度各缺 2 次；opt_in 不进 gap_top
    assert all(counter[k] == 2 for k in skill_ledger._BUSINESS_REQUIRED)
    assert "fewshot" not in counter and "materials" not in counter
    counts = [g["count"] for g in s["gap_top"]]
    assert counts == sorted(counts, reverse=True)     # 按缺失数降序


# ══════════════════════════════════════════════════════════════════════════
# 4. detail 明细
# ══════════════════════════════════════════════════════════════════════════
def test_detail_full_fields(env):
    _full("d1")
    rec = skill_ledger.detail(ADMIN_CTX, "d1")
    assert rec is not None and rec["agent_id"] == "d1"
    # 覆盖度字段透传
    assert rec["score"] == 1.0 and rec["gaps"] == [] and rec["complete"] is True
    # 明细扩展
    assert rec["checklist"] == ["历史报价", "询价邮件"]
    assert {s["kind"] for s in rec["samples"]} == {"input", "output"}
    assert {s["filename"] for s in rec["samples"]} == {"input.csv", "out.html"}
    assert rec["reference_specs"] == [{"name": "领域规范", "chars": 100}]
    assert rec["acceptance"] == "验收标准"
    assert rec["fewshot_version"] == "fs-1"
    assert rec["hard_rules_version"] == "hr-2"
    assert rec["reference_specs_version"] == "rs-3"
    assert rec["source_path"] == "pack://full" and rec["pack_found"] is True


def test_detail_returns_none_for_missing_builtin_and_invisible(env):
    _full("ok")
    _put("bi", builtin=True)
    _put("secret", tenant_id="t_private", knowledge_scope="department",
         dept_id="d_private", registered_by="u_secret")
    intruder = CallerContext(userId="u_x", deptId="d_x", role="employee", tenantId="t_public")
    assert skill_ledger.detail(ADMIN_CTX, "nope") is None
    assert skill_ledger.detail(ADMIN_CTX, "bi") is None            # builtin 不进台账
    assert skill_ledger.detail(intruder, "secret") is None          # 跨租户不可见
    assert skill_ledger.detail(ADMIN_CTX, "secret") is not None     # 超管全量


# ══════════════════════════════════════════════════════════════════════════
# 5. 可见域（复用 agent_store._visible）
# ══════════════════════════════════════════════════════════════════════════
def _seed_visibility():
    _full("platform_pub", tenant_id="", knowledge_scope="global")            # 全可见
    _empty("private_a", tenant_id="t_a", knowledge_scope="department",
           dept_id="d_a", registered_by="u_a")                               # 仅 t_a / u_a
    _empty("dept_b", tenant_id="", knowledge_scope="department",
           dept_id="d_b", registered_by="bootstrap")                          # 仅 d_b
    _put("builtin_v", builtin=True)


def test_visible_domain_employee_and_admin(env):
    _seed_visibility()
    emp_a = CallerContext(userId="u_a", deptId="d_a", role="employee", tenantId="t_a")
    emp_b = CallerContext(userId="u_x", deptId="d_x", role="employee", tenantId="t_b")
    # emp_a：平台包 + 自己注册/本部门私有包（dept_b 跨部门不可见）
    ids_a = {r["agent_id"] for r in skill_ledger.index(emp_a, limit=500)["items"]}
    assert "platform_pub" in ids_a and "private_a" in ids_a
    assert "dept_b" not in ids_a and "builtin_v" not in ids_a
    # emp_b：只看到平台共享包（private_a 跨租户、dept_b 跨部门都不可见）
    ids_b = {r["agent_id"] for r in skill_ledger.index(emp_b, limit=500)["items"]}
    assert ids_b == {"platform_pub"}
    # platform_admin：全量（跨租户也可见）
    ids_admin = {r["agent_id"] for r in skill_ledger.index(ADMIN_CTX, limit=500)["items"]}
    assert ids_admin == {"platform_pub", "private_a", "dept_b"}
    # stats 亦遵循可见域
    assert skill_ledger.stats(emp_b)["total"] == 1
    assert skill_ledger.stats(ADMIN_CTX)["total"] == 3


# ══════════════════════════════════════════════════════════════════════════
# 6. 路由层（HTTP）
# ══════════════════════════════════════════════════════════════════════════
def test_http_stats_route_precedes_param_route(env):
    """/skills/stats 必须先于 /skills/{agent_id} 注册，否则被当 agent_id 吞掉→404。"""
    _full("h1")
    r = client.get(f"{API}/stats", headers=ADMIN)
    assert r.status_code == 200, r.text[:300]
    d = r.json()["data"]
    assert r.json()["code"] == 0
    assert "dimension_coverage" in d and "by_industry" in d and "score_buckets" in d
    assert d["total"] == 1


def test_http_index_and_detail_envelope(env):
    _full("h1")
    r = client.get(API, headers=ADMIN)
    assert r.status_code == 200 and r.json()["code"] == 0
    d = r.json()["data"]
    assert d["total"] == 1 and d["count"] == 1 and d["items"][0]["agent_id"] == "h1"
    # query 参数透传
    assert client.get(API, params={"kind": "public"}, headers=ADMIN).json()["data"]["total"] == 0
    assert client.get(API, params={"flag": "role"}, headers=ADMIN).json()["data"]["total"] == 0
    rd = client.get(f"{API}/h1", headers=ADMIN)
    assert rd.status_code == 200 and rd.json()["code"] == 0
    assert "checklist" in rd.json()["data"] and "samples" in rd.json()["data"]


def test_http_requires_auth(env):
    _full("h1")
    assert client.get(f"{API}/stats").status_code == 401
    assert client.get(API).status_code == 401


def test_http_detail_404_not_distinguishing(env):
    """不存在 / 无权限 / builtin 都统一 404，避免信息泄露。"""
    _full("ok")
    _put("bi", builtin=True)
    _put("secret", tenant_id="t_private", knowledge_scope="department",
         dept_id="d_private", registered_by="u_secret")
    assert client.get(f"{API}/nope", headers=ADMIN).status_code == 404
    assert client.get(f"{API}/bi", headers=ADMIN).status_code == 404
    assert client.get(f"{API}/secret", headers=EMP_B).status_code == 404
    assert client.get(f"{API}/secret", headers=ADMIN).status_code == 200


def test_http_employee_visibility_via_token(env):
    _seed_visibility()
    a = client.get(API, headers=EMP_A).json()["data"]
    b = client.get(API, headers=EMP_B).json()["data"]
    ids_a = {r["agent_id"] for r in a["items"]}
    ids_b = {r["agent_id"] for r in b["items"]}
    assert "private_a" in ids_a and "platform_pub" in ids_a
    assert ids_b == {"platform_pub"}
