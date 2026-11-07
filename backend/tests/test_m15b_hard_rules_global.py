"""M15/M25 P1 · 平台硬规则全局统一（业务包补齐）。"""
import pytest

from app.services import agent_store, hard_rules
from app.services.hard_rules import HARD_RULES_HEADER


def test_build_system_prompt_includes_platform_hard_rules():
    import import_solutions
    sec = {
        "scene": "演示场景", "inputs": "给数据", "workflow": "1. 读 2. 算",
        "outputs": "出表", "rules": "不许删行", "other": "",
    }
    sp = import_solutions.build_system_prompt("测试技能", "描述", sec)
    assert HARD_RULES_HEADER in sp
    # 业务包自有章节仍在
    assert "## 适用场景" in sp and "## 通用红线" in sp


def test_ensure_hard_rules_idempotent():
    body = "你是专家。\n## 适用场景\nx"
    once = hard_rules.ensure_hard_rules(body)
    twice = hard_rules.ensure_hard_rules(once)
    assert once == twice
    assert once.count(HARD_RULES_HEADER) == 1


def test_has_and_needs_hard_rules():
    assert hard_rules.has_hard_rules(hard_rules.ensure_hard_rules("x")) is True
    assert hard_rules.needs_hard_rules("没有硬规则的提示词") is True
    assert hard_rules.needs_hard_rules(hard_rules.ensure_hard_rules("x")) is False


def test_backfill_patches_and_is_idempotent():
    # 注入一个缺硬规则的业务包 + 一个已含的 + 一个公共库（应跳过）
    agent_store.save({
        "agent_id": "ag_biz_missing", "name": "缺规则", "category": "制造",
        "category_cn": "制造业", "status": "published", "builtin": False,
        "system_prompt": "你是制造专家。\n## 适用场景\nx",
    })
    agent_store.save({
        "agent_id": "ag_biz_ok", "name": "已有规则", "category": "零售",
        "category_cn": "零售", "status": "published", "builtin": False,
        "system_prompt": hard_rules.ensure_hard_rules("你是零售专家。"),
    })
    agent_store.save({
        "agent_id": "ag_pub", "name": "公共库技能", "category": "public",
        "status": "published", "builtin": False,
        "system_prompt": "公共库提示词（无平台硬规则，应被跳过）",
    })
    try:
        r1 = agent_store.backfill_hard_rules()
        assert r1["patched"] >= 1
        missing = agent_store.get("ag_biz_missing")
        assert hard_rules.has_hard_rules(missing["system_prompt"])
        assert missing["hard_rules_version"] == hard_rules.HARD_RULES_VERSION
        # 公共库未被改动
        assert not hard_rules.has_hard_rules(agent_store.get("ag_pub")["system_prompt"])

        # 再跑一次应不再补齐（幂等）
        r2 = agent_store.backfill_hard_rules()
        assert r2["patched"] == 0
    finally:
        # 清理注入，避免污染依赖 agent_store 计数的其它测试
        for aid in ("ag_biz_missing", "ag_biz_ok", "ag_pub"):
            agent_store._AGENTS.pop(aid, None)
