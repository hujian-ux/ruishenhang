"""M27 · 运维可观测 / 就绪度 / 审计留存 / 数据主体删除（第 26/29/31 章）。

设计取舍：
  - 进程内聚合：计数器全量累计（int 不膨胀），延迟/明细样本固定窗口 2000 条，
    p95 从窗口算；按 path 分桶。reset_state() 供测试与演练清零。
  - 诚实口径：health 只把「实际观测到的」写成 observed，外部依赖一律
    not_verified；readiness 的 configured 只表示「检测到配置」，绝不冒充验收通过；
    免费网页抓取源不计入生产就绪；告警在样本不足时宁可不报也不误报。
  - 审计导出复用 services/audit.py 的既有 RBAC，不另起判权；删除数据对
    会话/附件/产出物等其它 store 管理的部分明确列 manual_review，不谎称全删。
"""
from __future__ import annotations

import math
import os
import threading
from collections import deque
from typing import Any, Dict, List

from app.core.config import settings
from app.services._m27_store import Store, caller_id, caller_role, iso, new_id, now

_LOCK = threading.RLock()

# 最近样本窗口：固定上限，防止内存随流量无限膨胀
MAX_SAMPLES = 2000
# 告警最小样本：低于它不做阈值判断（1 次失败就报 100% 错误率是典型误报）
MIN_SAMPLES = 20

_COUNTERS: Dict[str, int] = {"requests_total": 0, "errors_total": 0, "model_429_total": 0}
_SAMPLES: deque = deque(maxlen=MAX_SAMPLES)
_BY_PATH: Dict[str, Dict[str, Any]] = {}
_LAST_ALERT_NOTE: str = ""

# 本模块承接的用户反馈台账（供 delete_my_data 级联清理 / 备份快照）
FEEDBACK = Store("m27_feedback")

# 四项外部输入的「一键验收命令」：只给可复制命令，不代替跑出结论
_VERIFY_COMMANDS = {
    "llm_key": ".venv/Scripts/python.exe scripts/accept_real_llm.py",
    "search_key": ".venv/Scripts/python.exe scripts/verify_web_search.py",
    "oidc_idp": ".venv/Scripts/python.exe scripts/pilot_second_dept.py",
    "real_corpus": ".venv/Scripts/python.exe scripts/verify_rag_answers.py",
}

_DISCLAIMER = (
    "configured 仅表示在 os.environ 检测到配置，不代表端到端验收通过；"
    "免费网页抓取源（WEB_SEARCH_ENABLED/PROVIDER=auto）不计入生产就绪；"
    "请以各项 verify_command 的实测结论为准。"
)


# ── 观测采集 ────────────────────────────────────────────────────────────────

def observe_request(method: str, path: str, status: int, elapsed_ms: float,
                    trace_id: str = "") -> None:
    """累计请求/错误/429、延迟样本、按 path 分桶；样本窗口固定 2000 条。"""
    with _LOCK:
        st = int(status)
        ms = float(elapsed_ms)
        is_error = st >= 400
        is_429 = st == 429

        _COUNTERS["requests_total"] += 1
        if is_error:
            _COUNTERS["errors_total"] += 1
        if is_429:
            _COUNTERS["model_429_total"] += 1

        bucket = _BY_PATH.setdefault(
            str(path), {"requests": 0, "errors": 0, "429": 0, "latency_sum": 0.0}
        )
        bucket["requests"] += 1
        if is_error:
            bucket["errors"] += 1
        if is_429:
            bucket["429"] += 1
        bucket["latency_sum"] += ms

        _SAMPLES.append({
            "method": str(method), "path": str(path), "status": st,
            "elapsed_ms": ms, "trace_id": trace_id or "", "ts": now(),
        })


def _p95_locked() -> float:
    """nearest-rank p95，基于最近窗口样本。调用方须持有 _LOCK。"""
    xs = sorted(s["elapsed_ms"] for s in _SAMPLES)
    if not xs:
        return 0.0
    rank = max(1, math.ceil(0.95 * len(xs)))
    return round(xs[rank - 1], 2)


