"""P1-10 · 多模态图像理解（可插拔通道 + 配置开关）。

分工：
- **本模块**只负责「图片 → 结构化描述」：校验、取字节、组装 OpenAI 兼容的多模态
  messages、调用视觉通道、把结论转成可注入 `file_context` 的文本。
- **主对话**不改 messages 结构：视觉结论以文本形式并入附件上下文，因此
  `agent_runtime` / `model_gateway` / 语义缓存 / 计量链路全部保持原样。

四条红线：
1. **默认零行为变更**：`VISION_ENABLED=0` 时不读文件、不联网、不产生任何结论，
   路由也只回一个 `reason=disabled` 的结构化结果。
2. **绝不伪造识别结论**：stub / 未配置 / 调用失败 / 空正文一律 `ok=False`、
   `text=""`、`error` 带出。宁可说「没看懂」，也不让占位文字被当成图片事实。
3. **权限同口径**：沿用 `file_store.can_access`，越权与不存在统一 404，
   不给出可枚举信号；图片字节不出服务端（base64 只在本进程内构造）。
4. **图片不进表格解析器**：`tabular.parse` 会把二进制读成乱码再塞进上下文，
   所以调用方必须先把图片从 `file_ids` 里摘出来（见 routers/invoke.py）。

可插拔：`register_provider(name, factory)` 注册通道，`set_provider(instance)` 直接注入
实例（测试/私有化部署用）。内置 `stub`（默认）与 `openai_compatible`（OpenAI 兼容
`/chat/completions` + `image_url` data URL）。接真模型不需要改本文件以外的任何代码。
"""
from __future__ import annotations

import base64
import time
from typing import Any, Callable, Dict, List, Optional

import httpx
from fastapi import HTTPException

from app.core.config import settings
from app.services import file_store

# ── 图片类型识别 ──────────────────────────────────────────────────────────────
IMAGE_EXT = {"png", "jpg", "jpeg", "webp", "gif", "bmp"}

MIME_BY_EXT = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "bmp": "image/bmp",
}

# 魔数校验：只认文件头，不认扩展名。把 .txt 改名成 .png 混过白名单，
# 结果是 base64 一堆文本塞给视觉模型，既费钱又可能触发奇怪的解析行为。
_MAGIC: List[tuple] = [
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"BM", "bmp"),
]


