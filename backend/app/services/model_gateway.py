"""模型网关（第25章）：可插拔模型注册表 + 双通道真实模型接入。

业务侧仅通过 model_id 透明调用，不感知底层厂商与接入方式：

- `LLM_PROVIDER=litellm`：经 LiteLLM 代理（多厂商统一账单，生产推荐）
- `LLM_PROVIDER=direct`：直连厂商 OpenAI 兼容端点（单厂商起步最快，不起额外容器）
  内置 zhipu / dashscope / openai / deepseek / moonshot 端点预设，换厂商只改环境变量。

M6：`chat_sync` 同步调用（供 agent_runtime 编排节点使用）+ 探活缓存。
M17：真模型失败时**不再静默吞掉**——结果里带 `error`，响应里回显 `llm.real`，
     并把 `LLM_STRICT=1` 作为验收/生产的开关：宁可直接报错，也不要拿 stub 冒充模型。
"""
import json
import threading
import time

import httpx

from app.core.config import settings
from app.services import model_router, observability, tenant_store


# ── 厂商错误的可执行结论 ──
# 各家返回的 4xx 语义一致，但原文是英文且不含处置动作。接入期最常见的三类问题
# （key 拿错厂商 / 余额为 0 / 模型名不存在）光看 "HTTP 402" 完全判断不出来，
# 这里统一翻成「为什么 + 下一步做什么」。
_HTTP_HINTS = {
    400: "请求被拒：多半是模型名写错，检查 LLM_DEFAULT_MODEL 是否在该厂商的模型清单里",
    401: "密钥无效：可能已删除、复制不全，或这串 key 不属于当前 LLM_VENDOR 指向的厂商",
    402: "账户余额不足：key 本身有效，但账户没钱了，去厂商控制台充值",
    403: "无权限：模型未开通/已下线，或该 key 没有此模型的调用权限",
    404: "接口地址或模型不存在：检查 LLM_BASE_URL 与模型名",
    429: "触发限流或配额用尽：降低并发或稍后重试，长期问题需提配额",
}


def diagnose(status_code: int, body: str = "") -> str:
    """把厂商 HTTP 状态翻成一句可执行的结论（无匹配则返回空串）。"""
    if status_code >= 500:
        return f"厂商侧服务异常：稍后重试；持续出现联系厂商（HTTP {status_code}）"
    hint = _HTTP_HINTS.get(status_code, "")
    if not hint:
        return ""
    return f"{hint}（HTTP {status_code}{'：' + body[:160] if body else ''}）"


class LLMUnavailable(Exception):
    """真模型不可用且 LLM_STRICT=1 —— 由调用方转成 502，不允许回退 stub。

    刻意**不继承 RuntimeError**：agent_runtime 用 `except RuntimeError` 兜
    「LangGraph 没装」的降级路径，若本异常也是 RuntimeError，会被误捕并重跑一遍，
    真模型故障就被伪装成骨架降级了。
    """


class RateLimited(Exception):
    """网关节流触发：令牌桶耗尽，由调用方转成 429（削峰，保护下游模型服务）。"""


# ── 网关令牌桶（削峰） ────────────────────────────────────────────────────────
# 默认 GATEWAY_THROTTLE_RPS=0 关闭，零行为变更；>0 时按 令牌/秒 平滑放行，
# 突发余量 = GATEWAY_THROTTLE_BURST；超限抛 RateLimited，调用方转 429。
class _TokenBucket:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rps = 0.0
        self._burst = 0.0
        self._tokens = 0.0
        self._last = time.time()

    def configure(self, rps: float, burst: float) -> None:
        with self._lock:
            self._rps = float(rps)
            self._burst = float(burst)
            self._tokens = self._burst  # 启动即给满突发余量

    def _refill(self) -> None:
        now = time.time()
        if self._rps > 0:
            self._tokens = min(self._burst, self._tokens + (now - self._last) * self._rps)
        self._last = now

    def try_acquire(self) -> bool:
        """获取一个令牌；关闭（rps<=0）或仍有余量返回 True，否则 False。"""
        if self._rps <= 0:
            return True
        with self._lock:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False


_BUCKET = _TokenBucket()
_BUCKET.configure(settings.GATEWAY_THROTTLE_RPS, settings.GATEWAY_THROTTLE_BURST)


