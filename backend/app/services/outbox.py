"""P0-5 可靠投递（outbox 模式）。

设计目标：自动化任务触发后，执行结果「至少一次」送达，不因进程崩溃 / 重启丢失。
机制：消息先落 outbox（内存 + PG 持久副本），由后台 worker 轮询最终投递。
- 投递成功才标记 delivered；失败按指数退避重试，超过 OUTBOX_MAX_ATTEMPTS 标记 dead（留台账人工重投）。
- 投递目标：channel=webhook 时 POST 到 webhook_url；artifact/notification 视为「已落盘/已通知」即送达
  （真实外发通道——企业微信/钉钉——在 P1 接入，本层只保证不丢、可重投）。

所有持久化失败静默（离线/抖动不阻断业务），内存始终为主存。
"""
import json
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.services import pg_store

_AUTOS_LOCK = threading.RLock()
_OUTBOX: Dict[str, Dict[str, Any]] = {}
_worker_thread: Optional[threading.Thread] = None
_worker_stop = threading.Event()


def _now() -> float:
    return time.time()


def enabled() -> bool:
    return settings.SCHEDULER_ENABLED


def _backoff(attempt: int) -> float:
    """指数退避（秒）：30s, 60s, 120s ... 上限 1h。"""
    return min(30 * (2 ** (attempt - 1)), 3600)


def _persist(item: Dict[str, Any]) -> None:
    pg_store.upsert("outbox", item["outbox_id"], item)


def enqueue(tenant_id: str, automation_id: str, run_id: str,
            channel: str, payload: Dict[str, Any],
            webhook_url: Optional[str] = None) -> str:
    """入队一条待投递消息，返回 outbox_id。"""
    oid = "ob_" + uuid.uuid4().hex[:12]
    now = _now()
    item = {
        "outbox_id": oid,
        "tenant_id": tenant_id or "",
        "automation_id": automation_id or "",
        "run_id": run_id or "",
        "channel": channel,
        "webhook_url": webhook_url or "",
        "payload": payload,
        "status": "pending",          # pending | delivered | failed | dead
        "attempts": 0,
        "next_retry": now,
        "last_error": "",
        "created_at": now,
        "updated_at": now,
    }
    with _AUTOS_LOCK:
        _OUTBOX[oid] = item
    _persist(item)
    return oid


def _post_webhook(url: str, payload: Dict[str, Any]) -> Tuple[int, str]:
    """默认用标准库 urllib（无额外依赖）。测试可 monkeypatch 此函数。"""
    import urllib.request
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, resp.read().decode("utf-8", "replace")


def _deliver(item: Dict[str, Any]) -> bool:
    """执行一次投递，返回是否成功。"""
    if item["channel"] == "webhook" and item["webhook_url"]:
        try:
            code, _ = _post_webhook(item["webhook_url"], item["payload"])
            if 200 <= code < 300:
                return True
            item["last_error"] = f"webhook returned {code}"
            return False
        except Exception as e:  # 网络抖动 / 超时：留给退避重试
            item["last_error"] = f"{type(e).__name__}: {e}"
            return False
    # artifact / notification：产出物已落盘、通知已记录 → 视为送达
    # （真实外发企业在 P1 接入，本层只保证不丢与可重投）
    return True


def worker_tick() -> int:
    """轮询一次：投递所有到点的 pending/failed 条目。返回成功送达数。"""
    now = _now()
    with _AUTOS_LOCK:
        due = [i for i in _OUTBOX.values()
               if i["status"] in ("pending", "failed")
               and i["attempts"] < settings.OUTBOX_MAX_ATTEMPTS
               and i["next_retry"] <= now]
    delivered = 0
    for item in due:
        ok = _deliver(item)
        item["updated_at"] = _now()
        if ok:
            item["status"] = "delivered"
            item["last_error"] = ""
            delivered += 1
        else:
            item["status"] = "failed"
            item["attempts"] += 1
            item["next_retry"] = _now() + _backoff(item["attempts"])
        _persist(item)
    return delivered


def _worker_loop() -> None:
    while not _worker_stop.is_set():
        try:
            worker_tick()
        except Exception:
            pass
        _worker_stop.wait(settings.OUTBOX_INTERVAL_SEC)


def start_worker() -> None:
    """启动后台投递 worker 线程（main lifespan 调用）。"""
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _worker_stop.clear()
    _worker_thread = threading.Thread(target=_worker_loop, name="outbox-worker", daemon=True)
    _worker_thread.start()


def stop_worker() -> None:
    _worker_stop.set()


def list_outbox(tenant_id: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
    with _AUTOS_LOCK:
        rows = [dict(i) for i in _OUTBOX.values() if i["tenant_id"] == (tenant_id or "")]
    if status:
        rows = [r for r in rows if r["status"] == status]
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    return rows


def retry(outbox_id: str) -> bool:
    """手动重投一条（重置退避立即投）。"""
    with _AUTOS_LOCK:
        item = _OUTBOX.get(outbox_id)
        if not item:
            return False
        item["attempts"] = 0
        item["next_retry"] = _now()
        item["status"] = "pending"
        _persist(item)
    return True


def load_from_pg() -> None:
    """启动载回持久化 outbox（重启不丢待投递消息）。"""
    for item in pg_store.load_all("outbox"):
        with _AUTOS_LOCK:
            _OUTBOX[item["outbox_id"]] = item


def reset() -> None:
    """测试隔离：清空内存 outbox。"""
    with _AUTOS_LOCK:
        _OUTBOX.clear()
    _worker_stop.set()
    _worker_stop.clear()
