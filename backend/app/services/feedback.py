"""M27 · 反馈闭环 / 演化建议台 / 运营与质量度量（蓝图第 20/28/30/41 章）。

业务层只依赖 _m27_store 的轻量内存存储与 core.config 已有的四个开关，不改任何
生产数据。三条红线：
  1. 演化建议一律 pending，绝不自动生效；只有管理员 approve 才落一条版本快照。
  2. 回滚只生成一条新的 pending 建议，不静默改库（人在环不可绕过）。
  3. 没有真实埋点就返回 0 + insufficient_data，DAU / ROI 一律不编造。

状态是进程内内存态，重启即丢（与项目「无 PG 全内存」口径一致）。
caller 在只读/聚合函数里保留用于将来按部门收口；当前返回全量真实数据，不做过滤，
避免把真实埋点误藏、也避免跨部门读数被当成造假。
"""
from __future__ import annotations

import copy
import hashlib
import math
from typing import Any, Optional

from app.core.config import settings
from app.services._m27_store import (
    Store,
    audit_event,
    caller_id,
    is_admin,
    iso,
    new_id,
    now,
)

# ---- 状态 ------------------------------------------------------------------
_FEEDBACK = Store("fb")        # 反馈明细
_GAPS = Store("gap")           # 知识缺口候选（负反馈 / 纠错自动进入）
_PROPOSALS = Store("prop")     # 演化建议
_VERSIONS = Store("ver")       # 审批通过后的版本快照（回滚来源）
_QUALITY = Store("quality")    # invoke 埋点的质量事件

_VOTES = ("up", "down")
_KINDS = ("prompt", "knowledge", "workflow")
_DECISIONS = ("approve", "reject")
_MIN_QUALITY_SAMPLES = 5       # 低于该样本量的比率 / 百分位标注 insufficient_data
_DAY = 86400.0


# ---- 小工具 ----------------------------------------------------------------
def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _opt_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _percentile(values: list[float], p: float) -> float:
    """线性插值百分位；空样本返回 0。"""
    s = sorted(values)
    if not s:
        return 0.0
    if len(s) == 1:
        return float(s[0])
    k = (len(s) - 1) * (p / 100.0)
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return float(s[int(k)])
    return float(s[lo]) + (float(s[hi]) - float(s[lo])) * (k - lo)


def _row(row: dict) -> dict:
    """补齐时间字段。

    注意 Store.put 会无条件计算 iso(row["created_at"])，所以 created_at 必须是
    数值时间戳；可读时间放在 created_iso（由 put 自动补，这里也显式给一份）。
    """
    ts = now()
    row.setdefault("created_at", ts)
    row.setdefault("created_iso", iso(row["created_at"]))
    return row


# ---- 反馈闭环 --------------------------------------------------------------
def add_feedback(run_id: str, session_id: str, agent_id: str, caller,
                 vote: str, correction: str = "", reason: str = "",
                 score: float | None = None) -> dict:
    """记一条赞/踩；负反馈或纠错非空时自动进入知识缺口候选。"""
    vote = str(vote or "")
    if vote not in _VOTES:
        raise ValueError(f"vote 只接受 up/down，收到 {vote!r}")
    if not settings.FEEDBACK_ENABLED:
        raise ValueError("反馈采集已关闭（FEEDBACK_ENABLED=0）")
    correction = str(correction or "")
    reason = str(reason or "")
    ts = now()
    saved = _FEEDBACK.put(_row({
        "id": new_id("fb"),
        "run_id": str(run_id or ""),
        "session_id": str(session_id or ""),
        "agent_id": str(agent_id or ""),
        "vote": vote,
        "correction": correction,
        "reason": reason,
        "score": _opt_float(score),
        "userId": caller_id(caller),
        "deptId": str(getattr(caller, "deptId", "") or ""),
        "created_at": ts,
        "created_iso": iso(ts),
    }))
    # 一条反馈只产一条候选，避免「踩 + 纠错」被重复计数。
    if vote == "down" or correction:
        _GAPS.put(_row({
            "id": new_id("gap"),
            "feedback_id": saved["id"],
            "run_id": saved["run_id"],
            "session_id": saved["session_id"],
            "agent_id": saved["agent_id"],
            "reason": reason,
            "correction": correction,
            "vote": vote,
            "userId": saved["userId"],
            "deptId": saved["deptId"],
            "created_at": ts,
            "created_iso": iso(ts),
        }))
    audit_event("feedback.add", caller, target=saved["id"],
                meta={"vote": vote, "agent_id": saved["agent_id"]})
    return saved


