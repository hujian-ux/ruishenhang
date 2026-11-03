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
# M16：二进制兜底（xlsx 产出物 / 上传件）。MinIO 不可达时也能跑通完整链路。
_FALLBACK_BYTES: Dict[str, bytes] = {}


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


def delete_text(key: str) -> bool:
    """删除文档原文（M7 KB 删除：对象存储与检索语料同步清理）。"""
    _FALLBACK.pop(key, None)
    c = _get_client()
    if not c:
        return False
    try:
        c.remove_object(settings.MINIO_BUCKET, key)
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# M16 · 二进制对象（P0 文件进出）：上传的 xlsx 输入件 + 产出的 xlsx 产出物
# ─────────────────────────────────────────────────────────────────────────────
def put_bytes(key: str, data: bytes, content_type: str = "application/octet-stream",
              metadata: Dict[str, str] | None = None) -> str:
    """写入二进制对象；返回存储位置（minio://bucket/key 或 memory://key）。"""
    c = _get_client()
    if c:
        try:
            import io as _io
            c.put_object(settings.MINIO_BUCKET, key, _io.BytesIO(data), len(data),
                         content_type=content_type, metadata=metadata or None)
            return f"minio://{settings.MINIO_BUCKET}/{key}"
        except Exception:
            pass
    _FALLBACK_BYTES[key] = data
    return f"memory://{key}"


def get_bytes(key: str) -> bytes | None:
    """读取二进制对象；不存在返回 None（调用方据此回 404，而不是返回空文件）。"""
    c = _get_client()
    if c:
        try:
            resp = c.get_object(settings.MINIO_BUCKET, key)
            try:
                return resp.read()
            finally:
                resp.close()
                resp.release_conn()
        except Exception:
            pass
    return _FALLBACK_BYTES.get(key)


def delete_object(key: str) -> bool:
    """删除二进制对象（内存兜底与 MinIO 同时清）。"""
    hit = _FALLBACK_BYTES.pop(key, None) is not None
    c = _get_client()
    if not c:
        return hit
    try:
        c.remove_object(settings.MINIO_BUCKET, key)
        return True
    except Exception:
        return hit
