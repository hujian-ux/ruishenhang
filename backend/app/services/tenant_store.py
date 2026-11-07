"""M43 · 租户注册表（多企业 SaaS 的开通底座）。

- 内存 + PG 写穿（对齐 agent_store 范式，PG 不可达时静默降级为内存）。
- platform_admin 开通新企业 → 生成 tenant_id、建空私有语料空间。
- 115 业务包 / 28 公共库 / 内置助手是**平台级共享资产**（tenant_id 留空），
  所有租户自动可见，开通时**不克隆**（通用化设计：共享、统一升级）。
- 租户级 config（LLM/品牌/SSO 覆盖）存 jsonb，存在则优先，否则回落全局。

设计取舍：租户隔离在应用层（agent_store._visible / approval / invoke / session 均按
tenant_id 判），PG 仅做持久副本；tenant_id 同时写在业务对象的 JSONB payload 里，
与项目「内存为主存、PG 为副本」口径一致。
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from app.services import pg_store

_TABLE = "tenants"
_TENANTS: Dict[str, Dict[str, Any]] = {}

# 平台级共享资产使用的租户占位（空串）：对所有租户可见，开通时不克隆。
PLATFORM_TENANT = ""


def _new_tenant_id() -> str:
    return "t_" + uuid.uuid4().hex[:10]


def create_tenant(name: str, config: Dict[str, Any] | None = None,
                  status: str = "pending") -> Dict[str, Any]:
    """开通一个新企业租户（空私有语料，共享 115 平台资产）。

    config 可携带租户级覆盖：{"llm": {...}, "brand": {...}, "sso": {...}, "im": {...}}。
    """
    tid = _new_tenant_id()
    row = {
        "tenant_id": tid,
        "name": name,
        "status": status,
        "config": config or {},
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    _TENANTS[tid] = row
    pg_store.upsert(_TABLE, tid, row)
    return row


def get_tenant(tid: str) -> Optional[Dict[str, Any]]:
    return _TENANTS.get(tid)


def list_tenants() -> List[Dict[str, Any]]:
    return sorted(_TENANTS.values(), key=lambda t: t.get("created_at", 0))


def activate(tid: str) -> Optional[Dict[str, Any]]:
    t = _TENANTS.get(tid)
    if not t:
        return None
    t["status"] = "active"
    t["updated_at"] = time.time()
    pg_store.upsert(_TABLE, tid, t)
    return t


def get_config(tid: str) -> Dict[str, Any]:
    """返回租户级配置（存在则优先），全局回落由调用方处理。"""
    t = _TENANTS.get(tid)
    return (t or {}).get("config") or {}


def update_config(tid: str, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """更新租户级配置（brand/llm/sso/im 覆盖）；返回更新后的租户，不存在返回 None。"""
    t = _TENANTS.get(tid)
    if not t:
        return None
    t["config"] = config or {}
    t["updated_at"] = time.time()
    pg_store.upsert(_TABLE, tid, t)
    return t


# 平台级覆盖的合法顶层键；auth/im/brand 边界在取配置时按这些键回落。
CONFIG_KEYS = ("llm", "brand", "sso", "im")

# 平台默认品牌（登录页/未配置租户的回落）：与前端 theme.ts 主色保持一致。
DEFAULT_BRAND: Dict[str, str] = {
    "name": "企业 AI 智能体中台",
    "logo_url": "",
    "primary_color": "#1677ff",
}


def resolve_tenant_by_org(org_id: str) -> Optional[str]:
    """Casdoor 多组织：企业组织名 → 本平台 tenant_id。

    每家企业在 Casdoor 建一个 organization，登录后 id_token 带 organization claim，
    网关（或后端自签 token）据此落 tenant。本函数供映射使用，扫描各租户的
    `config.sso.casdoor_org`。找不到返回 None（回落默认租户）。
    """
    if not org_id:
        return None
    for t in _TENANTS.values():
        sso = (t.get("config") or {}).get("sso") or {}
        if sso.get("casdoor_org") == org_id:
            return t["tenant_id"]
    return None


def get_branding(tid: Optional[str] = None) -> Dict[str, str]:
    """返回某租户品牌；tid 为空/平台占位/未知租户时回落平台默认。"""
    brand: Dict[str, str] = {}
    if tid and tid != PLATFORM_TENANT:
        brand = (get_config(tid) or {}).get("brand") or {}
    return {**DEFAULT_BRAND, **brand}


def merged_overrides(tid: str, global_defaults: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """把租户级配置叠加到全局默认值之上，返回合并后的覆盖字典。

    边界集成点（auth OIDC / IM 企微钉钉 / 前端品牌）调用本函数取 tenant 覆盖；
    tenant 未配置的项回落 global_defaults，保证零配置时行为与全局一致。
    """
    cfg = get_config(tid)
    out: Dict[str, Any] = {}
    for k in CONFIG_KEYS:
        base = (global_defaults or {}).get(k) or {}
        tenant = cfg.get(k) or {}
        if isinstance(base, dict) and isinstance(tenant, dict):
            out[k] = {**base, **tenant}
        else:
            out[k] = tenant or base
    return out


def load_from_pg() -> int:
    """启动时载回租户注册表；PG 不可达时为空操作。"""
    n = 0
    for payload in pg_store.load_all(_TABLE):
        try:
            _TENANTS[payload["tenant_id"]] = payload
            n += 1
        except Exception:
            continue
    return n