def ext_of(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""


def is_image(filename: str) -> bool:
    """按扩展名判定（用于把图片从表格附件里分流，不读字节）。"""
    return ext_of(filename or "") in IMAGE_EXT


def sniff(data: bytes) -> str:
    """返回真实图片格式；不可识别返回空串。WEBP 需要同时校验 RIFF 与 WEBP 标记。"""
    if not data:
        return ""
    for magic, kind in _MAGIC:
        if data.startswith(magic):
            return kind
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return ""


def mime_of(fmt: str, filename: str = "") -> str:
    return MIME_BY_EXT.get(fmt) or MIME_BY_EXT.get(ext_of(filename)) or "application/octet-stream"


DEFAULT_PROMPT = (
    "请客观描述这张/这些图片中可核实的内容：主体、文字、表格字段与数值、图表趋势、"
    "印章或标识、明显异常。只写图上确实看得见的信息；看不清或不确定的地方明确写"
    "「无法辨认」，不要推测、不要补充图中不存在的背景。"
)

SYSTEM_PROMPT = (
    "你是企业资料核验助手。你的输出会作为「图像解读」进入业务分析的上下文，"
    "因此必须区分「图上可见的事实」与「你的推断」。禁止编造数字、日期、单位、姓名。"
)


# ── 可插拔通道 ────────────────────────────────────────────────────────────────
def _base_result(provider: str = "stub", model: str = "") -> Dict[str, Any]:
    return {"ok": False, "text": "", "real": False, "provider": provider,
            "model": model, "usage": {}, "error": ""}


class StubProvider:
    """默认通道：明确报「不可用」，不产出任何看起来像识别结果的内容。"""

    name = "stub"
    model = ""

    def describe(self, parts: List[dict], prompt: str) -> Dict[str, Any]:
        r = _base_result("stub")
        r["error"] = "视觉通道未配置（VISION_PROVIDER=stub 或缺少 VISION_MODEL）"
        return r


class OpenAICompatibleProvider:
    """OpenAI 兼容视觉通道：`messages[].content` 为 [text, image_url...] 列表。

    与 model_gateway 的差别只有两点：走独立端点/模型、消息体带 data URL 图片。
    不复用 `chat_sync_ex` 是因为它会做模型路由、轮换、stub 回退——视觉模型不该
    被文本模型的轮换链接管，更不能失败时拿 stub 文本冒充「看懂了图」。
    """

    name = "openai_compatible"

    def __init__(self, model: str = "", base_url: str = "", api_key: str = "",
                 timeout: float = 0, max_tokens: int = 0):
        self.model = (model or settings.VISION_MODEL or "").strip()
        self.base_url = (base_url or settings.VISION_BASE_URL
                         or settings.LLM_BASE_URL or "").strip()
        self.api_key = (api_key or settings.VISION_API_KEY
                        or settings.LLM_API_KEY or "").strip()
        self.timeout = float(timeout or settings.VISION_TIMEOUT_S or 60)
        self.max_tokens = int(max_tokens or settings.VISION_MAX_TOKENS or 1200)

    def endpoint(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions" if base else ""

    def describe(self, parts: List[dict], prompt: str) -> Dict[str, Any]:
        r = _base_result(self.name, self.model)
        endpoint = self.endpoint()
        if not endpoint or not self.model:
            r["error"] = "视觉通道缺少 VISION_BASE_URL 或 VISION_MODEL 配置"
            return r
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": [{"type": "text", "text": prompt}, *parts]},
            ],
            "max_tokens": self.max_tokens,
            "temperature": 0,
        }
        try:
            resp = httpx.post(endpoint, json=body, headers=headers, timeout=self.timeout)
            if resp.status_code >= 400:
                r["error"] = f"HTTP {resp.status_code}: {resp.text[:200]}"
                return r
            data = resp.json()
            msg = ((data.get("choices") or [{}])[0].get("message") or {})
            text = msg.get("content") or ""
            # 少数厂商把正文拆成 [{"type":"text","text":...}] 分块
            if isinstance(text, list):
                text = "".join(str(p.get("text") or "")
                               for p in text if isinstance(p, dict))
            if not str(text).strip():
                r["error"] = "视觉模型响应正文为空（可能被 max_tokens 截断或触发内容策略）"
                return r
            u = data.get("usage") or {}
            r.update({"ok": True, "text": str(text).strip(), "real": True,
                      "model": data.get("model") or self.model,
                      "usage": {"prompt_tokens": int(u.get("prompt_tokens") or 0),
                                "completion_tokens": int(u.get("completion_tokens") or 0)}})
            return r
        except Exception as exc:
            r["error"] = f"{type(exc).__name__}: {exc}"[:300]
            return r


_PROVIDERS: Dict[str, Callable[[], Any]] = {
    "stub": StubProvider,
    "openai_compatible": OpenAICompatibleProvider,
}
_OVERRIDE: Optional[Any] = None


def register_provider(name: str, factory: Callable[[], Any]) -> None:
    """注册/替换一个通道工厂（可插拔扩展点）。"""
    _PROVIDERS[str(name).strip().lower()] = factory


def set_provider(instance: Any) -> None:
    """直接注入通道实例（测试桩 / 私有化部署自定义通道）。"""
    global _OVERRIDE
    _OVERRIDE = instance


def clear_provider() -> None:
    global _OVERRIDE
    _OVERRIDE = None


def get_provider() -> Any:
    if _OVERRIDE is not None:
        return _OVERRIDE
    name = (getattr(settings, "VISION_PROVIDER", "stub") or "stub").strip().lower()
    factory = _PROVIDERS.get(name)
    if factory is None:
        return StubProvider()
    try:
        instance = factory()
    except Exception:
        return StubProvider()
    # 配了 openai_compatible 却没给模型名，不能算「已就绪」
    if name == "openai_compatible" and not (getattr(settings, "VISION_MODEL", "") or "").strip():
        return StubProvider()
    return instance


