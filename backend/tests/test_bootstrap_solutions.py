"""启动自动补库（bootstrap_solutions）测试。

背景：客户本地安装/无 PG 内存模式重启后业务技能包为空 → 岗位选择器无选项
（交付硬伤）。修复：仓库内置包源（backend/solutions）+ lifespan 自动补。

M53 技能库从 115 扩到 203（新增 7 个行业），灌库语义从「空库才灌」改成
「按包源增量补」，所以这里锁五个行为：
1. 空库 → 全部包 published（开箱即用，无需审批）+ 平台级 tenant
2. 幂等：重复运行不新增
3. **增量**：老库只缺一部分时，只补缺的，已有的原样不动
4. **删了不复活**：管理员主动删掉的包，重启不会再灌回来（已灌清单持久）
5. 源目录默认落在仓库内（自包含，不依赖开发机外部目录）
"""
import os

import pytest

from app.services import agent_store, pg_store


def _pack_total() -> int:
    """包总数以导入器扫描为准（唯一真源），不在测试里写死数字。"""
    from scripts.import_solutions import scan
    return len(scan())


@pytest.fixture
def clean_store():
    """清场：避免其它用例注册的智能体污染计数断言（对齐 test_m15_skills 惯例）。"""
    saved = dict(agent_store._AGENTS)
    agent_store._AGENTS.clear()
    yield
    agent_store._AGENTS.clear()
    agent_store._AGENTS.update(saved)


@pytest.fixture
def fake_pg_state(monkeypatch):
    """把 app_state 换成本进程内的 dict，模拟「有 PG 的部署」。

    内存模式的 get_state 恒为 None，测不出「已灌清单」的效果——那正是要锁的东西。
    """
    store = {}

    def _get(key):
        return store.get(key)

    def _put(key, payload):
        store[key] = dict(payload)
        return True

    monkeypatch.setattr(pg_store, "get_state", _get)
    monkeypatch.setattr(pg_store, "put_state", _put)
    return store


def _business():
    return [a for a in agent_store._AGENTS.values()
            if not a.get("builtin") and not agent_store.is_public(a)]


def test_bootstrap_seeds_all_published(clean_store):
    """空库 → 全部业务包 published，tenant_id=平台级（全租户可见）。"""
    total = _pack_total()
    assert not agent_store.has_business_agents()
    n = agent_store.bootstrap_solutions()
    biz = _business()
    assert n == total
    assert len(biz) == total
    assert all(a["status"] == "published" for a in biz)
    assert all(a.get("tenant_id") == agent_store.PLATFORM_TENANT for a in biz)
    # 岗位目录由业务包派生：每个包都必须有归属，合计不能少于包数
    roles = agent_store.role_map.catalog(list(agent_store._AGENTS.values()))
    assert len(roles) == len(agent_store.role_map.ROLES)
    assert sum(r["count"] for r in roles) == total


def test_bootstrap_idempotent(clean_store, fake_pg_state):
    """重复运行不新增（已灌清单 + source_path 双保险）。"""
    total = _pack_total()
    agent_store.bootstrap_solutions()
    assert agent_store.bootstrap_solutions() == 0
    assert len(_business()) == total


def test_bootstrap_incremental_fills_missing(clean_store, fake_pg_state):
    """老部署升级：库里只有一部分包时，只补缺的，不重写已有的。

    模拟 M52 → M53：115 个老包已在库、且没有 marker（M50~M52 灌的数据），
    新增的 88 个必须自动补进来。
    """
    from scripts.import_solutions import scan
    from app.routers.agents import _new_agent_id

    defs = scan()
    total = len(defs)
    old = defs[:total - 10]          # 假装老库只有前面这些
    for d in old:
        agent_store.save({
            "agent_id": _new_agent_id(d["name"]),
            "name": d["name"], "adapter_type": d["adapter_type"],
            "status": "published", "description": d["description"],
            "system_prompt": "客户改过的提示词",   # 标记：存量不能被覆盖
            "category": d["kind"], "category_cn": d["board_cn"],
            "source_path": d["source"],
            "tenant_id": agent_store.PLATFORM_TENANT,
        })
    assert len(_business()) == total - 10

    created = agent_store.bootstrap_solutions()
    assert created == 10, "只该补缺的 10 个"
    assert len(_business()) == total
    # 存量提示词没被覆盖
    kept = [a for a in _business() if a["name"] == old[0]["name"]][0]
    assert kept["system_prompt"] == "客户改过的提示词"


def test_bootstrap_does_not_resurrect_deleted(clean_store, fake_pg_state):
    """管理员删掉的包，重启不能再灌回来（否则「下架某个行业包」永远做不成）。"""
    agent_store.bootstrap_solutions()
    victim = _business()[0]
    agent_store._AGENTS.pop(victim["agent_id"])
    before = len(_business())

    created = agent_store.bootstrap_solutions()
    assert created == 0
    assert len(_business()) == before
    assert victim["name"] not in {a["name"] for a in _business()}


def test_bootstrap_keeps_customer_packs(clean_store):
    """客户自建的技能包不能被内置包挤掉或覆盖。"""
    agent_store.save({
        "agent_id": "ag_custom", "name": "客户自建技能",
        "adapter_type": "llm", "knowledge_scope": "global",
        "status": "published", "description": "x", "system_prompt": "y",
        "category": "industry", "category_cn": "制造业",
    })
    agent_store.bootstrap_solutions()
    names = {a["name"] for a in _business()}
    assert "客户自建技能" in names
    assert agent_store._AGENTS["ag_custom"]["system_prompt"] == "y"


def test_solutions_root_is_in_repo():
    """默认源目录必须落在仓库内（backend/solutions），客户 clone 即自包含。"""
    from scripts.import_solutions import SOLUTIONS_ROOT
    assert os.path.isdir(SOLUTIONS_ROOT)
    assert os.path.basename(os.path.normpath(SOLUTIONS_ROOT)) == "solutions"
    count = 0
    for _root, _dirs, files in os.walk(SOLUTIONS_ROOT):
        count += files.count("skill.md")
    # 磁盘上的包数必须与导入器扫描到的一致（防止「目录里有、扫不出来」的静默丢失）
    assert count == _pack_total()