def resolve_model(user_input: str, agent: dict | None = None,
                  requested: str | None = None,
                  tenant_id: str | None = None) -> tuple[str, dict | None]:
    """把路由建议真正落到模型选择（蓝图第 34 章，applied=true）。

    - M43：租户级 LLM 覆盖优先（tenant config.llm.model_id），存在则直接用、不做路由。
    - ``requested`` 显式指定（非 "auto"）→ 直接用它，不做路由（业务包可硬指定模型）。
    - ``requested`` 为 None / "auto" 且 ``ROUTING_ENABLED`` → 调 ``model_router.route`` 选档。
    - 否则回落平台默认模型。

    返回 ``(model_id, route_decision)``；route_decision 为 None 表示未走路由（便于计量埋点标注）。
    """
    # M43：租户级 LLM 模型覆盖（per-tenant 配置优先于全局路由）
    if tenant_id:
        override = tenant_store.get_config(tenant_id).get("llm", {}).get("model_id")
        if override and override != "auto":
            return override, {"tenant_override": True, "tenant_id": tenant_id}
    if requested and requested != "auto":
        return requested, None
    if settings.ROUTING_ENABLED:
        r = model_router.route(user_input or "", agent or {})
        return r["model"], r
    return settings.LLM_DEFAULT_MODEL, None


def apply_throttle() -> None:
    """网关注册：超出令牌桶抛 RateLimited。默认关闭时直接放行。"""
    if not _BUCKET.try_acquire():
        raise RateLimited("网关请求速率超过 GATEWAY_THROTTLE_RPS 上限，已限流（削峰保护）")


# ── 厂商预设：直连通道只需要 base_url + key ──
# 全部是 OpenAI 兼容的 /chat/completions 端点，故一套代码通吃。
# models/small_model 只用于界面下拉建议（不给死：客户常有私有化模型名），
# 真调哪个模型永远以 LLM_DEFAULT_MODEL 为准。
PROVIDER_PRESETS = {
    "zhipu": {"base_url": "https://open.bigmodel.cn/api/paas/v4",
              "key_env": "ZHIPU_API_KEY", "label": "智谱 GLM",
              "models": ["glm-4.6", "glm-4-plus", "glm-4-flash"], "small_model": "glm-4-flash"},
    "dashscope": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                  "key_env": "DASHSCOPE_API_KEY", "label": "阿里通义千问",
                  "models": ["qwen-max", "qwen-plus", "qwen-turbo"], "small_model": "qwen-turbo"},
    "openai": {"base_url": "https://api.openai.com/v1",
               "key_env": "OPENAI_API_KEY", "label": "OpenAI",
               "models": ["gpt-4o", "gpt-4o-mini"], "small_model": "gpt-4o-mini"},
    "deepseek": {"base_url": "https://api.deepseek.com/v1",
                 "key_env": "DEEPSEEK_API_KEY", "label": "DeepSeek",
                 "models": ["deepseek-chat", "deepseek-reasoner"], "small_model": "deepseek-chat"},
    "moonshot": {"base_url": "https://api.moonshot.cn/v1",
                 "key_env": "MOONSHOT_API_KEY", "label": "月之暗面 Kimi",
                 "models": ["moonshot-v1-8k", "moonshot-v1-32k", "kimi-latest"],
                 "small_model": "moonshot-v1-8k"},
    # 硅基流动：一家聚合国内外主流开源模型（含 zai-org/GLM-*、deepseek-ai/*、Qwen/*），
    # 一个 key 跑多模型，适合做「同一技能包换模型比效果」的横向评测。
    # free_chain：官方免费额度池（每模型独立 RPM/TPM），按此顺序轮换最省钱。
    "siliconflow": {"base_url": "https://api.siliconflow.cn/v1",
                    "key_env": "SILICONFLOW_API_KEY", "label": "硅基流动（聚合）",
                    "models": ["Qwen/Qwen2.5-7B-Instruct", "Qwen/Qwen3-8B",
                               "THUDM/GLM-4-9B-0414", "Qwen/Qwen3.5-4B",
                               "deepseek-ai/DeepSeek-R1-0528-Qwen3-8B",
                               "THUDM/GLM-Z1-9B-0414",
                               "zai-org/GLM-4.6", "deepseek-ai/DeepSeek-V3"],
                    "small_model": "Qwen/Qwen2.5-7B-Instruct",
                    "free_chain": ["Qwen/Qwen2.5-7B-Instruct", "Qwen/Qwen3-8B",
                                   "THUDM/GLM-4-9B-0414", "Qwen/Qwen3.5-4B",
                                   "deepseek-ai/DeepSeek-R1-0528-Qwen3-8B",
                                   "THUDM/GLM-Z1-9B-0414"]},
    # 私有化 / 自建 OpenAI 兼容服务（vLLM、Ollama、一体机）：base_url 由用户填
    "custom": {"base_url": "", "key_env": "LLM_API_KEY", "label": "自建 / 私有化",
               "models": [], "small_model": ""},
}


def _direct_key() -> str:
    """直连密钥：显式 LLM_API_KEY 优先，其次按厂商读惯用环境变量名。"""
    if settings.LLM_API_KEY:
        return settings.LLM_API_KEY
    import os
    preset = PROVIDER_PRESETS.get(settings.LLM_VENDOR, {})
    return os.environ.get(preset.get("key_env", ""), "")


