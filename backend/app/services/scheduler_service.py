"""P0-5 调度服务（自动化任务）。

把「定时/周期触发一个智能体」建模为 automation：
- schedule_type ∈ {cron, interval, once}；expr 为标准 cron / 秒数(可带 s/m/h) / ISO 时间。
- 触发时构造 owner 的 CallerContext，渲染 input_template（仅 {today}/{date}/{agent_name} 占位），
  调 agent_runtime.run_agent，结果入 outbox 可靠投递。
- tenant 隔离是红线：本租户只能看到/触发本租户的任务。

APScheduler 后台线程仅在非 test 且 SCHEDULER_ENABLED 时真正 start；测试通过 run_now_sync 直接触发。
"""
import datetime
import re
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.models.caller import CallerContext
from app.services import agent_runtime, agent_store, outbox, pg_store

_SCHED = None
_AUTOS: Dict[str, Dict[str, Any]] = {}
_lock = threading.RLock()

# 可被测试替换（默认指向真实 run_agent）
_run_agent = agent_runtime.run_agent


# ── 表达式校验与触发器构造 ───────────────────────────────────────────────
def _parse_interval(expr: str) -> int:
    expr = (expr or "").strip().lower()
    m = re.match(r"^(\d+)\s*([smh]?)$", expr)
    if not m:
        raise ValueError(f"interval 表达式非法：{expr!r}（示例 30 / 30s / 5m / 2h）")
    n = int(m.group(1))
    unit = m.group(2)
    return n * {"s": 1, "m": 60, "h": 3600, "": 1}[unit]


def _build_trigger(stype: str, expr: str):
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
    from apscheduler.triggers.date import DateTrigger
    if stype == "cron":
        return CronTrigger.from_crontab(expr)
    if stype == "interval":
        return IntervalTrigger(seconds=_parse_interval(expr))
    if stype == "once":
        if not expr or not expr.strip():
            raise ValueError("once 类型需提供 ISO 时间（如 2026-12-01T09:00:00）")
        return DateTrigger(run_date=datetime.datetime.fromisoformat(expr.strip()))


def _validate_expr(stype: str, expr: str) -> None:
    _build_trigger(stype, expr)  # 构造失败即抛


# ── input 模板渲染（受限占位，禁用任意代码） ──────────────────────────────
def _render_input(template: str, agent_name: str) -> str:
    if not template:
        return ""
    today = datetime.date.today().isoformat()
    return (template
            .replace("{today}", today)
            .replace("{date}", today)
            .replace("{agent_name}", agent_name or ""))


# ── 持久化 ───────────────────────────────────────────────────────────────
def _persist(auto: Dict[str, Any]) -> None:
    pg_store.upsert("automations", auto["automation_id"], auto)


# ── 触发执行 ─────────────────────────────────────────────────────────────
def _fire(automation_id: str) -> Dict[str, Any]:
    with _lock:
        auto = _AUTOS.get(automation_id)
    if not auto:
        return {"run_id": "", "error": "not_found"}
    tenant = auto["tenant_id"]
    caller = CallerContext(userId=auto["owner_id"], deptId=auto.get("dept_id", ""),
                           role=auto.get("role", "platform_admin"), tenantId=tenant)
    agent_name = ""
    try:
        ag = agent_store.get_agent(auto["agent_id"])
        agent_name = (ag or {}).get("name", "")
    except Exception:
        pass
    user_input = _render_input(auto["input_template"], agent_name)
    run_id = "run_" + uuid.uuid4().hex[:10]
    started = _now()
    err = ""
    answer = ""
    arts: List[Any] = []
    try:
        result = _run_agent(auto["agent_id"], user_input, caller,
                            history=[], system_prompt=None)
        answer = result.get("answer", "") or ""
        arts = result.get("artifacts", []) or []
    except Exception as e:  # 任务体异常不拖垮 scheduler
        err = f"{type(e).__name__}: {e}"
    finished = _now()
    with _lock:
        auto["last_run"] = started
        auto["last_error"] = err
        _persist(auto)
    file_ids = [a.get("file_id") if isinstance(a, dict) else a for a in arts]
    # 可靠投递：产出物落盘即 artifact 渠道送达；配 webhook 再发一份
    outbox.enqueue(tenant, automation_id, run_id, "artifact",
                   {"run_id": run_id, "answer": answer[:2000],
                    "artifacts": file_ids, "error": err,
                    "latency_ms": int((finished - started) * 1000)})
    if auto.get("webhook_url"):
        outbox.enqueue(tenant, automation_id, run_id, "webhook",
                       {"run_id": run_id, "answer": answer[:4000], "artifacts": file_ids},
                       webhook_url=auto["webhook_url"])
    return {"run_id": run_id, "error": err}


def _now() -> float:
    return time.time()


