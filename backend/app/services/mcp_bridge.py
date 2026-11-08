"""MCP 桥接层：把平台「已验证、且带租户隔离」的 REST 接口，封装成可被 MCP Server 调用的纯函数。

设计要点：
- 不依赖 mcp SDK（SDK 仅在 scripts/mcp_stdio_server.py 中使用），便于单元测试、不污染后端依赖。
- 所有调用走平台既有 REST 端点，天然复用网关鉴权 + 租户过滤 + 红线（未发布不可调、越权 404）。
- 鉴权头是「裸 token」（形如 `dev <...>` 或 JWT），不带 `Bearer ` 前缀——与网关 decode_token 一致。
- 返回的永远是「经过归一化」的纯 dict，MCP 工具层只负责格式化文本，不关心后端字段细节。
"""
from __future__ import annotations

import httpx
from typing import Any, Dict, List, Optional

DEFAULT_BASE_URL = "http://localhost:8080/api/v1"
DEFAULT_TIMEOUT = 30.0


class BridgeError(Exception):
    """平台侧连接 / 返回异常，MCP 工具应把它转成可读文本返回给模型，而非抛栈。"""


def _headers(token: str) -> Dict[str, str]:
    # 平台鉴权头是裸 token（dev <...> 或 JWT），不带 Bearer 前缀
    return {"Authorization": token, "Content-Type": "application/json"}


def _call(method: str, base_url: str, token: str, path: str,
          *, json: Optional[dict] = None, params: Optional[dict] = None,
          timeout: float = DEFAULT_TIMEOUT) -> dict:
    url = base_url.rstrip("/") + path
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.request(method, url, json=json, params=params, headers=_headers(token))
    except httpx.HTTPError as e:  # 连接/超时等
        raise BridgeError(f"平台连接失败（{url}）：{e}") from e
    if r.status_code >= 400:
        raise BridgeError(f"平台返回 {r.status_code}：{r.text[:300]}")
    try:
        return r.json()
    except ValueError:
        raise BridgeError(f"平台返回非 JSON：{r.text[:300]}")


def _data(resp: dict) -> dict:
    """拆信封：{code, data} 或裸 data。"""
    if isinstance(resp, dict) and "data" in resp:
        return resp["data"]
    return resp or {}


def _brief_agent(a: dict, group: str = "") -> Dict[str, Any]:
    return {
        "id": a.get("agent_id") or a.get("id"),
        "name": a.get("name") or a.get("title") or a.get("agent_id") or a.get("id"),
        "description": a.get("description") or a.get("summary") or "",
        "category": group or a.get("category_cn") or a.get("category") or "",
    }


def list_agents(base_url: str, token: str, role: str = "") -> List[Dict[str, Any]]:
    """列出当前租户可见、已发布的智能体（catalog 三块：行业 / 职业 / 公共库）。"""
    params = {"role": role} if role else None
    resp = _call("GET", base_url, token, "/agents/catalog", params=params)
    data = _data(resp)
    out: List[Dict[str, Any]] = []
    for block_key, group_label in (("categories", "行业"), ("roles", "职业")):
        for node in data.get(block_key, []) or []:
            label = node.get("category_cn") or node.get("name") or group_label
            for agents in (node.get("groups") or {}).values():
                for a in agents:
                    out.append(_brief_agent(a, label))
    for a in data.get("public", []) or []:
        out.append(_brief_agent(a, "公共库"))
    # 去重（同一包可能同时出现在多视角）
    seen, uniq = set(), []
    for a in out:
        if a["id"] and a["id"] not in seen:
            seen.add(a["id"])
            uniq.append(a)
    return uniq


def invoke_agent(base_url: str, token: str, agent_id: str, query: str,
                 session_id: Optional[str] = None,
                 output_formats: Optional[List[str]] = None) -> Dict[str, Any]:
    """调用一个已发布智能体（非流式，便于工具返回完整答案）。"""
    body = {
        "agent_id": agent_id,
        "input": query,
        "stream": False,
        "session_id": session_id,
        "output_formats": output_formats or [],
    }
    resp = _call("POST", base_url, token, "/invoke", json=body)
    data = _data(resp)
    return {
        "answer": data.get("answer", ""),
        "citations": data.get("citations", []) or [],
        "artifacts": data.get("artifacts", []) or [],
        "agent_id": agent_id,
        "run_id": data.get("run_id"),
        "session_id": (data.get("session") or {}).get("session_id"),
        "llm_real": data.get("llm_real"),
    }


def search_knowledge(base_url: str, token: str, query: str, top_k: int = 6) -> Dict[str, Any]:
    """语义检索（RAG 带权召回），越权内容在召回阶段即被剔除。"""
    body = {"query": query, "top_k": top_k, "kb_scope": "all"}
    resp = _call("POST", base_url, token, "/rag/retrieve", json=body)
    data = _data(resp)
    hits = [{
        "doc_id": h.get("doc_id"),
        "title": h.get("title", ""),
        "snippet": h.get("snippet", ""),
        "score": h.get("score"),
        "scope": h.get("scope", ""),
    } for h in data.get("hits", []) or []]
    return {"query": query, "hits": hits, "filtered_out": data.get("filtered_out", 0)}


def list_documents(base_url: str, token: str) -> List[Dict[str, Any]]:
    """列出当前用户可见的知识库文档。"""
    resp = _call("GET", base_url, token, "/kb/documents")
    data = _data(resp)
    docs = data.get("documents", data) if isinstance(data, dict) else data
    out = []
    for d in docs or []:
        if isinstance(d, dict):
            out.append({
                "doc_id": d.get("doc_id") or d.get("id"),
                "title": d.get("title") or d.get("name") or "",
                "kb": d.get("kb") or d.get("kb_id") or "",
            })
    return out