def feedback_stats(caller=None) -> dict:
    """反馈聚合：total/up/down/corrections/by_agent/satisfaction。

    满意度 = up / (up + down)；无投票时分母为 0，返回 0.0（不编造）。
    """
    rows = _FEEDBACK.list(limit=10 ** 6)
    up = sum(1 for r in rows if r.get("vote") == "up")
    down = sum(1 for r in rows if r.get("vote") == "down")
    corrections = sum(1 for r in rows if r.get("correction"))
    by_agent: dict[str, int] = {}
    for r in rows:
        aid = str(r.get("agent_id") or "(unknown)")
        by_agent[aid] = by_agent.get(aid, 0) + 1
    denom = up + down
    return {
        "total": len(rows),
        "up": up,
        "down": down,
        "corrections": corrections,
        "by_agent": by_agent,
        "satisfaction": round(up / denom, 4) if denom else 0.0,
    }


def detect_gaps() -> list:
    """按 agent_id + reason 聚类负反馈/纠错，返回按 count 倒序的缺口列表。"""
    groups: dict[str, dict] = {}
    for g in _GAPS.list(limit=10 ** 6):
        agent_id = str(g.get("agent_id") or "(unknown)")
        reason = str(g.get("reason") or "").strip() or "(未分类)"
        key = f"{agent_id}|{reason}"
        grp = groups.setdefault(key, {
            "key": key, "agent_id": agent_id, "count": 0,
            "sample": {}, "suggestion": "",
        })
        grp["count"] += 1
        cur = grp["sample"]
        if not cur or _as_float(g.get("created_at")) >= _as_float(cur.get("created_at")):
            grp["sample"] = {
                "run_id": g.get("run_id", ""),
                "agent_id": agent_id,
                "reason": reason,
                "correction": g.get("correction", ""),
                "vote": g.get("vote", ""),
                "created_at": g.get("created_at"),
            }
    out = []
    for grp in groups.values():
        count, label = grp["count"], grp["key"].split("|", 1)[1]
        grp["suggestion"] = (
            f"智能体「{grp['agent_id']}」在「{label}」上累计 {count} 次负反馈/纠错；"
            f"建议补充对应知识条目或优化提示词，并经演化建议台审批后生效"
        )
        out.append(grp)
    out.sort(key=lambda x: (-x["count"], x["key"]))
    return out


# ---- 演化建议台 ------------------------------------------------------------
def create_proposal(kind: str, title: str, payload: dict, caller,
                    source: str = "") -> dict:
    """新建建议，status 恒为 pending。

    EVOLUTION_REQUIRE_APPROVAL=1 时绝不自动生效。本实现任何情况下都不自动生效：
    即使关掉该开关也保持 pending，统一走管理员审批，避免绕过人在环。
    """
    if kind not in _KINDS:
        raise ValueError(f"kind 只接受 {'|'.join(_KINDS)}，收到 {kind!r}")
    require_approval = bool(settings.EVOLUTION_REQUIRE_APPROVAL)
    ts = now()
    saved = _PROPOSALS.put(_row({
        "id": new_id("prop"),
        "kind": kind,
        "title": str(title or ""),
        "payload": copy.deepcopy(payload or {}),
        "source": str(source or ""),
        "status": "pending",
        "require_approval": require_approval,
        "auto_applied": False,
        "applied_version_id": None,
        "decided_by": None,
        "decision": None,
        "decided_at": None,
        "userId": caller_id(caller),
        "deptId": str(getattr(caller, "deptId", "") or ""),
        "created_at": ts,
        "created_iso": iso(ts),
    }))
    audit_event("evolution.propose", caller, target=saved["id"],
                meta={"kind": kind, "require_approval": require_approval})
    return saved


