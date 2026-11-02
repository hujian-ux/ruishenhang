"""Embedding 结果持久化缓存（M9）：重启载回 5000 篇不重算向量。

为什么需要它：
  索引重建的代价几乎全在 `embed()` 上——250 篇 = 3052 个子块 = 3052 次向量化。
  伪向量（离线兜底）每次 0.1ms 看不出来，但真 bge-m3 在 CPU 上约 15–30ms/条，
  3052 条就是 45–90 秒；5000 篇量级直接变成分钟级启动。每次重启、每批增量都重算一遍
  显然不可接受——向量对同一段文本是确定性的，天然该缓存。

设计取舍：
  - **sqlite 单文件**而不是 npz：npz 每次落盘要整体重写（250MB 级），sqlite 支持单条写入 +
    主键查，百万行也不慌，且是 Python 标准库，不引入新依赖。
  - **键含模型名与维度**：换 bge-m3 → bge-large 或改维度时旧缓存自然失效，不会串味。
  - **默认开启、失败静默降级**：缓存不可写（只读目录/磁盘满）时退化为纯计算，
    功能不受影响，只是慢一点——不能因为一个加速项把主流程拖死。
  - **攒批提交**（EMBED_CACHE_FLUSH_EVERY）而不是每条一事务：单条事务在 5000 条量级
    会有明显的 fsync 开销。
"""
import hashlib
import sqlite3
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.core.config import settings

_BACKEND_ROOT = Path(__file__).resolve().parents[2]

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None
_init_failed = False
_pending: List[Tuple[str, bytes, int, str]] = []
# 已算但还没落盘的向量（攒批期间的回查层）。没有它，单篇入库后立刻再查同文本
# 会因为「还没 flush 到 sqlite」而 miss，重复计算一遍——试点里同一份制度反复上传很常见。
_recent: Dict[str, bytes] = {}
_RECENT_CAP = 20000
_stats = {"hits": 0, "misses": 0, "writes": 0, "errors": 0}


def _path() -> Path:
    p = Path(settings.EMBED_CACHE_PATH)
    return p if p.is_absolute() else (_BACKEND_ROOT / p)


def _connect() -> Optional[sqlite3.Connection]:
    """懒连接 + 建表；不可用时置 _init_failed，之后一律走纯计算。"""
    global _conn, _init_failed
    if _conn is not None or _init_failed:
        return _conn
    if not settings.EMBED_CACHE:
        _init_failed = True
        return None
    try:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS embed_cache ("
            " key TEXT PRIMARY KEY, vec BLOB NOT NULL, dim INTEGER NOT NULL,"
            " model TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.commit()
        _conn = conn
    except Exception:
        _init_failed = True
        _conn = None
    return _conn


def make_key(text: str) -> str:
    """缓存键：模型名 + 维度 + 文本（含这两项，换模型不会命中旧向量）。"""
    raw = f"{settings.EMBEDDING_MODEL}|{settings.EMBEDDING_DIM}|{text}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def get_many(texts: List[str]) -> Dict[str, bytes]:
    """批量取：返回 {key: 向量字节}。未命中的不出现在结果里。"""
    if not texts:
        return {}
    keys = [make_key(t) for t in texts]
    out: Dict[str, bytes] = {}
    with _lock:
        for k in keys:
            b = _recent.get(k)
            if b is not None:
                out[k] = b
    conn = _connect()
    if conn is None:
        _stats["hits"] += len(out)
        _stats["misses"] += len(texts) - len(out)
        return out
    rest = [k for k in keys if k not in out]
    try:
        with _lock:
            # 分批 IN 查询，避免 SQLite 变量上限（默认 999）
            for i in range(0, len(rest), 400):
                chunk = rest[i:i + 400]
                ph = ",".join("?" * len(chunk))
                for k, blob in conn.execute(
                        f"SELECT key, vec FROM embed_cache WHERE key IN ({ph})", chunk):
                    out[k] = blob
    except Exception:
        _stats["errors"] += 1
        _stats["misses"] += len(texts)
        return {}
    _stats["hits"] += len(out)
    _stats["misses"] += len(texts) - len(out)
    return out


def put_many(items: List[Tuple[str, bytes]]) -> None:
    """批量写（攒批提交）。items: [(key, vec_bytes)]"""
    if not items:
        return
    with _lock:
        if len(_recent) > _RECENT_CAP:
            _recent.clear()
        for k, b in items:
            _recent[k] = b
    conn = _connect()
    if conn is None:
        return
    with _lock:
        _pending.extend((k, b, settings.EMBEDDING_DIM, settings.EMBEDDING_MODEL)
                        for k, b in items)
        if len(_pending) >= max(1, settings.EMBED_CACHE_FLUSH_EVERY):
            _flush_locked()


def _flush_locked() -> None:
    global _pending
    if _conn is None or not _pending:
        return
    batch, _pending = _pending, []
    try:
        with _lock:
            _conn.executemany(
                "INSERT OR REPLACE INTO embed_cache (key, vec, dim, model) VALUES (?,?,?,?)",
                batch,
            )
            _conn.commit()
            # 已落盘的从内存层摘掉，后续命中走 sqlite，避免内存层一直涨
            _recent.clear()
        _stats["writes"] += len(batch)
    except Exception:
        _stats["errors"] += 1


def flush() -> None:
    """强制落盘（应用关闭 / 批量导入结束时调用）。"""
    with _lock:
        _flush_locked()


def stats() -> Dict[str, object]:
    """缓存画像：命中率是判断「重启到底快了多少」的直接证据。"""
    total = _stats["hits"] + _stats["misses"]
    return {
        "enabled": settings.EMBED_CACHE and not _init_failed,
        "available": _conn is not None,
        "path": str(_path()),
        "entries": _count(),
        "hits": _stats["hits"],
        "misses": _stats["misses"],
        "hit_rate": round(_stats["hits"] / total, 4) if total else None,
        "pending": len(_pending),
        "errors": _stats["errors"],
    }


def _count() -> Optional[int]:
    conn = _connect()
    if conn is None:
        return None
    try:
        with _lock:
            return int(conn.execute("SELECT COUNT(*) FROM embed_cache").fetchone()[0])
    except Exception:
        return None


def reset_stats() -> None:
    for k in _stats:
        _stats[k] = 0


def clear() -> int:
    """清空缓存（测试/换模型时用）。"""
    with _lock:
        _recent.clear()
    conn = _connect()
    if conn is None:
        return 0
    try:
        with _lock:
            n = int(conn.execute("SELECT COUNT(*) FROM embed_cache").fetchone()[0])
            conn.execute("DELETE FROM embed_cache")
            conn.commit()
        return n
    except Exception:
        return 0
