"""M19 · 给已入库的技能包补职业标签。

场景：115 个包在 M15 就导入了，那时还没有 role_* 字段。本脚本遍历已入库记录，
按 role_map 补齐 role_tags / role_primary / role_core 并写穿 PG。

幂等：已有 role_tags 的跳过；--force 才重算（改了映射表后用）。

用法：
    .venv/Scripts/python.exe scripts/backfill_roles.py            # 补齐缺失
    .venv/Scripts/python.exe scripts/backfill_roles.py --force    # 按最新映射重算
    .venv/Scripts/python.exe scripts/backfill_roles.py --dry-run  # 只看不写
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import agent_store, role_map  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="按最新映射表重算（覆盖已有标签）")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写入")
    a = ap.parse_args()

    loaded = agent_store.load_from_pg()
    print(f"从 PG 载入 {loaded} 条；内存中共 {len(agent_store._AGENTS)} 条")

    changed, skipped, unknown = 0, 0, []
    for agent in list(agent_store._AGENTS.values()):
        if agent.get("builtin"):
            continue
        if agent.get("role_tags") and not a.force:
            skipped += 1
            continue
        roles = role_map.roles_for(agent.get("name", ""),
                                   agent.get("sub_function") or "",
                                   agent.get("category_cn") or "")
        if not roles or not roles[0]:
            unknown.append(agent.get("name"))
        if a.dry_run:
            changed += 1
            continue
        if a.force:
            agent.pop("role_tags", None)
            agent.pop("role_primary", None)
            agent.pop("role_core", None)
        agent_store.save(agent)
        changed += 1

    print(f"{'将改' if a.dry_run else '已改'} {changed} 条，跳过（已有标签）{skipped} 条")
    if unknown:
        print(f"未能识别职业 {len(unknown)} 条（会落到「其他」，需补映射表）：")
        for n in unknown[:20]:
            print("   -", n)

    # 分布回显
    from collections import Counter
    c = Counter(a_.get("role_primary") or "其他"
                for a_ in agent_store._AGENTS.values() if not a_.get("builtin"))
    print("\n职业分布：")
    for r in role_map.ROLES:
        print(f"  {r['name']:8s} {c.get(r['id'], 0):3d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