def list_proposals(caller=None, status: str | None = None) -> list:
    """建议列表（最新在前），可按 status 过滤。"""
    return _PROPOSALS.list(filters={"status": status} if status else None,
                           limit=10 ** 6)


def decide_proposal(pid: str, decision: str, caller) -> dict:
    """审批：仅 platform_admin / dept_admin；approve → applied 并写一条版本快照。"""
    if decision not in _DECISIONS:
        raise ValueError(f"decision 只接受 approve|reject，收到 {decision!r}")
    if not is_admin(caller):
        raise PermissionError("forbidden: 仅 platform_admin / dept_admin 可审批演化建议")
    prop = _PROPOSALS.get(pid)
    if prop is None:
        raise KeyError(f"演化建议不存在：{pid}")
    if prop.get("status") != "pending":
        raise ValueError(f"该建议已决策（status={prop.get('status')}），终态不可改")
    ts = now()
    decided_at = iso(ts)
    patch: dict[str, Any] = {
        "status": "applied" if decision == "approve" else "rejected",
        "decision": decision,
        "decided_by": caller_id(caller),
        "decided_at": decided_at,
    }
    if decision == "approve":
        vid = new_id("ver")
        _VERSIONS.put(_row({
            "id": vid,
            "version_id": vid,
            "proposal_id": pid,
            "kind": prop.get("kind"),
            "title": prop.get("title"),
            "payload": copy.deepcopy(prop.get("payload") or {}),
            "source": prop.get("source", ""),
            "decided_by": caller_id(caller),
            "created_at": ts,
            "created_iso": decided_at,
            "note": "审批通过后的版本快照，可用于回滚；回滚本身仍需再次审批",
        }))
        patch["applied_version_id"] = vid
    updated = _PROPOSALS.update(pid, patch) or prop
    audit_event("evolution.decide", caller, target=pid,
                meta={"decision": decision, "status": patch["status"]})
    return updated


def list_versions() -> list:
    """版本快照（最新在前）。每条含 id / version_id / payload / proposal_id。"""
    return _VERSIONS.list(limit=10 ** 6)


def rollback(version_id: str, caller) -> dict:
    """回滚 = 提议，不是静默改库。

    把快照 payload 恢复成一条新的 pending 建议，返回 {"restored_proposal":...}；
    不直接修改任何生产数据，需管理员再次审批才会生效（人在环不可绕过）。
    """
    if not is_admin(caller):
        raise PermissionError("forbidden: 仅 platform_admin / dept_admin 可执行回滚")
    ver = _VERSIONS.get(str(version_id or ""))
    if ver is None:
        raise KeyError(f"版本快照不存在：{version_id}")
    restored = create_proposal(
        kind=ver.get("kind") or "knowledge",
        title=f"[回滚] {ver.get('title') or ver.get('version_id')}",
        payload=copy.deepcopy(ver.get("payload") or {}),
        caller=caller,
        source=f"rollback:{ver.get('version_id')}",
    )
    result = {
        "restored_proposal": restored,
        "version_id": ver.get("version_id"),
        "original_proposal_id": ver.get("proposal_id"),
        "requires_approval": True,
        "silent_db_write": False,
        "note": "回滚是提议，不是静默改库：仅生成一条新的 pending 建议，"
                "需管理员再次审批通过后才会生效；生产数据未被直接修改（人在环不可绕过）",
    }
    audit_event("evolution.rollback", caller, target=ver.get("version_id"),
                meta={"restored_proposal": restored["id"]})
    return result


