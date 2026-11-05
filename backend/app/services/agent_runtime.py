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
    file_ctx: str        # M16：附件解析出的结构化上下文（表格画像 + 样本）


SYSTEM_PROMPT = "企业AI智能体中台知识助手，只基于授权片段作答，不编造。"

# M15：需要先检索再执行的适配器类型（技能包标注为 RAG 类才注入企业知识段落）
RETRIEVAL_ADAPTERS = {"llm_retrieval", "ml_llm_retrieval"}

# 单篇文档在证据块里最多占多少字；多篇命中时不至于把上下文撑爆
EVIDENCE_PER_DOC_CHARS = 600


def _call_llm(prompt: str, system_prompt: str | None = None) -> Dict[str, Any]:
    """调模型网关并带回用量（M7：token/延迟原样上抛，由路由层落账）。

    M15：system_prompt 由调用方按 agent 传入（技能包提示词），缺省用平台默认助手。
    """
    out = model_gateway.chat_sync_ex(settings.LLM_DEFAULT_MODEL, [
        {"role": "system", "content": system_prompt or SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ])
    return {
        "text": out["text"],
        "real": out["real"],
        "model_id": out["model_id"],
        "usage": out["usage"],
        "latency_ms": out["latency_ms"],
        # M17：真模型失败的原因要一路透到响应，不能被 stub 文本掩盖
        "error": out.get("error") or "",
        "prompt_text": prompt,          # 无 usage 时供估算兜底
        "completion_text": out["text"],
    }


def compose_evidence(hits: List[Dict[str, Any]]) -> str:
    """把检索命中拼成**带正文、带编号**的证据块。

    M25：以前只拼 `h["title"]`，snippet 根本没进 prompt。后果是
    「逐字引用并标出处」「冲突并列」「查不到说没查到」这些写在技能提示词里的
    要求全都落不了地——模型只看得到几个文档标题，只能靠常识编。
    编号 [1][2] 让模型有稳定的引用锚点，也让验收脚本能核对引用是否对得上片段。
    """
    if not hits:
        return ("（本次检索没有任何可见的授权片段。按规则明确回答「未查到」，"
                "不得用常识、行业惯例或示例补一段像答案的话。）")
    lines = []
    for i, h in enumerate(hits, 1):
        title = h.get("title") or "(无标题)"
        # M25：优先用同 parent 的多句证据（knowledge.retrieve 附的 snippets），
        # 兼容只有单句 snippet 的老调用方/测试夹具。
        texts = [t.strip() for t in (h.get("snippets") or []) if (t or "").strip()]
        if not texts:
            texts = [(h.get("snippet") or "").strip()]
        body = " / ".join(t for t in texts if t)
        if len(body) > EVIDENCE_PER_DOC_CHARS:
            body = body[:EVIDENCE_PER_DOC_CHARS] + "…"
        lines.append(f"[{i}] 《{title}》：{body or '（片段正文为空）'}")
    return "\n".join(lines)


def _compose_prompt(user_input: str, ctx: str, mem: str, skill: bool,
                    file_ctx: str = "", grounded: bool = False) -> str:
    """技能包（skill=True）走「按流程执行」口径，默认助手走「仅据片段作答」口径。

    M16：file_ctx 是附件解析出的表格上下文。放在任务输入之前，并显式允许模型
    引用附件里的数字——但只允许引用附件里真实存在的，避免它"看着表头补数据"。
    M25：grounded=True 表示这条技能挂了 llm_retrieval，检索片段是**依据**而不是
    「可选参考」，且零命中时必须能给模型一个明确的空结果信号。
    """
    if skill:
        parts = []
        if file_ctx:
            parts.append(file_ctx)
        if grounded:
            parts.append(f"授权检索依据（引用时逐条标注 [编号]）：\n{ctx}")
        elif ctx:
            parts.append(f"可参考的企业授权片段（仅在技能包输入契约需要时使用）：{ctx}")
        if mem:
            parts.append(f"对话记忆：{mem.replace(chr(10), ' / ')}")
        parts.append(f"本次任务输入：\n{user_input}")
        if grounded:
            parts.append("执行要求：只依据上方授权检索依据作答，逐字引用并标 [编号]；"
                         "多份依据冲突时并列各方与生效日期、标注「待确认」，不自行选一个；"
                         "依据里没有答案就明确写「未查到相关规定」，不得用常识补充；"
                         "按技能包的产出契约用 Markdown 二级标题分节输出。")
        elif file_ctx:
            parts.append("执行要求：按技能包的产出契约输出；凡引用数据必须来自上方附件，"
                         "不得臆造行或列。结论中要落成可导出的表格（markdown 表格或 json 数组），"
                         "表头与附件列名保持一致。")
        else:
            parts.append("请严格按技能包规定的工作流程与产出契约执行；缺失输入列清单，不得臆造。")
        return "\n\n".join(parts)
    return (f"你是企业知识助手，仅基于授权片段与用户附件回答。\n"
            + (f"{file_ctx}\n" if file_ctx else "")
            + f"授权片段：\n{ctx or '（无）'}\n"
            + (f"对话记忆：{mem.replace(chr(10), ' / ')}\n" if mem else "")
            + f"用户问题：{user_input}")


def build_graph(system_prompt: str | None = None, use_retrieval: bool = True,
                grounded: bool = False):
    """构造 LangGraph 状态图（retrieve -> generate，含会话记忆状态）。

    M15：system_prompt / use_retrieval 由图构造参数注入，不再全局写死；
    技能包默认不检索（输入是用户给的结构化数据），RAG 类适配器才检索。
    """
    try:
        from langgraph.graph import StateGraph, END  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("LangGraph 未安装：pip install langgraph") from exc

    skill = bool(system_prompt) and system_prompt != SYSTEM_PROMPT

    def retrieve(state: Dict[str, Any]) -> Dict[str, Any]:
        if not use_retrieval:
            return {"hits": [], "filtered_out": 0, "query_rewritten": False}
        # 会话记忆：query 改写（上一轮用户问题并入，指代消解）
        history = state.get("history") or []
        query = session_store.rewritten_query(state["input"], history)
        hits, filtered = knowledge.retrieve(query, state["caller"], top_k=6)
        return {"hits": hits, "filtered_out": filtered, "query_rewritten": query != state["input"]}

    def generate(state: Dict[str, Any]) -> Dict[str, Any]:
        # 仅基于可见片段生成，越权内容已被过滤点拦截，不进上下文
        # 只有真检索过才拼证据块；不检索的技能不该收到「本次没有片段」这种干扰信息
        ctx = compose_evidence(state.get("hits", [])) if use_retrieval else ""
        turn_no = 1 + sum(1 for t in state.get("history") or [] if t["role"] == "user")
        # 会话记忆：最近 N 轮对话摘要进生成上下文
        mem = session_store.build_context({"turns": state.get("history") or []}, max_turns=2)
        # M6：真实 LLM 生成（LiteLLM 探活通过时），否则确定性 stub
        prompt = _compose_prompt(state["input"], ctx, mem, skill,
                                 state.get("file_ctx") or "", grounded=grounded)
        llm = _call_llm(prompt, system_prompt)
        answer = llm["text"]
        # 轮次标记只在平台助手的多轮对话上加（证明记忆生效）；
        # 技能包产出是直接交付物的正文，带调试前缀会污染 Word/PPT/PDF 首页。
        if not answer.startswith("[stub") and not skill:
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
              history: Optional[List[Dict[str, Any]]] = None,
              system_prompt: Optional[str] = None,
              adapter_type: str = "",
              file_context: str = "") -> Dict[str, Any]:
    """运行智能体：多轮编排（记忆可选）。history 为该会话既有轮次。

    M15：system_prompt 为该智能体（技能包）的执行提示词；adapter_type 决定是否检索。
    未传 system_prompt 时行为与 M0–M14 完全一致（平台默认知识助手）。
    M16：file_context 为附件解析后的结构化上下文（tabular.to_prompt_context 产出）。
    """
    history = history or []
    use_retrieval = adapter_type in RETRIEVAL_ADAPTERS if adapter_type else True
    skill = bool(system_prompt) and system_prompt != SYSTEM_PROMPT
    # grounded 只认「技能包明确挂了 RAG 适配器」这一种情况；
    # 默认助手或没标 adapter_type 的旧业务包仍走原来的「片段仅作参考」口径，
    # 避免为了查证类技能改提示词，把 115 个业务包的产出格式一起带歪。
    grounded = skill and adapter_type in RETRIEVAL_ADAPTERS
    try:
        app = build_graph(system_prompt, use_retrieval, grounded=grounded)
        result = app.invoke({"input": user_input, "caller": caller, "history": history,
                             "file_ctx": file_context})
        citations = [{"doc_id": h["doc_id"], "title": h["title"], "scope": h["scope"]}
                     for h in result.get("hits", [])]
        return {"answer": result["answer"], "citations": citations,
                "query_rewritten": result.get("query_rewritten", False),
                "turn_no": 1 + sum(1 for t in history if t["role"] == "user"),
                "usage": result.get("usage") or {},
                "tool_calls": ([{"tool": "rag_retrieve", "authorized": True}] if use_retrieval else [])
                              + [{"tool": "session_memory", "authorized": True}],
                "skill_applied": skill}
    except RuntimeError:
        # 骨架 stub：不依赖 LangGraph 也能演示「记忆→检索→生成」链路（LLM 接入见 model_gateway）
        query = session_store.rewritten_query(user_input, history) if use_retrieval else user_input
        hits, filtered = (knowledge.retrieve(query, caller, top_k=6) if use_retrieval else ([], 0))
        ctx = compose_evidence(hits) if use_retrieval else ""
        turn_no = 1 + sum(1 for t in history if t["role"] == "user")
        mem = session_store.build_context({"turns": history}, max_turns=2)
        prompt = _compose_prompt(user_input, ctx, mem, skill, file_context,
                                 grounded=grounded)
        llm = _call_llm(prompt, system_prompt)
        answer = llm["text"]
        if not answer.startswith("[stub") and not skill:
            answer = f"[stub|第{turn_no}轮|LLM生成] {answer}"
        elif query != user_input:
            answer += f" ‖query改写: {query}"
        return {"answer": answer,
                "citations": [{"doc_id": h["doc_id"], "title": h["title"], "scope": h["scope"]} for h in hits],
                "query_rewritten": query != user_input,
                "turn_no": turn_no,
                "usage": llm,
                "tool_calls": ([{"tool": "rag_retrieve", "authorized": True}] if use_retrieval else [])
                              + [{"tool": "session_memory", "authorized": True}],
                "skill_applied": skill}