def current_key() -> str:
    """当前通道实际会用的密钥（仅供脱敏展示/诊断，禁止写进日志）。"""
    if settings.LLM_PROVIDER == "direct":
        return _direct_key()
    return getattr(settings, "LITELLM_MASTER_KEY", "") or ""


def _headers() -> dict:
    """鉴权头：直连通道用厂商 key，litellm 通道用代理主密钥。"""
    if settings.LLM_PROVIDER == "direct":
        key = _direct_key()
        return {"Content-Type": "application/json",
                "Authorization": f"Bearer {key}"} if key else {"Content-Type": "application/json"}
    key = getattr(settings, "LITELLM_MASTER_KEY", "")
    return {"Authorization": f"Bearer {key}"} if key else {}


def _endpoint() -> str:
    """chat/completions 完整 URL（两条通道的唯一差异点）。"""
    if settings.LLM_PROVIDER == "direct":
        base = (settings.LLM_BASE_URL
                or PROVIDER_PRESETS.get(settings.LLM_VENDOR, {}).get("base_url", "")).rstrip("/")
        return f"{base}/chat/completions"
    return f"{settings.LITELLM_BASE_URL.rstrip('/')}/v1/chat/completions"


def access_mode() -> dict:
    """当前接入方式的可读摘要（健康检查/验收报告要能一眼看出跑的是哪条通道）。"""
    if settings.LLM_PROVIDER == "direct":
        preset = PROVIDER_PRESETS.get(settings.LLM_VENDOR, {})
        return {"provider": "direct", "vendor": settings.LLM_VENDOR,
                "vendor_label": preset.get("label", settings.LLM_VENDOR),
                "base_url": _endpoint().rsplit("/chat/completions", 1)[0],
                "key_configured": bool(_direct_key()),
                "key_hint": preset.get("key_env", "")}
    return {"provider": "litellm", "vendor": "litellm",
            "vendor_label": "LiteLLM 代理",
            "base_url": settings.LITELLM_BASE_URL,
            "key_configured": bool(getattr(settings, "LITELLM_MASTER_KEY", "")),
            "key_hint": "LITELLM_MASTER_KEY"}