# ---- 运营度量 --------------------------------------------------------------
def adoption_metrics(caller=None) -> dict:
    """DAU / WAU / 采纳率 / 任务完成率 / 部门渗透率 / ROI。

    全部取自真实埋点（quality + feedback）；没有真实数据就返回 0 +
    insufficient_data=True。ROI 缺少成本/收益数据，恒为 0，绝不编造。
    """
    window_days = max(int(settings.AUDIT_RETENTION_DAYS), 1)
    events = _QUALITY.list(limit=10 ** 6)
    feedback = _FEEDBACK.list(limit=10 ** 6)
    empty = {
        "dau": 0, "wau": 0, "adoption_rate": 0.0,
        "task_completion_rate": 0.0, "dept_penetration": 0.0, "roi": 0.0,
        "insufficient_data": True, "sample_size": 0,
        "window_days": window_days,
        "note": "无真实埋点数据：全部指标返回 0；ROI 需接入真实成本/收益数据，当前不编造",
    }
    n = len(events)
    if n == 0:
        return empty
    t0 = now()
    wau_window = min(7 * _DAY, window_days * _DAY)
    dau_users: set[str] = set()
    wau_users: set[str] = set()
    success = real = 0
    for e in events:
        uid = str(e.get("userId") or "")
        ts = _as_float(e.get("created_at"))
        if uid and t0 - ts <= _DAY:
            dau_users.add(uid)
        if uid and t0 - ts <= wau_window:
            wau_users.add(uid)
        if bool(e.get("success")):
            success += 1
        if bool(e.get("real")):
            real += 1
    active_depts: set[str] = set()
    all_depts: set[str] = set()
    for r in list(events) + list(feedback) + list(_PROPOSALS.list(limit=10 ** 6)):
        dept = str(r.get("deptId") or "")
        if dept:
            all_depts.add(dept)
    for r in list(events) + list(feedback):
        dept = str(r.get("deptId") or "")
        if dept:
            active_depts.add(dept)
    return {
        "dau": len(dau_users),
        "wau": len(wau_users),
        "adoption_rate": round(real / n, 4),            # 真实(非 stub)调用占比
        "task_completion_rate": round(success / n, 4),
        "dept_penetration": round(len(active_depts) / max(len(all_depts), 1), 4),
        "roi": 0.0,                                     # 无成本收益数据，恒 0
        "insufficient_data": n < _MIN_QUALITY_SAMPLES,
        "sample_size": n,
        "window_days": window_days,
        "note": "指标基于真实埋点；adoption_rate=真实(非stub)调用占比，"
                "dept_penetration=有活跃部门/平台已见部门；ROI 无成本收益数据恒为 0，不编造",
    }


# ---- 质量度量 --------------------------------------------------------------
def record_quality(event: dict) -> None:
    """记录一条 invoke 埋点。字段缺省按「无异常证据」处理，不臆造失败/幻觉。"""
    e = event or {}
    ts = now()
    _QUALITY.put(_row({
        "id": new_id("quality"),
        "success": bool(e.get("success", True)),
        "real": bool(e.get("real", True)),
        "latency_ms": _as_float(e.get("latency_ms")),
        "hallucination_flag": bool(e.get("hallucination_flag", False)),
        "handoff": bool(e.get("handoff", False)),
        "confidence": _opt_float(e.get("confidence")),
        "agent_id": str(e.get("agent_id") or ""),
        "userId": str(e.get("userId") or ""),
        "deptId": str(e.get("deptId") or ""),
        "created_at": ts,
        "created_iso": iso(ts),
    }))


