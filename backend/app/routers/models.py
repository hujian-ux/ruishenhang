"""models 路由：可插拔模型注册表（第25章）。

M7 管理页支撑：列表带网关健康状态（LiteLLM 是否可达 + 当前默认模型），
并支持平台管理员启停模型（下线某个模型不需改业务代码）。
"""
from fastapi import APIRouter, Depends, HTTPException, Query
import os

from app.core.config import settings
from app.deps import get_caller
from app.models.caller import CallerContext
from app.models.schemas import (
    ModelListResponse, ModelRegisterRequest, ModelRegisterResponse,
)
from app.services import model_gateway, runtime_config

router = APIRouter(prefix="/models", tags=["models"])


def _gateway_status() -> dict:
    mode = model_gateway.access_mode()
    return {
        "available": model_gateway.llm_available(),
        "base_url": mode["base_url"],
        "default_model": settings.LLM_DEFAULT_MODEL,
        # M17：一眼看出跑的是 litellm 代理还是直连厂商、key 到底配没配
        "provider": mode["provider"],
        "vendor": mode["vendor"],
        "vendor_label": mode["vendor_label"],
        "key_configured": mode["key_configured"],
        "key_hint": mode["key_hint"],
        "strict": settings.LLM_STRICT,
    }


def _config_view() -> dict:
    """配置页要的全部信息：当前通道 + 脱敏后的密钥 + 厂商预设 + 路由档位。"""
    mode = model_gateway.access_mode()
    return {
        **mode,
        "model": settings.LLM_DEFAULT_MODEL,
        "temperature": settings.LLM_TEMPERATURE,
        "max_tokens": settings.LLM_MAX_TOKENS,
        # 密钥只回显前 4 后 4：够确认"配的是哪一串"，又不至于在截图里泄露
        "key_masked": runtime_config.mask(model_gateway.current_key()),
        "presets": [
            {"id": k, "label": v.get("label", k), "base_url": v.get("base_url", ""),
             "models": v.get("models") or [], "small_model": v.get("small_model", ""),
             "key_env": v.get("key_env", "")}
            for k, v in model_gateway.PROVIDER_PRESETS.items()
        ],
        "routing": {
            "enabled": bool(settings.ROUTING_ENABLED),
            "small": settings.ROUTING_SMALL_MODEL,
            "large": settings.ROUTING_LARGE_MODEL,
        },
        "registered": [m.get("model_id") for m in model_gateway.registry_snapshot()],
        # 让用户知道密钥写到哪：私有化部署排查"改了重启为啥没生效"就看这个文件
        "env_file": str(runtime_config.env_local_path()),
    }


@router.get("/config")
async def get_model_config(caller: CallerContext = Depends(get_caller)):
    """当前模型接入配置（密钥脱敏）。登录即可读，写入需平台管理员。"""
    return {"code": 0, "data": _config_view()}


