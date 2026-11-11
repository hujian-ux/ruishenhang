"""M55 · 把方案库「验收清单 + 领域规范」揉进技能：提取/挂载/注入测试。

不依赖 D 盘真实语料（CI 无 D 盘），全部用临时目录构造样例，验证：
  - extract_acceptance 抽关键追问+验收清单、无则返回 None；
  - collect_reference_specs 跳过元信息 md、收领域规范 md；
  - attach_materials 幂等挂版本号、空则清除；
  - compose_materials 受 MATERIALS_ENABLED 控制，开启注入且标注「范本非数据」。
"""
import os
import tempfile

import pytest

from app.services import solution_materials as sm


def _write(root: str, rel: str, text: str) -> str:
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


def _pack(tmp):
    # 元信息文件（应被跳过）
    _write(tmp, "完整提示词.md", "# X 主提示词\n## 使用说明\n1. 启用技能\n\n"
            "## 本案例的关键追问（拿到结果后必须问）\n1. 为什么归A？期望：按E1。\n"
            "## 验收清单\n- [ ] 输出9列\n- [ ] 无授信结论\n")
    _write(tmp, "zhu-ti-shi-ci.md", "# 冗余副本\n## 主提示词\n一样的内容\n")
    _write(tmp, "页面内容.md", "# 营销稿\n无关\n")
    _write(tmp, "readme.md", "# readme\n说明\n")
    _write(tmp, "03-ji-neng-bao/skill.md", "---\nname: x\n---\n# skill\n")
    # 领域规范（应被收为 reference_specs）
    _write(tmp, "赛道清单与边界.md", "# 边界\nS1 半导体：纳入①…\nE1 裁决：…\n")
    _write(tmp, "字段映射规则.md", "# 字段\n映射规则明细\n")
    return tmp


def test_extract_acceptance_picks_sections():
    with tempfile.TemporaryDirectory() as tmp:
        _pack(tmp)
        acc = sm.extract_acceptance(tmp)
        assert acc and "关键追问" in acc and "验收清单" in acc
        # 强制剔除 ## 防误切页
        assert "## " not in acc
        assert "＃＃" in acc


def test_extract_acceptance_none_without_sections():
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, "完整提示词.md", "# X\n## 使用说明\n1. 启用\n## 主提示词\n复制即用\n")
        assert sm.extract_acceptance(tmp) is None


def test_collect_reference_specs_skips_meta():
    with tempfile.TemporaryDirectory() as tmp:
        _pack(tmp)
        specs = sm.collect_reference_specs(tmp)
        names = [s["name"] for s in specs]
        assert "赛道清单与边界.md" in names
        assert "字段映射规则.md" in names
        # 元信息文件全部跳过
        for meta in ("完整提示词.md", "zhu-ti-shi-ci.md", "页面内容.md", "readme.md", "skill.md"):
            assert meta not in names
        # 截断生效
        big = "# 大规范\n" + ("x" * 5000)
        _write(tmp, "超大规范.md", big)
        specs2 = sm.collect_reference_specs(tmp)
        big_spec = [s for s in specs2 if s["name"] == "超大规范.md"][0]
        assert big_spec["chars"] > 5000  # 原文未被截断统计
        assert len(big_spec["content"]) <= sm._MAX_SPEC_CHARS + 50


def test_attach_materials_idempotent_and_clear():
    agent = {"agent_id": "ag_test"}
    sm.attach_materials(agent, "验收标准块", [{"name": "a.md", "content": "x", "chars": 1}])
    assert agent["acceptance"] == "验收标准块"
    assert agent["acceptance_version"]
    assert agent["reference_specs_version"]
    # 清空：传入 None / [] 应移除字段
    sm.attach_materials(agent, None, [])
    assert "acceptance" not in agent
    assert "reference_specs" not in agent


def test_compose_materials_opt_in(monkeypatch):
    agent = {"acceptance": "＃＃ 验收清单\n- [ ] 9列",
             "reference_specs": [{"name": "边界.md", "content": "S1…", "chars": 10}]}
    base = "你是执行专家。"
    # 默认关 → 不注入
    monkeypatch.setattr("app.core.config.settings.MATERIALS_ENABLED", False)
    assert sm.compose_materials(base, agent) == base
    # 开启 → 注入且标注「范本非数据」「自检」
    monkeypatch.setattr("app.core.config.settings.MATERIALS_ENABLED", True)
    out = sm.compose_materials(base, agent)
    assert "交付验收自检" in out and "验收清单" in out
    assert "标准模板参考" in out and "非本次用户数据" in out and "边界.md" in out
    # 无材料 → 即便开启也不改
    assert sm.compose_materials(base, {}) == base
