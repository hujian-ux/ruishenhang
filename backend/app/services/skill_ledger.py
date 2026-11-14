"""P1-8 · 技能索引台账（#303）。

为什么要有这一层：平台 203 个技能包（业务包 115 + 公共库 28 + 内置助手），每个包在
「提示词 / 硬规则 / few-shot / 岗位映射 / 输入契约 / 产出契约 / 材料 / 源码 / 样例输入」
这些维度上的覆盖程度并不一致。运营/管理员需要一个**覆盖度台账**看清每个包缺什么、
按缺口筛选，避免覆盖盲区与口径不一致（例如某个业务包没带硬规则、没挂岗位、没来源目录）。

本模块是台账的**纯聚合查询层**：不写库、不改动任何技能包字段，只把散落在
agent_store / hard_rules / role_map / skill_pack 各处的覆盖度字段聚合成一条可读记录，
并给出完备度评分。可见域完全复用 agent_store._visible（RBAC），不另起一套权限。

口径约定：
- 台账只统计「技能包」——即业务包（非 public、非 builtin）与公共库（public）。
  平台内置通用助手 / 审批助手（builtin）不进台账，因为它们不是交付物。
- few-shot / 材料（M55）是 opt-in（FEWSHOT_ENABLED / MATERIALS_ENABLED 默认关），
  因此作为**低权重加分项**计入完备度，缺失不直接判为盲区，但台账会如实标出。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services import agent_store, hard_rules, role_map, skill_pack

# 完备度维度：key 同时用于记录 flag、统计聚合与前端筛选。
# weight 决定评分占比；opt_in 维度缺失不计入"必须盲区"。
DIMENSIONS: List[Dict[str, Any]] = [
    {"key": "prompt", "label": "提示词", "weight": 3, "opt_in": False,
     "desc": "system_prompt 非空且足够长（≥50 字）"},
    {"key": "hard_rules", "label": "硬规则", "weight": 2, "opt_in": False,
     "desc": "提示词含平台硬规则头（HARD_RULES_HEADER）"},
    {"key": "role", "label": "岗位映射", "weight": 2, "opt_in": False,
     "desc": "挂载到主岗位 role_primary（公共库不强制）"},
    {"key": "inputs", "label": "输入契约", "weight": 2, "opt_in": False,
     "desc": "有 inputs_contract（或 skill.md 可解析出输入章节）"},
    {"key": "outputs", "label": "产出契约", "weight": 2, "opt_in": False,
     "desc": "有 outputs_contract（或 skill.md 可解析出产出章节）"},
    {"key": "source", "label": "源码目录", "weight": 1, "opt_in": False,
     "desc": "source_path 定位到包目录（pack_dir 存在）"},
    {"key": "samples", "label": "样例输入", "weight": 1, "opt_in": False,
     "desc": "包目录里带原始输入文件（可一键挂载试跑）"},
    {"key": "fewshot", "label": "few-shot", "weight": 1, "opt_in": True,
     "desc": "已注入产出物样例块（FEWSHOT_ENABLED 开启后生效）"},
    {"key": "materials", "label": "材料/规范", "weight": 1, "opt_in": True,
     "desc": "已挂验收标准或领域规范参考（MATERIALS_ENABLED 开启后生效）"},
]
_DIM_BY_KEY = {d["key"]: d for d in DIMENSIONS}
_WEIGHT_TOTAL = sum(d["weight"] for d in DIMENSIONS)
_PROMPT_MIN_LEN = 50

# 业务包必查维度（含岗位映射 role，业务包必须挂主岗位）；
# 公共库豁免 role——它挂给所有岗位、不挂单一主岗位（见 DIMENSIONS role.desc）。
# 两者都排除 opt_in：few-shot / 材料缺失不算盲区，仅小幅扣分。
# 注：早期版本两个常量都误排除了 role，导致「岗位映射」维度永不进 gaps、形同虚设。
_BUSINESS_REQUIRED = [d["key"] for d in DIMENSIONS if not d["opt_in"]]
_PUBLIC_REQUIRED = [d["key"] for d in DIMENSIONS if not d["opt_in"] and d["key"] != "role"]


def _is_public(agent: Dict[str, Any]) -> bool:
    return agent_store.is_public(agent)


def _has_inputs_contract(agent: Dict[str, Any]) -> bool:
    if (agent.get("inputs_contract") or "").strip():
        return True
    src = agent.get("source_path") or ""
    if not src:
        return False
    try:
        sections = skill_pack.split_sections(_skill_md_text(src))
        return bool(sections.get("inputs", "").strip())
    except Exception:
        return False


def _has_outputs_contract(agent: Dict[str, Any]) -> bool:
    if (agent.get("outputs_contract") or "").strip():
        return True
    src = agent.get("source_path") or ""
    if not src:
        return False
    try:
        sections = skill_pack.split_sections(_skill_md_text(src))
        return bool(sections.get("outputs", "").strip())
    except Exception:
        return False


def _skill_md_text(source_path: str) -> str:
    """读 skill.md 正文（剥离 frontmatter）供章节解析。"""
    md = skill_pack._skill_md(source_path)
    if not md:
        return ""
    try:
        import re
        raw = open(md, encoding="utf-8", errors="ignore").read()
        return re.sub(r"^---\n.*?\n---\n?", "", raw, flags=re.S)
    except OSError:
        return ""


def _record(agent: Dict[str, Any]) -> Dict[str, Any]:
    """对单个技能包计算覆盖度台账记录。"""
    public = _is_public(agent)
    sp = agent.get("system_prompt") or ""
    src = agent.get("source_path") or ""
    rid = agent.get("role_primary") or ""

    # 包目录 / 样例 / 清单（仅当 source_path 可定位时有效）
    pack_dir = skill_pack.pack_dir(src) if src else ""
    samples = skill_pack.samples(src) if src else []
    inputs = [s for s in samples if s["kind"] == "input" and s["size"] > 0]
    checklist = skill_pack.checklist(agent)
    ref_specs = agent.get("reference_specs") or []
    acceptance = bool(agent.get("acceptance"))

    flags = {
        "prompt": len(sp.strip()) >= _PROMPT_MIN_LEN,
        "hard_rules": hard_rules.has_hard_rules(sp),
        "role": bool(rid),
        "inputs": _has_inputs_contract(agent),
        "outputs": _has_outputs_contract(agent),
        "source": bool(pack_dir),
        "samples": len(inputs) > 0,
        "fewshot": bool(agent.get("fewshot")),
        "materials": bool(ref_specs) or acceptance,
    }

    required = _PUBLIC_REQUIRED if public else _BUSINESS_REQUIRED
    coverage = [d["key"] for d in DIMENSIONS if flags.get(d["key"])]
    # 评分：仅按全部维度加权（opt_in 维度缺失仅小幅扣权重，不判盲区）
    earned = sum(_DIM_BY_KEY[k]["weight"] for k in coverage)
    score = earned / _WEIGHT_TOTAL if _WEIGHT_TOTAL else 0.0
    # 必查盲区：required 维度里缺失的（opt_in 不算盲区）
    gaps = [k for k in required if not flags.get(k)]

    return {
        "agent_id": agent["agent_id"],
        "name": agent.get("name") or "",
        "status": agent.get("status") or "",
        "kind": "public" if public else "business",
        "category_cn": agent.get("category_cn") or agent.get("category") or "",
        "sub_function": agent.get("sub_function") or "",
        "role_primary": rid,
        "role_name": role_map.ROLE_NAME.get(rid, "") if rid else "",
        "adapter_type": agent.get("adapter_type") or "",
        "is_core": bool(agent.get("role_core")),
        "prompt_len": len(sp.strip()),
        "sample_count": len(samples),
        "input_sample_count": len(inputs),
        "checklist_count": len(checklist),
        "reference_spec_count": len(ref_specs),
        "has_acceptance": acceptance,
        "flags": flags,
        "coverage": coverage,
        "gaps": gaps,
        "score": round(score, 4),
        "score_pct": int(round(score * 100)),
        "complete": len(gaps) == 0,
    }


def _visible_skills(caller: Any) -> List[Dict[str, Any]]:
    """可见技能列表：业务包 + 公共库，排除内置助手（builtin）。"""
    rows = agent_store.list_all(caller)
    return [a for a in rows if not a.get("builtin")]


def index(
    caller: Any,
    industry: str = "",
    role: str = "",
    status: str = "",
    q: str = "",
    kind: str = "",          # business | public | ""(all)
    flag: str = "",          # 维度 key：仅返回缺失该维度的包
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """覆盖度台账列表（带过滤）。返回 {items, total, count}。"""
    rows = _visible_skills(caller)
    # 岗位过滤（复用 agent_store.filter_role 的口径：本岗 + 公共库）
    if role:
        rid = (role_map.resolve(role) or {}).get("id") or role
        rows = agent_store.filter_role(rows, rid)
    if industry:
        rows = [a for a in rows if (a.get("category_cn") or a.get("category")) == industry]
    if status:
        rows = [a for a in rows if a.get("status") == status]
    if kind:
        want_public = (kind == "public")
        rows = [a for a in rows if _is_public(a) == want_public]
    if q:
        ql = q.lower()
        rows = [a for a in rows if (
            (a.get("name") or "").lower().find(ql) >= 0
            or (a.get("description") or "").lower().find(ql) >= 0
            or (a.get("sub_function") or "").lower().find(ql) >= 0
            or (role_map.ROLE_NAME.get(a.get("role_primary") or "", "").lower().find(ql) >= 0)
        )]

    records = [_record(a) for a in rows]

    # 维度缺口筛选：flag 命中"缺失该维度"
    if flag and flag in _DIM_BY_KEY:
        records = [r for r in records if not r["flags"].get(flag)]

    # 稳定排序：按完备度升序（缺口最多的排最前）+ 名称
    records.sort(key=lambda r: (r["score"], r["name"]))
    total = len(records)
    page = records[offset:offset + limit]
    return {"items": page, "total": total, "count": len(page)}


def stats(caller: Any) -> Dict[str, Any]:
    """聚合覆盖看板：总数 / 按行业 / 按岗位 / 维度覆盖计数 / 评分分布。"""
    rows = _visible_skills(caller)
    records = [_record(a) for a in rows]
    total = len(records)
    if total == 0:
        return {
            "total": 0, "business": 0, "public": 0,
            "avg_score_pct": 0, "complete_count": 0,
            "by_industry": [], "by_role": [],
            "dimension_coverage": {d["key"]: {"label": d["label"], "covered": 0,
                                              "ratio": 0.0, "opt_in": d["opt_in"]}
                                   for d in DIMENSIONS},
            "score_buckets": {"0-40": 0, "40-60": 0, "60-80": 0, "80-100": 0},
            "gap_top": [],
        }

    business = [r for r in records if r["kind"] == "business"]
    pub = [r for r in records if r["kind"] == "public"]
    avg = sum(r["score_pct"] for r in records) / total
    complete = sum(1 for r in records if r["complete"])

    # 按行业（仅业务包，公共库不进行业口径）
    ind: Dict[str, Dict[str, Any]] = {}
    for r in business:
        k = r["category_cn"] or "未分类"
        slot = ind.setdefault(k, {"category_cn": k, "count": 0, "sum_score": 0, "gaps": 0, "complete": 0})
        slot["count"] += 1
        slot["sum_score"] += r["score_pct"]
        slot["complete"] += 1 if r["complete"] else 0
        if r["gaps"]:
            slot["gaps"] += 1
    by_industry = sorted(
        ({"category_cn": v["category_cn"], "count": v["count"],
          "avg_score_pct": int(round(v["sum_score"] / v["count"])),
          "with_gap": v["gaps"], "complete": v["complete"]}
         for v in ind.values()),
        key=lambda x: (-x["count"], x["category_cn"]),
    )

    # 按岗位（仅业务包）
    rl: Dict[str, Dict[str, Any]] = {}
    for r in business:
        k = r["role_primary"] or ""
        if not k:
            continue
        slot = rl.setdefault(k, {"role_primary": k, "role_name": r["role_name"],
                                "count": 0, "sum_score": 0, "gaps": 0, "complete": 0})
        slot["count"] += 1
        slot["sum_score"] += r["score_pct"]
        slot["complete"] += 1 if r["complete"] else 0
        if r["gaps"]:
            slot["gaps"] += 1
    by_role = sorted(
        ({"role_primary": v["role_primary"], "role_name": v["role_name"],
          "count": v["count"], "avg_score_pct": int(round(v["sum_score"] / v["count"])),
          "with_gap": v["gaps"], "complete": v["complete"]}
         for v in rl.values()),
        key=lambda x: (-x["count"], x["role_name"]),
    )

    # 维度覆盖聚合
    dim_cov: Dict[str, Any] = {}
    for d in DIMENSIONS:
        cov = sum(1 for r in records if r["flags"].get(d["key"]))
        dim_cov[d["key"]] = {
            "label": d["label"], "covered": cov,
            "ratio": round(cov / total, 4), "opt_in": d["opt_in"],
        }

    # 评分分布
    buckets = {"0-40": 0, "40-60": 0, "60-80": 0, "80-100": 0}
    for r in records:
        p = r["score_pct"]
        if p < 40:
            buckets["0-40"] += 1
        elif p < 60:
            buckets["40-60"] += 1
        elif p < 80:
            buckets["60-80"] += 1
        else:
            buckets["80-100"] += 1

    # 缺口 TOP：按各维度缺失数量降序
    gap_counter: Dict[str, int] = {d["key"]: 0 for d in DIMENSIONS}
    for r in records:
        for g in r["gaps"]:
            gap_counter[g] = gap_counter.get(g, 0) + 1
    gap_top = sorted(
        ({"key": k, "label": _DIM_BY_KEY[k]["label"], "count": c}
         for k, c in gap_counter.items() if c > 0),
        key=lambda x: (-x["count"], x["label"]),
    )

    return {
        "total": total,
        "business": len(business),
        "public": len(pub),
        "avg_score_pct": int(round(avg)),
        "complete_count": complete,
        "by_industry": by_industry,
        "by_role": by_role,
        "dimension_coverage": dim_cov,
        "score_buckets": buckets,
        "gap_top": gap_top,
    }


def detail(caller: Any, agent_id: str) -> Optional[Dict[str, Any]]:
    """台账明细：覆盖记录 + 输入清单 + 样例输入 + 材料/规范摘要。"""
    agent = agent_store.get(agent_id)
    if not agent or agent.get("builtin"):
        return None
    if not agent_store._visible(agent, caller):
        return None
    rec = _record(agent)
    src = agent.get("source_path") or ""
    samples = skill_pack.samples(src) if src else []
    rec["checklist"] = skill_pack.checklist(agent)
    rec["samples"] = [{
        "filename": s["filename"], "display_name": s.get("display_name") or s["filename"],
        "kind": s["kind"], "size": s["size"], "ext": s["ext"], "from_readme": s["from_readme"],
    } for s in samples]
    rec["reference_specs"] = [{"name": s["name"], "chars": s.get("chars", 0)}
                              for s in (agent.get("reference_specs") or [])]
    rec["acceptance"] = agent.get("acceptance") or ""
    rec["fewshot_version"] = agent.get("fewshot_version") or ""
    rec["hard_rules_version"] = agent.get("hard_rules_version") or ""
    rec["reference_specs_version"] = agent.get("reference_specs_version") or ""
    rec["source_path"] = src
    rec["pack_found"] = bool(skill_pack.pack_dir(src)) if src else False
    return rec
