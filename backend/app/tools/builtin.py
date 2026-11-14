"""内置工具实现（P0-2）。每个 execute 返回 {ok, content, error}。

安全约定：
  - 所有文件类工具都先走 sandbox.check（路径隔离），再读写；
  - rag_retrieve / web_search 走 guardrails.guard_tool_call 单一过滤点（由 loop 统一调，
    这里不再重复调，避免双份审计计数）；
  - shell 默认禁用（沙箱白名单 + loop 高危转审批双重拦），本模块只实现执行体。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services import knowledge, web_search
from app.tools import registry, sandbox


# ── rag_retrieve ─────────────────────────────────────────────────────────────
def _rag(args: dict, caller: Any) -> dict:
    query = args.get("query") or ""
    if not query:
        return {"ok": False, "content": "", "error": "query required"}
    try:
        hits, _ = knowledge.retrieve(query, caller, top_k=int(args.get("top_k", 5)))
    except Exception as exc:
        return {"ok": False, "content": "", "error": f"retrieve_error:{exc}"}
    items = [{
        "title": h.get("title"),
        "snippet": (h.get("snippets") or [h.get("snippet") or ""])[0],
    } for h in hits[:5]]
    return {"ok": True, "content": json.dumps(items, ensure_ascii=False), "error": ""}


# ── web_search ────────────────────────────────────────────────────────────────
def _web(args: dict, caller: Any) -> dict:
    q = args.get("query") or ""
    if not q:
        return {"ok": False, "content": "", "error": "query required"}
    try:
        out = web_search.search(q, settings.WEB_SEARCH_MAX_RESULTS,
                                fetch_top=int(args.get("fetch_top", 0)))
    except Exception as exc:
        return {"ok": False, "content": "", "error": f"search_error:{exc}"}
    hits = out.get("hits", [])[:5]
    items = [{"title": h.get("title"), "url": h.get("url"), "snippet": h.get("snippet")}
             for h in hits]
    return {"ok": True, "content": json.dumps(items, ensure_ascii=False), "error": ""}


# ── file_read / file_write（路径隔离） ────────────────────────────────────────
def _file_read(args: dict, caller: Any) -> dict:
    path = args.get("path") or args.get("file") or args.get("filename")
    if not path:
        return {"ok": False, "content": "", "error": "path required"}
    sb = sandbox.check("file_read", {"path": path}, caller)
    if not sb["allowed"]:
        return {"ok": False, "content": "", "error": sb["reason"]}
    p = Path(sb["resolved_path"])
    if not p.exists():
        return {"ok": False, "content": "", "error": "file not found"}
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return {"ok": False, "content": "", "error": str(exc)}
    return {"ok": True, "content": text[:8000], "error": ""}


def _file_write(args: dict, caller: Any) -> dict:
    path = args.get("path") or args.get("file") or args.get("filename")
    content = args.get("content") or ""
    if not path:
        return {"ok": False, "content": "", "error": "path required"}
    sb = sandbox.check("file_write", {"path": path}, caller)
    if not sb["allowed"]:
        return {"ok": False, "content": "", "error": sb["reason"]}
    p = Path(sb["resolved_path"])
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    except Exception as exc:
        return {"ok": False, "content": "", "error": str(exc)}
    return {"ok": True, "content": f"written {len(content)} chars to {p.name}", "error": ""}


# ── kb_write ──────────────────────────────────────────────────────────────────
def _kb_write(args: dict, caller: Any) -> dict:
    title = args.get("title") or args.get("name") or "未命名知识条目"
    content = args.get("content") or args.get("text") or ""
    if not content:
        return {"ok": False, "content": "", "error": "content required"}
    scope = args.get("scope") or "department"
    owner = caller.userId if caller else "system"
    dept = caller.deptId if caller else ""
    tenant = caller.tenantId if caller else ""
    try:
        doc_id = knowledge.add_document(title, content, scope, owner, dept, tenant)
    except Exception as exc:
        return {"ok": False, "content": "", "error": f"add_document_error:{exc}"}
    return {"ok": True, "content": f"已入库 {doc_id}（scope={scope}）", "error": ""}


# ── shell（默认禁用；高危，loop 转审批） ──────────────────────────────────────
def _shell(args: dict, caller: Any) -> dict:
    cmd = args.get("command") or ""
    sb = sandbox.check("shell", {"command": cmd}, caller)
    if not sb["allowed"]:
        return {"ok": False, "content": "", "error": sb["reason"]}
    import shlex
    try:
        proc = subprocess.run(
            shlex.split(cmd), capture_output=True, text=True, timeout=30,
            cwd=str(Path(sandbox.load_rules().get("workspace_root"))),
            check=False,
        )
    except Exception as exc:
        return {"ok": False, "content": "", "error": str(exc)}
    out = (proc.stdout or "") + (proc.stderr or "")
    return {"ok": proc.returncode == 0, "content": out[:8000], "error": ""}


# ── 注册（模块导入即生效） ───────────────────────────────────────────────────
registry.register("rag_retrieve", "检索企业授权知识库（RAG），返回标题与摘要",
                  {"query": "检索词", "top_k": "返回条数(默认5)"}, _rag)
registry.register("web_search", "公网检索，与内部知识库严格隔离，返回标题/链接/摘要",
                  {"query": "检索词", "fetch_top": "抓取前N篇正文(默认0)"}, _web)
registry.register("file_read", "读取工作区（workspace_root）内的文件",
                  {"path": "相对工作区根的路径"}, _file_read)
registry.register("file_write", "写入工作区（workspace_root）内的文件",
                  {"path": "相对工作区根的路径", "content": "文件内容"}, _file_write)
registry.register("kb_write", "写入一条企业知识条目（scope 控制可见范围）",
                  {"title": "标题", "content": "正文", "scope": "personal|owner|department|global"},
                  _kb_write)
registry.register("shell", "执行白名单内的系统命令（高危，默认禁用，需审批）",
                  {"command": "命令"}, _shell, risk="high")