def status() -> Dict[str, Any]:
    """通道状态（前端据此决定要不要放开图片上传）。"""
    p = get_provider()
    provider = getattr(p, "name", "stub")
    model = getattr(p, "model", "") or ""
    enabled = bool(getattr(settings, "VISION_ENABLED", False))
    if not enabled:
        reason = "图像理解未启用（VISION_ENABLED=0）"
    elif provider == "stub":
        reason = "视觉通道未配置（需 VISION_PROVIDER=openai_compatible 与 VISION_MODEL）"
    elif not model:
        reason = "视觉模型名未配置（VISION_MODEL）"
    else:
        reason = ""
    return {
        "enabled": enabled,
        "ready": enabled and provider != "stub" and bool(model) and not reason,
        "provider": provider,
        "model": model,
        "reason": reason,
        "max_images": int(getattr(settings, "VISION_MAX_IMAGES", 4)),
        "max_image_bytes": int(getattr(settings, "VISION_MAX_IMAGE_BYTES", 5 * 1024 * 1024)),
        "max_prompt_chars": int(getattr(settings, "VISION_MAX_PROMPT_CHARS", 2000)),
        "timeout_s": float(getattr(settings, "VISION_TIMEOUT_S", 60)),
        "allowed_ext": sorted(IMAGE_EXT),
        "registered_providers": sorted(_PROVIDERS),
        "override_active": _OVERRIDE is not None,
    }


def reset_state() -> None:
    """测试用：清掉注入的通道与自定义注册表，恢复内置两个通道。"""
    global _OVERRIDE, _PROVIDERS
    _OVERRIDE = None
    _PROVIDERS = {"stub": StubProvider, "openai_compatible": OpenAICompatibleProvider}


# ── 图片准备 / 分析 ───────────────────────────────────────────────────────────
def prepare(file_ids: List[str], caller: Any) -> Dict[str, Any]:
    """把可见图片读成 OpenAI 兼容的 `image_url` parts。

    单份图片失败只记 error 并跳过，不阻断其它图片；但**越权/不存在直接 404**
    （与 invoke._load_attachments 同口径，避免用错误信息枚举他人文件）。
    """
    out: Dict[str, Any] = {"parts": [], "images": [], "errors": [],
                           "total_bytes": 0, "skipped": 0}
    max_images = max(1, int(getattr(settings, "VISION_MAX_IMAGES", 4)))
    max_bytes = max(1, int(getattr(settings, "VISION_MAX_IMAGE_BYTES", 5 * 1024 * 1024)))
    for fid in list(file_ids or []):
        rec = file_store.get(fid)
        if not rec or not file_store.can_access(rec, caller):
            raise HTTPException(status_code=404, detail=f"文件不存在或无权访问：{fid}")
        name = rec.get("filename") or ""
        if not is_image(name):
            out["errors"].append({"file_id": fid, "filename": name,
                                  "error": "非图片文件，已按普通附件处理"})
            continue
        if len(out["images"]) >= max_images:
            out["skipped"] += 1
            out["errors"].append({"file_id": fid, "filename": name,
                                  "error": f"图片数量超过上限 {max_images}，已跳过"})
            continue
        data = file_store.read(rec)
        if data is None:
            out["errors"].append({"file_id": fid, "filename": name,
                                  "error": "文件内容已丢失（对象存储不可达或已清理）"})
            continue
        fmt = sniff(data)
        if not fmt:
            out["errors"].append({"file_id": fid, "filename": name,
                                  "error": "文件内容不是可识别的图片格式（扩展名与文件头不符）"})
            continue
        if len(data) > max_bytes:
            out["errors"].append({"file_id": fid, "filename": name,
                                  "error": f"图片 {len(data)} 字节超过上限 {max_bytes}，已跳过"})
            continue
        mime = mime_of(fmt, name)
        b64 = base64.b64encode(data).decode("ascii")
        out["parts"].append({"type": "image_url",
                             "image_url": {"url": f"data:{mime};base64,{b64}"}})
        out["images"].append({"file_id": fid, "filename": name, "size": len(data),
                              "mime": mime, "format": fmt})
        out["total_bytes"] += len(data)
    return out


