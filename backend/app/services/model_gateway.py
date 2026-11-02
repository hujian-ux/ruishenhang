"""模型网关（第25章）：可插拔模型注册表 + LiteLLM 路由。

业务侧仅通过 model_id 透明调用，不感知底层厂商。新增模型只需实现 ModelAdapter
并登记到 Registry，业务代码无需改动（契约 /models 已定义注册接口）。
M6：chat_sync 同步调用（供 agent_runtime 编排节点使用）+ 探活缓存；
LiteLLM 代理不可达时回退确定性 stub，测试与离线演示不受影响。
"""
import time

import httpx

from app.core.config import settings


def _headers() -> dict:
    """LiteLLM 鉴权头：配了主密钥就透传，否则为空（本地开发免鉴权）。"""
    key = getattr(settings, "LITELLM_MASTER_KEY", "")
    return {"Authorization": f"Bearer {key}"} if key else {}


# 内存注册表（M1 接入 PostgreSQL models 表；此处为单体骨架默认种子）
# 单价单位：元 / 1k tokens（CNY），落账时按调用时的单价固化，改价不影响历史账单。
_REGISTRY = {
    "gpt-4o-mini": {"model_id": "gpt-4o-mini", "vendor": "openai", "adapter": "openai_compatible",
                    "capabilities": ["chat", "stream"], "context_window": 128000,
                    "status": "active", "price_per_1k_prompt": 0.0011, "price_per_1k_completion": 0.0044},
    "qwen-plus": {"model_id": "qwen-plus", "vendor": "dashscope", "adapter": "openai_compatible",
                  "capabilities": ["chat", "stream"], "context_window": 131072,
                  "status": "active", "price_per_1k_prompt": 0.0008, "price_per_1k_completion": 0.002},
    "qwen-turbo": {"model_id": "qwen-turbo", "vendor": "dashscope", "adapter": "openai_compatible",
                   "capabilities": ["chat", "stream"], "context_window": 131072,
                   "status": "active", "price_per_1k_prompt": 0.0003, "price_per_1k_completion": 0.0006},
    "qwen-local": {"model_id": "qwen-local", "vendor": "private", "adapter": "openai_compatible",
                   "capabilities": ["chat", "embed"], "context_window": 32000,
                   "status": "active", "price_per_1k_prompt": 0.0, "price_per_1k_completion": 0.0},
    "bge-m3": {"model_id": "bge-m3", "vendor": "private", "adapter": "openai_compatible",
               "capabilities": ["embed"], "context_window": 8192,
               "status": "active", "price_per_1k_prompt": 0.0, "price_per_1k_completion": 0.0},
}


def list_models(status: str = "active") -> list[dict]:
    out = [m for m in _REGISTRY.values()]
    if status != "all":
        out = [m for m in out if m["status"] == status]
    return out


def get_model(model_id: str) -> dict | None:
    return _REGISTRY.get(model_id)


def price_of(model_id: str) -> tuple[float, float]:
    """模型单价（每 1k prompt / completion tokens）；未登记模型按 0 计（私有化部署）。"""
    m = _REGISTRY.get(model_id) or {}
    return float(m.get("price_per_1k_prompt") or 0.0), float(m.get("price_per_1k_completion") or 0.0)


def set_status(model_id: str, status: str) -> dict:
    """启停模型（第25章：网关侧可下线某个模型而不改业务代码）。"""
    if model_id not in _REGISTRY:
        raise KeyError(model_id)
    if status not in ("active", "inactive"):
        raise ValueError(f"非法状态：{status}（active|inactive）")
    _REGISTRY[model_id]["status"] = status
    return _REGISTRY[model_id]


def register_model(req: dict) -> dict:
    """登记新模型适配器（热插拔）。M1 落库 + 写 LiteLLM config。"""
    mid = req["model_id"]
    _REGISTRY[mid] = {**req, "status": "active"}
    return {"model_id": mid, "status": "active", "registered_by": "platform_admin"}


async def chat(model_id: str, messages: list[dict]) -> str:
    """经 LiteLLM 网关调用（第25章）。失败回退本地 stub，保证可演示。"""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{settings.LITELLM_BASE_URL}/v1/chat/completions",
                             json={"model": model_id, "messages": messages},
                             headers=_headers())
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
    except Exception:
        return f"[stub:{model_id}] 回复内容（LiteLLM 未连接时为骨架回退）"


# ── M6：同步调用 + 探活缓存（agent_runtime 编排节点用） ──
_last_probe = 0.0
_probe_ok = False
_PROBE_TTL = 30.0  # 秒


def llm_available(force: bool = False) -> bool:
    """LiteLLM 代理探活（带 30s 缓存，避免每轮对话都探测）。"""
    global _last_probe, _probe_ok
    now = time.time()
    if not force and now - _last_probe < _PROBE_TTL:
        return _probe_ok
    _last_probe = now
    try:
        r = httpx.get(f"{settings.LITELLM_BASE_URL}/health/liveliness", timeout=1.5)
        _probe_ok = (r.status_code == 200)
    except Exception:
        _probe_ok = False
    return _probe_ok


def chat_sync(model_id: str, messages: list[dict], timeout: float = 15.0) -> str:
    """同步调用 LLM；不可达/失败回退确定性 stub（回答带 [stub] 标记便于识别）。"""
    return chat_sync_ex(model_id, messages, timeout)["text"]


def chat_sync_ex(model_id: str, messages: list[dict], timeout: float = 15.0) -> dict:
    """同步调用并**回传真实 token 用量**（第25章计量数据源）。

    返回 {text, real, model_id, usage{prompt_tokens,completion_tokens}, latency_ms}；
    real=False 表示走了 stub 回退，此时 usage 为空，由调用方按文本估算（仍要落账）。
    """
    t0 = time.time()
    if not llm_available():
        return {"text": _stub_answer(model_id, messages), "real": False,
                "model_id": model_id, "usage": {},
                "latency_ms": int((time.time() - t0) * 1000)}
    try:
        r = httpx.post(f"{settings.LITELLM_BASE_URL}/v1/chat/completions",
                       json={"model": model_id, "messages": messages},
                       headers=_headers(), timeout=timeout)
        r.raise_for_status()
        body = r.json()
        text = body["choices"][0]["message"]["content"] or ""
        u = body.get("usage") or {}
        usage = {
            "prompt_tokens": int(u.get("prompt_tokens") or 0),
            "completion_tokens": int(u.get("completion_tokens") or 0),
        }
        return {"text": text, "real": not text.startswith("[stub"),
                "model_id": body.get("model") or model_id, "usage": usage,
                "latency_ms": int((time.time() - t0) * 1000)}
    except Exception:
        return {"text": _stub_answer(model_id, messages), "real": False,
                "model_id": model_id, "usage": {},
                "latency_ms": int((time.time() - t0) * 1000)}


def _stub_answer(model_id: str, messages: list[dict]) -> str:
    user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    return f"[stub:{model_id}] 已基于授权上下文作答：{user[:80]}"
