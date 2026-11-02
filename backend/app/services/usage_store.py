"""M7 用量计量与出账单（第25章计量 / 第30章成本）。

设计要点（与 M6 会话·审批同一套「内存主存 + PG 写穿」模式，接口不变）：
  - **每条真实调用落一条账**：token 数取 LiteLLM 响应的 usage（真值），取不到才估算；
    成本按模型注册表单价（price_per_1k_*）折算，模型单价改了历史账不变（落账即固化）。
  - **PG 为持久副本**：写穿 usage_records 真实列（非 jsonb），重启后 load_from_pg 载回，
    账单不清零；PG 不可达时内存照常出聚合，业务不阻塞。
  - **权限收敛**（第18章语义延伸到计量）：员工只看自己、部门管理员看本部门、
    平台管理员看全量；跨部门汇总的金额只有平台管理员能看到完整列表。
  - **配额红线**：按部门滚动 30 天 token 用量封顶（settings.USAGE_QUOTA_TOKENS，0=不限额），
    超限调用前直接 429——计费不只是展示，是真的会拦。
"""
import calendar
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.models.caller import CallerContext
from app.services import model_gateway, pg_store

# 内存保留上限（更早的账只在 PG 里，出账单按维度聚合用不到全量明细）
MAX_INMEM = 20000
DIMENSIONS = ("dept", "agent", "model", "user", "day")
WINDOW_DAYS = 30  # 计量/配额滚动窗口

_RECORDS: List[Dict[str, Any]] = []


class QuotaExceeded(Exception):
    """部门配额超限：调用前拦截（429），不是事后告警。"""

    def __init__(self, used: int, quota: int):
        self.used = used
        self.quota = quota
        super().__init__(f"quota exceeded: {used}/{quota} tokens（{WINDOW_DAYS} 天）")