# 内存注册表（M1 接入 PostgreSQL models 表；此处为单体骨架默认种子）
# 单价单位：元 / 1k tokens（CNY），落账时按调用时的单价固化，改价不影响历史账单。
# 智谱牌价为 2025 年官网/公开价格页口径（GLM-4.6 官方 $0.60/$2.20 每百万 tokens，
# 按 7.2 汇率折算），属参考牌价 —— 真实合同价用 MODEL_PRICES_JSON 覆盖，不发版。
_REGISTRY = {
    "glm-4.6": {"model_id": "glm-4.6", "vendor": "zhipu", "adapter": "openai_compatible",
                "capabilities": ["chat", "stream", "long_output"], "context_window": 200000,
                "status": "active", "price_per_1k_prompt": 0.0043, "price_per_1k_completion": 0.0158},
    "glm-4-plus": {"model_id": "glm-4-plus", "vendor": "zhipu", "adapter": "openai_compatible",
                   "capabilities": ["chat", "stream"], "context_window": 128000,
                   "status": "active", "price_per_1k_prompt": 0.0036, "price_per_1k_completion": 0.0036},
    "glm-4-flash": {"model_id": "glm-4-flash", "vendor": "zhipu", "adapter": "openai_compatible",
                    "capabilities": ["chat", "stream"], "context_window": 128000,
                    "status": "active", "price_per_1k_prompt": 0.0, "price_per_1k_completion": 0.0},
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


def _apply_price_override() -> None:
    """允许用 MODEL_PRICES_JSON 覆盖牌价，例如：
    {"glm-4.6": [0.004, 0.014]}（单位 元/1k tokens，[输入, 输出]）。
    合同价与牌价常常不同，计费表不能写死在代码里等发版。
    """
    raw = getattr(settings, "MODEL_PRICES_JSON", "") or ""
    if not raw:
        return
    try:
        data = json.loads(raw)
    except Exception:
        return
    for mid, pair in (data or {}).items():
        if mid in _REGISTRY and isinstance(pair, (list, tuple)) and len(pair) == 2:
            _REGISTRY[mid]["price_per_1k_prompt"] = float(pair[0])
            _REGISTRY[mid]["price_per_1k_completion"] = float(pair[1])


_apply_price_override()


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


def ensure_registered(model_id: str, vendor: str = "", *, price: tuple | None = None) -> dict:
    """确保模型在注册表里（幂等）。在线配模型时用：客户填了一个没见过的模型名，
    注册表里没有就会在「模型注册表」和成本报表里查不到，看起来像没配上。

    未给价 → 单价 0 并打 `unpriced` 标记，账单上能看出「这个模型还没定价」，
    而不是静悄悄按 0 元入账。
    """
    if not model_id:
        return {}
    hit = _REGISTRY.get(model_id)
    if hit:
        if vendor and not hit.get("vendor"):
            hit["vendor"] = vendor
        return hit
    p_in, p_out = (price or (0.0, 0.0))
    row = {
        "model_id": model_id,
        "vendor": vendor or "custom",
        "adapter": "openai_compatible",
        "capabilities": ["chat", "stream"],
        "context_window": 0,
        "status": "active",
        "price_per_1k_prompt": float(p_in),
        "price_per_1k_completion": float(p_out),
        "unpriced": not price,
        "registered_by": "config",
    }
    _REGISTRY[model_id] = row
    return row


def registry_snapshot() -> list[dict]:
    """注册表只读快照（供配置页展示「配的模型登记了没」）。"""
    return list(_REGISTRY.values())


def _payload(model_id: str, messages: list[dict], **kw) -> dict:
    """统一请求体。max_tokens 给小了，技能包的多张表会被截断成半张。"""
    body = {
        "model": model_id,
        "messages": messages,
        "temperature": kw.get("temperature", settings.LLM_TEMPERATURE),
        "max_tokens": kw.get("max_tokens", settings.LLM_MAX_TOKENS),
    }
    # GLM-4.5/4.6 默认混合思考：财务归因要稳定输出结构化表格，关掉思考更快更可复现。
    if model_id.startswith(("glm-4.6", "glm-4.5")) and getattr(settings, "LLM_DISABLE_THINKING", True):
        body["thinking"] = {"type": "disabled"}
    return body


def _extract(body: dict) -> tuple[str, dict]:
    """从响应取正文与用量。思考型模型偶尔把正文放在 reasoning_content，
    正文为空时兜底，否则会拿到空字符串还以为成功。"""
    msg = (body.get("choices") or [{}])[0].get("message") or {}
    text = msg.get("content") or ""
    if not text.strip() and msg.get("reasoning_content"):
        text = msg["reasoning_content"]
    u = body.get("usage") or {}
    usage = {
        "prompt_tokens": int(u.get("prompt_tokens") or 0),
        "completion_tokens": int(u.get("completion_tokens") or 0),
    }
    return text, usage


async def chat(model_id: str, messages: list[dict]) -> str:
    """经网关调用（第25章）。失败时按 LLM_STRICT 决定抛错或回退 stub。"""
    try:
        async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT_S) as c:
            r = await c.post(_endpoint(), json=_payload(model_id, messages), headers=_headers())
            r.raise_for_status()
            return _extract(r.json())[0]
    except Exception as exc:
        if settings.LLM_STRICT:
            raise LLMUnavailable(f"模型调用失败：{type(exc).__name__}: {exc}") from exc
        return f"[stub:{model_id}] 回复内容（真模型未连接时为骨架回退）"


# ── M51：模型轮换链 ──────────────────────────────────────────────────────────
# 场景：客户拿免费额度跑（硅基流动免费模型池、每模型独立 RPM/TPM 与日额度），
# 某个模型额度用尽后不能整条链停摆。策略是**按顺序顺延**：主模型不可用就换下一个，
# 全部不可用才回退 stub。状态放内存（重启即重置），冷却时间分两类：
#   额度型（402/403 余额或配额）= 半小时，等日额度重置；
#   限流型（429、上游饱和）= 一分钟，等瞬时压力过去。
# 关键取舍：**401 不轮换**——同一把 key 换模型照样 401，白打 N 次只是延迟报错。
_MAX_ROTATE = 5
_CHAIN_QUOTA_COOLDOWN = 1800.0   # 由 settings 覆盖，见 _cooldown_of
_CHAIN_STATE: dict = {}          # model_id -> {cool_until, kind, last_error, fails, ok_at}
_LAST_GOOD = ""                  # 最近一次真答话的模型：下轮优先用它，省一次 402 往返
_SIG = ""                        # 配置指纹：换了厂商/默认模型就作废旧记忆
_CHAIN_LOCK = threading.Lock()

# 非对话模型混进链路只会换来 400：向量/重排/OCR/语音/图像一律跳过
_NON_CHAT_HINTS = ("embed", "rerank", "ocr", "asr", "speech", "voice", "kolors",
                   "image", "tts", "bge-", "whisper")


def is_chat_model(mid: str) -> bool:
    low = (mid or "").lower()
    return bool(low) and not any(h in low for h in _NON_CHAT_HINTS)


def _sig() -> str:
    mode = access_mode()
    return f"{mode['provider']}|{mode['vendor']}|{mode['base_url']}|{settings.LLM_DEFAULT_MODEL}"


