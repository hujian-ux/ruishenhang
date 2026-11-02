"""M4 会话记忆服务（第9章会话上下文 / 第22章智能体运行）。

内存版 SessionStore：按 session_id 存多轮对话，owner 归属校验（会话仅本人可见），
上下文窗口封顶（防止无限增长）。生产替换为 Redis/Postgres 持久层，接口不变。
"""
import time
import uuid
from typing import Any, Dict, List, Optional

from app.models.caller import CallerContext
from app.services import pg_store

# 会话上下文窗口：注入生成器的最大轮次（用户+助手各算一 turn）
MAX_CONTEXT_TURNS = 6
# 单会话最大保留轮次（超出丢弃最早的，滑动窗口）
MAX_SESSION_TURNS = 40

_SESSIONS: Dict[str, Dict[str, Any]] = {}


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
    """取会话；归属校验：仅会话创建者本人可访问（第18章权限语义延伸到会话）。"""
    sess = _SESSIONS.get(session_id)
    if not sess:
        raise KeyError(session_id)
    if sess["user_id"] != caller.userId:
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
    if len(sess["turns"]) > MAX_SESSION_TURNS:
        sess["turns"] = sess["turns"][-MAX_SESSION_TURNS:]
    _persist(sess)


def history_of(session_id: str) -> List[Dict[str, Any]]:
    sess = _SESSIONS.get(session_id)
    return list(sess["turns"]) if sess else []


def build_context(session: Dict[str, Any], max_turns: int = MAX_CONTEXT_TURNS) -> str:
    """把最近 N 轮拼成生成器可用的上下文文本（越界截断，滑动窗口）。"""
    recent = session["turns"][-max_turns:]
    return "\n".join(f"{'用户' if t['role'] == 'user' else '助手'}: {t['content']}" for t in recent)


def rewritten_query(user_input: str, history: List[Dict[str, Any]], max_chars: int = 40) -> str:
    """检索 query 改写：把上一轮用户问题并入本轮查询（指代消解的确定性近似）。

    例：上一轮"五一活动方案有哪些" → 本轮"预算多少" → 改写为"预算多少 五一活动方案有哪些"。
    仅并入用户轮内容（不含助手回答），截断防超长。
    """
    last_user = next((t["content"] for t in reversed(history) if t["role"] == "user"), "")
    if not last_user or last_user == user_input:
        return user_input
    return f"{user_input} {last_user[:max_chars]}".strip()


def session_summary(sess: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "session_id": sess["session_id"],
        "agent_id": sess["agent_id"],
        "user_id": sess["user_id"],
        "turn_count": len(sess["turns"]),
        "created_at": sess["created_at"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# M7 会话管理（列表 / 删除）：默认只看自己的会话，平台管理员可看全量
# ─────────────────────────────────────────────────────────────────────────────
def list_sessions(caller: CallerContext, include_all: bool = False,
                  limit: int = 20) -> List[Dict[str, Any]]:
    items = [s for s in _SESSIONS.values() if include_all or s["user_id"] == caller.userId]
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
