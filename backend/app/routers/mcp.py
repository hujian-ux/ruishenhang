"""MCP 方向2管理端点：查看平台已接入的外部 MCP 工具、重连配置。

平台作为 MCP Client，连接企业/第三方暴露的 MCP Server（stdio/http/sse），
把它们的工具注册为平台可用工具（命名 mcp__<server>__<tool>），供工作流 tool 节点调用。
详见 services/mcp_client.py 与 services/guardrails.py:_guard_mcp_tool。
"""
from fastapi import APIRouter, Depends

from app.core.config import settings
from app.deps import get_caller
from app.models.caller import CallerContext
from app.services import mcp_client

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
