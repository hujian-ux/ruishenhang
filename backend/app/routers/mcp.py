"""MCP 方向2管理端点：查看平台已接入的外部 MCP 工具、重连配置。

平台作为 MCP Client，连接企业/第三方暴露的 MCP Server（stdio/http/sse），
把它们的工具注册为平台可用工具（命名 mcp__<server>__<tool>），供工作流 tool 节点调用。
详见 services/mcp_client.py 与 services/guardrails.py:_guard_mcp_tool。

P1-2 新增：平台同时作为 MCP Server（/mcp/serve），把自身带租户隔离的能力
（kb_search / skill_invoke / artifact_get / list_skills）以 HTTP/JSON-RPC 暴露给
客户 IDE / 自有 Agent。详见 services/mcp_server.py。
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from app.core.config import settings
from app.deps import get_caller
from app.models.caller import CallerContext
from app.services import mcp_client, mcp_server

router = APIRouter(prefix="/mcp", tags=["mcp"])


@router.get("/tools")
async def list_external_tools(caller: CallerContext = Depends(get_caller)):
    """列出已接入的外部 MCP 工具（含 server / 名称 / 描述 / 入参 schema / 平台内限定名）。"""
    tools = mcp_client.list_mcp_tools()
    return {
        "code": 0,
        "data": {
            "tools": tools,
            "status": mcp_client.mcp_status(),
        },
    }


@router.get("/status")
async def mcp_status(caller: CallerContext = Depends(get_caller)):
    """MCP Client 连接状态：已配置/已连接的 server 清单。"""
    return {"code": 0, "data": mcp_client.mcp_status()}


@router.post("/reload")
async def reload_external_tools(caller: CallerContext = Depends(get_caller)):
    """重读 MCP_SERVERS 环境变量并重新连接所有外部 MCP Server（platform_admin 专用）。"""
    if caller.role != "platform_admin":
        return {"code": 403, "msg": "forbidden: 仅平台管理员可重连外部 MCP"}
    mcp_client.configure_manager(settings.MCP_SERVERS)
    mcp_client.reload_mcp()
    return {"code": 0, "data": mcp_client.mcp_status()}


# ── P1-2 平台作为 MCP Server：HTTP / JSON-RPC 2.0 暴露内部能力 ──────────────────
@router.get("/serve")
async def mcp_serve_info():
    """连接信息（well-known）：返回 MCP 端点、传输方式、鉴权要求与工具清单。

    客户 IDE / 自有 Agent 配置本平台为 MCP Server 时读取此描述即可。
    """
    return {"code": 0, "data": mcp_server.discovery()}


@router.post("/serve")
async def mcp_serve(req: Request, caller: CallerContext = Depends(get_caller)):
    """MCP Server 主入口：接收 JSON-RPC 2.0 请求（单条或批量），返回 JSON-RPC 响应。

    - 鉴权：复用网关 decode_token（dev / JWT / sso 前缀均支持），租户隔离随 caller。
    - 配额：每租户每分钟工具调用上限（MCP_QUOTA_PER_MIN），超限返回 -32002。
    - 传输：streamable-http 形态——客户端 POST 请求体，服务端回 JSON-RPC 响应；
      通知类（无 id 的 notifications/*）返回 202 无响应体。
    """
    if not settings.MCP_SERVE_ENABLED:
        return JSONResponse(
            status_code=503,
            content=mcp_server._resp(None, error={"code": -32000, "message": "MCP Server 未启用（MCP_SERVE_ENABLED=1）"}),
        )
    try:
        payload = await req.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content=mcp_server._resp(None, error={"code": -32700, "message": "JSON 解析失败"}),
        )
    result = mcp_server.handle_jsonrpc(caller, payload)
    if result is None:
        return Response(status_code=202)  # 通知类，无需响应体
    return JSONResponse(content=result)
