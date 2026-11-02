"""智能体运行时（决策：LangGraph 为主框架）。

M4 编排升级：多轮会话记忆接入 —— 图结构 retrieve -> generate，
history 由 session_store 注入：
  - 检索 query 改写（上一轮用户问题并入本轮查询，指代消解确定性近似）
  - 生成上下文注入（最近 N 轮对话进 generate，答案可引用历史）
LangGraph 为可选依赖；未安装时退化为确定性 stub，保证可运行。
"""
from typing import Any, Dict, List, Optional

from app.models.caller import CallerContext
from app.services import knowledge, session_store


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
        answer = (f"[第{turn_no}轮|基于 {len(state.get('hits', []))} 条授权片段作答] "
                  f"{state['input']} → {ctx or '暂无授权知识'}")
        if mem:
            answer += f" ‖记忆上下文: {mem.replace(chr(10), ' / ')}"
        return {"answer": answer}

    g = StateGraph(dict)
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
                "tool_calls": [{"tool": "rag_retrieve", "authorized": True},
                               {"tool": "session_memory", "authorized": True}]}
    except RuntimeError:
        # 骨架 stub：不依赖 LangGraph 也能演示「记忆→检索→生成」链路
        query = session_store.rewritten_query(user_input, history)
        hits, filtered = knowledge.retrieve(query, caller, top_k=6)
        ctx = "；".join(h["title"] for h in hits)
        turn_no = 1 + sum(1 for t in history if t["role"] == "user")
        answer = f"[stub|第{turn_no}轮] {user_input} → {ctx or '暂无授权知识'}"
        if query != user_input:
            answer += f" ‖query改写: {query}"
        return {"answer": answer,
                "citations": [{"doc_id": h["doc_id"], "title": h["title"], "scope": h["scope"]} for h in hits],
                "query_rewritten": query != user_input,
                "turn_no": turn_no,
                "tool_calls": [{"tool": "rag_retrieve", "authorized": True},
                               {"tool": "session_memory", "authorized": True}]}
