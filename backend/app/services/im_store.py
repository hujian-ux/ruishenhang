"""IM/OA 租户级配置存储（P1-173）。

每个租户可按 provider（wecom / dingtalk）保存对接凭据。凭据仅在内存存储（重启即丢），
生产应接 PG / 密钥管理服务；本模块对 secret 类字段做脱敏，list 时绝不回显明文。
opt-in：未配置则 IM 能力不可用，不影响其它功能。
"""
from typing import Dict, List, Optional

# 需要脱敏的字段（list / get 时只回显前 4 位 + ****）
_SECRET_FIELDS = ("secret", "app_secret", "encoding_aes_key", "webhook_token")

# 内存态：{(tenant, provider): {...fields}}。生产应替换为持久化存储。
_STORE: Dict[tuple, Dict[str, str]] = {}


def save_config(tenant: str, provider: str, **fields) -> Dict[str, str]:
    """保存某租户的某 provider 配置（全量覆盖该 provider）。空字符串字段会被丢弃。"""
    provider = provider.lower()
    clean = {k: str(v) for k, v in fields.items() if v not in (None, "")}
    _STORE[(tenant, provider)] = clean
    return _mask(_STORE[(tenant, provider)])


def get_config(tenant: str, provider: str) -> Optional[Dict[str, str]]:
    cfg = _STORE.get((tenant, provider))
    return dict(cfg) if cfg else None


def list_configs(tenant: str) -> List[Dict[str, str]]:
    """列出某租户下所有 provider 的配置（脱敏）。"""
    out = []
    for (t, p), cfg in _STORE.items():
        if t == tenant:
            item = _mask(cfg)
            item["provider"] = p
            out.append(item)
    return out


def delete_config(tenant: str, provider: str) -> bool:
    return _STORE.pop((tenant, provider), None) is not None


def _mask(cfg: Dict[str, str]) -> Dict[str, str]:
    """脱敏：secret 类字段只留前 4 位 + ****，其余原样。"""
    out = {}
    for k, v in cfg.items():
        if k in _SECRET_FIELDS and v:
            out[k] = (v[:4] + "****") if len(v) > 4 else "****"
        else:
            out[k] = v
    return out


def _has_secret(cfg: Optional[Dict[str, str]]) -> bool:
    if not cfg:
        return False
    return any(cfg.get(f) for f in ("secret", "app_secret", "webhook_token"))
