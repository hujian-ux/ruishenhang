"""M27 · 事件总线（第 9 章）：订阅 / 事件台账 / Webhook 投递。

设计取舍：
  - 订阅与事件台账走 _m27_store.Store（进程内字典 + RLock），与 M27 其它能力同层，
    将来接 PG 只换存储层，不动业务代码。
  - 订阅 token 是投递签名用的共享密钥。服务端用 WEBHOOK_TOKEN_SECRET 生成
    HMAC-SHA256 校验值，并以「secret 派生 keystream 做 XOR + base64」的可逆混淆
    密文存储，**库里不落明文**；响应只回显 token 前缀。
    明确边界：这是开发级保护（混淆不是加密强度承诺），生产必须换成 KMS /
    专用密钥管理服务，并走出站白名单，见各视图的 protection / note 字段。
  - deliver=False（默认）只落台账 + 统计匹配订阅数，绝不发网络请求；
    deliver=True 才用 httpx 短超时投递，失败记 failed 且不抛异常。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import secrets
from typing import Any, Dict, List
from urllib.parse import urlparse

from app.core.config import settings
from app.services._m27_store import Store, caller_id, new_id

# 订阅台账 / 事件台账（模块级单例，reset_state 可整体清空）
_SUBS = Store("m27_subscription")
_EVENTS = Store("m27_event")

# 回环域名（不是 IP 字面量，但同样指向本机，必须拒绝）
_BLOCKED_HOSTNAMES = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}

# 保护说明：写进每条订阅视图，避免调用方误以为「混淆 == 生产级加密」
_PROTECTION = "HMAC-SHA256(WEBHOOK_TOKEN_SECRET) + 可逆混淆(XOR+base64)，开发级保护；生产需 KMS/密钥管理"
_NOTE = "完整 token 仅在服务端密文保存，响应只回显前缀，不回显完整密钥"


# ── URL 安全校验（SSRF 第一道闸）─────────────────────────────────────────────

def _is_blocked_host(host: str) -> bool:
    """host 是否为私网 / 回环 / 链路本地 / 保留的字面 IP，或回环域名。

    域名（如 example.com）不在字面 IP 之列，交由出站侧 DNS/白名单进一步收敛；
    这里只按契约拦截「字面 IP」的私网/回环，以及 localhost 这类等价回环域名。
    """
    h = (host or "").strip().lower().rstrip(".")
    if not h:
        return True
    if h in _BLOCKED_HOSTNAMES or h.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False  # 非字面 IP（域名）：本层不拦
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _validate_url(url: str) -> str:
    """校验订阅地址：仅 http/https，拒绝私网/回环字面 IP。失败抛 ValueError。"""
    u = (url or "").strip()
    parsed = urlparse(u)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("订阅 URL 必须使用 http 或 https")
    if _is_blocked_host(parsed.hostname or ""):
        raise ValueError("拒绝私网/回环/保留字面 IP 的订阅地址（SSRF 防护）")
    return u


# ── token：HMAC 校验值 + 可逆混淆密文（不落明文）──────────────────────────────

def _secret_bytes() -> bytes:
    return str(settings.WEBHOOK_TOKEN_SECRET or "dev-only-change-me").encode("utf-8")


def _keystream(key: bytes, length: int) -> bytes:
    """由 secret 派生确定性 keystream（可复现，故可逆）。"""
    out = bytearray()
    counter = 0
    while len(out) < length:
        out.extend(hashlib.sha256(key + b"|m27-obf|" + str(counter).encode("ascii")).digest())
        counter += 1
    return bytes(out[:length])


def _obfuscate(token: str) -> str:
    raw = token.encode("utf-8")
    stream = _keystream(_secret_bytes(), len(raw))
    masked = bytes(b ^ s for b, s in zip(raw, stream))
    return base64.urlsafe_b64encode(masked).decode("ascii")


def _deobfuscate(enc: str) -> str:
    data = base64.urlsafe_b64decode(enc.encode("ascii"))
    stream = _keystream(_secret_bytes(), len(data))
    raw = bytes(b ^ s for b, s in zip(data, stream))
    return raw.decode("utf-8")


def _token_hmac(token: str) -> str:
    return hmac.new(_secret_bytes(), token.encode("utf-8"), hashlib.sha256).hexdigest()


def _public_view(row: Dict[str, Any]) -> Dict[str, Any]:
    """脱敏视图：绝不外泄 token_enc / token_hmac / 完整 token。"""
    return {
        "id": row.get("id"),
        "url": row.get("url"),
        "event_types": list(row.get("event_types") or []),
        "owner": row.get("owner"),
        "token_prefix": row.get("token_prefix"),
        "created_at": row.get("created_at"),
        "created_iso": row.get("created_iso"),
        "protection": _PROTECTION,
        "note": _NOTE,
    }


# ── 对外能力 ────────────────────────────────────────────────────────────────

def subscribe(url: str, event_types: list, caller) -> dict:
    """新建订阅。校验 http/https + 拒绝私网/回环字面 IP；token 只回显前缀。"""
    u = _validate_url(url)
    types = [str(t).strip() for t in (event_types or []) if str(t).strip()]
    if not types:
        raise ValueError("event_types 不能为空")

    token = "whsec_" + secrets.token_hex(24)
    row = _SUBS.put({
        "id": new_id("sub"),
        "url": u,
        "event_types": types,
        "owner": caller_id(caller),
        "token_prefix": token[:12] + "...",
        "token_hmac": _token_hmac(token),   # 解混淆后校验完整性，防密文被篡改
        "token_enc": _obfuscate(token),     # 可逆混淆密文，库里不落明文
    }, key="id")
    return _public_view(row)


def list_subscriptions(caller=None) -> list:
    """列出订阅（脱敏）。caller 仅用于签名兼容，视图不含任何密钥材料。"""
    return [_public_view(r) for r in _SUBS.list(limit=10 ** 6, newest_first=True)]


def _deliver(event_type: str, payload: dict, subs: list) -> tuple:
    """按订阅投递；httpx 懒加载 + 短超时；单条失败只计数、不抛异常。"""
    import httpx  # 懒加载：deliver=False 路径完全不依赖第三方 HTTP 库

    timeout = float(settings.WEBHOOK_TIMEOUT_S or 3.0)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    ok = fail = 0
    for s in subs:
        try:
            token = _deobfuscate(s["token_enc"])
            if not hmac.compare_digest(_token_hmac(token), s.get("token_hmac", "")):
                fail += 1
                continue
            sig = hmac.new(token.encode("utf-8"), raw, hashlib.sha256).hexdigest()
            headers = {
                "Content-Type": "application/json",
                "X-Webhook-Event": event_type,
                "X-Webhook-Signature": f"sha256={sig}",
                "X-Webhook-Token-Prefix": str(s.get("token_prefix", "")),
            }
            resp = httpx.post(s["url"], content=raw, headers=headers, timeout=timeout)
            if 200 <= resp.status_code < 300:
                ok += 1
            else:
                fail += 1
        except Exception:
            fail += 1
    return ok, fail


def publish(event_type: str, payload: dict, caller=None, deliver: bool = False) -> dict:
    """落事件台账 + 返回匹配订阅数。deliver=False（默认）绝不发网络请求。"""
    et = str(event_type or "").strip()
    if not et:
        raise ValueError("event_type 不能为空")
    body = dict(payload or {})

    matched = [r for r in _SUBS.list(limit=10 ** 6)
               if et in (r.get("event_types") or []) or "*" in (r.get("event_types") or [])]

    record: Dict[str, Any] = {
        "id": new_id("evt"),
        "event_type": et,
        "payload": body,
        "publisher": caller_id(caller) if caller is not None else "system",
        "matched_subscriptions": len(matched),
        "deliver": bool(deliver),
        "delivered": 0,
        "failed": 0,
        "status": "recorded",
    }
    if deliver:
        if matched:
            ok, fail = _deliver(et, body, matched)
            record["delivered"] = ok
            record["failed"] = fail
            record["status"] = "delivered" if fail == 0 else "partial_failed"
        else:
            record["status"] = "no_subscribers"
    _EVENTS.put(record, key="id")

    return {
        "id": record["id"],
        "event_type": et,
        "matched_subscriptions": len(matched),
        "deliver": bool(deliver),
        "delivered": record["delivered"],
        "failed": record["failed"],
        "status": record["status"],
    }


def list_events() -> list:
    """事件台账（最新在前）。"""
    return _EVENTS.list(limit=10 ** 6, newest_first=True)


def purge_user(user_id: str) -> dict:
    """数据主体删除：清理该用户名下的订阅与事件，返回各类删除数。

    供 observability.delete_my_data 级联调用（事件类数据由本模块所有）。
    """
    uid = str(user_id or "")
    subs = [r for r in _SUBS.list(limit=10 ** 6) if r.get("owner") == uid]
    evts = [r for r in _EVENTS.list(limit=10 ** 6) if r.get("publisher") == uid]
    for r in subs:
        _SUBS.delete(r["id"])
    for r in evts:
        _EVENTS.delete(r["id"])
    return {"subscriptions": len(subs), "events": len(evts)}


# ── 备份 / 恢复（供 scripts/backup_restore.py 调用）────────────────────────────

def snapshot() -> dict:
    """导出全部台账的可序列化快照（含密文 token，不含额外密钥）。"""
    return {
        "subscriptions": _SUBS.list(limit=10 ** 6, newest_first=False),
        "events": _EVENTS.list(limit=10 ** 6, newest_first=False),
    }


def restore(data: dict) -> None:
    """从快照写回台账（先清空再按原 id 写回，保持插入顺序）。"""
    reset_state()
    for r in (data or {}).get("subscriptions", []):
        _SUBS.put(dict(r), key="id")
    for r in (data or {}).get("events", []):
        _EVENTS.put(dict(r), key="id")


def reset_state() -> None:
    _SUBS.clear()
    _EVENTS.clear()