# ── 增删改查 ─────────────────────────────────────────────────────────────
def create_automation(caller: CallerContext, name: str, agent_id: str,
                      input_template: str, schedule_type: str, schedule_expr: str,
                      enabled: bool = True, webhook_url: Optional[str] = None,
                      payload: Optional[Dict[str, Any]] = None) -> str:
    if schedule_type not in ("cron", "interval", "once"):
        raise ValueError("schedule_type 必须是 cron / interval / once")
    if schedule_type != "once":
        _validate_expr(schedule_type, schedule_expr)
    aid = "auto_" + uuid.uuid4().hex[:12]
    auto = {
        "automation_id": aid,
        "name": name,
        "agent_id": agent_id,
        "input_template": input_template,
        "schedule_type": schedule_type,
        "schedule_expr": schedule_expr,
        "enabled": bool(enabled),
        "webhook_url": webhook_url or "",
        "payload": payload or {},
        "owner_id": caller.userId,
        "dept_id": caller.deptId,
        "role": caller.role,
        "tenant_id": caller.tenantId,
        "created_at": _now(),
        "last_run": None,
        "last_error": "",
    }
    with _lock:
        _AUTOS[aid] = auto
    _persist(auto)
    if auto["enabled"]:
        _register_job(auto)
        if schedule_type == "once" and not schedule_expr.strip():
            run_now_sync(aid)  # 无指定时间则立即跑一次
    return aid


def _register_job(auto: Dict[str, Any]) -> None:
    if _SCHED is None:
        return
    try:
        if auto["schedule_type"] == "once" and not auto["schedule_expr"].strip():
            return  # 无时间不挂定时（已立即跑）
        trigger = _build_trigger(auto["schedule_type"], auto["schedule_expr"])
        _SCHED.add_job(_fire, trigger, id=auto["automation_id"],
                       replace_existing=True, args=[auto["automation_id"]])
    except Exception:
        pass


def pause(automation_id: str) -> bool:
    with _lock:
        auto = _AUTOS.get(automation_id)
        if not auto:
            return False
        auto["enabled"] = False
        _persist(auto)
    if _SCHED:
        try:
            _SCHED.remove_job(automation_id)
        except Exception:
            pass
    return True


def resume(automation_id: str) -> bool:
    with _lock:
        auto = _AUTOS.get(automation_id)
        if not auto:
            return False
        auto["enabled"] = True
        _persist(auto)
    _register_job(auto)
    return True


def delete(automation_id: str) -> bool:
    with _lock:
        if automation_id not in _AUTOS:
            return False
        del _AUTOS[automation_id]
    if _SCHED:
        try:
            _SCHED.remove_job(automation_id)
        except Exception:
            pass
    return True


def list_automations(tenant_id: str) -> List[Dict[str, Any]]:
    with _lock:
        rows = [dict(a) for a in _AUTOS.values() if a["tenant_id"] == (tenant_id or "")]
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    return rows


def get(automation_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        return dict(_AUTOS[automation_id]) if automation_id in _AUTOS else None


def run_now_sync(automation_id: str) -> Dict[str, Any]:
    """同步触发（测试/手动按钮用）。"""
    return _fire(automation_id)


def run_now(automation_id: str) -> Dict[str, Any]:
    """异步触发（HTTP 端点用），丢到线程避免阻塞请求。"""
    import threading
    out: Dict[str, Any] = {}
    done = threading.Event()

    def _t():
        nonlocal out
        out = _fire(automation_id)
        done.set()
    threading.Thread(target=_t, daemon=True).start()
    done.wait(timeout=60)
    return out or {"run_id": "", "error": "timeout"}


# ── 生命周期 ─────────────────────────────────────────────────────────────
def init_scheduler() -> bool:
    """创建 APScheduler 后台线程并注册所有 enabled 任务。"""
    global _SCHED
    if _SCHED is not None:
        return True
    if not settings.SCHEDULER_ENABLED:
        return False
    from apscheduler.schedulers.background import BackgroundScheduler
    _SCHED = BackgroundScheduler(timezone="Asia/Shanghai")
    with _lock:
        for auto in list(_AUTOS.values()):
            if auto.get("enabled"):
                _register_job(auto)
    # test 环境不真正 start（避免挂线程/误触发）；由 run_now_sync 驱动
    if settings.APP_ENV != "test":
        _SCHED.start()
    return True


def shutdown_scheduler() -> None:
    global _SCHED
    if _SCHED is not None:
        try:
            _SCHED.shutdown(wait=False)
        except Exception:
            pass
        _SCHED = None


def load_from_pg() -> None:
    for auto in pg_store.load_all("automations"):
        with _lock:
            _AUTOS[auto["automation_id"]] = auto


def reset() -> None:
    """测试隔离：清空状态并停掉 scheduler。"""
    shutdown_scheduler()
    with _lock:
        _AUTOS.clear()