def _snapshot_locked() -> Dict[str, Any]:
    """统一指标快照，health / alerts 共用，避免两套口径。调用方须持有 _LOCK。"""
    req = _COUNTERS["requests_total"]
    return {
        "requests_total": req,
        "errors_total": _COUNTERS["errors_total"],
        "model_429_total": _COUNTERS["model_429_total"],
        "error_rate": (_COUNTERS["errors_total"] / req) if req else 0.0,
        "latency_p95_ms": _p95_locked(),
        "sample_window": len(_SAMPLES),
        "by_path": {p: dict(v) for p, v in _BY_PATH.items()},
        "sufficient": req >= MIN_SAMPLES,
    }


def _esc_label(value: Any) -> str:
    """Prometheus label 值转义（反斜杠 / 双引号 / 换行）。"""
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def prometheus_text() -> str:
    """合法 Prometheus text exposition：HELP/TYPE + 4 个核心指标 + 按 path 分桶。"""
    with _LOCK:
        snap = _snapshot_locked()
        by_path = snap["by_path"]
    c = snap
    lines: List[str] = [
        "# HELP requests_total Total observed HTTP requests.",
        "# TYPE requests_total counter",
        f"requests_total {c['requests_total']}",
        "",
        "# HELP errors_total Total observed HTTP error responses (status >= 400).",
        "# TYPE errors_total counter",
        f"errors_total {c['errors_total']}",
        "",
        "# HELP latency_p95_ms 95th percentile of recent request latency in milliseconds.",
        "# TYPE latency_p95_ms gauge",
        f"latency_p95_ms {c['latency_p95_ms']}",
        "",
        "# HELP model_429_total Total observed HTTP 429 responses.",
        "# TYPE model_429_total counter",
        f"model_429_total {c['model_429_total']}",
        "",
        "# HELP requests_by_path_total Observed requests grouped by path.",
        "# TYPE requests_by_path_total counter",
    ]
    for p, b in sorted(by_path.items()):
        lines.append(f'requests_by_path_total{{path="{_esc_label(p)}"}} {b["requests"]}')
    return "\n".join(lines) + "\n"


# ── 健康 / 告警 ─────────────────────────────────────────────────────────────

def health(caller=None) -> dict:
    """健康画像。只把实际观测到的写成 observed，未验的外部依赖不写 PASS。"""
    with _LOCK:
        snap = _snapshot_locked()
    insufficient = not snap["sufficient"]
    note = (f"样本不足（{snap['requests_total']} < {MIN_SAMPLES}），"
            "暂不评估错误率/延迟/429 告警阈值，避免误报"
            if insufficient else f"样本充足（>= {MIN_SAMPLES}），告警阈值已启用")
    return {
        "status": "insufficient_data" if insufficient else "operational",
        "components": {
            "metrics": {
                "status": "observed" if snap["requests_total"] > 0 else "no_data",
                "requests_total": snap["requests_total"],
                "errors_total": snap["errors_total"],
                "error_rate": round(snap["error_rate"], 4),
                "latency_p95_ms": snap["latency_p95_ms"],
                "model_429_total": snap["model_429_total"],
                "sample_window": snap["sample_window"],
                "sample_window_cap": MAX_SAMPLES,
                "by_path": snap["by_path"],
            },
            # 未做端到端验收，一律 not_verified，绝不写 PASS
            "audit_backend": {"status": "not_verified", "note": "health 未探测审计后端可用性"},
            "llm_key": {"status": "not_verified", "note": "见 /ops/readiness，需 accept_real_llm.py 实测"},
            "search_key": {"status": "not_verified", "note": "见 /ops/readiness，需 verify_web_search.py 实测"},
            "oidc_idp": {"status": "not_verified", "note": "见 /ops/readiness，需 pilot_second_dept.py 实测"},
            "real_corpus": {"status": "not_verified", "note": "见 /ops/readiness，需 verify_rag_answers.py 实测"},
        },
        "retention_days": settings.AUDIT_RETENTION_DAYS,
        "security_event_retention_days": settings.SECURITY_EVENT_RETENTION_DAYS,
        "insufficient_data": {
            "insufficient": insufficient,
            "min_samples": MIN_SAMPLES,
            "observed_samples": snap["requests_total"],
            "reason": note,
        },
        "caller": caller_id(caller) if caller is not None else None,
    }


