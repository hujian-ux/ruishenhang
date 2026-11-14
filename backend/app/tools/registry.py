"""P0-2 工具注册表：声明式注册 + 清单生成 + 执行分发。

每个工具 = (name, description, params, execute, risk)。
execute(args, caller) -> {"ok": bool, "content": str, "error": str}（同步执行，
适配文件 IO / 知识库 / 子进程；流式路径在 run_agent_stream 内同步调用，
v1 不要求 execute 本身异步）。

高风险工具（risk="high"，如 shell）由 loop 在执行前转 /approval，注册表只登记，
不决定执行与否。
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from app.tools import sandbox

_REGISTRY: Dict[str, dict] = {}


def register(name: str, description: str, params: dict | None = None,
             execute: Callable | None = None, risk: str = "normal") -> dict:
    """注册一个工具。execute 缺省为 None（占位，未实现）。"""
    _REGISTRY[name] = {
        "name": name,
        "description": description,
        "params": params or {},
        "execute": execute,
        "risk": risk,
    }
    return _REGISTRY[name]


def get(name: str) -> dict | None:
    return _REGISTRY.get(name)


def list_tools(enabled_only: bool = False, rules: dict | None = None) -> List[dict]:
    """列出工具。enabled_only=True 时剔除 sandbox_rules 里显式 disabled 的
    （含 shell——由 shell.enabled 控制，不在 tools 映射里）。"""
    rules = rules or sandbox.load_rules()
    disabled = {
        n for n, c in (rules.get("tools", {}) or {}).items()
        if isinstance(c, dict) and c.get("enabled") is False
    }
    shell_cfg = rules.get("shell", {}) or {}
    if not bool(shell_cfg.get("enabled")):
        disabled.add("shell")
    out: List[dict] = []
    for n, t in _REGISTRY.items():
        if enabled_only and n in disabled:
            continue
        out.append({
            "name": n,
            "description": t["description"],
            "params": t["params"],
            "risk": t["risk"],
        })
    return out


def manifest_prompt(enabled_only: bool = True) -> str:
    """生成注入 system_prompt 的工具清单，让模型知道可用工具与调用格式。"""
    tools = list_tools(enabled_only=enabled_only)
    if not tools:
        return ""
    lines = [
        "你可以在需要时调用以下工具。调用格式（放在你的回复里，一次最多一个，"
        "用三反引号包裹，不要额外解释）：",
        "```tool_call",
        '{"tool": "<工具名>", "args": {<参数>}}',
        "```",
        "例如要调用 web_search 搜索“今日AI新闻”，就原样输出：",
        "```tool_call",
        '{"tool": "web_search", "args": {"query": "今日AI新闻"}}',
        "```",
        "工具结果会作为后续上下文回传给你。拿到结果后请直接给出最终答案，"
        "不要重复调用同一工具，除非仍缺关键信息。",
        "可用工具：",
    ]
    for t in tools:
        lines.append(
            f"- {t['name']}（风险：{t['risk']}）：{t['description']} "
            f"参数：{json.dumps(t['params'], ensure_ascii=False)}"
        )
    return "\n".join(lines)


def execute(tool: str, args: dict, caller: Any = None) -> dict:
    """执行已注册工具。返回统一的 {ok, content, error}。"""
    t = get(tool)
    if not t or not t.get("execute"):
        return {"ok": False, "content": "", "error": f"unknown_or_unimplemented_tool:{tool}"}
    try:
        res = t["execute"](args or {}, caller)
    except Exception as exc:  # 工具内部异常不外抛，转为结构化失败
        return {"ok": False, "content": "", "error": f"{type(exc).__name__}: {exc}"[:300]}
    if not isinstance(res, dict):
        res = {"ok": True, "content": str(res), "error": ""}
    res.setdefault("ok", True)
    res.setdefault("content", "")
    res.setdefault("error", "")
    return res