def reset_chain_state() -> None:
    """配置变了（换 key/换厂商/换主模型）就清空冷却与记忆：新 key 的额度是新的。"""
    global _LAST_GOOD, _SIG
    with _CHAIN_LOCK:
        _CHAIN_STATE.clear()
        _LAST_GOOD = ""
        _SIG = _sig()


def chain_models() -> list:
    """解析 LLM_MODEL_CHAIN（逗号/分号/换行分隔），过滤非对话模型。"""
    raw = str(getattr(settings, "LLM_MODEL_CHAIN", "") or "")
    out: list = []
    for x in raw.replace(";", ",").replace("\n", ",").split(","):
        m = x.strip()
        if m and m not in out and is_chat_model(m):
            out.append(m)
    return out


def classify_status(status_code: int, body: str = "") -> str:
    """把一次失败归成可决策类型：quota/ratelimit/notfound/timeout/auth/fatal。

    「能不能轮换下一个模型」全靠这个判断：
    - quota / ratelimit / notfound → 换模型有意义
    - auth（401）→ 一把 key 全废，换了也白换
    - fatal（400 参数错、5xx 厂商故障）→ 不轮换，把原因原样抛给调用方
    """
    if status_code == 401:
        return "auth"
    if status_code == 402:
        return "quota"
    if status_code == 403:
        low = (body or "").lower()
        # 免费模型常见的 403 是「上游负载饱和」，属瞬时压力，不是额度没了
        if any(k in low for k in ("饱和", "busy", "overload", "rate limit", "too many")):
            return "ratelimit"
        return "quota"
    if status_code == 429:
        return "ratelimit"
    if status_code == 404:
        return "notfound"
    if status_code == 400:
        low = (body or "").lower()
        # 模型名不在清单里，硅基流动这类聚合网关返回 400 而不是 404
        if any(k in low for k in ("model", "not exist", "not found", "不存在", "invalid model")):
            return "notfound"
    return "fatal"


def _cooldown_of(kind: str) -> float:
    if kind == "quota":
        return float(getattr(settings, "LLM_CHAIN_QUOTA_COOLDOWN_S", _CHAIN_QUOTA_COOLDOWN) or 1800)
    if kind == "ratelimit":
        return float(getattr(settings, "LLM_CHAIN_RATE_COOLDOWN_S", 60) or 60)
    if kind == "notfound":
        return 86400.0     # 模型下线/改名：一天内别再试，等人工修配置
    if kind == "timeout":
        return 120.0
    return 0.0


def _mark_fail(mid: str, kind: str, err: str) -> None:
    cd = _cooldown_of(kind)
    with _CHAIN_LOCK:
        st = _CHAIN_STATE.setdefault(mid, {"fails": 0, "ok_at": 0.0})
        st.update({"fails": st.get("fails", 0) + 1, "kind": kind,
                   "last_error": (err or "")[:200],
                   "cool_until": time.time() + cd if cd else 0.0})


def _mark_ok(mid: str, recall: bool = True) -> None:
    """记一次成功。recall=True 时把它记成"下次优先用"的模型。

    体检（probe_all）传 recall=False：它是诊断行为，不是真实调用，
    不该因为并发里最后返回的那个模型就把默认模型悄悄换掉。
    """
    global _LAST_GOOD
    with _CHAIN_LOCK:
        if recall:
            _LAST_GOOD = mid
        st = _CHAIN_STATE.setdefault(mid, {"fails": 0})
        st.update({"kind": "", "last_error": "", "cool_until": 0.0,
                   "ok_at": time.time(), "fails": 0})


def _cooling(mid: str, now: float) -> bool:
    st = _CHAIN_STATE.get(mid) or {}
    return float(st.get("cool_until") or 0) > now


def rotation_order(primary: str = "") -> list:
    """本次调用要依次尝试的模型：主模型 → 链上其余 → 上次成功的兜底。

    规则细节（都踩过）：
    - 主模型在冷却中，才把"上次成功的"提到最前（省一次注定 402 的往返）；
      主模型没冷却就按配置顺序先试它 —— 否则一旦轮换过去就永远回不到主模型了。
    - 冷却中的模型排到后面；如果全在冷却，仍拿冷却最快结束的那个试一次
      （额度可能是按天重置的，不试就永远起不来）。
    """
    primary_m = primary or settings.LLM_DEFAULT_MODEL
    cands: list = []
    for m in (primary_m, *chain_models(), _LAST_GOOD):
        if m and m not in cands and is_chat_model(m):
            cands.append(m)
    now = time.time()
    alive = [m for m in cands if not _cooling(m, now)]
    if _LAST_GOOD and _LAST_GOOD in alive and _cooling(primary_m, now):
        alive.remove(_LAST_GOOD)
        alive.insert(0, _LAST_GOOD)
    if not alive and cands:
        alive = sorted(cands, key=lambda m: (_CHAIN_STATE.get(m) or {}).get("cool_until", 0))[:1]
    return alive[:_MAX_ROTATE]


