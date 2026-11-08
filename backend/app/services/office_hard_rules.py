"""M15/M25 P1 · 办公类（62 个）业务技能的领域硬规则。

平台级硬规则（hard_rules.py）已对所有 115 个业务包统一注入红线（不编造、带出处、
冲突并列等）。本模块在平台红线之外，补「办公域」特有的业务红线，仅作用于办公类技能包，
避免把制造/零售的行业红线强加给办公场景。

设计（对齐 hard_rules.py 的范式）：
  - ``OFFICE_HARD_RULES``：办公域红线文本（幂等、可重跑）。
  - ``ensure_office_rules(system_prompt)``：幂等把办公红线块接到提示词末尾。
  - ``has_office_rules / needs_office_rules``：校验。
  - ``backfill_office_hard_rules()``：对办公类业务包追加重线（跳过内置/公共库/已含）。
"""
from __future__ import annotations

from typing import Any, Dict, List

OFFICE_RULES_HEADER = "## 办公域硬规则（高于上述任何流程）"
OFFICE_RULES_VERSION = "2026-11-08.office"

OFFICE_HARD_RULES = f"""{OFFICE_RULES_HEADER}
1. 财务/金额：不得凭空编造任何数字；所有金额、税率、余额必须来自用户提供的台账或公开可核验来源，并标注口径。
2. 合同/文书：输出版本须带版本号与日期；不得删除、篡改输入中的条款原文，只能引用与对齐。
3. 报表/表格：表头、单位、口径必须显式写出；同一指标在不同 sheet 的口径须一致，禁止静默换算。
4. 个人信息（PII）：姓名、手机号、身份证、工资等须脱敏或仅在本会话内使用，不得写入可下载交付物明文。
5. 排期/日程：涉及跨部门或外部节点的时间，须标注「待确认」而非承诺确定时间；冲突项必须并列提示。
6. 合规：涉及劳动、税务、数据出境的结论须标注「建议咨询法务/专业人士」，不得替代专业意见。
"""

_MARK = OFFICE_RULES_HEADER


def has_office_rules(system_prompt: str) -> bool:
    return _MARK in (system_prompt or "")


def needs_office_rules(system_prompt: str) -> bool:
    return not has_office_rules(system_prompt)


def ensure_office_rules(system_prompt: str) -> str:
    if needs_office_rules(system_prompt):
        if system_prompt and not system_prompt.endswith("\n"):
            system_prompt = system_prompt + "\n\n"
        elif system_prompt:
            system_prompt = system_prompt + "\n"
        return system_prompt + OFFICE_HARD_RULES
    return system_prompt


def _is_office(a: Dict[str, Any]) -> bool:
    cat = (a.get("category_cn") or a.get("category") or "")
    return ("办公" in str(cat)) or (str(a.get("category") or "") == "office")


def backfill_office_hard_rules() -> Dict[str, Any]:
    """对办公类业务包补办公域硬规则（幂等、可重跑）。返回 {total, already, patched, by_category}。"""
    from app.services import agent_store

    total = already = patched = 0
    by_category: Dict[str, Dict[str, int]] = {}
    for a in list(agent_store._AGENTS.values()):
        if a.get("builtin") or a.get("category") == "public":
            continue
        if not _is_office(a):
            continue
        total += 1
        sp = a.get("system_prompt") or ""
        cat = a.get("category_cn") or a.get("category") or "办公"
        slot = by_category.setdefault(cat, {"total": 0, "already": 0, "patched": 0})
        slot["total"] += 1
        if has_office_rules(sp):
            already += 1
            slot["already"] += 1
            continue
        a["system_prompt"] = ensure_office_rules(sp)
        a["office_rules_version"] = OFFICE_RULES_VERSION
        agent_store.save(a)
        patched += 1
        slot["patched"] += 1
    return {
        "total": total, "already": already, "patched": patched,
        "by_category": by_category, "office_rules_version": OFFICE_RULES_VERSION,
    }