def alerts() -> list:
    """按 OBSERVABILITY 阈值生成告警；样本不足返回空列表（说明见 health）。

    样本不足的「说明」落在 health()["insufficient_data"]["reason"] 与
    _LAST_ALERT_NOTE（模块级），避免为了塞一句话而污染告警列表类型。
    """
    global _LAST_ALERT_NOTE
    with _LOCK:
        snap = _snapshot_locked()

    if not snap["sufficient"]:
        _LAST_ALERT_NOTE = (f"样本不足（{snap['requests_total']} < {MIN_SAMPLES}），"
                            "不评估告警阈值，避免误报")
        return []

    _LAST_ALERT_NOTE = "样本充足，告警阈值已启用"
    out: List[Dict[str, Any]] = []
    if snap["error_rate"] > settings.OPS_ALERT_ERROR_RATE:
        out.append({
            "type": "error_rate", "severity": "warning",
            "metric": "errors_total / requests_total",
            "value": round(snap["error_rate"], 4),
            "threshold": settings.OPS_ALERT_ERROR_RATE,
            "message": f"错误率 {snap['error_rate']:.4f} 超过阈值 {settings.OPS_ALERT_ERROR_RATE}",
        })
    if snap["latency_p95_ms"] > settings.OPS_ALERT_P95_MS:
        out.append({
            "type": "latency_p95", "severity": "warning",
            "metric": "latency_p95_ms",
            "value": snap["latency_p95_ms"],
            "threshold": settings.OPS_ALERT_P95_MS,
            "message": f"延迟 p95 {snap['latency_p95_ms']}ms 超过阈值 {settings.OPS_ALERT_P95_MS}ms",
        })
    if snap["model_429_total"] >= settings.OPS_ALERT_MODEL_429_SPIKE:
        out.append({
            "type": "model_429_spike", "severity": "warning",
            "metric": "model_429_total",
            "value": snap["model_429_total"],
            "threshold": settings.OPS_ALERT_MODEL_429_SPIKE,
            "message": f"429 累计 {snap['model_429_total']} 达到尖峰阈值 {settings.OPS_ALERT_MODEL_429_SPIKE}",
        })
    return out


# ── 就绪度（4 项外部输入）─────────────────────────────────────────────────────

def _env_any(*names: str) -> bool:
    return any(os.environ.get(n, "").strip() for n in names)


def _ready_item(name: str, configured: bool, note: str) -> dict:
    return {
        "configured": bool(configured),
        # 即便配置了也只说「待验收」，绝不写 PASS / ready
        "status": "configured_unverified" if configured else "not_configured",
        "verify_command": _VERIFY_COMMANDS[name],
        "note": note,
    }


def readiness() -> dict:
    """4 项外部输入的就绪度；configured 完全由 os.environ 真实检测，不伪造。"""
    env = os.environ
    llm = _env_any("LLM_API_KEY", "ZHIPU_API_KEY", "OPENAI_API_KEY")
    search = _env_any("BOCHA_API_KEY", "SERPER_API_KEY", "TAVILY_API_KEY", "BING_SEARCH_KEY")
    oidc = (env.get("OIDC_ENABLED", "").strip().lower() in ("1", "true", "yes")
            and bool(env.get("OIDC_ISSUER", "").strip())
            and bool(env.get("OIDC_AUDIENCE", "").strip()))
    # 真实语料进场 = 显式关闭演示语料（SEED_DEMO_CORPUS=0）。仅表示「已切换模式」，
    # 真实语料是否真的有效仍需 verify_rag_answers.py 验收。
    real_corpus = env.get("SEED_DEMO_CORPUS", "1").strip().lower() in ("0", "false", "no")

    return {
        "llm_key": _ready_item(
            "llm_key", llm,
            "检测 LLM_API_KEY / ZHIPU_API_KEY / OPENAI_API_KEY；留空即走 stub，不算真模型就绪。"
        ),
        "search_key": _ready_item(
            "search_key", search,
            "仅检测官方 key（BOCHA/SERPER/TAVILY/BING）；免费网页抓取源（搜狗/Bing 页面）"
            "不算生产就绪，WEB_SEARCH_ENABLED/PROVIDER 不参与判定。"
        ),
        "oidc_idp": _ready_item(
            "oidc_idp", oidc,
            "需 OIDC_ENABLED=1 且 OIDC_ISSUER / OIDC_AUDIENCE 均非空；dev token 不算企业 SSO 就绪。"
        ),
        "real_corpus": _ready_item(
            "real_corpus", real_corpus,
            "SEED_DEMO_CORPUS=0 表示已关闭演示语料；这只代表切换模式，真实语料是否"
            "进场并达标须跑 verify_rag_answers.py 验收。"
        ),
        "disclaimer": _DISCLAIMER,
        "evaluated_at": iso(),
    }