def quality_metrics(caller=None) -> dict:
    """成功率 / stub 占比 / 幻觉标记率 / 转人工率 / 满意度 / 延迟 p50/p95/p99。

    样本不足（< _MIN_QUALITY_SAMPLES）标 insufficient_data；低置信率以
    CONFIDENCE_THRESHOLD 为界。统计窗口取 AUDIT_RETENTION_DAYS。
    """
    window_days = max(int(settings.AUDIT_RETENTION_DAYS), 1)
    cutoff = now() - window_days * _DAY
    events = [e for e in _QUALITY.list(limit=10 ** 6)
              if _as_float(e.get("created_at")) >= cutoff]
    n = len(events)
    lat = [x for x in (_as_float(e.get("latency_ms")) for e in events) if x > 0]
    success = sum(1 for e in events if bool(e.get("success")))
    stub = sum(1 for e in events if not bool(e.get("real")))
    hallucination = sum(1 for e in events if bool(e.get("hallucination_flag")))
    handoff = sum(1 for e in events if bool(e.get("handoff")))
    threshold = float(settings.CONFIDENCE_THRESHOLD)
    low_conf = sum(1 for e in events
                   if e.get("confidence") is not None
                   and _as_float(e["confidence"]) < threshold)
    p50 = round(_percentile(lat, 50), 2) if lat else 0
    p95 = round(_percentile(lat, 95), 2) if lat else 0
    p99 = round(_percentile(lat, 99), 2) if lat else 0
    return {
        "sample_size": n,
        "success_rate": round(success / n, 4) if n else 0.0,
        "stub_ratio": round(stub / n, 4) if n else 0.0,
        "hallucination_rate": round(hallucination / n, 4) if n else 0.0,
        "handoff_rate": round(handoff / n, 4) if n else 0.0,
        "satisfaction": feedback_stats(caller)["satisfaction"],
        "low_confidence_rate": round(low_conf / n, 4) if n else 0.0,
        "confidence_threshold": threshold,
        "latency_p50": p50,
        "latency_p95": p95,
        "latency_p99": p99,
        "latency_ms": {"p50": p50, "p95": p95, "p99": p99, "samples": len(lat)},
        "insufficient_data": n < _MIN_QUALITY_SAMPLES,
        "window_days": window_days,
    }


# ---- A/B 实验与灰度发布（蓝图第 20/30/41 章）---------------------------------
_EXPERIMENTS = Store("ab_exp")      # 实验定义（含 variants 权重）
_EXP_OUTCOMES = Store("ab_outcome")  # 实验产出（按 variant 归集）

_EXP_MODES = ("ab", "gray")
_AB_MIN_SAMPLES = int(getattr(settings, "AB_MIN_SAMPLES", "30"))


def _bucket(user_key: str, total: int = 10000) -> int:
    """确定性分桶：同一 user_key 永远落在同一区间（不依赖 PYTHONHASHSEED）。"""
    h = hashlib.sha256(str(user_key).encode("utf-8")).hexdigest()
    return int(h[:8], 16) % total


def create_experiment(agent_id: str, title: str, variants: list, caller,
                     mode: str = "ab") -> dict:
    """新建 A/B 实验或灰度发布。

    variants: [{"name":"A","prompt_override":"...","weight":0.5}, ...]
      - prompt_override 可选（灰度基准可为空，沿用线上 prompt）。
      - weight 可选，缺省均分；ab 模式要求权重和≈1，gray 允许不等（基准占大头）。
    mode:
      - "ab"  对照实验：对照组 vs 实验组，达标后选优 promote。
      - "gray" 灰度发布：新变体按权重逐步放量（10%→50%→100%），异常可回退。
    status 恒为 running；绝不自动改线上 prompt，promote 走演化建议台审批（人在环）。
    """
    if mode not in _EXP_MODES:
        raise ValueError(f"mode 只接受 {'|'.join(_EXP_MODES)}，收到 {mode!r}")
    if not isinstance(variants, list) or len(variants) < 2:
        raise ValueError("variants 至少 2 项")
    names = [str(v.get("name") or "") for v in variants]
    if any(not n for n in names) or len(set(names)) != len(names):
        raise ValueError("variants 每项须有唯一 name")
    weights = [max(0.0, float(v.get("weight", 0))) for v in variants]
    s = sum(weights)
    if s <= 0:
        weights = [1.0 / len(variants)] * len(variants)
    elif mode == "ab" and abs(s - 1.0) > 0.01:
        raise ValueError("ab 模式要求权重和≈1")
    ts = now()
    exp = _EXPERIMENTS.put(_row({
        "id": new_id("ab"),
        "agent_id": str(agent_id or ""),
        "title": str(title or ""),
        "mode": mode,
        "variants": [{"name": names[i],
                      "prompt_override": str(variants[i].get("prompt_override") or ""),
                      "weight": round(weights[i], 4)} for i in range(len(variants))],
        "status": "running",
        "started_by": caller_id(caller),
        "ended_at": None,
        "created_at": ts,
        "created_iso": iso(ts),
    }))
    audit_event("ab.create", caller, target=exp["id"],
                meta={"agent_id": exp["agent_id"], "mode": mode, "variants": names})
    return exp