def analyze(file_ids: List[str], caller: Any, prompt: str = "") -> Dict[str, Any]:
    """一站式图像理解：状态判定 → 取图 → 调通道 → 结构化结果。

    任何失败路径 `text` 都是空串，`error` 写明原因——前端据此显示「未产出结论」，
    不把降级文案混进业务上下文。
    """
    st = status()
    q = (prompt or "").strip()[:int(getattr(settings, "VISION_MAX_PROMPT_CHARS", 2000))] or DEFAULT_PROMPT
    base: Dict[str, Any] = {
        "ok": False, "reason": "", "text": "", "real": False,
        "provider": st["provider"], "model": st["model"],
        "images": [], "errors": [], "total_bytes": 0, "usage": {},
        "error": "", "prompt": q, "elapsed_ms": 0, "status": st,
    }
    def _finish(d: Dict[str, Any]) -> Dict[str, Any]:
        """把「会进入主对话上下文的那段文本」一并回给前端，便于人工核对口径。"""
        if d.get("ok"):
            d["context"] = build_context(d)
        elif d.get("reason") in ("unavailable", "no_images", "provider_error"):
            d["context"] = build_failure_hint(d)
        else:
            d["context"] = ""
        return d

    if not st["enabled"]:
        base.update({"reason": "disabled", "error": st["reason"]})
        return _finish(base)
    prep = prepare(file_ids, caller)
    base.update({"images": prep["images"], "errors": prep["errors"],
                 "total_bytes": prep["total_bytes"]})
    if not prep["parts"]:
        base.update({"reason": "no_images",
                     "error": "没有可识别的图片（未传 file_ids，或图片全部被校验跳过）"})
        return _finish(base)
    p = get_provider()
    t0 = time.time()
    try:
        res = p.describe(prep["parts"], q) or {}
    except Exception as exc:
        res = {"ok": False, "text": "", "real": False,
               "error": f"provider_error: {type(exc).__name__}: {exc}"[:300]}
    base["elapsed_ms"] = int((time.time() - t0) * 1000)
    text = str(res.get("text") or "").strip()
    ok = bool(res.get("ok")) and bool(text) and bool(res.get("real"))
    base.update({
        "ok": ok,
        "reason": "ok" if ok else "unavailable",
        "text": text if ok else "",
        "real": bool(res.get("real")) and ok,
        "provider": res.get("provider") or st["provider"],
        "model": res.get("model") or st["model"],
        "usage": res.get("usage") or {},
        "error": "" if ok else (res.get("error") or "视觉通道未产出结论"),
    })
    return _finish(base)


def build_context(result: Dict[str, Any]) -> str:
    """把识别结论转成可并入 `file_context` 的段落；失败时返回空串（不注入噪声）。

    明确标注「模型解读」而非原始事实，避免主模型把 OCR 猜测当成用户提供的凭据。
    """
    if not result or not result.get("ok") or not (result.get("text") or "").strip():
        return ""
    names = "、".join(i.get("filename") or i.get("file_id") or ""
                     for i in (result.get("images") or []))
    return (
        "【图像理解结果】以下内容由视觉模型对本次随附图片生成，属「模型解读」而非原始事实，"
        f"引用时须标注来源并核对原件；图片：{names or '（未记录）'}。\n"
        + result["text"].strip()
    )


def build_failure_hint(result: Dict[str, Any]) -> str:
    """识别失败时注入的「缺料提示」。

    不能静默当作没传过图：主模型看不到图片却要回答，最容易编造图上数字。
    明确告诉它「图在、但没读出来」，才能触发 M55「缺料必拒」的既有硬规则。
    """
    if not result or result.get("ok"):
        return ""
    reason = str(result.get("error") or result.get("reason") or "未知原因")[:200]
    names = "、".join(i.get("filename") or i.get("file_id") or ""
                     for i in (result.get("images") or []))
    return (
        "【图像理解未产出结论】本次随附图片"
        f"（{names or '已挂载'}）未能完成识别，原因：{reason}。"
        "当前上下文不含任何图片解读，不得据此推断图片内容、数字或文字；"
        "若结论依赖图片信息，必须明确提示用户人工核对原件或重新上传。"
    )
