"""M6 MinIO 对象存储（第23章 文件网关）：知识文档原文持久化。

docker-compose 的 minio 就绪时自动启用（boto3 或 minio SDK 任一）；
不可达时 available()=False，文档原文落内存兜底（接口不变）。
"""
import threading
from typing import Dict

from app.core.config import settings

_client = None
_checked = False
_lock = threading.Lock()

# 离线兜底：对象键 → 文本内容（生产由 MinIO 承担）
_FALLBACK: Dict[str, str] = {}


def _get_client():
    global _client, _checked
    with _lock:
        if _checked:
            return _client
        _checked = True
        try:
            from minio import Minio  # type: ignore
            c = Minio(settings.MINIO_ENDPOINT,
                      access_key=settings.MINIO_USER,
                      secret_key=settings.MINIO_PASSWORD,
                      secure=False)
            if not c.bucket_exists(settings.MINIO_BUCKET):
                c.make_bucket(settings.MINIO_BUCKET)
            _client = c
        except Exception:
            _client = None
    return _client


def available() -> bool:
    return _get_client() is not None


def put_text(key: str, content: str, metadata: Dict[str, str] | None = None) -> str:
    """写入文档原文；返回存储位置（minio://bucket/key 或 memory://key）。"""
    c = _get_client()
    if c:
        try:
            import io  # type: ignore
            data = content.encode("utf-8")
            c.put_object(settings.MINIO_BUCKET, key, io.BytesIO(data), len(data),
                         content_type="text/plain; charset=utf-8",
                         metadata=metadata or None)
            return f"minio://{settings.MINIO_BUCKET}/{key}"
        except Exception:
            pass
    _FALLBACK[key] = content
    return f"memory://{key}"


def get_text(key: str) -> str:
    c = _get_client()
    if c:
        try:
            resp = c.get_object(settings.MINIO_BUCKET, key)
            try:
                return resp.read().decode("utf-8")
            finally:
                resp.close()
                resp.release_conn()
        except Exception:
            pass
    return _FALLBACK.get(key, "")
