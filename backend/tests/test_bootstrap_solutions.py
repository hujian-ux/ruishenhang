"""首次启动自动灌库（bootstrap_solutions）测试。

背景：客户本地安装/无 PG 内存模式重启后业务技能包为空 → 岗位选择器无选项
（交付硬伤）。修复：仓库内置 115 包源（backend/solutions）+ lifespan 自修复。

锁四个行为：
1. 空库时自动灌入 115 个业务包，全部 published（开箱即用，无需审批）
2. 幂等：同 source_path 重复运行不新增
3. 已有业务包时不动作（不能把客户刻意删空的库偷偷灌满之外的场景误伤——
   有包即跳过）
4. 源目录默认落在仓库内（自包含，不依赖开发机外部目录）
"""
import os

import pytest

from app.services import agent_store


@pytest.fixture
def clean_store():
    """清场：避免其它用例注册的智能体污染计数断言（对齐 test_m15_skills 惯例）。"""
    saved = dict(agent_store._AGENTS)
    agent_store._AGENTS.clear()
    yield
    agent_store._AGENTS.clear()
    agent_store._AGENTS.update(saved)


def _business():
    return [a for a in agent_store._AGENTS.values()
            if not a.get("builtin") and not agent_store.is_public(a)]


def test_bootstrap_seeds_115_published(clean_store):
    """空库 → 115 个业务包全部 published，tenant_id=平台级（全租户可见）。"""
    assert not agent_store.has_business_agents()
    n = agent_store.bootstrap_solutions()
    biz = _business()
    assert n == 115
    assert len(biz) == 115
    assert all(a["status"] == "published" for a in biz)
    assert all(a.get("tenant_id") == agent_store.PLATFORM_TENANT for a in biz)
    # 岗位目录由业务包派生：11 个岗位，合计 115
    roles = [
        r for r in (agent_store.role_map.catalog(list(agent_store._AGENTS.values())))
    ]
    assert len(roles) == 11
    assert sum(r["count"] for r in roles) == 115


def test_bootstrap_idempotent(clean_store):
    """重复运行不新增（同 source_path 跳过）。"""
    agent_store.bootstrap_solutions()
    assert agent_store.bootstrap_solutions() == 0
    assert len(_business()) == 115


def test_bootstrap_noop_when_business_exists(clean_store):
    """已有业务包时不动作——不能覆盖客户自建/已导入的内容。"""
    agent_store.save({
        "agent_id": "ag_custom", "name": "客户自建技能",
        "adapter_type": "llm", "knowledge_scope": "global",
        "status": "published", "description": "x", "system_prompt": "y",
        "category": "industry", "category_cn": "制造业",
    })
    assert agent_store.bootstrap_solutions() == 0
    assert len(_business()) == 1


def test_solutions_root_is_in_repo():
    """默认源目录必须落在仓库内（backend/solutions），客户 clone 即自包含。"""
    from scripts.import_solutions import SOLUTIONS_ROOT
    assert os.path.isdir(SOLUTIONS_ROOT)
    assert os.path.basename(os.path.normpath(SOLUTIONS_ROOT)) == "solutions"
    # 仓库内置源必须真的带 115 个 skill.md
    count = 0
    for _root, _dirs, files in os.walk(SOLUTIONS_ROOT):
        count += files.count("skill.md")
    assert count == 115
