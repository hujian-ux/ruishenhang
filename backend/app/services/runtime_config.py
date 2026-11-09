"""运行时配置写入（在线配置入口共用）。

M27 的平台运营台「外部输入就绪度」和 M49 的「模型配置」都要做同样三件事：

1. 写 `os.environ` —— 立刻影响直接读环境变量的代码；
2. 同步 `settings` 单例 —— 本进程里所有读 `settings.XXX` 的代码立刻看到新值
   （Settings 是 import 时求值的类属性快照，只写 os.environ 不生效）；
3. 落盘 `backend/.env.local` —— 重启后由 `config._load_env_files` 读回。

抽出来是为了避免各写一份、再次踩「键名写错」的坑：M27 的在线配置写的是
`LLM_MODEL`，而网关读的是 `LLM_DEFAULT_MODEL` —— 界面上传模型名、实际没生效。

诚实边界：这是 dev / 私有化部署的便捷通道，**明文密钥落盘**。生产应接
KMS / Vault，并靠 `AUTH_ENFORCE_OIDC=1` + 网络隔离收紧该端点可达性。
"""
from __future__ import annotations

import os
import pathlib
from typing import Any, Dict, Optional


def env_local_path() -> pathlib.Path:
    """backend/.env.local（与 config._load_env_files 的定位保持同一层）。"""
    return pathlib.Path(__file__).resolve().parents[2] / ".env.local"


def _coerce(current: Any, value: Any) -> Any:
    """按 settings 现有属性的类型转换，避免把 "0" 写成真值。

    典型坑：`ROUTING_ENABLED` 是 bool，若把字符串 "0" 塞进去，
    `if settings.ROUTING_ENABLED` 是非空字符串 → 恒为真，关掉的开关其实没关。
    """
    if isinstance(current, bool):
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    if isinstance(current, int):
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return current
    if isinstance(current, float):
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            return current
    return str(value)


def apply(updates: Dict[str, Any]) -> Dict[str, Any]:
    """写 os.environ 并同步 settings 单例（即时生效，不重启）。"""
    import app.core.config as cfg

    for k, v in (updates or {}).items():
        os.environ[k] = str(v)
        if hasattr(cfg.settings, k):
            setattr(cfg.settings, k, _coerce(getattr(cfg.settings, k), v))
    return updates


def persist(updates: Dict[str, Any], path: Optional[str] = None) -> str:
    """把配置落盘到 .env.local（gitignore），只覆盖本次涉及的行，注释与其它行原样保留。"""
    p = pathlib.Path(path) if path else env_local_path()
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    keys = set(updates.keys())
    out = []
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            if s.split("=", 1)[0].strip() in keys:
                continue
        out.append(line)
    for k, v in updates.items():
        out.append(f"{k}={v}")
    p.write_text("\n".join(out) + "\n", encoding="utf-8")
    return str(p)


def mask(secret: str) -> str:
    """密钥脱敏：只留前 4 后 4，长度不够就整串打码。"""
    s = (secret or "").strip()
    if not s:
        return ""
    if len(s) <= 8:
        return "•" * len(s)
    return f"{s[:4]}…{s[-4:]}"