def chain_state() -> dict:
    """轮换链运行态（配置页展示用）：谁在用、谁在冷却、为什么冷却。"""
    now = time.time()
    sig_ok = _SIG == _sig()
    rows = []
    for m in ([_LAST_GOOD] if _LAST_GOOD and sig_ok else []) + chain_models():
        if m in [r["model"] for r in rows]:
            continue
        st = _CHAIN_STATE.get(m) or {}
        cool = float(st.get("cool_until") or 0)
        rows.append({
            "model": m,
            "cooling": cool > now,
            "cool_remaining_s": int(max(0, cool - now)),
            "kind": st.get("kind") or ("ok" if st.get("ok_at") else "untried"),
            "last_error": st.get("last_error") or "",
            "ok_at": st.get("ok_at") or 0,
        })
    return {"chain": chain_models(), "last_good": _LAST_GOOD if sig_ok else "",
            "models": rows, "max_rotate": _MAX_ROTATE}


# ── M6：同步调用 + 探活缓存（agent_runtime 编排节点用） ──
_last_probe = 0.0
_probe_ok = False
_PROBE_TTL = 30.0  # 秒


def llm_available(force: bool = False) -> bool:
    """探活（30s 缓存，避免每轮对话都探测）。

    - litellm 通道：打 /health/liveliness（代理本身有健康端点）
    - direct 通道：不做无谓的 token 消耗，只校验密钥是否配置；
      真正可用性由调用结果判定（失败会带 error 上抛，不静默）
    """
    global _last_probe, _probe_ok
    now = time.time()
    if not force and now - _last_probe < _PROBE_TTL:
        return _probe_ok
    _last_probe = now
    if settings.LLM_PROVIDER == "direct":
        _probe_ok = bool(_direct_key())
        return _probe_ok
    try:
        r = httpx.get(f"{settings.LITELLM_BASE_URL}/health/liveliness", timeout=1.5)
        _probe_ok = (r.status_code == 200)
    except Exception:
        _probe_ok = False
    return _probe_ok


def chat_sync(model_id: str, messages: list[dict], timeout: float = 0) -> str:
    """同步调用 LLM；不可达/失败时按 LLM_STRICT 抛错或回退确定性 stub。"""
    return chat_sync_ex(model_id, messages, timeout)["text"]


