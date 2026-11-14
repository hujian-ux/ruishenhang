"""P0-4 跨会话长期记忆。

把「值得长期记住」的用户偏好 / 稳定事实 / 明确约束从每轮对话抽取出来，
存成 memory_facts（内存为主存、PG 为持久副本，重启不丢），并在后续对话
注入 system_prompt，实现跨会话、跨智能体的长期记忆。

设计要点：
- tenant 隔离是红线：fact 必带 tenant_id，查询只取同租户；同 userId 跨租户不得串记忆。
- 维度共享：fact 可带 dept_id / user_id（可空）。查询时 tenant 相同的前提下，
  (dept 空或==caller.dept) 且 (user 空或==caller.user) 即可命中，同部门/同人共享。
- agent 维度：fact.agent_id 为空表示「通用事实」所有智能体可见；非空则仅该智能体可见。
- 抽取走一次轻量 LLM 调用；模型不可用（llm_available()=False）时安全跳过，零开销、零报错。
- PG 不可达时只看内存，upsert 静默失败但不阻断业务（与 session_store 一致）。
"""
import hashlib
import json
import re
import time
import uuid
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.services import pg_store

# fact_id -> fact dict。内存为主存；PG 为持久副本（重启载回）。
_FACTS: Dict[str, Dict[str, Any]] = {}

# 抽取输出块标记
_MEMORY_FENCE = re.compile(r"```memory\s*\n(.*?)```", re.DOTALL)

_CATEGORIES = {"preference", "fact", "constraint"}


def enabled() -> bool:
    """跨会话记忆总开关（默认开）。关闭后既不注入也不抽取。"""
    return bool(getattr(settings, "MEMORY_ENABLED", True))


def _now() -> float:
    return time.time()


def _hash(content: str) -> str:
    return hashlib.sha1(content.strip().encode("utf-8")).hexdigest()[:16]


def _persist(fact: Dict[str, Any]) -> None:
    """写穿 PG（离线/不可达静默跳过，内存为主存）。"""
    pg_store.upsert("memory_facts", fact["fact_id"], fact)


def load_from_pg() -> int:
    """启动时从 PG 载回记忆（重启不丢）；PG 不可达时为空操作。"""
    n = 0
    for payload in pg_store.load_all("memory_facts"):
        try:
            _FACTS[payload["fact_id"]] = payload
            n += 1
        except Exception:
            continue
    return n


def remember(caller: Any, content: str, category: str = "fact",
             agent_id: Optional[str] = None,
             session_id: Optional[str] = None,
             scope: str = "user") -> Optional[str]:
    """手工写入一条长期事实。返回 fact_id；content 为空或开关关闭时返回 None。

    scope 控制可见维度（tenant 隔离始终生效，是红线）：
      - "user"   个人专属（默认）：仅本人跨会话可见。
      - "dept"   同部门共享：同租户同部门均可见（user_id 留空）。
      - "tenant" 全租户共享：同租户所有人可见（dept/user 留空）。
    防重复：同 (tenant_id, content_hash) 已存在则更新 last_used，不新增。
    """
    if not enabled() or not content or not content.strip():
        return None
    content = content.strip()
    ch = _hash(content)
    tenant = getattr(caller, "tenantId", "") or ""
    if scope == "tenant":
        dept, user = "", ""
    elif scope == "dept":
        dept, user = getattr(caller, "deptId", "") or "", ""
    else:  # user（默认）
        dept, user = getattr(caller, "deptId", "") or "", getattr(caller, "userId", "") or ""
    # 同租户内同内容去重（跨个人/部门的同一事实只留一条）
    for f in _FACTS.values():
        if f.get("tenant_id") == tenant and f.get("content_hash") == ch:
            f["last_used"] = _now()
            _persist(f)
            return f["fact_id"]
    fact_id = "mem_" + uuid.uuid4().hex[:12]
    fact = {
        "fact_id": fact_id,
        "tenant_id": tenant,
        "dept_id": dept,
        "user_id": user,
        "agent_id": agent_id or "",
        "category": category if category in _CATEGORIES else "fact",
        "content": content,
        "source_session": session_id or "",
        "content_hash": ch,
        "created_at": _now(),
        "last_used": _now(),
        "archived": False,
    }
    _FACTS[fact_id] = fact
    _persist(fact)
    return fact_id


