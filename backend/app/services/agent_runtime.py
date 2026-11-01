"""智能体运行时（决策：LangGraph 为主框架）。

脚手架阶段提供图结构骨架（StateGraph：retrieve -> generate），
真实节点逻辑在 P0 第36章 RAG 与第37章评测接入后填充。
LangGraph 为可选依赖；未安装时退化为确定性 stub，保证可运行。
"""
from typing import Any, Dict

from app.models.caller import CallerContext
from app.services import knowledge


def build_graph():
    """构造 LangGraph 状态图（骨架）。"""
    try:
        from langgraph.graph import StateGraph, END  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("LangGraph 未安装：pip install langgraph") from exc

    def retrieve(state: Dict[str, Any]) -> Dict[str, Any]:
        hits, filtered = knowledge.retrieve(state["input"], state["caller"], top_k=6)
        return {"hits": hits, "filtered_out": filtered}

    def generate(state: Dict[str, Any]) -> Dict[str, Any]:
        # 仅基于可见片段生成，越权内容已被过滤点拦截，不进上下文
        ctx = "；".join(h["title"] for h in state.get("hits", []))
        answer = f"[基于 {len(state.get('hits', []))} 条授权片段作答] {state['input']} → {ctx or '暂无授权知识'}"
        return {"answer": answer}

    g = StateGraph(dict)
    g.add_node("retrieve", retrieve)
    g.add_node("generate", generate)
    g.add_edge("retrieve", "generate")
    g.add_edge("generate", END)
    g.set_entry_point("retrieve")
    return g.compile()


def run_agent(agent_id: str, user_input: str, caller: CallerContext) -> Dict[str, Any]:
    """运行智能体：优先 LangGraph 编译图，否则确定性 stub。"""
    try:
        app = build_graph()
        result = app.invoke({"input": user_input, "caller": caller})
        citations = [{"doc_id": h["doc_id"], "title": h["title"], "scope": h["scope"]}
                     for h in result.get("hits", [])]
        return {"answer": result["answer"], "citations": citations,
                "tool_calls": [{"tool": "rag_retrieve", "authorized": True}]}
    except RuntimeError:
        # 骨架 stub：不依赖 LangGraph 也能演示「检索→生成」链路
        hits, filtered = knowledge.retrieve(user_input, caller, top_k=6)
        ctx = "；".join(h["title"] for h in hits)
        return {"answer": f"[stub] {user_input} → {ctx or '暂无授权知识'}",
                "citations": [{"doc_id": h["doc_id"], "title": h["title"], "scope": h["scope"]} for h in hits],
                "tool_calls": [{"tool": "rag_retrieve", "authorized": True}]}
