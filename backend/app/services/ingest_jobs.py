"""异步导入任务（M10）：把万篇级导入从「请求里干等」变成「提交即返回 + 轮询进度」。

为什么需要这一层：
  同步 `/kb/documents/bulk` 一次最多 50 个文件，导入 1 万篇要分 200 次请求；
  CLI（ingest_corpus.py）能导入但不能在服务里跑。真实客户把 1 万篇丢进来时，
  同步接口会占住一个 worker 几十秒（接真 embedding 模型后是分钟级），前端只能转圈。

设计取舍：
  - **单 worker 串行**：索引结构不是并发安全的容器，多任务并行只会互相破坏；
    串行还能保证「先提交先完成」，进度条不会跳。
  - **200 篇一批**：进度可见粒度 + 单批内存可控，失败只丢一批不影响已入库的。
  - **任务记录只放内存**：重启丢任务单不会丢数据（已入库的文档在 PG 里），
    为任务单再建一张表不值；`GET /kb/stats` 的 documents 才是真相。
"""
from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from app.models.caller import CallerContext
from app.services import kb_store

CHUNK = 200          # 每批入库篇数（进度可见粒度）
MAX_DOCS = 20000     # 单任务上限（防一次请求把内存打爆）
MAX_JOBS = 100       # 保留最近任务数（超出淘汰最老的）
KEEP_FAILURES = 20   # 任务里最多留多少条失败明细

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kb-ingest")
_jobs: Dict[str, Dict[str, Any]] = {}
_order: List[str] = []
_lock = threading.Lock()


def _now() -> float:
    return time.time()


def _public(job: Dict[str, Any]) -> Dict[str, Any]:
    """对外视图：不带原始 rows（可能是几 MB 正文）。"""
    return {k: v for k, v in job.items() if not k.startswith("_")}


def submit(rows: List[Dict[str, Any]], caller: CallerContext, scope: str,
           dept_id: Optional[str] = None, source: str = "async_ingest") -> Dict[str, Any]:
    """提交一个导入任务，立即返回任务单（不阻塞）。"""
    if not rows:
        raise ValueError("documents 为空")
    if len(rows) > MAX_DOCS:
        raise ValueError(f"单任务最多 {MAX_DOCS} 篇（本次 {len(rows)} 篇），请拆批提交")

    job_id = "job_" + uuid.uuid4().hex[:10]
    job: Dict[str, Any] = {
        "job_id": job_id,
        "status": "queued",
        "total": len(rows),
        "processed": 0,
        "added": 0,
        "duplicated": 0,
        "failed": 0,
        "failures": [],
        "chars_added": 0,
        "scope": scope,
        "dept_id": dept_id or caller.deptId,
        "owner_id": caller.userId,
        "tenant_id": caller.tenantId,
        "source": source,
        "created_at": _now(),
        "started_at": None,
        "finished_at": None,
        "elapsed_s": None,
        "error": None,
        "_rows": rows,
        "_caller": caller,
        "_cancel": False,
    }
    with _lock:
        _jobs[job_id] = job
        _order.append(job_id)
        while len(_order) > MAX_JOBS:
            _jobs.pop(_order.pop(0), None)

    _executor.submit(_run, job_id)
    return _public(job)


def _run(job_id: str) -> None:
    """后台执行体：分批入库，逐批回写进度。异常只让任务失败，不炸进程。"""
    job = _jobs.get(job_id)
    if not job:
        return
    rows: List[Dict[str, Any]] = job["_rows"]
    caller: CallerContext = job["_caller"]
    job["status"] = "running"
    job["started_at"] = _now()
    try:
        for start in range(0, len(rows), CHUNK):
            if job.get("_cancel"):
                job["status"] = "cancelled"
                break
            chunk = rows[start:start + CHUNK]
            try:
                res = kb_store.register_many(
                    chunk, caller, scope=job["scope"],
                    dept_id=job["dept_id"], source=job["source"])
                job["added"] += res.get("added", 0)
                job["duplicated"] += res.get("duplicated", 0)
                job["chars_added"] += res.get("chars_added", 0)
            except Exception as e:  # 单批失败不放弃整单
                job["failed"] += len(chunk)
                if len(job["failures"]) < KEEP_FAILURES:
                    job["failures"].append({"batch_start": start, "count": len(chunk),
                                            "reason": f"{type(e).__name__}: {e}"})
            job["processed"] = min(start + len(chunk), len(rows))
        if job["status"] != "cancelled":
            job["status"] = "done"
    except Exception as e:
        job["status"] = "failed"
        job["error"] = f"{type(e).__name__}: {e}"
    finally:
        job["finished_at"] = _now()
        if job["started_at"]:
            job["elapsed_s"] = round(job["finished_at"] - job["started_at"], 3)
        job.pop("_rows", None)
        job.pop("_caller", None)


def get(job_id: str, caller: Optional[CallerContext] = None) -> Optional[Dict[str, Any]]:
    """取任务单。带 caller 时校验归属（本人任务或平台管理员）。"""
    job = _jobs.get(job_id)
    if not job:
        return None
    if caller is not None:
        same_user = job["owner_id"] == caller.userId
        same_tenant = job["tenant_id"] == caller.tenantId
        if not (same_tenant and (same_user or caller.role == "platform_admin")):
            return None
    return _public(job)


def list_jobs(caller: CallerContext, limit: int = 20) -> List[Dict[str, Any]]:
    """最近任务（新→旧）。平台管理员看全租户，其他人只看自己的。"""
    out = []
    for job_id in reversed(_order):
        job = _jobs.get(job_id)
        if not job:
            continue
        if job["tenant_id"] != caller.tenantId:
            continue
        if caller.role != "platform_admin" and job["owner_id"] != caller.userId:
            continue
        out.append(_public(job))
        if len(out) >= limit:
            break
    return out


def cancel(job_id: str, caller: CallerContext) -> bool:
    """取消（尽力而为）：当前批跑完就停，已入库的不会回滚。"""
    job = _jobs.get(job_id)
    if not job or job["status"] in ("done", "failed", "cancelled"):
        return False
    if job["owner_id"] != caller.userId and caller.role != "platform_admin":
        return False
    job["_cancel"] = True
    return True


def wait(job_id: str, timeout: float = 300.0, interval: float = 0.05) -> Optional[Dict[str, Any]]:
    """等任务终态（脚本与测试用，不给前端）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = _jobs.get(job_id)
        if not job:
            return None
        if job["status"] in ("done", "failed", "cancelled"):
            return _public(job)
        time.sleep(interval)
    return None


def reset() -> None:
    """清空任务单（测试用）。"""
    with _lock:
        _jobs.clear()
        _order.clear()