def chat_sync_ex(model_id: str | None, messages: list[dict], timeout: float = 0,
                 max_tokens: int | None = None, temperature: float | None = None,
                 user_input: str = "", agent: dict | None = None,
                 tenant_id: str | None = None) -> dict:
    """同步调用并**回传真实 token 用量**（第25章计量数据源）。

    模型选择：``model_id`` 为 None / "auto" 时按路由策略选档（applied=true）；
    显式传模型名则跳过路由（业务包可硬指定）。调用前过网关节流（令牌桶）。

    返回 {text, real, model_id, routed_tier, routed_model, usage, latency_ms, error}；
    real=False 表示走了 stub 回退（或真模型失败），此时 error 写明原因，
    调用方要把 error 透到响应里 —— 「看起来跑了」比「跑失败」更危险。
    """
    t0 = time.time()
    kw = {}
    if max_tokens:
        kw["max_tokens"] = max_tokens
    if temperature is not None:
        kw["temperature"] = temperature
    to = timeout or settings.LLM_TIMEOUT_S

    # 路由：把建议真正落到模型选择（蓝图第 34 章 applied=true）
    effective_model, route_decision = resolve_model(user_input, agent, model_id, tenant_id)
    routed_tier = (route_decision or {}).get("tier") if route_decision else None
    routed_model = effective_model if route_decision else None

    # 网关节流（削峰）：超限抛 RateLimited，由调用方转 429
    try:
        apply_throttle()
    except RateLimited as exc:
        return {"text": "", "real": False, "model_id": effective_model,
                "routed_tier": routed_tier, "routed_model": routed_model,
                "usage": {}, "error": f"rate_limited: {exc}", "throttled": True,
                "latency_ms": int((time.time() - t0) * 1000)}

    if not llm_available():
        err = ("未配置模型密钥（LLM_API_KEY / 厂商环境变量）" if settings.LLM_PROVIDER == "direct"
               else "LiteLLM 代理不可达")
        if settings.LLM_STRICT:
            raise LLMUnavailable(err)
        observability.observe_llm_usage(0, 0, False)  # stub 回退：计一次调用，无 token 消耗
        return {"text": _stub_answer(effective_model, messages), "real": False,
                "model_id": effective_model, "routed_tier": routed_tier,
                "routed_model": routed_model, "usage": {}, "error": err,
                "latency_ms": int((time.time() - t0) * 1000)}

    # M51：按轮换链依次尝试。主模型额度用尽/限流/下线 → 顺延下一个，
    # 全部失败才回退 stub。attempts 回传，页面能看到"实际是哪个模型答的"。
    candidates = rotation_order(effective_model) or [effective_model]
    attempts: list = []
    last_err = ""
    last_status = 0
    for idx, mid in enumerate(candidates):
        try:
            r = httpx.post(_endpoint(), json=_payload(mid, messages, **kw),
                           headers=_headers(), timeout=to)
            if r.status_code >= 400:
                kind = classify_status(r.status_code, r.text)
                # 把厂商错误原文带出去：401/429/无权限/模型名不对，排查方向完全不同；
                # 能翻成结论的（401/402/403…）附一句人话，避免只看到 HTTP 402 卡住。
                hint = diagnose(r.status_code, r.text)
                last_err = hint or f"HTTP {r.status_code}: {r.text[:300]}"
                last_status = r.status_code
                attempts.append({"model": mid, "status": r.status_code,
                                 "kind": kind, "error": last_err[:200]})
                _mark_fail(mid, kind, last_err)
                # 401 换模型也没用（同一把 key）；fatal 是请求本身的问题，重试无意义
                if kind in ("auth", "fatal"):
                    break
                continue
            body = r.json()
            text, usage = _extract(body)
            if not text.strip():
                raise RuntimeError("响应正文为空（可能被 max_tokens 截断或触发内容策略）")
            _mark_ok(mid)
            observability.observe_llm_usage(usage["prompt_tokens"], usage["completion_tokens"], True)
            return {"text": text, "real": True,
                    "model_id": body.get("model") or mid,
                    "routed_tier": routed_tier, "routed_model": routed_model,
                    "usage": usage, "error": "",
                    "rotated": mid != effective_model, "attempts": attempts,
                    "latency_ms": int((time.time() - t0) * 1000)}
        except Exception as exc:
            last_err = f"{type(exc).__name__}: {exc}"
            # 连不上主机说明网络/地址错，换模型是同一个地址，再试只是把超时乘 N
            network = isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))
            timeout = isinstance(exc, httpx.TimeoutException)
            kind = "network" if network else ("timeout" if timeout else "fatal")
            attempts.append({"model": mid, "status": 0, "kind": kind,
                             "error": last_err[:200]})
            _mark_fail(mid, kind, last_err)
            # 网络不通是地址问题（换模型同一个地址）、解析/正文空是内容问题，
            # 这两类换模型都不会变好；只有超时可能是某个模型太慢，值得顺延。
            if network or not timeout:
                break
            continue

    # 全军覆没：不是"跑了"，是"跑失败"，error 必须带出去
    if settings.LLM_STRICT:
        raise LLMUnavailable(last_err or "所有候选模型均不可用")
    observability.observe_llm_usage(0, 0, False)
    return {"text": _stub_answer(effective_model, messages), "real": False,
            "model_id": effective_model, "routed_tier": routed_tier,
            "routed_model": routed_model, "usage": {},
            "error": last_err or "所有候选模型均不可用",
            "rotated": False, "attempts": attempts, "last_status": last_status,
            "latency_ms": int((time.time() - t0) * 1000)}


def routed_chat_sync_ex(messages: list[dict], user_input: str = "", agent: dict | None = None,
                        timeout: float = 0, max_tokens: int | None = None,
                        temperature: float | None = None) -> dict:
    """路由感知的便捷封装：自动按 user_input/agent 选档并调用。等价 chat_sync_ex(None, ...)。"""
    return chat_sync_ex(None, messages, timeout, max_tokens=max_tokens,
                        temperature=temperature, user_input=user_input, agent=agent)


def _probe_one(mid: str, timeout: float) -> dict:
    """体检单个模型（probe_all 的并发单元）。"""
    row = {"model": mid}
    try:
        r = httpx.post(_endpoint(),
                       json=_payload(mid, [{"role": "user", "content": "只回复两个字：通了"}],
                                     max_tokens=16, temperature=0),
                       headers=_headers(), timeout=timeout)
        ok = r.status_code == 200
        row.update({"ok": ok, "status": r.status_code,
                    "hint": "" if ok else diagnose(r.status_code, r.text),
                    "error": "" if ok else r.text[:200]})
        if ok:
            _mark_ok(mid, recall=False)   # 体检不改"下次优先用谁"
        else:
            _mark_fail(mid, classify_status(r.status_code, r.text), r.text[:200])
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        row.update({"ok": False, "status": 0, "error": msg[:200],
                    "hint": "网络不可达：检查网络/代理"})
        _mark_fail(mid, "network" if isinstance(exc, httpx.ConnectError) else "timeout", msg)
    return row


