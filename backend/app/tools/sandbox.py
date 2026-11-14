"""P0-3 工具执行沙箱边界。

在 guardrails.guard_tool_call（白名单 / 私网拦截 / 审计单一过滤点）之上，再叠加三层
硬边界，作为工具真正执行前的最后一道闸：

  1. 路径隔离 is_inside_root：file_read / file_write 的目标必须落在 workspace_root 内，
     防 ../ 越界、symlink 逃逸（allow_symlink_escape=False 时解析真实路径再判）；
  2. 凭据三态硬禁：工具入参出现明文密钥（键名 denylist + 值正则命中）直接拒绝，
     连 guardrails 的脱敏都不走 —— 明文密钥绝不能进任何工具；
  3. 命令白名单：shell 工具只放 whitelist 里的命令基名，其余一律拒绝。

规则来自 backend/sandbox_rules.json（路径可被环境变量 EAP_SANDBOX_RULES 覆盖）。
所有函数都是纯函数，无副作用，便于单元测试与审计回放。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Tuple

# ── 路径解析 ────────────────────────────────────────────────────────────────
def _backend_root() -> Path:
    # 本文件位于 backend/app/tools/sandbox.py → parents[2] 即 backend/
    return Path(__file__).resolve().parents[2]


def _default_rules_path() -> Path:
    env = os.environ.get("EAP_SANDBOX_RULES")
    if env:
        return Path(env)
    return _backend_root() / "sandbox_rules.json"


def _default_workspace() -> Path:
    env = os.environ.get("EAP_TOOLS_WORKSPACE")
    if env:
        return Path(env)
    return _backend_root() / "data" / "tools_workspace"


# ── 凭据三态硬禁：明文密钥绝不进工具 ───────────────────────────────────────
# 键名含这些词即视为密钥（忽略大小写）。值正则命中的形态：sk- / AKIA / ghp_ /
# Slack token / JWT / BEGIN PRIVATE KEY。刻意不放「任意 32+ 位字母数字」这种
# 宽松规则，避免把长文档 id、base64 片段误判成密钥。
_SECRET_KEY_HINTS = (
    "api_key", "apikey", "secret", "password", "passwd", "pwd", "token",
    "private_key", "access_key", "ak", "sk", "credential", "authorization",
)
_SECRET_VALUE_RE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{12,}"
    r"|AKIA[0-9A-Z]{12,}"
    r"|ghp_[A-Za-z0-9]{16,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----)",
    re.IGNORECASE,
)


def load_rules(path: Path | None = None) -> dict:
    """读取沙箱规则；缺项补默认值（绝不抛错，符合配置容错约定）。"""
    p = path or _default_rules_path()
    try:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("workspace_root", str(_default_workspace()))
    data.setdefault("allow_symlink_escape", False)
    data.setdefault("deny_secret_keys", list(_SECRET_KEY_HINTS))
    data.setdefault("tools", {})
    data.setdefault("shell", {"enabled": False, "command_whitelist": []})
    return data


def is_inside_root(root: str | Path, target: str | Path,
                  allow_symlink_escape: bool = False) -> Tuple[bool, str]:
    """判断 target 是否落在 root 内。返回 (inside, resolved_path)。

    inside=False 表示越界（含 symlink 逃逸、相对路径逃出）。
    allow_symlink_escape=False（默认）：解析真实路径后再判，symlink 指向 root
    外即判为越界。
    """
    try:
        root_p = Path(root).resolve()
        t = Path(target)
        if not t.is_absolute():
            t = root_p / t
        resolved = t.resolve()  # 跟随 symlink
        try:
            resolved.relative_to(root_p)
            return True, str(resolved)
        except ValueError:
            return False, str(resolved)
    except Exception as exc:  # pragma: no cover - 解析异常统一判越界
        return False, f"<resolve-error:{exc}>"


def _scan_secret(obj: Any, deny_keys: list, path: str = "") -> str:
    """递归扫描入参。命中返回原因串，空串表示无明文密钥。"""
    deny = [k.lower() for k in deny_keys]
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            if any(h in kl for h in deny):
                return f"secret_key:{k}"
            r = _scan_secret(v, deny, f"{path}/{k}")
            if r:
                return r
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            r = _scan_secret(v, deny, f"{path}[{i}]")
            if r:
                return r
    elif isinstance(obj, str):
        if _SECRET_VALUE_RE.search(obj):
            return f"secret_value_at:{path}" if path else "secret_value"
    return ""


def check(tool: str, args: dict, caller: Any = None, rules: dict | None = None) -> dict:
    """沙箱硬边界检查。返回 {allowed, reason, resolved_path?}。

    检查顺序：工具开关 → 凭据三态硬禁 → （file_* 路径隔离 / shell 命令白名单） →
    其余工具交 guardrails 决定（返回 reason=delegated_to_guardrails）。
    """
    rules = rules or load_rules()
    tool = str(tool or "")
    args = args if isinstance(args, dict) else {}

    # 1) 工具开关（shell 由 shell.enabled 控制，不在此判；见下方 shell 分支）
    if tool != "shell":
        tool_cfg = (rules.get("tools", {}) or {}).get(tool)
        if isinstance(tool_cfg, dict) and tool_cfg.get("enabled") is False:
            return {"allowed": False, "reason": f"tool_disabled:{tool}"}

    # 2) 凭据三态硬禁：任何工具都先查，明文密钥一律拒（连脱敏都不走）
    hit = _scan_secret(args, rules.get("deny_secret_keys", list(_SECRET_KEY_HINTS)))
    if hit:
        return {"allowed": False, "reason": f"secret_forbidden:{hit}"}

    # 2.5) 外部 MCP 端点硬闸：仅 MCP_TOOL_ALLOW 显式声明的端点放行（P1-3）。
    # 沙箱作为「最后一道硬闸」，未显式声明的外部端点一律拒绝，连判权都不进入——
    # 外部工具是最大不可控面，绝不能靠默认放行。判权真源复用 guardrails.mcp_tool_declared。
    if tool.startswith("mcp__"):
        from app.services import guardrails
        if not guardrails.mcp_tool_declared(tool):
            return {"allowed": False, "reason": "mcp_endpoint_not_declared"}
        return {"allowed": True, "reason": "delegated_to_guardrails"}

    # 3) 路径隔离（file_read / file_write）
    if tool in ("file_read", "file_write"):
        target = args.get("path") or args.get("file") or args.get("filename")
        if not target:
            return {"allowed": False, "reason": "file_path_required"}
        inside, resolved = is_inside_root(
            rules.get("workspace_root"), target,
            bool(rules.get("allow_symlink_escape", False)),
        )
        if not inside:
            return {"allowed": False, "reason": f"path_escape:{resolved}"}
        return {"allowed": True, "resolved_path": resolved, "reason": "ok"}

    # 4) 命令白名单（shell）
    if tool == "shell":
        shell_cfg = rules.get("shell", {}) or {}
        if not bool(shell_cfg.get("enabled")):
            return {"allowed": False, "reason": "shell_disabled"}
        cmd = args.get("command") or ""
        base = cmd.strip().split()[0] if cmd.strip() else ""
        wl = set(shell_cfg.get("command_whitelist", []) or [])
        if base not in wl:
            return {"allowed": False, "reason": f"command_not_whitelisted:{base}"}
        return {"allowed": True, "reason": "ok"}

    # 其余工具（rag_retrieve / web_search / kb_write 等）交 guardrails 单一过滤点
    return {"allowed": True, "reason": "delegated_to_guardrails"}
