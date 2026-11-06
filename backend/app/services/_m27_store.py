"""M27 蓝图缺口收口 · 共用轻量存储。

设计取舍：这批能力（反馈、建议台、工单、事件、部门向导、工作流）都需要「能跑、
能验、重启不炸」的状态，但不值得为每一条都建一张 PG 表。统一用进程内字典 +
RLock，接口形状与现有 pg_store 一致（put/get/list/update/delete），将来接 PG 时
只换这一层，不动业务代码。

明确边界：内存态重启即丢，与项目现有的「无 PG 全内存」口径一致；审计动作仍走
services/audit.py（PG / SQLite 兜底），不在这里另起一套日志。
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_LOCK = threading.RLock()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now() -> float:
    return time.time()


def iso(ts: Optional[float] = None) -> str:
    return datetime.fromtimestamp(ts if ts is not None else now(), timezone.utc).isoformat()


class Store:
    """按 id 存 dict，保留插入顺序；list() 默认倒序（最新在前）。"""

    def __init__(self, name: str = "store"):
        self.name = name
        self._rows: Dict[str, Dict[str, Any]] = {}
        self._order: List[str] = []

    def put(self, row: Dict[str, Any], key: str = "id") -> Dict[str, Any]:
        with _LOCK:
            rid = str(row.get(key) or new_id(self.name))
            row = dict(row)
            row[key] = rid
            row.setdefault("created_at", now())
            row.setdefault("created_iso", iso(row["created_at"]))
            if rid not in self._rows:
                self._order.append(rid)
            self._rows[rid] = row
            return dict(row)

    def get(self, rid: str) -> Optional[Dict[str, Any]]:
        with _LOCK:
            row = self._rows.get(rid)
            return dict(row) if row else None

    def update(self, rid: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with _LOCK:
            row = self._rows.get(rid)
            if row is None:
                return None
            row.update(patch)
            row["updated_at"] = now()
            self._rows[rid] = row
            return dict(row)

    def delete(self, rid: str) -> bool:
        with _LOCK:
            if rid not in self._rows:
                return False
            del self._rows[rid]
            try:
                self._order.remove(rid)
            except ValueError:
                pass
            return True

    def list(self, *, filters: Optional[Dict[str, Any]] = None,
             limit: int = 200, newest_first: bool = True) -> List[Dict[str, Any]]:
        f = filters or {}
        with _LOCK:
            ids = list(reversed(self._order)) if newest_first else list(self._order)
            rows = []
            for rid in ids:
                row = self._rows.get(rid)
                if row is None:
                    continue
                if all(row.get(k) == v for k, v in f.items() if v is not None):
                    rows.append(dict(row))
                if len(rows) >= limit:
                    break
            return rows

    def count(self, **filters: Any) -> int:
        return len(self.list(filters=filters, limit=10 ** 6))

    def clear(self) -> None:
        with _LOCK:
            self._rows.clear()
            self._order.clear()


def audit_event(action: str, caller: Any = None, target: str = "",
                meta: Optional[Dict[str, Any]] = None, result: str = "ok") -> None:
    """审计动作统一出口；审计后端不可用时静默降级，不影响主链路。"""
    try:
        from app.services import audit
        audit.record(action, caller, target=target, result=result, meta=meta or {})
    except Exception:
        pass


def caller_id(caller: Any) -> str:
    return str(getattr(caller, "userId", "") or "anonymous")


def caller_role(caller: Any) -> str:
    return str(getattr(caller, "role", "") or "employee")


def is_admin(caller: Any) -> bool:
    return caller_role(caller) in ("platform_admin", "dept_admin")
