"""M52 · 技能包的「输入清单」与「包内样例」。

问题现场：用户测试技能，模型第一句就是「请提供输入清单」——而清单和样例都是包作者
已经放在包里的东西。三个断点：
1. `## Inputs`（英文标题）不在别名表里，整段落进 other，`inputs_contract` 为空；
2. 前端只显示一句自由文本，没法变成可核对的清单；
3. 113/115 个包目录里带原始输入（客户邮件、材料价格表、历史台账），平台从头到尾没读过。

这一层守住：
- 115 个包全部能解析出输入契约、流程与规则（不许再有静默丢段）；
- 办公角色包（skill.md 在 03-ji-neng-bao/<拼音>/）也能定位到上两级的素材目录；
- 样例按 readme 文件清单分类，只有「原始输入」会被自动挂载，交付物不挂；
- 带数据的轮次不进语义缓存（按问题文本做键会把 A 公司的答案复用给 B 公司）。
"""
import os

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_dev_token
from app.main import app
from app.services import agent_store, semantic_cache, skill_pack

client = TestClient(app)

ADMIN = {"Authorization": create_dev_token("p_admin", "d_platform", "platform_admin")}
EMP = {"Authorization": create_dev_token("u_1024", "d_sales", "employee")}

# 真实包路径（仓库内置 backend/solutions，与交付环境一致）
MFG_PACK = "01_制造业/01_图纸询价智能报价/skill.md"
OFFICE_PACK = "04_角色/HR/招聘漏斗分析/03-ji-neng-bao/zhao-pin-lou-dou-fen-xi/skill.md"


@pytest.fixture
def clean_store():
    backup = dict(agent_store._AGENTS)
    agent_store._AGENTS.clear()
    yield
    agent_store._AGENTS.clear()
    agent_store._AGENTS.update(backup)


@pytest.fixture
def mfg_skill(clean_store):
    return agent_store.save({
        "agent_id": "agt_m52_quote",
        "name": "图纸询价智能报价",
        "adapter_type": "llm",
        "description": "客户图纸询价当天回价",
        "knowledge_scope": "global",
        "status": "published",
        "category": "industry", "category_cn": "制造业", "sub_function": "",
        "system_prompt": "你是「图纸询价智能报价」执行专家。",
        "source_path": MFG_PACK,
        "inputs_contract": "",
        "outputs_contract": "",
        "tenant_id": "",
    })


# ── 1. 解析层：不再丢段 ────────────────────────────────────────────────────
def test_english_inputs_header_is_parsed():
    """`## Inputs` 是包作者的主流写法之一，认不出来就等于没有输入契约。"""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts"))
    from import_solutions import parse_skill

    d = parse_skill(os.path.join(skill_pack.solutions_root(), *MFG_PACK.split("/")))
    assert d["inputs_contract"].strip(), "英文 Inputs 章节必须进输入契约"
    assert "客户询价邮件" in d["inputs_contract"]
    assert "## 输入契约" in d["system_prompt"]
    # 流程段不能被「每步都有明确产出」里的“产出”抢走（关键词优先级）
    assert "## 工作流程" in d["system_prompt"]
    assert "## 硬规则" in d["system_prompt"]


