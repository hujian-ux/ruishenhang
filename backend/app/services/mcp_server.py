"""P1-2 MCP 服务端：把平台「带租户隔离」的能力暴露成标准 MCP Server（HTTP / JSON-RPC 2.0）。

与 scripts/mcp_stdio_server.py（stdio 传输）同源，但：
- 传输改为 HTTP（streamable-http 形态）：客户端 POST JSON-RPC 到 /api/v1/mcp/serve，
  复用网关 decode_token 做租户级鉴权（dev / JWT / sso 前缀均支持），无需单独配 token 走 HTTP 回环。
- 工具在进程内直接调用平台「已验证、带红线」的服务层（knowledge / agent_runtime /
  agent_store / file_store），而非经外部 REST 回环，零额外网络、天然复用全部隔离与审计。
- 配额：每租户每分钟工具调用次数上限（MCP_QUOTA_PER_MIN），超限返回 -32002。

暴露工具：
  kb_search      —— 租户私有知识库语义检索（RAG 带权召回）
  skill_invoke   —— 调用一个已发布智能体执行任务（返回答案 + 引用 + 产出物 file_id）
  artifact_get   —— 取一次执行的产出物文件内容（文本直出 / 二进制 base64，复用 file_store 可见性判定）
  list_skills    —— 列出当前租户可见、已发布的智能体（供 skill_invoke 发现 agent_id）
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.models.caller import CallerContext

# ── 工具清单（name / description / inputSchema，供 tools/list 与 initialize 使用）──────
TOOLS: List[Dict[str, Any]] = [
    {
        "name": "kb_search",
        "description": "在租户私有知识库做语义检索（RAG 带权召回）。返回命中文档标题、摘要与相关度；越权内容在召回阶段即被剔除。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索问题或关键词"},
                "top_k": {"type": "integer", "default": 6, "description": "返回条数（1-20）"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "skill_invoke",
        "description": "调用一个已发布智能体（技能包）执行自然语言任务。返回模型答案、引用来源与产出物 file_id（可再用 artifact_get 取内容）。agent_id 来自 list_skills。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "智能体 id（list_skills 返回的 id）"},
                "query": {"type": "string", "description": "自然语言指令"},
                "session_id": {"type": "string", "default": "", "description": "留空新建会话；传入以续聊"},
            },
            "required": ["agent_id", "query"],
        },
    },
    {
        "name": "artifact_get",
        "description": "取一次智能体执行的产出物文件内容。文本类（md/txt/json/html/csv/code）直出文本；二进制返回 base64 字符串。复用文件可见性判定（无权访问返回错误）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string", "description": "产出物 file_id（skill_invoke 返回）"},
            },
            "required": ["file_id"],
        },
    },
    {
        "name": "list_skills",
        "description": "列出当前租户可见、已发布的智能体（AI 工具）。返回 id / 名称 / 分类，供 skill_invoke 发现目标。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "role": {"type": "string", "default": "", "description": "按角色过滤（可选）"},
            },
            "required": [],
        },
    },
]

_PROTOCOL_VERSION = "2024-11-05"
_TEXT_EXT = {"md", "markdown", "txt", "json", "html", "htm", "csv", "tsv", "py",
             "js", "ts", "yaml", "yml", "xml", "tex", "log"}


class McpError(Exception):
    """MCP JSON-RPC 错误：code 符合 JSON-RPC 约定（或 MCP 扩展码）。"""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ── 配额：每租户每分钟工具调用次数上限（进程内计数，重启清零；演示/轻量够用）──────
class _Quota:
    def __init__(self):
        self._lock = threading.Lock()
        self._buckets: Dict[str, int] = {}

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()

    def consume(self, tenant_id: str) -> None:
        limit = settings.MCP_QUOTA_PER_MIN
        if limit <= 0:
            return
        minute = int(time.time() // 60)
        key = f"{tenant_id}:{minute}"
        with self._lock:
            n = self._buckets.get(key, 0) + 1
            self._buckets[key] = n
            # 顺手清理上一分钟之前的桶，避免无限增长
            for k in list(self._buckets.keys()):
                if k.rsplit(":", 1)[1] != str(minute):
                    self._buckets.pop(k, None)
            if n > limit:
                raise McpError(-32002, f"租户 {tenant_id} 已达每分钟 {limit} 次调用配额上限，请稍后再试")


_quota = _Quota()


def reset_quota() -> None:
    """测试用：清空配额计数。"""
    _quota.reset()


# ── 工具实现（进程内复用平台服务层，带租户隔离 + 红线）────────────────────────────
def _fmt_hits(hits: List[Dict[str, Any]]) -> str:
    if not hits:
        return "（无命中）"
    lines = []
    for i, h in enumerate(hits, 1):
        lines.append(
            f"{i}. 《{h.get('title', '')}》  相关度={h.get('score')}  范围={h.get('scope', '')}\n"
            f"   {(h.get('snippet', '') or '')[:240]}"
        )
    return "\n".join(lines)


def kb_search(caller: CallerContext, query: str, top_k: int = 6) -> str:
    from app.services import knowledge
    top_k = max(1, min(int(top_k or 6), 20))
    hits, filtered_out = knowledge.retrieve(query, caller, top_k=top_k)
    return (f"检索「{query}」命中 {len(hits)} 条"
            f"（召回阶段已剔除越权内容 {filtered_out} 条）：\n" + _fmt_hits(hits))


def skill_invoke(caller: CallerContext, agent_id: str, query: str,
                 session_id: str = "") -> str:
    from app.services import agent_runtime
    res = agent_runtime.run_agent(agent_id, query, caller, session_id=session_id or "")
    answer = res.get("answer", "")
    text = f"【{agent_id} 回答】\n{answer}\n"
    cites = res.get("citations") or []
    if cites:
        text += "\n引用来源：\n" + "\n".join(
            f"- 《{c.get('title', '')}》" for c in cites)
    arts = res.get("artifacts") or []
    if arts:
        text += "\n产出物（用 artifact_get 取内容）：\n" + "\n".join(
            f"- file_id={a.get('file_id')}  文件名={a.get('filename', '')}"
            for a in arts)
    if res.get("llm_real") is False:
        text += "\n（提示：本次为 stub/降级结果，非真实模型产出）"
    return text


def artifact_get(caller: CallerContext, file_id: str) -> str:
    from app.services import file_store
    rec = file_store.get(file_id)
    if not rec or not file_store.can_access(rec, caller):
        raise McpError(-32001, f"文件不存在或无权访问：{file_id}")
    data = file_store.read(rec)
    if data is None:
        raise McpError(-32001, f"文件内容已丢失（对象存储不可达或已清理）：{file_id}")
    fname = rec.get("filename") or file_id
    ext = (fname.rsplit(".", 1)[-1].lower() if "." in fname else "")
    if ext in _TEXT_EXT:
        try:
            return f"【{fname}】\n{data.decode('utf-8', errors='replace')}"
        except Exception:
            pass
    # 二进制：base64 直出，客户端按需解码
    import base64
    return f"【{fname}｜base64】\n{base64.b64encode(data).decode('ascii')}"


def list_skills(caller: CallerContext, role: str = "") -> str:
    from app.services import agent_store
    data = agent_store.catalog(caller, role=role)
    out: List[Dict[str, Any]] = []

    def _brief(a: Dict[str, Any], group: str) -> Dict[str, Any]:
        return {
            "id": a.get("agent_id") or a.get("id"),
            "name": a.get("name") or a.get("title") or a.get("agent_id") or a.get("id"),
            "description": a.get("description") or a.get("summary") or "",
            "category": group or a.get("category_cn") or a.get("category") or "",
        }

    for block_key, group_label in (("categories", "行业"), ("roles", "职业")):
        for node in data.get(block_key, []) or []:
            label = node.get("category_cn") or node.get("name") or group_label
            for agents in (node.get("groups") or {}).values():
                for a in agents:
                    out.append(_brief(a, label))
    for a in data.get("public", []) or []:
        out.append(_brief(a, "公共库"))
    if not out:
        return "（当前租户无可见智能体）"
    seen, uniq = set(), []
    for a in out:
        if a["id"] and a["id"] not in seen:
            seen.add(a["id"])
            uniq.append(a)
    return "可用智能体：\n" + "\n".join(
        f"- id={a['id']}  名称={a['name']}  分类={a['category']}" +
        (f"  说明={a['description'][:60]}" if a["description"] else "")
        for a in uniq
    )


# ── 工具分派 ─────────────────────────────────────────────────────────────────────
def _call_tool(caller: CallerContext, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    arguments = arguments or {}
    if name == "kb_search":
        text = kb_search(caller, arguments["query"], arguments.get("top_k", 6))
    elif name == "skill_invoke":
        text = skill_invoke(caller, arguments["agent_id"], arguments["query"],
                            arguments.get("session_id", ""))
    elif name == "artifact_get":
        text = artifact_get(caller, arguments["file_id"])
    elif name == "list_skills":
        text = list_skills(caller, arguments.get("role", ""))
    else:
        raise McpError(-32601, f"未知工具：{name}")
    return {"content": [{"type": "text", "text": text}]}


def dispatch(caller: CallerContext, method: str, params: Optional[Dict[str, Any]]) -> Any:
    """分派一个 JSON-RPC 方法，返回 result（通知类方法返回 None）。"""
    params = params or {}
    if method == "initialize":
        return {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "企业AI中台 MCP Server", "version": "1.0"},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        name = params.get("name")
        if not name:
            raise McpError(-32602, "tools/call 缺少 name")
        # 租户级配额：每个 tools/call 计 1 次
        _quota.consume(caller.tenantId)
        return _call_tool(caller, name, params.get("arguments", {}))
    if method.startswith("notifications/"):
        return None
    raise McpError(-32601, f"方法不存在：{method}")


def _resp(msg_id: Any, result: Any = None, error: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    r: Dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        r["error"] = error
    else:
        r["result"] = result
    return r


def handle_jsonrpc(caller: CallerContext, payload: Any) -> Optional[Any]:
    """处理一个 JSON-RPC 请求（或批量）。返回响应对象（dict / list），通知返回 None。"""
    if isinstance(payload, list):
        out = []
        for item in payload:
            r = _handle_one(caller, item)
            if r is not None:
                out.append(r)
        return out if out else None

    r = _handle_one(caller, payload)
    return r


def _handle_one(caller: CallerContext, item: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return _resp(None, error={"code": -32700, "message": "invalid JSON-RPC message"})
    method = item.get("method")
    msg_id = item.get("id", None)
    params = item.get("params")
    try:
        result = dispatch(caller, method, params)
    except McpError as e:
        return _resp(msg_id, error={"code": e.code, "message": e.message})
    except Exception as e:  # noqa: BLE001
        return _resp(msg_id, error={"code": -32603, "message": f"内部错误：{e}"})
    # 通知（无 id）不回响应
    if msg_id is None and method and method.startswith("notifications/"):
        return None
    return _resp(msg_id, result=result)


def discovery() -> Dict[str, Any]:
    """连接信息（well-known）：供客户端配置 MCP Server 端点与鉴权方式。"""
    return {
        "mcp_endpoint": f"{settings.API_PREFIX}/mcp/serve",
        "transport": "streamable-http (JSON-RPC 2.0 over POST)",
        "protocolVersion": _PROTOCOL_VERSION,
        "auth": "在 Authorization 头携带平台裸 token（dev <...> / JWT / sso <...>），不带 Bearer 前缀",
        "tools": [{"name": t["name"], "description": t["description"]} for t in TOOLS],
    }
