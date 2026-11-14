"""P1-4 / #299 · 提示词模板外置 + 模式矩阵。

校验：
1. 旧契约不变：zh_prompts.render 已知场景渲染含变量与「简体中文」；未知场景/缺变量抛 KeyError；
   available() 含全部 7 场景。
2. 外置 .tpl 等价渲染：default 7 场景各自渲染正确，且都复现「简体中文」安全节。
3. 模式 × 场景矩阵：strict 覆盖场景(qa)命中覆盖并带「模式提醒」；未覆盖场景(summarize)回退 default。
4. compose() 固定组装序：主提示→系统提醒→模式提醒→身份与记忆→历史。
5. matrix() 结构正确。
6. template_source() 返回源码（含 Jinja2 语法）。
"""
import json
import pytest

from app.services import zh_prompts
from app.prompts import loader


# 各场景的合法变量样例（值去重便于断言）
_SCENE_VARS = {
    "summarize": {"context": "CTX_SUM", "input": "IN_SUM"},
    "extract": {"context": "CTX_EXT", "input": "IN_EXT"},
    "classify": {"labels": "LBL_A/LBL_B", "input": "IN_CLS"},
    "qa": {"context": "CTX_QA", "input": "IN_QA"},
    "rewrite": {"input": "IN_RW", "instruction": "INS_RW"},
    "compliance": {"context": "CTX_CMP", "input": "IN_CMP"},
    "table": {"context": "CTX_TBL", "input": "IN_TBL"},
}
_ALL_SCENES = list(_SCENE_VARS.keys())


# ── 1. 旧契约不变 ──────────────────────────────────────────────────────────
def test_zh_prompts_render_known_contains_vars_and_constraint():
    out = zh_prompts.render("summarize", context="资料内容", input="请摘要")
    assert "资料内容" in out
    assert "请摘要" in out
    assert "简体中文" in out


def test_zh_prompts_render_unknown_raises_keyerror():
    with pytest.raises(KeyError):
        zh_prompts.render("nope", input="x")


def test_zh_prompts_render_missing_var_raises_keyerror():
    with pytest.raises(KeyError):  # classify 缺 labels
        zh_prompts.render("classify", input="x")


def test_zh_prompts_available_all_seven():
    tasks = zh_prompts.available()
    for t in _ALL_SCENES:
        assert t in tasks
    assert len(tasks) == 7


# ── 2. 外置 .tpl 等价渲染 + 安全节复现 ──────────────────────────────────────
@pytest.mark.parametrize("scene", _ALL_SCENES)
def test_default_scene_renders_and_reproduces_constraint(scene):
    out = loader.render(scene, "default", **_SCENE_VARS[scene])
    for v in _SCENE_VARS[scene].values():
        assert v in out
    # 安全四节之一（中文输出约束）稳定复现
    assert "简体中文" in out


def test_default_scene_unknown_raises_keyerror():
    with pytest.raises(KeyError):
        loader.render("ghost", "default", input="x")


def test_default_scene_missing_var_raises_keyerror():
    with pytest.raises(KeyError):  # qa 缺 context
        loader.render("qa", "default", input="x")


# ── 3. 模式 × 场景矩阵：覆盖与回退 ─────────────────────────────────────────
def test_strict_override_qa_includes_mode_reminder():
    out = loader.render("qa", "strict", **_SCENE_VARS["qa"])
    # 命中 strict/zh_qa.tpl 覆盖：既含安全节也含模式提醒
    assert "简体中文" in out
    assert "模式提醒" in out
    assert _SCENE_VARS["qa"]["context"] in out


def test_strict_fallback_summarize_uses_default_no_mode_reminder():
    out = loader.render("summarize", "strict", **_SCENE_VARS["summarize"])
    # 未覆盖 → 回退 default：有安全节，但无模式提醒
    assert "简体中文" in out
    assert "模式提醒" not in out


def test_unknown_mode_raises_keyerror():
    with pytest.raises(KeyError):
        loader.render("qa", "lax", **_SCENE_VARS["qa"])


# ── 4. compose() 固定组装序 ─────────────────────────────────────────────────
def test_compose_fixed_order():
    out = loader.compose(
        main="M_MAIN",
        system_reminder="S_SYS",
        mode_reminder="M_MODE",
        identity_memory="I_MEM",
        history="H_HIST",
    )
    # 顺序：主提示 → 系统提醒 → 模式提醒 → 身份与记忆 → 历史
    assert out.index("【主提示】") < out.index("【系统提醒】")
    assert out.index("【系统提醒】") < out.index("【模式提醒】")
    assert out.index("【模式提醒】") < out.index("【身份与记忆】")
    assert out.index("【身份与记忆】") < out.index("【历史】")
    for v in ("M_MAIN", "S_SYS", "M_MODE", "I_MEM", "H_HIST"):
        assert v in out


def test_compose_skips_empty_sections():
    out = loader.compose(main="ONLY_MAIN", system_reminder="", mode_reminder="",
                         identity_memory="", history="")
    assert "ONLY_MAIN" in out
    assert "【系统提醒】" not in out
    assert "【历史】" not in out


# ── 5. matrix() 结构 ───────────────────────────────────────────────────────
def test_matrix_structure():
    m = loader.matrix()
    assert set(m["modes"]) == {"default", "strict"}
    assert m["scenes"] == sorted(_ALL_SCENES)
    assert m["matrix"]["default"] == sorted(_ALL_SCENES)
    # strict 仅覆盖 qa（zh_qa.tpl）
    assert m["matrix"]["strict"] == ["qa"]


# ── 6. template_source() 返回源码 ──────────────────────────────────────────
def test_template_source_contains_jinja_and_constraint():
    src = loader.template_source("summarize", "default")
    assert "{{ context }}" in src
    assert "{% include '_out_constraint.tpl' %}" in src


def test_template_source_unknown_scene_raises():
    with pytest.raises(KeyError):
        loader.template_source("ghost")
