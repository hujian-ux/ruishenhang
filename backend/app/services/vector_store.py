"""向量检索后端（M9）：可插拔的 Qdrant 适配层，默认 numpy 内存矩阵。

为什么要有这一层：
  M9 之前向量检索是「全量内存矩阵 matmul」。numpy 化后 2700 篇只要 3ms，够用；
  但它有硬上限——向量常驻内存，5 万篇就要 200MB+，再多就顶不住；也无法多实例共享。
  Qdrant 把这些交给专门的向量库，内存只留 BM25 与元数据。

三种后端（`VECTOR_BACKEND`）：
  - `memory`（默认）：numpy 矩阵，零外部依赖，重启从 PG 重建。
  - `qdrant` + `QDRANT_MODE=http`：连远程 Qdrant（docker-compose 里的那个）。
  - `qdrant` + `QDRANT_MODE=local`：qdrant-client 的本地文件模式——**真实 Qdrant 引擎，
    不需要 Docker**。用于验证代码路径与离线环境，不适合多进程共享（有文件锁）。
  - `auto`：远程可达就用 qdrant，不可达静默用 memory。

一致性策略：无论哪个后端，**PG 的 kb_documents 才是真源**；Qdrant 只是可重建的索引。
所以这里所有写失败都只降级、不阻塞业务——索引丢了可以从 PG 重建，业务不能因此停。
"""
import threading
from typing import Dict, List, Optional, Tuple

from app.core.config import settings

_lock = threading.RLock()
_client = None
_backend = "memory"
_init_done = False
_init_error = ""
_collection_ready = False
_stats = {"upserts": 0, "searches": 0, "errors": 0, "deletes": 0}


def collection_name() -> str:
    return settings.QDRANT_COLLECTION


def _make_client():
    """按配置建客户端：优先远程，auto 模式下远程不可达则放弃（不静默转本地文件）。"""
    from qdrant_client import QdrantClient  # type: ignore

    mode = settings.QDRANT_MODE
    if mode == "local":
        p = settings.QDRANT_PATH
        return QdrantClient(path=p), "local"
    client = QdrantClient(host=settings.QDRANT_HOST, port=settings.QDRANT_PORT,
                          timeout=settings.QDRANT_TIMEOUT_S)
    # 探活：拿 collections 列表，连不上会抛
    client.get_collections()
    return client, "http"


def _init() -> None:
    global _client, _backend, _init_done, _init_error
    with _lock:
        if _init_done:
            return
        _init_done = True
        want = settings.VECTOR_BACKEND
        if want == "memory":
            _backend = "memory"
            return
        try:
            client, kind = _make_client()
            _client = client
            _backend = f"qdrant:{kind}"
        except Exception as e:
            _init_error = f"{type(e).__name__}: {e}"
            if want == "qdrant":
                # 显式要求 qdrant 却连不上：明确降级并记下原因，不静默假装成功
                _backend = "memory"
                _init_error = "qdrant 不可达，已降级 memory — " + _init_error
            else:
                _backend = "memory"


def backend() -> str:
    _init()
    return _backend


def available() -> bool:
    _init()
    return _client is not None


def _ensure_collection() -> bool:
    """建集合（维度取 settings.EMBEDDING_DIM）；失败则降级 memory。"""
    global _collection_ready, _backend
    if _client is None:
        return False
    if _collection_ready:
        return True
    try:
        from qdrant_client.models import Distance, VectorParams  # type: ignore
        with _lock:
            if not _client.collection_exists(collection_name()):
                _client.create_collection(
                    collection_name(),
                    vectors_config=VectorParams(size=settings.EMBEDDING_DIM,
                                                distance=Distance.COSINE),
                )
            _collection_ready = True
        return True
    except Exception as e:
        _stats["errors"] += 1
        _init_error = f"建集合失败：{type(e).__name__}: {e}"
        _backend = "memory"
        return False


def upsert(rows: List[Tuple[int, List[float], Dict]]) -> int:
    """写入/覆盖向量。rows: [(row_id, vec, payload)]，point id = row_id + 1。

    row_id 与 knowledge._CHILDREN 下标一一对应，检索回来直接映射，无需额外查表。
    """
    if not available() or not _ensure_collection() or not rows:
        return 0
    try:
        from qdrant_client.models import PointStruct  # type: ignore
        points = [PointStruct(id=r + 1, vector=v, payload=p) for r, v, p in rows]
        with _lock:
            _client.upsert(collection_name(), points, wait=False)
        _stats["upserts"] += len(points)
        return len(points)
    except Exception:
        _stats["errors"] += 1
        return 0


def delete(rows: List[int]) -> int:
    if not available() or not rows:
        return 0
    try:
        from qdrant_client.models import PointIdsList  # type: ignore
        with _lock:
            _client.delete(collection_name(), PointIdsList(points=[r + 1 for r in rows]))
        _stats["deletes"] += len(rows)
        return len(rows)
    except Exception:
        _stats["errors"] += 1
        return 0


def reset() -> None:
    """清空集合（全量重建索引前调用），避免残留旧点被召回。"""
    global _collection_ready
    if not available():
        return
    try:
        from qdrant_client.models import Distance, VectorParams  # type: ignore
        with _lock:
            if _client.collection_exists(collection_name()):
                _client.delete_collection(collection_name())
            _client.create_collection(
                collection_name(),
                vectors_config=VectorParams(size=settings.EMBEDDING_DIM,
                                            distance=Distance.COSINE),
            )
            _collection_ready = True
    except Exception:
        _stats["errors"] += 1
        _collection_ready = False


def search(qvec: List[float], limit: int) -> Optional[List[Tuple[int, float]]]:
    """向量检索：返回 [(row_id, score)]。不可用/异常返回 None，调用方退回内存矩阵。"""
    if not available() or not _ensure_collection():
        return None
    try:
        with _lock:
            res = _client.query_points(collection_name(), query=qvec, limit=limit)
        _stats["searches"] += 1
        return [(int(p.id) - 1, float(p.score)) for p in res.points]
    except Exception:
        _stats["errors"] += 1
        return None


def count() -> Optional[int]:
    if not available():
        return None
    try:
        with _lock:
            return int(_client.count(collection_name()).count)
    except Exception:
        return None


def stats() -> Dict[str, object]:
    _init()
    return {
        "requested": settings.VECTOR_BACKEND,
        "backend": _backend,
        "available": _client is not None,
        "collection": collection_name(),
        "points": count() if _client is not None else None,
        "mode": settings.QDRANT_MODE,
        "endpoint": (settings.QDRANT_PATH if settings.QDRANT_MODE == "local"
                     else f"{settings.QDRANT_HOST}:{settings.QDRANT_PORT}"),
        "error": _init_error or None,
        **{f"{k}": v for k, v in _stats.items()},
    }


def reset_state() -> None:
    """重置连接与统计（测试用：切换后端配置后需要重新初始化）。"""
    global _client, _backend, _init_done, _init_error, _collection_ready
    with _lock:
        try:
            if _client is not None:
                _client.close()
        except Exception:
            pass
        _client = None
        _backend = "memory"
        _init_done = False
        _init_error = ""
        _collection_ready = False
        for k in _stats:
            _stats[k] = 0
