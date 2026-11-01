"""模型网关（第25章）：可插拔模型注册表 + LiteLLM 路由。

业务侧仅通过 model_id 透明调用，不感知底层厂商。新增模型只需实现 ModelAdapter
并登记到 Registry，业务代码无需改动（契约 /models 已定义注册接口）。
"""
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