def list_experiments(status: str | None = None) -> list:
    return _EXPERIMENTS.list(filters={"status": status} if status else None,
                             limit=10 ** 6)


def find_running_experiment(agent_id: str) -> dict | None:
    """查某 agent 当前 running 实验（供 invoke opt-in 接入，默认不启用）。"""
    for e in _EXPERIMENTS.list(limit=10 ** 6):
        if e.get("agent_id") == str(agent_id) and e.get("status") == "running":
            return e
    return None


def assign_variant(exp: dict, user_key: str) -> dict | None:
    """对 running 实验做确定性变体分配；返回 {exp_id,variant,prompt_override,mode}。"""
    if exp is None or exp.get("status") != "running":
        return None
    variants = exp.get("variants") or []
    weights = [max(0.0, float(v.get("weight", 0))) for v in variants]
    s = sum(weights) or 1.0
    cum: list[float] = []
    acc = 0.0
    for w in weights:
        acc += w / s
        cum.append(acc)
    pos = _bucket(user_key) / 10000.0
    for i, c in enumerate(cum):
        if pos <= c or i == len(cum) - 1:
            return {"exp_id": exp["id"], "variant": variants[i]["name"],
                    "prompt_override": variants[i].get("prompt_override", ""),
                    "mode": exp.get("mode")}
    return None


def record_outcome(exp_id: str, variant: str, outcome: dict) -> dict:
    """记录某变体的一次产出：adopted / satisfaction / success。"""
    o = outcome or {}
    ts = now()
    return _EXP_OUTCOMES.put(_row({
        "id": new_id("abo"),
        "exp_id": str(exp_id or ""),
        "variant": str(variant or ""),
        "adopted": bool(o.get("adopted")),
        "satisfaction": _opt_float(o.get("satisfaction")),
        "success": bool(o.get("success", True)),
        "userId": str(o.get("userId") or ""),
        "created_at": ts,
        "created_iso": iso(ts),
    }))


def experiment_report(exp_id: str) -> dict:
    """按 variant 对比采纳率/满意度/成功率；样本不足标 insufficient；给出 winner。"""
    exp = _EXPERIMENTS.get(str(exp_id or ""))
    if exp is None:
        raise KeyError(f"实验不存在：{exp_id}")
    outcomes = [o for o in _EXP_OUTCOMES.list(limit=10 ** 6)
                if o.get("exp_id") == exp_id]
    by_var: dict[str, dict] = {}
    for v in exp.get("variants") or []:
        by_var[v["name"]] = {"variant": v["name"], "samples": 0, "adopted": 0,
                             "success": 0, "sat_sum": 0.0, "sat_n": 0}
    for o in outcomes:
        slot = by_var.get(str(o.get("variant") or ""))
        if not slot:
            continue
        slot["samples"] += 1
        if bool(o.get("adopted")):
            slot["adopted"] += 1
        if bool(o.get("success")):
            slot["success"] += 1
        sat = o.get("satisfaction")
        if sat is not None:
            slot["sat_sum"] += float(sat)
            slot["sat_n"] += 1
    for slot in by_var.values():
        n = slot["samples"]
        slot["adoption_rate"] = round(slot["adopted"] / n, 4) if n else 0.0
        slot["success_rate"] = round(slot["success"] / n, 4) if n else 0.0
        slot["avg_satisfaction"] = round(slot["sat_sum"] / slot["sat_n"], 4) if slot["sat_n"] else None
        slot["sufficient"] = n >= _AB_MIN_SAMPLES
    rows = list(by_var.values())
    winner = None
    if exp.get("mode") == "ab":
        cand = [r for r in rows if r["sufficient"]]
        if cand:
            winner = max(cand, key=lambda r: r["adoption_rate"])["variant"]
    insufficient = all(not r["sufficient"] for r in rows)
    return {
        "exp_id": exp_id,
        "mode": exp.get("mode"),
        "status": exp.get("status"),
        "variants": rows,
        "winner": winner,
        "insufficient_data": insufficient,
        "min_samples_per_variant": _AB_MIN_SAMPLES,
        "note": ("ab 模式达样本阈值后给 winner；gray 模式只放量对比不自动选优"
                 if exp.get("mode") == "ab"
                 else "gray 模式：按权重放量，建议用 set_rollout_weight 渐进，达标后 promote"),
    }