@router.put("/config")
async def put_model_config(payload: dict, caller: CallerContext = Depends(get_caller)):
    """在线配置模型通道（仅平台管理员）：改完即时生效，并落盘 .env.local 重启不丢。

    支持两条通道：`direct`（直连厂商 OpenAI 兼容端点）与 `litellm`（自建代理）。
    api_key 留空 = 不修改现有密钥（避免"只想换个模型名"却把 key 清空）。
    """
    if caller.role != "platform_admin":
        raise HTTPException(status_code=403, detail="forbidden: 仅平台管理员可配置模型")

    body = payload or {}
    provider = str(body.get("provider") or settings.LLM_PROVIDER).strip().lower()
    if provider not in ("direct", "litellm"):
        raise HTTPException(status_code=422, detail="provider 须为 direct 或 litellm")

    model = str(body.get("model") or "").strip()
    if not model:
        raise HTTPException(status_code=422, detail="model 不能为空（模型名写错会直接 404）")

    updates: dict = {"LLM_PROVIDER": provider, "LLM_DEFAULT_MODEL": model}

    if provider == "direct":
        vendor = str(body.get("vendor") or settings.LLM_VENDOR or "zhipu").strip().lower()
        preset = model_gateway.PROVIDER_PRESETS.get(vendor)
        if preset is None:
            raise HTTPException(
                status_code=422,
                detail=f"未知厂商：{vendor}（可选 {', '.join(model_gateway.PROVIDER_PRESETS)}）")
        base_url = str(body.get("base_url") or "").strip() or preset.get("base_url", "")
        if not base_url:
            # 自建/私有化必须给 base_url，否则请求会打到空地址，报错信息很难懂
            raise HTTPException(status_code=422, detail="厂商为「自建/私有化」时必须填写 base_url")
        updates["LLM_VENDOR"] = vendor
        updates["LLM_BASE_URL"] = base_url
        key_env = preset.get("key_env") or ""
        filled = str(body.get("api_key") or "").strip() or os.environ.get(key_env, "")
        if key_env and key_env != "LLM_API_KEY" and filled:
            # 同时写厂商惯用变量名：别的脚本/探活工具按 ZHIPU_API_KEY 之类读也能读到
            updates[key_env] = filled
    else:
        if str(body.get("base_url") or "").strip():
            updates["LITELLM_BASE_URL"] = str(body["base_url"]).strip()

    ak = str(body.get("api_key") or "").strip()
    if ak:
        updates["LLM_API_KEY"] = ak
        if provider == "litellm":
            updates["LITELLM_MASTER_KEY"] = ak

    # 成本路由：两档默认对齐到同一个模型。不这么做的话，路由会把请求改写成
    # 旧的 glm-4.6/glm-4-flash，换别家 key 一调就是 401 —— 用户以为"模型配不上"。
    routing = body.get("routing") or {}
    if "enabled" in routing:
        updates["ROUTING_ENABLED"] = "1" if routing.get("enabled") else "0"
    small = str(routing.get("small") or "").strip()
    large = str(routing.get("large") or "").strip()
    updates["ROUTING_SMALL_MODEL"] = small or model
    updates["ROUTING_LARGE_MODEL"] = large or model

    for k in ("temperature", "max_tokens"):
        v = body.get(k)
        if v is not None and str(v).strip() != "":
            updates["LLM_TEMPERATURE" if k == "temperature" else "LLM_MAX_TOKENS"] = v

    runtime_config.apply(updates)
    runtime_config.persist(updates)
    # 注册表里没有就先登记，否则「模型注册表」和成本报表里查不到这个模型
    model_gateway.ensure_registered(model, updates.get("LLM_VENDOR", "litellm"))
    # 清掉 30s 探活缓存，让状态灯立刻反映新配置
    model_gateway.llm_available(force=True)

    return {"code": 0, "data": {"config": _config_view(),
                               "gateway": _gateway_status(),
                               "changed_by": caller.userId}}



@router.get("", response_model=ModelListResponse)
async def list_models(status: str = Query("active"), caller: CallerContext = Depends(get_caller)):
    return {"code": 0, "data": {"models": model_gateway.list_models(status),
                                "gateway": _gateway_status()}}


@router.get("/gateway")
async def gateway_health(caller: CallerContext = Depends(get_caller)):
    """模型网关探活（页面顶部状态灯）；force 重新探测不读缓存。"""
    return {"code": 0, "data": {**_gateway_status(), "probe": "live"}}


@router.get("/probe")
async def probe_model(model_id: str = Query("", description="留空则测默认模型"),
                      caller: CallerContext = Depends(get_caller)):
    """真生成探活：花一次极短调用换确定结论（仅平台管理员）。

    和 /gateway 的区别：/gateway 只看"通道配好了没"，这里判断"模型真能答"。
    验收失败时先打这个接口，401/429/模型名不存在会原样回显，省去翻日志。
    """
    if caller.role != "platform_admin":
        raise HTTPException(status_code=403, detail="forbidden: 仅平台管理员可做真生成探活")
    return {"code": 0, "data": model_gateway.probe(model_id)}


@router.patch("/{model_id}")
async def patch_model(model_id: str, body: dict, caller: CallerContext = Depends(get_caller)):
    """启停模型（仅平台管理员）。body: {"status": "active"|"inactive"}"""
    if caller.role != "platform_admin":
        raise HTTPException(status_code=403, detail="forbidden: 仅平台管理员可启停模型")
    status = (body or {}).get("status")
    if status not in ("active", "inactive"):
        raise HTTPException(status_code=422, detail="status 必须为 active 或 inactive")
    try:
        m = model_gateway.set_status(model_id, status)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"模型不存在：{model_id}")
    return {"code": 0, "data": {"model": m, "changed_by": caller.userId}}


@router.post("", response_model=ModelRegisterResponse)
async def register_model(req: ModelRegisterRequest, caller: CallerContext = Depends(get_caller)):
    if caller.role != "platform_admin":
        raise HTTPException(status_code=403, detail="forbidden: 仅平台管理员可注册模型")
    return {"code": 0, "data": model_gateway.register_model(req.model_dump())}