def _matches(fact: Dict[str, Any], caller: Any, agent_id: Optional[str]) -> bool:
    if fact.get("archived"):
        return False
    if fact.get("tenant_id") and fact["tenant_id"] != getattr(caller, "tenantId", ""):
        return False  # tenant 隔离红线
    fd, fu = fact.get("dept_id") or "", fact.get("user_id") or ""
    if fd and fd != getattr(caller, "deptId", ""):
        return False
    if fu and fu != getattr(caller, "userId", ""):
        return False
    fa = fact.get("agent_id") or ""
    if fa and agent_id and fa != agent_id:
        return False  # 仅该智能体可见的事实，跨 agent 不命中
    return True


def relevant_facts(caller: Any, agent_id: Optional[str] = None,
                   limit: Optional[int] = None) -> str:
    """返回该 caller 当前可见的持久事实，拼成注入 system_prompt 的文本块。

    无记忆时返回空串（调用方据此决定是否注入，零噪声）。
    """
    if not enabled():
        return ""
    limit = limit or int(getattr(settings, "MEMORY_MAX_FACTS_INJECT", 8))
    facts = [f for f in _FACTS.values() if _matches(f, caller, agent_id)]
    facts.sort(key=lambda f: f.get("last_used", 0), reverse=True)
    facts = facts[:limit]
    if not facts:
        return ""
    lines = ["【长期记忆（来自与你的历史对话，跨会话保留，优先遵循）】"]
    label = {"preference": "偏好", "fact": "事实", "constraint": "约束"}
    for f in facts:
        lines.append(f"- ({label.get(f.get('category'), '事实')}) {f['content']}")
    return "\n".join(lines)


def list_facts(caller: Any, agent_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """列出该 caller 可见的全部事实（含 content/分类/时间），供运营台/调试。"""
    if not enabled():
        return []
    return [f for f in _FACTS.values() if _matches(f, caller, agent_id)]


def forget(fact_id: str, caller: Any) -> bool:
    """删除一条事实；仅同租户可删（防跨租户误删）。"""
    f = _FACTS.get(fact_id)
    if not f:
        return False
    if f.get("tenant_id") and f["tenant_id"] != getattr(caller, "tenantId", ""):
        return False
    f["archived"] = True  # 软删：保留行但标记，查询过滤
    _persist(f)
    return True


def extract_into(caller: Any, user_input: str, answer: str,
                 agent_id: Optional[str] = None,
                 session_id: Optional[str] = None) -> List[str]:
    """从一轮对话（用户问题 + 助手答案）抽取长期事实并入库。

    无模型（llm_available()=False）或开关关闭时安全空返；任何解析/模型异常都被吞掉，
    绝不阻断主流程、绝不报错。返回新建/命中的 fact_id 列表。
    """
    if not enabled():
        return []
    try:
        from app.services import model_gateway
        if not model_gateway.llm_available():
            return []  # 离线/无 key：零开销跳过，不调模型
    except Exception:
        return []

    prompt = (
        "你是记忆管理员。下面是用户的一句/段话和你（助手）的回复。\n"
        "仅当其中表达了「值得在今后所有对话中长期记住」的内容时才输出记忆，否则什么都不输出。\n"
        "判定标准（满足其一即记）：\n"
        "1. 用户的长期偏好（语言/输出格式/角色/忌讳）；\n"
        "2. 稳定事实（公司/行业/项目/所在地/岗位/产品）；\n"
        "3. 明确约束（『以后都…』『不要…』『只用…』等）。\n"
        "纯任务内容（本次要算什么、写了什么、本次结论）不记。\n"
        "若需记忆，只输出一个 markdown 代码块 ```memory，内部是 JSON 数组：\n"
        '[{"category":"preference|fact|constraint","content":"一句话事实"}]\n'
        "不要任何解释文字。\n\n"
        f"【用户】{user_input}\n\n【助手】{answer}"
    )
    try:
        out = model_gateway.chat_sync_ex(
            None,
            [{"role": "system", "content": "你是记忆抽取器，严格按格式输出。"},
             {"role": "user", "content": prompt}],
            user_input=user_input,
        )
        text = (out or {}).get("text", "") or ""
    except Exception:
        return []

    m = _MEMORY_FENCE.search(text)
    if not m:
        return []
    raw = m.group(1).strip()
    try:
        data = json.loads(raw)
    except Exception:
        return []
    if isinstance(data, dict):
        # 容错：有的模型会包一层 {"facts":[...]}
        data = data.get("facts") or data.get("items") or []
    if not isinstance(data, list):
        return []

    ids: List[str] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        content = (item.get("content") or "").strip()
        if not content:
            continue
        cat = item.get("category") or "fact"
        fid = remember(caller, content, category=cat, agent_id=agent_id,
                      session_id=session_id)
        if fid:
            ids.append(fid)
    return ids
