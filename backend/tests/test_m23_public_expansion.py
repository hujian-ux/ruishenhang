"""M23 · 公共库扩充（从「10 条」到「一整套办公室常用技能」）。

守的是**筛选口径**，不只是条数：
1. 数量上限：20-25 条。再多就变成第二个「不知道点哪个」；
2. 分组结构：≤6 组、每组 3-5 条，同组条目必须连续（前端按出现顺序分组，
   交叉会出现两个「写作」标题）；
3. 每条必须有真提示词（Markdown 分节 ≥3）和明确产出格式；
4. 口径不变：公共库不进 115、不挂职业标签；
5. 岗位收窄后仍带全公共库。
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

# 数量红线：低于 20 说明覆盖不全，高于 25 说明开始堆同质条目
MIN_SKILLS, MAX_SKILLS = 20, 25
GROUP_MIN, GROUP_MAX = 3, 5


@pytest.fixture(scope="module")
def seeded():
    before = set(agent_store._AGENTS)
    agent_store.ensure_public_skills()
    agent_store.ensure_builtin_assistant()
    yield
    for aid in list(agent_store._AGENTS):
        if aid not in before:
            agent_store._AGENTS.pop(aid, None)


def test_count_within_cap():
    """条数落在窗口内：够用，但不至于挑花眼。"""
    n = len(public_skills.PUBLIC_SKILLS)
    assert MIN_SKILLS <= n <= MAX_SKILLS, f"公共库 {n} 条，超出 {MIN_SKILLS}-{MAX_SKILLS}"


def test_groups_balanced():
    """分组不超过 6 组，每组 3-5 条，且 covered=全部条目（不许有漏网的孤立组）。"""
    counters = {g: 0 for g in public_skills.PUBLIC_GROUPS}
    for s in public_skills.PUBLIC_SKILLS:
        assert s["sub_function"] in counters, f"{s['name']} 的分组未登记：{s['sub_function']}"
        counters[s["sub_function"]] += 1

    assert len(public_skills.PUBLIC_GROUPS) <= 6
    for g, n in counters.items():
        assert GROUP_MIN <= n <= GROUP_MAX, f"「{g}」{n} 条，应为 {GROUP_MIN}-{GROUP_MAX}"
    assert sum(counters.values()) == len(public_skills.PUBLIC_SKILLS)


def test_group_entries_are_contiguous():
    """同组条目必须连着写。

    前端 public 列表按 sort 排序后**按出现顺序分组**，一旦出现
    写作→演示→写作，页面上就会冒出两个「写作」标题。
    """
    seq = [s["sub_function"] for s in public_skills.PUBLIC_SKILLS]
    compact = [g for i, g in enumerate(seq) if i == 0 or g != seq[i - 1]]
    dup = {g for g in compact if compact.count(g) > 1}
    assert not dup, f"分组被拆散了：{dup}；实际顺序 {compact}"


def test_keys_and_names_unique():
    keys = [s["key"] for s in public_skills.PUBLIC_SKILLS]
    names = [s["name"] for s in public_skills.PUBLIC_SKILLS]
    assert len(keys) == len(set(keys))
    assert len(names) == len(set(names))


def test_every_skill_has_real_prompt():
    """不是空壳：至少 3 个 Markdown 二级分节 + 350 字，否则渲染器切不出页/表。"""
    for s in public_skills.PUBLIC_SKILLS:
        p = s["system_prompt"]
        assert p.count("## ") >= 3, f"{s['name']} 分节不足，出不了多页产物"
        assert len(p) >= 350, f"{s['name']} 提示词过短"


def test_format_matches_group():
    """产出格式跟着活走：表格组必须 xlsx，演示组以 pptx 为主。

    「汇报框架」是例外：它交付的是能直接贴进邮件的汇报正文（docx），
    要 PPT 是 pub_ppt 的活。这类例外只许写在这里，不许悄悄放宽。
    """
    docx_in_demo = {"汇报框架"}
    pptx = 0
    for s in public_skills.PUBLIC_SKILLS:
        assert s["default_format"] in {"docx", "pptx", "xlsx", "pdf", "html"}
        if s["sub_function"] == "表格":
            assert s["default_format"] == "xlsx", f"{s['name']} 是表格活却出 {s['default_format']}"
        if s["sub_function"] == "演示":
            if s["default_format"] == "pptx":
                pptx += 1
            else:
                assert s["name"] in docx_in_demo, f"{s['name']} 是演示活却出 {s['default_format']}"
    assert pptx >= 2, "演示组至少要能真出 PPT"


def test_basics_still_present():
    """最早那批基础技能不能被换掉：它们是「没选岗位也能开工」的兜底。"""
    names = {s["name"] for s in public_skills.PUBLIC_SKILLS}
    for must in ("文案生成", "PPT 制作", "表格制作", "会议纪要", "文件信息提取"):
        assert must in names, f"基础技能丢了：{must}"


def test_new_skills_have_no_old_pack_dup():
    """公共库不许直接复制 115 业务包的名字——那会让员工看到两个同名入口。

    例外：业务包确实叫这个名、但公共库版本是通用化的，用白名单排除。
    """
    allow = {"方案初稿组装（带出处）"}
    pack_names = set()
    try:
        import json
        from pathlib import Path
        defs = json.loads((Path(__file__).parents[1] / "reports" / "defs.json").read_text("utf-8"))
        pack_names = {d["name"] for d in defs}
    except Exception:  # 没跑过导入的干净环境，跳过这条
        pytest.skip("defs.json 不可读")

    clash = {s["name"] for s in public_skills.PUBLIC_SKILLS if s["name"] in pack_names} - allow
    assert not clash, f"公共库与业务包重名：{clash}"


def test_public_total_still_out_of_115(seeded):
    """口径红线：23 条公共库不能让 115 变成 138。"""
    business = [a for a in agent_store._AGENTS.values()
                if not agent_store.is_public(a) and not a.get("builtin")]
    d = client.get("/api/v1/agents/catalog", headers=ADMIN).json()["data"]
    assert d["total"] == len(business)
    assert d["public_total"] == len(public_skills.PUBLIC_SKILLS)
    assert "公共库" not in [c["category_cn"] for c in d["categories"]]


def test_employee_gets_all_public(seeded):
    """普通员工（无岗位）也能看到全部 23 条，不能被 scope 挡住。"""
    d = client.get("/api/v1/agents?category=public", headers=EMP).json()["data"]["agents"]
    assert len(d) == len(public_skills.PUBLIC_SKILLS)


def test_role_view_carries_whole_public_library(seeded):
    """选了岗位也要带着整套公共库：写总结、做排期不该因为选了财务就消失。"""
    d = client.get("/api/v1/agents/roles?role=finance", headers=ADMIN).json()["data"]
    assert len(d["public"]) == len(public_skills.PUBLIC_SKILLS)
    assert {p["sub_function"] for p in d["public"]} == set(public_skills.PUBLIC_GROUPS)
