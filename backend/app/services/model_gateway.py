"""模型网关（第25章）：可插拔模型注册表 + LiteLLM 路由。

业务侧仅通过 model_id 透明调用，不感知底层厂商。新增模型只需实现 ModelAdapter
并登记到 Registry，业务代码无需改动（契约 /models 已定义注册接口）。
M6：chat_sync 同步调用（供 agent_runtime 编排节点使用）+ 探活缓存；
LiteLLM 代理不可达时回退确定性 stub，测试与离线演示不受影响。
"""
import time

import httpx

from app.core.config import settings

# 内存注册表（M1 接入 PostgreSQL models 表；此处为单体骨架默认种子）
_REGISTRY = {
    "gpt-4o-mini": {"model_id": "gpt-4o-mini", "vendor": "openai", "adapter": "openai_compatible",
                    "capabilities": ["chat", "stream"], "context_window": 128000,
                    "status": "active", "price_per_1k_prompt": 0.00015, "price_per_1k_completion": 0.0006},
    "qwen-local": {"model_id": "qwen-local", "vendor": "private", "adapter": "openai_compatible",
                   "capabilities": ["chat", "embed"], "context_window": 32000,
                   "status": "active", "price_per_1k_prompt": 0.0, "price_per_1k_completion": 0.0},
}


def list_models(status: str = "active") -> list[dict]:
    out = [m for m in _REGISTRY.values()]
    if status != "all":
        out = [m for m in out if m["status"] == status]
    return out


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
                             json={"model": model_id, "messages": messages})
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
    if not llm_available():
        return _stub_answer(model_id, messages)
    try:
        r = httpx.post(f"{settings.LITELLM_BASE_URL}/v1/chat/completions",
                       json={"model": model_id, "messages": messages}, timeout=timeout)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception:
        return _stub_answer(model_id, messages)


def _stub_answer(model_id: str, messages: list[dict]) -> str:
    user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    return f"[stub:{model_id}] 已基于授权上下文作答：{user[:80]}"