def probe_all(timeout: float = 30.0) -> dict:
    """逐个体检整条链（含冷却中的）：谁还有额度、谁今天用完了。

    和 probe 的区别：probe 命中第一个能答的就停（"用户能不能用"）；
    这个是"池子体检"——免费额度是按模型算的，得知道还剩几个能顶。
    每个模型只花 16 token，成本可忽略；结果同时刷新冷却状态。
    并发打（最多 6 个）：串行时 6 个模型要等近半分钟，按钮体验太差。
    """
    t0 = time.time()
    info = access_mode()
    targets: list = []
    for m in [settings.LLM_DEFAULT_MODEL, *chain_models()]:
        if m and m not in targets and is_chat_model(m):
            targets.append(m)
    targets = targets[:8]
    results: list = []
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(6, max(1, len(targets)))) as pool:
            results = list(pool.map(lambda m: _probe_one(m, timeout), targets))
    except Exception:
        # 并发池出问题不该让体检整体失败：退回串行
        results = [_probe_one(m, timeout) for m in targets]
    alive = [r["model"] for r in results if r.get("ok")]
    return {"ok": bool(alive), "alive": alive,
            "dead": [r["model"] for r in results if not r.get("ok")],
            "results": results, "tested": len(results),
            "latency_ms": int((time.time() - t0) * 1000), **info}


def probe(model_id: str = "", timeout: float = 30.0) -> dict:
    """真实生成探活：花 1 次极短调用换取确定结论（给运维与验收脚本用）。

    与 llm_available 的区别：那个只判断"通道是否配置好"，这个判断"模型是否真能答"。
    M51：按轮换链逐个试，返回第一个能答的模型 + 完整 attempts，
    这样"保存并测试"一次就能看出池子里谁还活着。
    """
    requested = model_id or settings.LLM_DEFAULT_MODEL
    t0 = time.time()
    info = access_mode()
    candidates = rotation_order(requested) or [requested]
    attempts: list = []
    last = {"ok": False, "status_code": 0, "reply": "", "error": "", "hint": ""}
    for mid in candidates:
        try:
            r = httpx.post(_endpoint(),
                           json=_payload(mid, [{"role": "user", "content": "只回复两个字：通了"}],
                                         max_tokens=16, temperature=0),
                           headers=_headers(), timeout=timeout)
            ok = r.status_code == 200
            text = ""
            if ok:
                try:
                    text = _extract(r.json())[0][:20]
                except Exception:
                    text = ""
            hint = "" if ok else diagnose(r.status_code, r.text)
            attempts.append({"model": mid, "status": r.status_code, "ok": ok,
                             "error": "" if ok else r.text[:200]})
            if ok:
                _mark_ok(mid)
                return {"ok": True, "model_id": mid, "requested": requested,
                        "status_code": 200, "reply": text, "error": "", "hint": "",
                        "rotated": mid != requested, "attempts": attempts,
                        "latency_ms": int((time.time() - t0) * 1000), **info}
            _mark_fail(mid, classify_status(r.status_code, r.text), r.text[:200])
            last = {"ok": False, "status_code": r.status_code, "reply": "",
                    "error": r.text[:300], "hint": hint}
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            attempts.append({"model": mid, "status": 0, "ok": False, "error": msg[:200]})
            _mark_fail(mid, "network" if isinstance(exc, httpx.ConnectError) else "timeout", msg)
            last = {"ok": False, "status_code": 0, "reply": "", "error": msg,
                    "hint": "网络不可达：检查网络/代理"}
    return {"ok": False, "model_id": requested, "requested": requested,
            "attempts": attempts, "rotated": False,
            **{k: v for k, v in last.items() if k != "ok"},
            "latency_ms": int((time.time() - t0) * 1000), **info}


def _stub_answer(model_id: str, messages: list[dict]) -> str:
    user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    # 摘要取「对话记忆 / 用户问题」，不再硬截 prompt 前 80 字。
    # M25 起检索证据带正文和编号，前 80 字全被证据块占满——stub 文本既看不出
    # 用户问了什么，也把会话记忆挤没了（M4 的「答案含首轮关键词」就靠这段）。
    # stub 只是占位，但占位也要像一句回答，不该把系统提示词回显给用户。
    lines = user.splitlines()
    parts = []
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith(("对话记忆：", "用户问题：")):
            parts.append(s[:160])
        elif s.startswith("本次任务输入：") and i + 1 < len(lines):
            # 技能包把任务输入另起一行，别只截到标题就把用户的话丢了
            parts.append("本次任务输入：" + lines[i + 1].strip()[:120])
    digest = " / ".join(parts) if parts else user[:80]
    return f"[stub:{model_id}] 已基于授权上下文作答：{digest}"[:400]