def set_rollout_weight(exp_id: str, variant: str, weight: float, caller) -> dict:
    """灰度发布：调整某变体权重（其余按比例缩放使总和=1）。仅管理员，仅 running。"""
    if not is_admin(caller):
        raise PermissionError("forbidden: 仅平台/部门管理员可调整灰度权重")
    exp = _EXPERIMENTS.get(str(exp_id or ""))
    if exp is None:
        raise KeyError(f"实验不存在：{exp_id}")
    if exp.get("status") != "running":
        raise ValueError(f"实验已 {exp.get('status')}，不可调整权重")
    variants = exp.get("variants") or []
    target = next((v for v in variants if v["name"] == str(variant)), None)
    if target is None:
        raise ValueError(f"变体不存在：{variant}")
    w = max(0.0, float(weight))
    others = [v for v in variants if v["name"] != str(variant)]
    other_sum = sum(max(0.0, v["weight"]) for v in others)
    if other_sum <= 0 and w < 1.0:
        raise ValueError("其余变体权重和为 0，无法缩放；请同时调整其它变体")
    remaining = 1.0 - w
    new_variants = []
    for v in variants:
        if v["name"] == str(variant):
            new_variants.append({**v, "weight": round(w, 4)})
        else:
            vw = max(0.0, v["weight"])
            new_variants.append({**v, "weight": round(vw / other_sum * remaining, 4)
                                 if other_sum else 0.0})
    updated = _EXPERIMENTS.update(exp_id, {"variants": new_variants}) or exp
    audit_event("ab.rollout", caller, target=exp_id, meta={"variant": variant, "weight": w})
    return updated


def promote_variant(exp_id: str, variant: str, caller) -> dict:
    """结束实验并把 winner 落一条演化建议（pending，人在环审批，不直接改 prompt）。"""
    if not is_admin(caller):
        raise PermissionError("forbidden: 仅平台/部门管理员可结束实验")
    exp = _EXPERIMENTS.get(str(exp_id or ""))
    if exp is None:
        raise KeyError(f"实验不存在：{exp_id}")
    if exp.get("status") != "running":
        raise ValueError(f"实验已 {exp.get('status')}，不可 promote")
    target = next((v for v in exp.get("variants") or [] if v["name"] == str(variant)), None)
    if target is None:
        raise ValueError(f"变体不存在：{variant}")
    ended = _EXPERIMENTS.update(exp_id, {"status": "ended", "ended_at": iso(now())}) or exp
    # 落一条 pending 演化建议（prompt 类），审批后才生效——绝不静默改线上
    prop = create_proposal(
        kind="prompt",
        title=f"[A/B winner] {exp.get('title')} → {variant}",
        payload={"agent_id": exp.get("agent_id"),
                 "prompt_override": target.get("prompt_override", ""),
                 "source_exp": exp_id},
        caller=caller,
        source=f"ab_promote:{exp_id}",
    )
    return {"ended_experiment": ended, "proposal": prop, "requires_approval": True,
            "silent_db_write": False,
            "note": "promote 结束实验并生成一条 pending 演化建议；审批通过后才改线上 prompt（人在环）"}


# ---- 测试隔离 --------------------------------------------------------------
def reset_state() -> None:
    """清空全部内存状态（测试间隔离 / 运维手动重置）。"""
    for store in (_FEEDBACK, _GAPS, _PROPOSALS, _VERSIONS, _QUALITY,
                  _EXPERIMENTS, _EXP_OUTCOMES):
        store.clear()
