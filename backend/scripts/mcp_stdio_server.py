#!/usr/bin/env python
"""企业 AI 中台 · MCP stdio Server（把平台暴露成标准 MCP Server）。

对外说 MCP（stdio 传输，Claude Desktop / Cursor / Cline 等可直接接入），
对内调用平台「已验证、带租户隔离」的 REST 接口，零侵入、复用全部鉴权与红线。

配置（环境变量）：
  ENTERPRISE_AI_BASE_URL   平台 API 基址，默认 http://localhost:8080/api/v1
  ENTERPRISE_AI_TOKEN      鉴权裸 token（dev <...> 或 JWT），必填
  ENTERPRISE_AI_TIMEOUT    单请求超时秒，默认 30

运行（需已装 mcp<2 与 httpx，且处于 backend venv）：
  cd backend && .venv/Scripts/python.exe scripts/mcp_stdio_server.py

接入 Claude Desktop（claude_desktop_config.json）：
  {
    "mcpServers": {
      "enterprise-ai": {
        "command": "D:/enterprise-ai-platform/backend/.venv/Scripts/python.exe",
        "args": ["D:/enterprise-ai-platform/backend/scripts/mcp_stdio_server.py"],
        "env": {
          "ENTERPRISE_AI_BASE_URL": "http://localhost:8080/api/v1",
          "ENTERPRISE_AI_TOKEN": "<你的平台 token>"
        }
      }
    }
  }
"""
from __future__ import annotations

import os
import sys
import json

# 让 scripts/ 能 import 到 backend/app 包
BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from mcp.server.fastmcp import FastMCP  # noqa: E402

from app.services import mcp_bridge as bridge  # noqa: E402
from app.services.mcp_bridge import BridgeError  # noqa: E402

mcp = FastMCP("企业AI中台")


def _cfg():
    base = os.environ.get("ENTERPRISE_AI_BASE_URL", bridge.DEFAULT_BASE_URL)
    token = os.environ.get("ENTERPRISE_AI_TOKEN", "")
    timeout = float(os.environ.get("ENTERPRISE_AI_TIMEOUT", bridge.DEFAULT_TIMEOUT))
    return base, token, timeout


def _need_token(token: str) -> str | None:
    if not token:
        return ("未配置 ENTERPRISE_AI_TOKEN。请在环境变量中填入平台鉴权裸 token"
                "（dev <...> 或 JWT），例如在 claude_desktop_config.json 的 env 里设置。")
    return None


def _fmt_hits(hits: list) -> str:
    if not hits:
        return "（无命中）"
    lines = []
    for i, h in enumerate(hits, 1):
        lines.append(f"{i}. 《{h['title']}》  相关度={h.get('score')}  范围={h.get('scope')}\n"
                     f"   {h.get('snippet', '')[:240]}")
    return "\n".join(lines)


@mcp.tool()
def list_agents(role: str = "") -> str:
    """列出当前租户可见、已发布的智能体（AI 工具）。返回 id / 名称 / 分类，供 invoke_agent 使用。"""
    base, token, timeout = _cfg()
    if (m := _need_token(token)):
        return m
    try:
        agents = bridge.list_agents(base, token, role=role, )
    except BridgeError as e:
        return f"列出智能体失败：{e}"
    if not agents:
        return "（当前租户无可见智能体）"
    return "可用智能体：\n" + "\n".join(
        f"- id={a['id']}  名称={a['name']}  分类={a['category']}" +
        (f"  说明={a['description'][:60]}" if a["description"] else "")
        for a in agents
    )


@mcp.tool()
def invoke_agent(agent_id: str, query: str, session_id: str = "") -> str:
    """调用一个已发布智能体执行任务。agent_id 来自 list_agents；query 是自然语言指令。
    返回模型答案与引用来源。session_id 可留空（新建会话）或传入以续聊。"""
    base, token, timeout = _cfg()
    if (m := _need_token(token)):
        return m
    try:
        r = bridge.invoke_agent(base, token, agent_id, query,
                                session_id=session_id or None)
    except BridgeError as e:
        return f"调用智能体失败：{e}"
    text = f"【{agent_id} 回答】\n{r['answer']}\n"
    cites = r.get("citations") or []
    if cites:
        text += "\n引用来源：\n" + "\n".join(f"- 《{c.get('title')}》" for c in cites)
    arts = r.get("artifacts") or []
    if arts:
        text += "\n产出物：\n" + "\n".join(
            f"- {a.get('filename')}  ({a.get('url') or a.get('file_id')})" for a in arts)
    if r.get("llm_real") is False:
        text += "\n（提示：本次为 stub/降级结果，非真实模型产出）"
    return text


@mcp.tool()
def search_knowledge(query: str, top_k: int = 6) -> str:
    """在租户私有知识库做语义检索（RAG 带权召回）。返回命中文档标题、摘要与相关度。"""
    base, token, timeout = _cfg()
    if (m := _need_token(token)):
        return m
    try:
        r = bridge.search_knowledge(base, token, query, top_k=top_k)
    except BridgeError as e:
        return f"检索失败：{e}"
    return (f"检索「{r['query']}」命中 {len(r['hits'])} 条"
            f"（召回阶段已剔除越权内容 {r['filtered_out']} 条）：\n" + _fmt_hits(r["hits"]))


@mcp.tool()
def list_documents() -> str:
    """列出当前用户可见的知识库文档（doc_id / 标题），可用于确认检索范围。"""
    base, token, timeout = _cfg()
    if (m := _need_token(token)):
        return m
    try:
        docs = bridge.list_documents(base, token)
    except BridgeError as e:
        return f"列出文档失败：{e}"
    if not docs:
        return "（无可见文档）"
    return "可见文档：\n" + "\n".join(
        f"- doc_id={d['doc_id']}  标题={d['title']}  知识库={d['kb']}" for d in docs)


if __name__ == "__main__":
    mcp.run()
