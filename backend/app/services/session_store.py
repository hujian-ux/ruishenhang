"""M4 会话记忆服务（第9章会话上下文 / 第22章智能体运行）。

内存版 SessionStore：按 session_id 存多轮对话，owner 归属校验（会话仅本人可见），
上下文窗口封顶（防止无限增长）。生产替换为 Redis/Postgres 持久层，接口不变。
"""
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from app.core.config import settings
from app.models.caller import CallerContext
from app.services import pg_store

# P1-12：窗口口径统一从 settings 动态解析（见下方 resolve_*）。这两个名字保留为
# 兼容别名——既有测试与外部模块会引用 MAX_SESSION_TURNS / MAX_CONTEXT_TURNS。
# 它们是模块加载时的快照，运行时逻辑一律走 resolve_*，不要直接读常量。
MAX_CONTEXT_TURNS = int(getattr(settings, "CTX_WINDOW_TURNS", 6))
MAX_SESSION_TURNS = int(getattr(settings, "CTX_SESSION_TURNS", 40))

_SESSIONS: Dict[str, Dict[str, Any]] = {}


def _cfg(name: str, default: int) -> int:
    """读运行时配置；坏值回落默认，不因一条环境变量把整条生成链路打挂。"""
    try:
        return int(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default


def _bounds() -> tuple:
    lo = max(1, _cfg("CTX_MIN_TURNS", 1))
    return lo, max(lo, _cfg("CTX_MAX_TURNS", 40))


def _clip(override: Optional[int], default: int) -> int:
    """请求级覆盖 + 上下限夹取；非数字覆盖按默认值处理。"""
    lo, hi = _bounds()
    try:
        raw = default if override is None else int(override)
    except (TypeError, ValueError):
        raw = default
    return max(lo, min(hi, raw))


def resolve_session_turns() -> int:
    """会话存储上限：滑动窗口最多保留多少轮（动态读 settings）。"""
    return max(1, _cfg("CTX_SESSION_TURNS", 40))


def resolve_window_turns(override: Optional[int] = None) -> int:
    """会话详情 / 无覆盖时的近窗轮数（默认 6，保持既有行为）。"""
    return _clip(override, _cfg("CTX_WINDOW_TURNS", 6))


def resolve_runtime_turns(override: Optional[int] = None) -> int:
    """生成器 5 条路径的近窗轮数（默认 2，保持既有行为）。"""
    return _clip(override, _cfg("CTX_RUNTIME_TURNS", 2))


def window_config() -> Dict[str, Any]:
    """当前窗口配置快照（运营台 / 前端展示用，只读）。"""
    lo, hi = _bounds()
    return {
        "session_turns": resolve_session_turns(),
        "window_turns": resolve_window_turns(),
        "runtime_turns": resolve_runtime_turns(),
        "min_turns": lo,
        "max_turns": hi,
        "compress_enabled": bool(getattr(settings, "CTX_COMPRESS_ENABLED", False)),
        "compress_provider": str(getattr(settings, "CTX_COMPRESS_PROVIDER", "extractive")),
        "compress_max_chars": _cfg("CTX_COMPRESS_MAX_CHARS", 1200),
        "compress_turn_chars": _cfg("CTX_COMPRESS_TURN_CHARS", 160),
        "compress_max_tokens": _cfg("CTX_COMPRESS_MAX_TOKENS", 300),
        "providers": sorted(_COMPRESSORS),
    }


def load_from_pg() -> int:
    """启动时从 PG 载回会话（重启不丢记忆）；PG 不可达时为空操作。"""
    n = 0
    for payload in pg_store.load_all("agent_sessions"):
        try:
            _SESSIONS[payload["session_id"]] = payload
            n += 1
        except Exception:
            continue
    return n


class SessionAccessError(Exception):
    """会话归属校验失败：非本人访问。"""


def _persist(sess: Dict[str, Any]) -> None:
    """写穿 PG（离线/不可达时静默跳过，内存为主存）。"""
    pg_store.upsert("agent_sessions", sess["session_id"], sess)


def create_session(agent_id: str, caller: CallerContext, session_id: Optional[str] = None) -> Dict[str, Any]:
    sid = session_id or ("sess_" + uuid.uuid4().hex[:12])
    sess = {
        "session_id": sid,
        "agent_id": agent_id,
        "user_id": caller.userId,
        "dept_id": caller.deptId,
        "tenant_id": caller.tenantId,
        "turns": [],  # [{role: user|assistant, content, ts}]
        "created_at": time.time(),
    }
    _SESSIONS[sid] = sess
    _persist(sess)
    return sess


def get_session(session_id: str, caller: CallerContext) -> Dict[str, Any]:
    """取会话；归属校验：仅会话创建者本人可访问（第18章权限语义延伸到会话）。

    M43：非平台管理员额外要求租户一致，防止同 userId 跨租户访问会话。
    """
    sess = _SESSIONS.get(session_id)
    if not sess:
        raise KeyError(session_id)
    if sess["user_id"] != caller.userId:
        raise SessionAccessError(session_id)
    if (sess.get("tenant_id") and sess["tenant_id"] != caller.tenantId
            and caller.role != "platform_admin"):
        raise SessionAccessError(session_id)
    return sess


def get_or_create(agent_id: str, caller: CallerContext, session_id: Optional[str]) -> Dict[str, Any]:
    if session_id:
        sess = get_session(session_id, caller)  # 不存在或非本人都会抛错
        # 会话必须属于同一个智能体，防止跨智能体串记忆
        if sess["agent_id"] != agent_id:
            raise KeyError(session_id)
        return sess
    return create_session(agent_id, caller)


def append_turn(session_id: str, role: str, content: str) -> None:
    sess = _SESSIONS.get(session_id)
    if not sess:
        return
    sess["turns"].append({"role": role, "content": content, "ts": time.time()})
    cap = resolve_session_turns()
    if len(sess["turns"]) > cap:
        sess["turns"] = sess["turns"][-cap:]
    _persist(sess)


def attach_meta(session_id: str, role: str, extra: Dict[str, Any]) -> None:
    """给最后一条指定角色的轮次挂附加信息（目前用于产出物）。

    为什么不在 append_turn 时一起写：产出物是生成之后才有的，
    而助手轮必须先落账（落账=这轮对话已经发生，不能因为渲染失败就丢记忆）。
    """
    sess = _SESSIONS.get(session_id)
    if not sess:
        return
    for t in reversed(sess["turns"]):
        if t["role"] == role:
            t.update(extra)
            _persist(sess)
            return


def history_of(session_id: str) -> List[Dict[str, Any]]:
    sess = _SESSIONS.get(session_id)
    return list(sess["turns"]) if sess else []


# ─────────────────────────────────────────────────────────────────────────────
# P1-12 上下文压缩：被窗口截掉的早期轮次压成摘要，而不是彻底丢弃。
# 可插拔：register_compressor / set_compressor / reset_compressor；
# 内置 extractive（确定性、零外部依赖）与 llm（无真模型绝不伪造）。
# ─────────────────────────────────────────────────────────────────────────────
_COMPRESSORS: Dict[str, Callable[[List[Dict[str, Any]]], Dict[str, Any]]] = {}
_COMPRESSOR_OVERRIDE = ""


def register_compressor(name: str,
                        fn: Callable[[List[Dict[str, Any]]], Dict[str, Any]]) -> None:
    """注册压缩通道；fn(turns) -> {ok, text, provider, real, error?, usage?}。"""
    _COMPRESSORS[name.strip().lower()] = fn


def set_compressor(name: str) -> None:
    """进程内切换通道（不改 settings，测试可安全 monkeypatch）。"""
    global _COMPRESSOR_OVERRIDE
    key = name.strip().lower()
    if key not in _COMPRESSORS:
        raise KeyError(key)
    _COMPRESSOR_OVERRIDE = key


def reset_compressor() -> None:
    global _COMPRESSOR_OVERRIDE
    _COMPRESSOR_OVERRIDE = ""


def _fmt_turn(t: Dict[str, Any]) -> str:
    """与改动前 build_context 完全一致的行格式。

    不要在这里做空白归一化——否则压缩开关前后的输出会有字节差异，
    「默认零行为变更」这条红线就破了。
    """
    return f"{'用户' if t.get('role') == 'user' else '助手'}: {t.get('content') or ''}"


def _norm(t: Dict[str, Any]) -> str:
    return " ".join(str(t.get("content") or "").split())


def _extractive(turns: List[Dict[str, Any]]) -> Dict[str, Any]:
    """确定性抽取式摘要：不调模型、不编造，只从原文摘录、去重、截断。

    压缩比来自两条规则：助手轮只保留首个完整句（结论），用户轮按字符上限截断；
    完全相同的内容去重；总长再受 CTX_COMPRESS_MAX_CHARS 封顶。
    """
    per_turn = max(40, _cfg("CTX_COMPRESS_TURN_CHARS", 160))
    limit = max(120, _cfg("CTX_COMPRESS_MAX_CHARS", 1200))
    lines: List[str] = []
    seen = set()
    for t in turns:
        raw = _norm(t)
        if not raw:
            continue
        if t.get("role") != "user":
            head = raw[:per_turn]
            for sep in ("。", "！", "？"):
                idx = head.find(sep)
                if 0 <= idx < len(head):
                    head = head[:idx + 1]
                    break
            raw = head.strip()
        else:
            raw = (raw[:per_turn] + "…") if len(raw) > per_turn else raw
        if not raw or raw in seen:
            continue
        seen.add(raw)
        role = "用户" if t.get("role") == "user" else "助手"
        lines.append(f"- {role}：{raw}")
    text = "\n".join(lines)
    truncated = len(text) > limit
    if truncated:
        text = text[:limit].rstrip() + "…"
    return {
        "ok": True, "text": text, "provider": "extractive", "real": False,
        "lines": len(lines), "truncated": truncated, "fallback": False, "error": "",
    }


def _llm(turns: List[Dict[str, Any]]) -> Dict[str, Any]:
    """模型摘要通道：无真模型 / 异常 / 空文本一律 ok=False，绝不把 stub 当摘要。"""
    try:
        from app.services import model_gateway
        if not model_gateway.llm_available():
            return {"ok": False, "text": "", "provider": "llm", "real": False,
                    "error": "llm_unavailable"}
        src = "\n".join(_fmt_turn(t) for t in turns)
        src = src[:max(500, _cfg("CTX_COMPRESS_MAX_CHARS", 1200) * 4)]
        out = model_gateway.chat_sync_ex(None, [
            {"role": "system", "content":
                "你是会话记忆压缩器。只依据给定对话，用中文输出不超过 200 字的要点摘要，"
                "按时间顺序保留用户诉求与已给出的结论。不得补充、推断或编造任何原文没有的信息；"
                "原文信息不足就只写能确认的部分。"},
            {"role": "user", "content": src},
        ], max_tokens=max(64, _cfg("CTX_COMPRESS_MAX_TOKENS", 300)))
        text = " ".join(str(out.get("text") or "").split())
        if not out.get("real") or out.get("error") or not text:
            return {"ok": False, "text": "", "provider": "llm", "real": False,
                    "error": str(out.get("error") or "no real model / empty summary")}
        return {"ok": True, "text": text[:_cfg("CTX_COMPRESS_MAX_CHARS", 1200)],
                "provider": "llm", "real": True, "lines": 0, "truncated": False,
                "fallback": False, "error": "", "usage": out.get("usage") or {},
                "model_id": out.get("model_id") or ""}
    except Exception as exc:
        return {"ok": False, "text": "", "provider": "llm", "real": False,
                "error": f"{type(exc).__name__}: {exc}"}


register_compressor("extractive", _extractive)
register_compressor("llm", _llm)


def _disabled_summary(provider: str = "") -> Dict[str, Any]:
    return {"enabled": False, "text": "", "turns": 0, "provider": provider,
            "real": False, "fallback": False, "error": "", "lines": 0,
            "truncated": False}


def compress_history(turns: List[Dict[str, Any]], *, force: bool = False,
                     provider: str = "") -> Dict[str, Any]:
    """压缩被窗口截掉的早期轮次。

    未开启压缩 → enabled=False / text=""（调用方输出与改动前逐字节一致）。
    llm 通道失败 → 回落 extractive 并置 fallback=True + error 带出，
    绝不返回 stub 文本或空壳摘要冒充结论（M55 缺料必拒）。
    """
    if not (force or getattr(settings, "CTX_COMPRESS_ENABLED", False)):
        return _disabled_summary()
    key = (provider or _COMPRESSOR_OVERRIDE
           or str(getattr(settings, "CTX_COMPRESS_PROVIDER", "extractive"))
           or "extractive").strip().lower()
    if not turns:
        return {**_disabled_summary(key), "enabled": True}
    fn = _COMPRESSORS.get(key, _COMPRESSORS["extractive"])
    res = dict(fn(list(turns)))
    res["turns"] = len(turns)
    if not res.get("ok"):
        ex = dict(_COMPRESSORS["extractive"](list(turns)))
        ex.update({"turns": len(turns), "fallback": True,
                   "requested_provider": key,
                   "error": res.get("error") or "compressor failed"})
        return {"enabled": True, **ex}
    res["enabled"] = True
    return res


def _summary_block(summary: Dict[str, Any]) -> str:
    """把摘要包成注入文本；明确标注来源与不完整性，防模型把摘要当一手事实。"""
    if not summary.get("enabled") or not (summary.get("text") or "").strip():
        return ""
    kind = "模型摘要" if summary.get("real") else "抽取摘要"
    if summary.get("fallback"):
        kind += "（模型通道不可用，已回落）"
    n = int(summary.get("turns") or 0)
    return (f"【历史摘要｜{n}轮｜{kind}｜仅作线索，可能不完整，"
            f"以下近窗原文为准】\n{summary['text']}")


def build_context_detail(session: Dict[str, Any], *,
                         context_window: Optional[int] = None,
                         compress: Optional[bool] = None,
                         runtime: bool = False) -> Dict[str, Any]:
    """结构化预览：近窗原文、历史摘要、被压缩轮数、字符/token 估算。只读无副作用。

    runtime=True 时用生成器口径（CTX_RUNTIME_TURNS=2），否则用会话详情口径（6）。
    compress: None=按配置 / True=强制开启 / False=强制关闭。
    """
    turns = list(session.get("turns") or [])
    n = (resolve_runtime_turns(context_window) if runtime
         else resolve_window_turns(context_window))
    recent = turns[-n:] if n > 0 else []
    dropped = turns[:-n] if n > 0 and len(turns) > n else []
    if compress is False:
        summary = _disabled_summary()
    else:
        summary = compress_history(dropped, force=(compress is True))
    head = _summary_block(summary)
    body = "\n".join(_fmt_turn(t) for t in recent)
    if head and body:
        context = f"{head}\n【近窗对话｜最近{len(recent)}轮】\n{body}"
    else:
        context = head or body
    chars = len(context)
    return {
        "window_turns": n,
        "total_turns": len(turns),
        "kept_turns": len(recent),
        "dropped_turns": len(dropped),
        "recent_text": body,
        "summary": summary,
        "summary_text": head,
        "context": context,
        "context_chars": chars,
        # 中文粗估：非计量口径，仅供前端判断窗口是否过宽
        "estimated_tokens": chars // 2 + 1,
        "config": window_config(),
        "runtime": runtime,
    }


def build_context(session: Dict[str, Any], max_turns: Optional[int] = None, *,
                  context_window: Optional[int] = None,
                  compress: Optional[bool] = None) -> str:
    """把最近 N 轮拼成生成器可用的上下文文本（滑动窗口 + 可选历史摘要）。

    P1-12：默认参数不再是定义时绑定的常量，而是运行时解析配置（改配置即生效）；
    压缩关闭（默认）时输出与改动前逐字节一致。唯一真源是 build_context_detail。
    """
    win = context_window if context_window is not None else max_turns
    return build_context_detail(session, context_window=win, compress=compress)["context"]


def context_observation(session: Dict[str, Any],
                        override: Optional[int] = None) -> Dict[str, Any]:
    """P1-12 轻量窗口观测：只算轮数，不触发压缩通道。

    生成路径已经构造过一次上下文，这里再跑一遍压缩器会多调一次模型（成本 + 延迟），
    所以响应里只给确定性的窗口数字；要看完整摘要用 /agent/context/preview。
    """
    win = resolve_runtime_turns(override)
    total = len(session.get("turns") or [])
    cfg = window_config()
    return {
        "window_turns": win,
        "total_turns": total,
        "kept_turns": min(total, win),
        "dropped_turns": max(0, total - win),
        "compress_enabled": cfg["compress_enabled"],
        "compress_provider": cfg["compress_provider"] if cfg["compress_enabled"] else "",
        "context_window_override": override,
    }


def rewritten_query(user_input: str, history: List[Dict[str, Any]], max_chars: int = 40) -> str:
    """检索 query 改写：把上一轮用户问题并入本轮查询（指代消解的确定性近似）。

    例：上一轮"五一活动方案有哪些" → 本轮"预算多少" → 改写为"预算多少 五一活动方案有哪些"。
    仅并入用户轮内容（不含助手回答），截断防超长。
    """
    last_user = next((t["content"] for t in reversed(history) if t["role"] == "user"), "")
    if not last_user or last_user == user_input:
        return user_input
    return f"{user_input} {last_user[:max_chars]}".strip()


def session_title(sess: Dict[str, Any], max_chars: int = 26) -> str:
    """会话标题：取首条用户输入。

    对外界面要在侧栏列历史对话，标题必须来自用户自己的话，
    不能显示 `sess_9f3a...` 这种内部 ID。
    """
    for t in sess["turns"]:
        if t["role"] == "user":
            text = " ".join((t["content"] or "").split())
            return (text[:max_chars] + "…") if len(text) > max_chars else (text or "新对话")
    return "新对话"


def session_summary(sess: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "session_id": sess["session_id"],
        "agent_id": sess["agent_id"],
        "user_id": sess["user_id"],
        "title": session_title(sess),
        "turn_count": len(sess["turns"]),
        "created_at": sess["created_at"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# M7 会话管理（列表 / 删除）：默认只看自己的会话，平台管理员可看全量
# ─────────────────────────────────────────────────────────────────────────────
def list_sessions(caller: CallerContext, include_all: bool = False,
                  limit: int = 20) -> List[Dict[str, Any]]:
    # M43：非平台管理员额外要求租户一致，防止同 userId 跨租户看到他人会话。
    if include_all or caller.role == "platform_admin":
        items = list(_SESSIONS.values())
    else:
        items = [s for s in _SESSIONS.values()
                 if s["user_id"] == caller.userId and s.get("tenant_id") == caller.tenantId]
    items.sort(key=lambda s: (s["turns"][-1]["ts"] if s["turns"] else s["created_at"]), reverse=True)
    out = []
    for s in items[:limit]:
        last = s["turns"][-1] if s["turns"] else None
        out.append({
            **session_summary(s),
            "dept_id": s["dept_id"],
            "updated_at": last["ts"] if last else s["created_at"],
            "last_role": last["role"] if last else None,
            "last_message": (last["content"][:80] if last else ""),
        })
    return out


def delete_session(session_id: str, caller: CallerContext) -> bool:
    """删除会话（仅创建者本人；PG 副本一并删除，避免重启后复活）。"""
    get_session(session_id, caller)  # 不存在抛 KeyError / 非本人抛 SessionAccessError
    _SESSIONS.pop(session_id, None)
    pg_store.delete_row("agent_sessions", "session_id", session_id)
    return True
