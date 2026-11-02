"""智能体运行时（决策：LangGraph 为主框架）。

M4 编排升级：多轮会话记忆接入 —— 图结构 retrieve -> generate，
history 由 session_store 注入：
  - 检索 query 改写（上一轮用户问题并入本轮查询，指代消解确定性近似）
  - 生成上下文注入（最近 N 轮对话进 generate，答案可引用历史）
LangGraph 为可选依赖；未安装时退化为确定性 stub，保证可运行。
"""
from typing import Any, Dict, List, Optional, TypedDict

from app.models.caller import CallerContext
from app.services import knowledge, model_gateway, session_store
from app.core.config import settings


class _AgentState(TypedDict, total=False):
    """显式状态 schema：新版 LangGraph 只保留声明过的键（dict schema 会丢键）。"""
    input: str
    caller: Any
    history: list
    hits: list
    filtered_out: int
    query_rewritten: bool
    answer: str
    usage: dict


SYSTEM_PROMPT = "企业AI智能体中台知识助手，只基于授权片段作答，不编造。"


def _call_llm(prompt: str) -> Dict[str, Any]:
    """调模型网关并带回用量（M7：token/延迟原样上抛，由路由层落账）。"""
    out = model_gateway.chat_sync_ex(settings.LLM_DEFAULT_MODEL, [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ])
    return {
        "text": out["text"],
        "real": out["real"],
        "model_id": out["model_id"],
        "usage": out["usage"],
        "latency_ms": out["latency_ms"],
        "prompt_text": prompt,          # 无 usage 时供估算兜底
        "completion_text": out["text"],
    }


def build_graph():
    """构造 LangGraph 状态图（retrieve -> generate，含会话记忆状态）。"""
    try:
        from langgraph.graph import StateGraph, END  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("LangGraph 未安装：pip install langgraph") from exc

    def retrieve(state: Dict[str, Any]) -> Dict[str, Any]:
        # 会话记忆：query 改写（上一轮用户问题并入，指代消解）
        history = state.get("history") or []
        query = session_store.rewritten_query(state["input"], history)
        hits, filtered = knowledge.retrieve(query, state["caller"], top_k=6)
        return {"hits": hits, "filtered_out": filtered, "query_rewritten": query != state["input"]}

    def generate(state: Dict[str, Any]) -> Dict[str, Any]:
        # 仅基于可见片段生成，越权内容已被过滤点拦截，不进上下文
        ctx = "；".join(h["title"] for h in state.get("hits", []))
        turn_no = 1 + sum(1 for t in state.get("history") or [] if t["role"] == "user")
        # 会话记忆：最近 N 轮对话摘要进生成上下文
        mem = session_store.build_context({"turns": state.get("history") or []}, max_turns=2)
        # M6：真实 LLM 生成（LiteLLM 探活通过时），否则确定性 stub
        prompt = (f"你是企业知识助手，仅基于授权片段回答。\n"
                  f"授权片段：{ctx or '暂无'}\n"
                  + (f"对话记忆：{mem.replace(chr(10), ' / ')}\n" if mem else "")
                  + f"用户问题：{state['input']}")
        llm = _call_llm(prompt)
        answer = llm["text"]
        if not answer.startswith("[stub"):
            answer = f"[第{turn_no}轮|LLM生成] {answer}"
        return {"answer": answer, "usage": llm}

    g = StateGraph(_AgentState)
    g.add_node("retrieve", retrieve)
    g.add_node("generate", generate)
    g.add_edge("retrieve", "generate")
    g.add_edge("generate", END)
    g.set_entry_point("retrieve")
    return g.compile()


def run_agent(agent_id: str, user_input: str, caller: CallerContext,
              history: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """运行智能体：多轮编排（记忆可选）。history 为该会话既有轮次。"""
    history = history or []
    try:
        app = build_graph()
        result = app.invoke({"input": user_input, "caller": caller, "history": history})
        citations = [{"doc_id": h["doc_id"], "title": h["title"], "scope": h["scope"]}
                     for h in result.get("hits", [])]
        return {"answer": result["answer"], "citations": citations,
                "query_rewritten": result.get("query_rewritten", False),
                "turn_no": 1 + sum(1 for t in history if t["role"] == "user"),
                "usage": result.get("usage") or {},
                "tool_calls": [{"tool": "rag_retrieve", "authorized": True},
                               {"tool": "session_memory", "authorized": True}]}
    except RuntimeError:
        # 骨架 stub：不依赖 LangGraph 也能演示「记忆→检索→生成」链路（LLM 接入见 model_gateway）
        query = session_store.rewritten_query(user_input, history)
        hits, filtered = knowledge.retrieve(query, caller, top_k=6)
        ctx = "；".join(h["title"] for h in hits)
        turn_no = 1 + sum(1 for t in history if t["role"] == "user")
        mem = session_store.build_context({"turns": history}, max_turns=2)
        prompt = (f"你是企业知识助手，仅基于授权片段回答。\n"
                  f"授权片段：{ctx or '暂无'}\n"
                  + (f"对话记忆：{mem.replace(chr(10), ' / ')}\n" if mem else "")
                  + f"用户问题：{user_input}")
        llm = _call_llm(prompt)
        answer = llm["text"]
        if not answer.startswith("[stub"):
            answer = f"[stub|第{turn_no}轮|LLM生成] {answer}"
        elif query != user_input:
            answer += f" ‖query改写: {query}"
        return {"answer": answer,
                "citations": [{"doc_id": h["doc_id"], "title": h["title"], "scope": h["scope"]} for h in hits],
                "query_rewritten": query != user_input,
                "turn_no": turn_no,
                "usage": llm,
                "tool_calls": [{"tool": "rag_retrieve", "authorized": True},
                               {"tool": "session_memory", "authorized": True}]}