def test_all_packs_have_inputs_contract():
    """全部包一个都不能空：空契约 = 试跑时模型必然反问用户要材料。"""
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts"))
    from import_solutions import scan

    defs = scan()
    n = len(defs)
    # 不写死包数（M53 从 115 扩到 203）：只要求不比 M52 少，且段命中率按比例
    assert n >= 115, f"包源不该比扩容前还少，实际 {n}"
    empty_in = [d["name"] for d in defs if not d["inputs_contract"].strip()]
    empty_out = [d["name"] for d in defs if not d["outputs_contract"].strip()]
    assert empty_in == [], f"输入契约为空的包：{empty_in}"
    assert len(empty_out) <= max(1, n // 100), f"产出契约缺失过多：{empty_out}"
    # 流程/规则命中率是「段有没有被正确归档」的直接指标
    assert sum(1 for d in defs if d["has_workflow"]) >= int(n * 0.9)
    assert sum(1 for d in defs if d["has_rules"]) >= int(n * 0.6)


def test_section_alias_variants():
    """逐条守住历史上真实出现过的标题变体。"""
    cases = {
        "Inputs": "inputs", "二、Inputs（输入）": "inputs", "输入（原始数据源）": "inputs",
        "Outputs": "outputs", "3. 输出": "outputs", "输出格式说明（Excel 交付）": "outputs",
        "Workflow（6 步，每步都有明确产出）": "workflow",
        "执行步骤（内部流程）": "workflow", "4. 工作流（5 步）": "workflow",
        "关键规则 / 提示": "rules", "计算规则(写死,不可自行变更)": "rules",
        "六、输出前自检清单": "rules", "七、禁止事项": "rules",
        "不依赖的前提": "disclaimer", "边界（不承诺什么）": "disclaimer",
        "一句话定位": "position", "触发条件": "scene", "适用场景清单": "scene",
        "内嵌提示词（主提示词要点）": "prompt_entry",
    }
    for title, want in cases.items():
        assert skill_pack.classify_title(title) == want, f"{title} → {want}"


# ── 2. 包目录定位与样例分类 ────────────────────────────────────────────────
def test_pack_dir_walks_up_for_office_pack():
    """办公角色包的 skill.md 在 03-ji-neng-bao/<拼音>/，素材在上两级。"""
    d = skill_pack.pack_dir(OFFICE_PACK)
    assert d and os.path.basename(d) == "招聘漏斗分析"
    assert any(f.endswith(".csv") for f in os.listdir(d))


def test_samples_classified_by_readme():
    """readme 的文件清单写明「原始输入 / 交付物」，按它分类，别猜。

    包目录里文件名有两种形态：开发机上是中文原名（客户询价邮件.md），仓库分发版是
    拼音（ke-hu-xun-jia-you-jian.md）。readme 只写中文，两种都要能对上。
    """
    files = skill_pack.samples(MFG_PACK)
    inputs = [f["filename"] for f in files if f["kind"] == "input"]
    outputs = [f["filename"] for f in files if f["kind"] == "output"]
    assert len(inputs) == 4, inputs
    assert any("xun-jia" in n or "询价" in n for n in inputs)      # 客户询价邮件
    assert any("cai-liao" in n or "材料" in n for n in inputs)      # 材料价格表
    assert len(outputs) == 1 and ("bao-jia-dan" in outputs[0] or "报价单" in outputs[0])
    kinds = {f["filename"]: f["kind"] for f in files}
    assert kinds["skill.md"] == "meta"
    # 磁盘上是拼音名，readme 里是中文名：还原中文名给用户看、也喂给模型
    disp = {f["filename"]: f["display_name"] for f in files}
    assert "历史报价" in disp["li-shi-bao-jia.xlsx"]
    assert "图纸询价报价单" in disp["tu-zhi-xun-jia-bao-jia-dan.xlsx"]
    # 提示词文件是「学员该发什么」，不是待处理数据，绝不能当输入喂回去
    assert all(f["kind"] == "meta" for f in files
               if "zhu-ti-shi-ci" in f["filename"] or "提示词" in f["filename"])


def test_input_samples_exclude_outputs_and_prompt_files():
    names = [i["filename"] for i in skill_pack.input_samples(MFG_PACK)]
    assert names, "包里有原始输入，必须能取到"
    assert not any("bao-jia-dan" in n or "报价单" in n for n in names), "交付物不能当输入喂回去"
    assert not any("zhu-ti-shi-ci" in n or "提示词" in n for n in names)


def test_sample_bytes_blocks_path_traversal():
    assert skill_pack.sample_bytes("../01_制造业/01_图纸询价智能报价/skill.md", MFG_PACK) is not None
    assert skill_pack.sample_bytes("../../../../etc/passwd", MFG_PACK) is None
    assert skill_pack.sample_bytes("不存在.csv", MFG_PACK) is None


# ── 3. 接口：前端能拿到「要给什么」 ────────────────────────────────────────
def test_inputs_endpoint_returns_checklist_and_samples(mfg_skill):
    r = client.get("/api/v1/agents/agt_m52_quote/inputs", headers=EMP)
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["can_run_with_samples"] is True
    assert len(d["checklist"]) >= 3
    assert any("询价邮件" in c or "询价" in c for c in d["checklist"])
    assert d["input_sample_count"] >= 3
    assert d["pack_found"] is True


def test_inputs_endpoint_404_for_unknown_and_cross_tenant(clean_store):
    assert client.get("/api/v1/agents/nope/inputs", headers=EMP).status_code == 404
    agent_store.save({
        "agent_id": "agt_m52_other_tenant", "name": "别家的包", "adapter_type": "llm",
        "description": "", "knowledge_scope": "global", "status": "published",
        "system_prompt": "x", "source_path": MFG_PACK, "tenant_id": "t_other",
    })
    assert client.get("/api/v1/agents/agt_m52_other_tenant/inputs",
                      headers=EMP).status_code == 404


def test_inputs_endpoint_handles_pack_without_samples(clean_store):
    """没有样例也要能答：清单给出来，并说明试跑得自己传文件。"""
    agent_store.save({
        "agent_id": "agt_m52_nopack", "name": "落库技能", "adapter_type": "llm",
        "description": "", "knowledge_scope": "global", "status": "published",
        "system_prompt": "x", "source_path": "99_不存在/xx/skill.md",
        "inputs_contract": "- 本月流水\n- 目标数字",
    })
    d = client.get("/api/v1/agents/agt_m52_nopack/inputs", headers=ADMIN).json()["data"]
    assert d["can_run_with_samples"] is False
    assert d["checklist"] == ["本月流水", "目标数字"]
    assert "自己上传" in d["hint"]


# ── 4. 试跑：一条开关把包里的数据挂上去 ────────────────────────────────────
def test_invoke_use_samples_attaches_pack_files(mfg_skill, monkeypatch):
    monkeypatch.setattr(semantic_cache, "get", lambda *a, **k: None)
    monkeypatch.setattr(semantic_cache, "put", lambda *a, **k: None)
    r = client.post("/api/v1/agent/invoke", headers=EMP,
                    json={"agent_id": "agt_m52_quote", "input": "按技能包报价",
                          "stream": False, "use_samples": True, "output_formats": ["html"]})
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    used = [s["filename"] for s in d["samples_used"]]
    assert used, "开了开关却没挂任何样例"
    assert any("询价" in n or "xun-jia" in n for n in used), used
    # 附件名要用中文原名：拼音名模型认不出，会把已附上的资料判成「未提供」
    assert any("历史报价" in n for n in used), used
    assert not any("报价单" in n for n in used), "交付物不能被当成输入挂上去"
    # 附件确实进了这一次的解析上下文（不是只回了条 metadata）
    assert {f["filename"] for f in d["files_used"]} >= set(used)


def test_invoke_without_use_samples_stays_clean(mfg_skill):
    r = client.post("/api/v1/agent/invoke", headers=EMP,
                    json={"agent_id": "agt_m52_quote", "input": "你好", "stream": False})
    assert r.status_code == 200
    assert r.json()["data"]["samples_used"] == []


def test_data_turns_skip_semantic_cache(monkeypatch):
    """带数据的轮次不进语义缓存：按问题文本做键，会把上一家的账套结果复用给下一家。"""
    put_calls = []
    monkeypatch.setattr(semantic_cache, "get", lambda *a, **k: None)
    monkeypatch.setattr(semantic_cache, "put", lambda *a, **k: put_calls.append(a))
    from app.routers import invoke as invoke_router

    class Req:
        agent_id = "agt_x"
        input = "按技能包处理附件"
        file_ids = []
        use_samples = True

    invoke_router._m27_postprocess(Req(), {"name": "x"}, {"answer": "a", "citations": [],
                                                          "usage": {"real": True}},
                                  type("C", (), {"userId": "u", "deptId": "d",
                                                 "role": "employee", "tenantId": ""})(),
                                  "run_1", 1, {"blocked": False, "hits": []})
    assert put_calls == [], "带样例的轮次不应写进语义缓存"