# ── 审计导出 / 数据主体删除 ──────────────────────────────────────────────────

def audit_export(caller) -> dict:
    """只读导出：复用 services/audit.get_logs 的既有 RBAC，不另起判权。"""
    from app.services import audit

    logs = audit.get_logs(caller)
    role = caller_role(caller)
    scope_map = {"platform_admin": "all", "dept_admin": "department", "employee": "self"}
    return {
        "logs": logs,
        "count": len(logs),
        "retention_days": settings.AUDIT_RETENTION_DAYS,
        "scope": scope_map.get(role, "self"),
        "role": role,
        "exported_at": iso(),
        "read_only": True,
        "rbac_note": ("复用 audit.get_logs 既有 RBAC：employee 只看自己、dept_admin 本部门、"
                      "platform_admin 全量；本导出不另起判权"),
    }


def delete_my_data(caller) -> dict:
    """数据主体权利：尽力级联清理，返回各类计数 + manual_review（不谎称全删）。"""
    from app.services import events, knowledge

    uid = caller_id(caller)

    # 1) 知识文档：owner == 本人
    doc_ids = [p["doc_id"] for p in knowledge.get_parents()
               if p.get("owner") == uid and p.get("doc_id")]
    doc_removed = knowledge.remove_documents(doc_ids)

    # 2) 本模块反馈台账
    fb_rows = [r for r in FEEDBACK.list(limit=10 ** 6) if r.get("user_id") == uid]
    for r in fb_rows:
        FEEDBACK.delete(r["id"])

    # 3) 事件总线（订阅 + 事件台账）
    purged = events.purge_user(uid)

    return {
        "documents": {"removed": doc_removed},
        "feedback": {"removed": len(fb_rows)},
        "events": {"removed": purged["events"]},
        "subscriptions": {"removed": purged["subscriptions"]},
        "manual_review": {
            "items": [
                "会话 / 对话记忆（session_store）",
                "附件 / 上传件（file_store、object_store / MinIO）",
                "生成产出物（artifact）",
                "向量库子块、嵌入缓存、外部索引残留",
            ],
            "note": ("以上数据由其它 store 管理，本操作未删除，需人工确认并另行清理；"
                     "不谎称已全删。审计日志依法留存（见 retention_days），不在自助删除范围。"),
            "retention_days": settings.AUDIT_RETENTION_DAYS,
        },
        "subject": uid,
        "executed_at": iso(),
    }


# ── 反馈台账写入（供主链路/测试使用）─────────────────────────────────────────

def add_feedback(caller, message: str, kind: str = "general") -> dict:
    return FEEDBACK.put({
        "id": new_id("fb"),
        "user_id": caller_id(caller),
        "kind": str(kind),
        "message": str(message),
    }, key="id")


# ── 备份 / 恢复（供 scripts/backup_restore.py 调用）────────────────────────────

def snapshot() -> dict:
    with _LOCK:
        return {
            "counters": dict(_COUNTERS),
            "samples": [dict(s) for s in _SAMPLES],
            "by_path": {p: dict(v) for p, v in _BY_PATH.items()},
            "feedback": FEEDBACK.list(limit=10 ** 6, newest_first=False),
        }


def restore(data: dict) -> None:
    reset_state()
    d = data or {}
    with _LOCK:
        for k, v in (d.get("counters") or {}).items():
            if k in _COUNTERS:
                _COUNTERS[k] = int(v)
        for s in (d.get("samples") or []):
            _SAMPLES.append(dict(s))
        for p, v in (d.get("by_path") or {}).items():
            _BY_PATH[p] = dict(v)
    for r in (d.get("feedback") or []):
        FEEDBACK.put(dict(r), key="id")


def reset_state() -> None:
    global _LAST_ALERT_NOTE
    with _LOCK:
        _COUNTERS.update(requests_total=0, errors_total=0, model_429_total=0)
        _SAMPLES.clear()
        _BY_PATH.clear()
        FEEDBACK.clear()
        _LAST_ALERT_NOTE = ""
