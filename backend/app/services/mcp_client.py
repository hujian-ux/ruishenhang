"""外部 MCP Server 客户端管理器（平台消费外部 MCP 工具）。

设计要点：
- 对外暴露 stdio / streamable_http / sse 三种传输，连接用户配置的外部 MCP Server。
- 枚举其 tools，命名规范 ``mcp__<server>__<tool>``，供平台智能体/工作流 tool 节点调用。
- 与 MCP Server 侧（scripts/mcp_stdio_server.py）对称：那边是平台做 Server，这边是平台做 Client。
- ``mcp`` SDK 仅在真正连接时才 import（懒加载），未配置外部 MCP 时平台零依赖、零副作用。
- 为方便测试，会话可被直接注入（无需真实网络）。

连接生命周期：每个 server 由一个常驻后台任务 ``_ServerConn._run`` 持有
``async with stdio_client / ClientSession`` 栈——栈的进入与退出都在**同一个任务**内，
避免 anyio 任务组跨任务退出的异常。同步代码（工作流 tool 节点）经自有事件循环线程
（``_submit``）安全调用异步会话。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("enterprise_ai.mcp_client")

MCP_TOOL_PREFIX = "mcp__"  # 工具名前缀：mcp__<server>__<tool>


def _compute_requires_approval(server_cfg: "MCPServerConfig", annotations: Dict[str, Any]) -> bool:
    """P1-3：计算某工具是否需人工审批（安全优先，默认 True）。

    - server 级 requires_approval 默认为 True（外部工具是最大不可控面）；
    - 仅当 server 显式设 requires_approval=False（受信任）且其工具 annotation
      readOnlyHint=True（纯只读）时，才豁免审批；其余一律需审批。
    """
    server_default = bool(getattr(server_cfg, "requires_approval", True))
    if server_default is False and (annotations or {}).get("readOnlyHint") is True:
        return False
    return server_default


@dataclass
class MCPServerConfig:
    name: str
    transport: str = "stdio"          # stdio | http | sse
    command: Optional[str] = None
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    url: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)
    timeout: float = 30.0
    # P1-3：外部 MCP 工具审批闸。默认 True（安全优先：外部工具是最大不可控面，
    # 无审批不能开给客户）；设为 False 表示「受信任 server」，仅其只读工具免审，
    # 其余（含变更类）仍走审批。
    requires_approval: bool = True

    @classmethod
    def from_dict(cls, d: Dict[str, Any], name: Optional[str] = None) -> "MCPServerConfig":
        return cls(
            name=name or d["name"],
            transport=(d.get("transport") or "stdio").lower(),
            command=d.get("command"),
            args=list(d.get("args") or []),
            env=dict(d.get("env") or {}),
            url=d.get("url"),
            headers=dict(d.get("headers") or {}),
            timeout=float(d.get("timeout") or 30.0),
            requires_approval=bool(d.get("requires_approval", True)),
        )


class _ServerConn:
    """单个外部 MCP Server 的连接：常驻任务持有 async-with 栈，提供 list/call。"""

    def __init__(self, cfg: MCPServerConfig):
        self.cfg = cfg
        self.tools: List[Dict[str, Any]] = []
        self._session: Any = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._error: Optional[BaseException] = None

    async def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._run())
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=self.cfg.timeout)
        except asyncio.TimeoutError:
            if self._task is not None:
                self._task.cancel()
            raise TimeoutError(f"MCP 连接超时：{self.cfg.name}")
        if self._error is not None:
            raise self._error

    async def _run(self) -> None:
        try:
            if self.cfg.transport == "stdio":
                await self._run_stdio()
            elif self.cfg.transport in ("http", "sse"):
                await self._run_http()
            else:
                raise ValueError(f"不支持的 MCP 传输：{self.cfg.transport}")
        except Exception as exc:  # 让 start() 能感知连接期错误；运行期错误仅记日志
            if not self._ready.is_set():
                self._error = exc
            logger.error("MCP 运行异常 %s：%s", self.cfg.name, exc)
        finally:
            self._ready.set()

    async def _run_stdio(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        if not self.cfg.command:
            raise ValueError(f"stdio 传输需要 command：{self.cfg.name}")
        params: Dict[str, Any] = {"command": self.cfg.command, "args": list(self.cfg.args)}
        if self.cfg.env:
            params["env"] = {**os.environ, **self.cfg.env}
        async with stdio_client(StdioServerParameters(**params)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                self._session = session
                self.tools = await self._list_tools(session)
                self._ready.set()
                await self._stop.wait()

    async def _run_http(self) -> None:
        from mcp import ClientSession
        if self.cfg.transport == "http":
            from mcp.client.streamable_http import streamablehttp_client as tr
        else:
            from mcp.client.sse import sse_client as tr
        if not self.cfg.url:
            raise ValueError(f"{self.cfg.transport} 传输需要 url：{self.cfg.name}")
        async with tr(self.cfg.url, headers=self.cfg.headers or None) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                self._session = session
                self.tools = await self._list_tools(session)
                self._ready.set()
                await self._stop.wait()

    async def _list_tools(self, session: Any) -> List[Dict[str, Any]]:
        """枚举工具，捕获 MCP annotations（readOnlyHint / destructiveHint 等），
        并据此 + server 级 requires_approval 计算每工具的是否需审批。"""
        res = await session.list_tools()
        out = []
        for t in getattr(res, "tools", []) or []:
            ann = getattr(t, "annotations", None)
            annotations: Dict[str, Any] = {}
            if ann is not None:
                annotations = {
                    "title": getattr(ann, "title", None),
                    "readOnlyHint": getattr(ann, "readOnlyHint", None),
                    "destructiveHint": getattr(ann, "destructiveHint", None),
                    "idempotentHint": getattr(ann, "idempotentHint", None),
                    "openWorldHint": getattr(ann, "openWorldHint", None),
                }
            rec = {
                "name": getattr(t, "name", ""),
                "description": getattr(t, "description", "") or "",
                "input_schema": getattr(t, "inputSchema", {}) or {},
                "annotations": annotations,
            }
            rec["requires_approval"] = _compute_requires_approval(self.cfg, annotations)
            out.append(rec)
        return out

    async def call(self, tool: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if self._session is None:
            raise RuntimeError(f"MCP 会话未就绪：{self.cfg.name}")
        res = await self._session.call_tool(tool, arguments or {})
        content = []
        for c in getattr(res, "content", []) or []:
            if getattr(c, "type", None) == "text":
                content.append(getattr(c, "text", ""))
            else:
                content.append(str(getattr(c, "data", c)))
        return {
            "content": "\n".join(x for x in content if x),
            "is_error": bool(getattr(res, "isError", False)),
        }

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                self._task.cancel()


class MCPSession:
    def __init__(self, config: MCPServerConfig, conn: _ServerConn):
        self.config = config
        self.conn = conn
        self.tools = conn.tools


def _split_qualified(qualified: str):
    """mcp__server__tool -> (server, tool)。"""
    if not qualified.startswith(MCP_TOOL_PREFIX):
        raise ValueError(f"不是 MCP 工具名：{qualified}")
    rest = qualified[len(MCP_TOOL_PREFIX):]
    parts = rest.split("__", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"MCP 工具名格式应为 mcp__<server>__<tool>：{qualified}")
    return parts[0], parts[1]


class ExternalMCPManager:
    def __init__(self):
        self._servers: Dict[str, MCPServerConfig] = {}
        self._sessions: Dict[str, MCPSession] = {}
        self._connected = False
        self._lock = asyncio.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None

    # --------------------------- 配置 ---------------------------
    def load_from_env(self, raw: Optional[str]) -> None:
        self._servers = {}
        if not raw:
            return
        data = self._parse_config(raw)
        if isinstance(data, list):
            for item in data:
                cfg = MCPServerConfig.from_dict(item)
                self._servers[cfg.name] = cfg
        elif isinstance(data, dict):
            for name, item in data.items():
                cfg = MCPServerConfig.from_dict(item, name=name)
                self._servers[cfg.name] = cfg
        else:
            raise ValueError("MCP_SERVERS 应为 JSON 数组或对象")

    @staticmethod
    def _parse_config(raw: str) -> Any:
        raw = raw.strip()
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            out = []
            for line in raw.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    out.append(json.loads(line))
            return out

    # --------------------------- 连接 ---------------------------
    def start_loop(self) -> None:
        if self._loop is not None:
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

    def _submit(self, coro, timeout: float = 60):
        self.start_loop()
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout)

    async def connect_all(self) -> None:
        async with self._lock:
            for name, cfg in self._servers.items():
                if name in self._sessions:
                    continue
                try:
                    conn = _ServerConn(cfg)
                    await conn.start()
                    self._sessions[name] = MCPSession(cfg, conn)
                    logger.info("MCP 已连接：%s（%d 个工具）", name, len(conn.tools))
                except Exception as exc:  # 单台失败不影响其它
                    logger.error("MCP 连接失败 %s：%s", name, exc)

    async def call_tool(self, server: str, tool: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        s = self._sessions.get(server)
        if s is None:
            raise KeyError(f"MCP Server 未连接：{server}")
        return await s.conn.call(tool, arguments)

    async def close_all(self) -> None:
        async with self._lock:
            for s in list(self._sessions.values()):
                try:
                    await s.conn.stop()
                except Exception as exc:  # pragma: no cover
                    logger.warning("MCP 关闭异常 %s：%s", s.config.name, exc)
            self._sessions = {}

    # --------------------------- 同步门面（供 guardrails / 路由调用） ---------------------------
    def ensure_connected(self) -> None:
        if self._connected or not self._servers:
            self._connected = True
            return
        self._submit(self.connect_all())
        self._connected = True

    def list_tools(self) -> List[Dict[str, Any]]:
        self.ensure_connected()
        out = []
        for name, s in self._sessions.items():
            for t in s.tools:
                out.append({
                    "server": name,
                    "name": t["name"],
                    "description": t["description"],
                    "input_schema": t["input_schema"],
                    "annotations": t.get("annotations", {}),
                    "requires_approval": bool(t.get("requires_approval", True)),
                    "server_requires_approval": bool(s.config.requires_approval),
                    "qualified": f"{MCP_TOOL_PREFIX}{name}__{t['name']}",
                })
        return out

    def requires_approval(self, qualified: str) -> bool:
        """P1-3：查询某 MCP 工具是否需要人工审批。未命中（未知工具）fail-closed 返回 True。"""
        try:
            server, tool = _split_qualified(qualified)
        except ValueError:
            return True
        s = self._sessions.get(server)
        if s is None:
            return True  # fail-closed：未连接 / 未知 server
        for t in s.tools:
            if t["name"] == tool:
                return bool(t.get("requires_approval", True))
        return True  # fail-closed：未知工具

    def call(self, qualified: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        server, tool = _split_qualified(qualified)
        self.ensure_connected()
        return self._submit(self.call_tool(server, tool, arguments))

    def reload(self) -> None:
        if self._loop is not None and self._sessions:
            self._submit(self.close_all())
        else:
            self._sessions = {}
        self._connected = False
        self.ensure_connected()

    def status(self) -> Dict[str, Any]:
        return {
            "configured": list(self._servers.keys()),
            "connected": list(self._sessions.keys()),
            "connected_flag": self._connected,
        }

    # --------------------------- 测试注入钩子 ---------------------------
    def inject_session(self, name: str, fake_session: Any, tools: List[Dict[str, Any]]) -> None:
        """测试用：直接注入一个假会话与工具列表，跳过真实网络。"""
        cfg = self._servers.get(name) or MCPServerConfig(name=name)
        self._servers[name] = cfg
        conn = _ServerConn(cfg)
        conn.tools = tools
        conn._session = fake_session
        conn._ready.set()
        self._sessions[name] = MCPSession(cfg, conn)
        self._connected = True


_manager = ExternalMCPManager()


def configure_manager(raw_env: Optional[str]) -> None:
    _manager.load_from_env(raw_env)


def ensure_connected() -> None:
    _manager.ensure_connected()


def list_mcp_tools() -> List[Dict[str, Any]]:
    return _manager.list_tools()


def call_mcp_tool(qualified: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    return _manager.call(qualified, arguments)


def mcp_requires_approval(qualified: str) -> bool:
    """P1-3：查询某 MCP 工具是否需要人工审批（fail-closed 默认 True）。"""
    return _manager.requires_approval(qualified)


def reload_mcp() -> None:
    _manager.reload()


def mcp_status() -> Dict[str, Any]:
    return _manager.status()


def split_qualified(qualified: str):
    return _split_qualified(qualified)