# ─────────────────────────────────────────────────────────────────────────────
# 落账
# ─────────────────────────────────────────────────────────────────────────────
def est_tokens(text: str) -> int:
    """token 估算兜底：CJK 1 字≈1 token，其余约 4 字符≈1 token（仅在无真 usage 时用）。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return cjk + max(1, round(other / 4))


def record(caller: CallerContext, agent_id: str, model_id: str,
           usage: Optional[Dict[str, Any]] = None,
           real: bool = False, latency_ms: int = 0,
           prompt_text: str = "", completion_text: str = "") -> Dict[str, Any]:
    """记录一次智能体调用。usage 缺失时按文本估算，保证每次调用都有账。"""
    usage = usage or {}
    prompt = int(usage.get("prompt_tokens") or 0) or est_tokens(prompt_text)
    completion = int(usage.get("completion_tokens") or 0) or est_tokens(completion_text)
    p_price, c_price = model_gateway.price_of(model_id)
    cost = round(prompt / 1000.0 * p_price + completion / 1000.0 * c_price, 6)
    rec = {
        "tenant_id": caller.tenantId,
        "dept_id": caller.deptId,
        "user_id": caller.userId,
        "agent_id": agent_id,
        "model_id": model_id,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cost": cost,
        "real": bool(real),          # 真 LLM 生成 or stub 回退
        "latency_ms": int(latency_ms),
        "ts": time.time(),
    }
    _RECORDS.append(rec)
    if len(_RECORDS) > MAX_INMEM:
        del _RECORDS[: len(_RECORDS) - MAX_INMEM]
    pg_store.insert_usage(rec)
    return rec


def load_from_pg() -> int:
    """启动时从 PG 载回用量记录（重启账单不清零）；PG 不可达时为空操作。"""
    rows = pg_store.load_usage(limit=MAX_INMEM)
    if not rows:
        return 0
    _RECORDS.clear()
    _RECORDS.extend(reversed(rows))  # PG 倒序取出，翻回时间正序
    return len(_RECORDS)


def reset() -> None:
    """清空内存计量（测试与本地重置用，不动 PG）。"""
    _RECORDS.clear()


# ─────────────────────────────────────────────────────────────────────────────
# 权限收敛 + 聚合
# ─────────────────────────────────────────────────────────────────────────────
def _visible(rec: Dict[str, Any], caller: CallerContext) -> bool:
    if rec.get("tenant_id") != caller.tenantId:
        return False
    if caller.role == "platform_admin":
        return True
    if caller.role == "dept_admin":
        return rec.get("dept_id") == caller.deptId
    return rec.get("user_id") == caller.userId


def _key_of(rec: Dict[str, Any], dimension: str) -> str:
    if dimension == "dept":
        return rec.get("dept_id") or "-"
    if dimension == "agent":
        return rec.get("agent_id") or "-"
    if dimension == "model":
        return rec.get("model_id") or "-"
    if dimension == "user":
        return rec.get("user_id") or "-"
    return time.strftime("%Y-%m-%d", time.localtime(rec.get("ts") or 0))  # day


def _window_records(caller: CallerContext, days: Optional[int] = WINDOW_DAYS) -> List[Dict[str, Any]]:
    since = time.time() - days * 86400 if days else 0
    return [r for r in _RECORDS if _visible(r, caller) and (r["ts"] >= since)]


def summary(dimension: str = "dept", caller: CallerContext = None,
            days: Optional[int] = WINDOW_DAYS) -> Dict[str, Any]:
    """按维度聚合出账单。quota 只在 dept 维度有意义（其余维度为 0）。"""
    if dimension not in DIMENSIONS:
        raise ValueError(f"不支持的维度：{dimension}（可选 {','.join(DIMENSIONS)}）")
    quota = int(getattr(settings, "USAGE_QUOTA_TOKENS", 0) or 0)
    bucket: Dict[str, Dict[str, Any]] = {}
    for rec in _window_records(caller, days):
        key = _key_of(rec, dimension)
        row = bucket.setdefault(key, {
            "key": key, "calls": 0, "real_calls": 0, "stub_calls": 0,
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            "cost": 0.0, "latency_ms": 0,
        })
        row["calls"] += 1
        row["real_calls"] += 1 if rec.get("real") else 0
        row["stub_calls"] += 0 if rec.get("real") else 1
        row["prompt_tokens"] += rec["prompt_tokens"]
        row["completion_tokens"] += rec["completion_tokens"]
        row["total_tokens"] += rec["prompt_tokens"] + rec["completion_tokens"]
        row["cost"] = round(row["cost"] + rec["cost"], 6)
        row["latency_ms"] += rec.get("latency_ms", 0)

    rows = []
    for row in bucket.values():
        row["avg_latency_ms"] = round(row["latency_ms"] / row["calls"], 1) if row["calls"] else 0
        if dimension == "dept" and quota:
            row["quota"] = quota
            row["quota_used_pct"] = round(row["total_tokens"] / quota * 100, 2)
        else:
            row["quota"] = 0
            row["quota_used_pct"] = 0.0
        rows.append(row)
    rows.sort(key=lambda r: r["total_tokens"], reverse=True)

    total = {
        "calls": sum(r["calls"] for r in rows),
        "real_calls": sum(r["real_calls"] for r in rows),
        "stub_calls": sum(r["stub_calls"] for r in rows),
        "prompt_tokens": sum(r["prompt_tokens"] for r in rows),
        "completion_tokens": sum(r["completion_tokens"] for r in rows),
        "total_tokens": sum(r["total_tokens"] for r in rows),
        "cost": round(sum(r["cost"] for r in rows), 6),
    }
    return {"dimension": dimension, "window_days": days, "rows": rows, "total": total,
            "quota_tokens": quota, "cost_currency": "CNY"}


def list_records(caller: CallerContext, limit: int = 50) -> List[Dict[str, Any]]:
    """最近调用明细（按权限收敛），页面表格直接渲染。"""
    rows = [r for r in _RECORDS if _visible(r, caller)]
    rows = rows[-limit:]
    rows.reverse()
    return [{
        "ts": r["ts"], "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["ts"])),
        "agent_id": r["agent_id"], "model_id": r["model_id"], "dept_id": r["dept_id"],
        "user_id": r["user_id"], "prompt_tokens": r["prompt_tokens"],
        "completion_tokens": r["completion_tokens"], "cost": r["cost"],
        "real": bool(r.get("real")), "latency_ms": int(r.get("latency_ms") or 0),
    } for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# 配额（红线）
# ─────────────────────────────────────────────────────────────────────────────
def quota_status(caller: CallerContext) -> Dict[str, Any]:
    """当前调用者所属部门的配额状态（滚动 30 天）。"""
    quota = int(getattr(settings, "USAGE_QUOTA_TOKENS", 0) or 0)
    used = sum(r["prompt_tokens"] + r["completion_tokens"]
               for r in _window_records(caller)
               if r.get("dept_id") == caller.deptId)
    return {
        "dept_id": caller.deptId,
        "quota_tokens": quota,
        "used_tokens": used,
        "remaining_tokens": max(0, quota - used) if quota else None,
        "used_pct": round(used / quota * 100, 2) if quota else 0.0,
        "exceeded": bool(quota) and used >= quota,
        "window_days": WINDOW_DAYS,
    }


def enforce_quota(caller: CallerContext) -> Dict[str, Any]:
    """调用前配额校验：超限抛 QuotaExceeded（路由层转 429）。0 表示不限额。"""
    st = quota_status(caller)
    if st["exceeded"]:
        raise QuotaExceeded(st["used_tokens"], st["quota_tokens"])
    return st


# ─────────────────────────────────────────────────────────────────────────────
# M8 成本曲线（试点两周的核心问题：这个部门到底烧不烧得起）
# ─────────────────────────────────────────────────────────────────────────────
def trend(caller: CallerContext, days: int = 14) -> Dict[str, Any]:
    """按自然日聚合的用量/成本曲线 + 月累计 + 月末外推 + 预算水位。

    - 序列**连续补齐**（没有调用的日子补 0）：否则前端折线会把空白日连成直线，
      看上去像"天天在用"，误导试点判断。
    - 月末外推用「近 7 个自然日日均 × 当月剩余天数 + 本月已发生」，比"按当前累计直接
      乘 30"稳，不会被月初的一次压测带飞。
    """
    days = max(1, min(365, int(days or 14)))
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=days - 1)
    since = start.timestamp()

    buckets: Dict[str, Dict[str, Any]] = {}
    for i in range(days):
        d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        buckets[d] = {"date": d, "calls": 0, "real_calls": 0, "stub_calls": 0,
                      "prompt_tokens": 0, "completion_tokens": 0, "tokens": 0, "cost": 0.0}

    for rec in _RECORDS:
        if not _visible(rec, caller) or rec["ts"] < since:
            continue
        key = time.strftime("%Y-%m-%d", time.localtime(rec["ts"]))
        b = buckets.get(key)
        if not b:
            continue
        b["calls"] += 1
        b["real_calls"] += 1 if rec.get("real") else 0
        b["stub_calls"] += 0 if rec.get("real") else 1
        b["prompt_tokens"] += rec["prompt_tokens"]
        b["completion_tokens"] += rec["completion_tokens"]
        b["tokens"] += rec["prompt_tokens"] + rec["completion_tokens"]
        b["cost"] = round(b["cost"] + rec["cost"], 6)

    series = [buckets[k] for k in sorted(buckets)]
    total_cost = round(sum(b["cost"] for b in series), 6)
    total_tokens = sum(b["tokens"] for b in series)
    total_calls = sum(b["calls"] for b in series)
    active_days = sum(1 for b in series if b["calls"])
    peak = max(series, key=lambda b: b["cost"], default={"date": "-", "cost": 0.0})

    month_key = today.strftime("%Y-%m")
    mtd = round(sum(b["cost"] for b in series if b["date"].startswith(month_key)), 6)
    mtd_tokens = sum(b["tokens"] for b in series if b["date"].startswith(month_key))
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    elapsed_days = today.day
    last7 = series[-7:]
    avg7 = round(sum(b["cost"] for b in last7) / max(1, len(last7)), 6)
    projected = round(mtd + avg7 * max(0, days_in_month - elapsed_days), 6)

    budget = float(getattr(settings, "USAGE_MONTHLY_BUDGET_CNY", 0) or 0)
    used_pct = round(mtd / budget * 100, 2) if budget else 0.0
    proj_pct = round(projected / budget * 100, 2) if budget else 0.0
    if not budget:
        status = "unset"
    elif proj_pct >= 100 or used_pct >= 100:
        status = "over"
    elif proj_pct >= 80:
        status = "warn"
    else:
        status = "ok"

    return {
        "window_days": days,
        "start": series[0]["date"] if series else start.strftime("%Y-%m-%d"),
        "end": series[-1]["date"] if series else today.strftime("%Y-%m-%d"),
        "series": series,
        "calls": total_calls,
        "tokens": total_tokens,
        "cost": total_cost,
        "real_calls": sum(b["real_calls"] for b in series),
        "active_days": active_days,
        "zero_days": days - active_days,
        "avg_daily_cost": round(total_cost / days, 6),
        "peak_day": {"date": peak["date"], "cost": peak["cost"]},
        "month_to_date_cost": mtd,
        "month_to_date_tokens": mtd_tokens,
        "days_in_month": days_in_month,
        "elapsed_days": elapsed_days,
        "avg_7d_cost": avg7,
        "projected_month_cost": projected,
        "budget_cny": budget,
        "budget_used_pct": used_pct,
        "projected_budget_pct": proj_pct,
        "budget_status": status,
        "currency": "CNY",
        "quota": quota_status(caller),
    }
