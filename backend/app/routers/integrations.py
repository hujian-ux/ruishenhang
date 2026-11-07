"""IM/OA 集成路由（P1-173）：企业微信 / 钉钉 对接入口。

能力：
- 租户级 IM 配置（corp_id/secret/agent_id/webhook_token 等），密钥脱敏。
- 企微/钉钉回调：验签（真实官方公式）+ 解析入站（钉钉 JSON 直解；企微 XML/AES 占位）。
- 主动下发文本消息、审批回推 OA 卡片。
opt-in：未配置则该租户 IM 不可用，不影响其它功能。全部需平台管理员或调用方所在租户。
"""
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.core.config import settings
from app.deps import get_caller
from app.models.caller import CallerContext
from app.services import approval_store, im_gateway, im_store

router = APIRouter(prefix="/integrations", tags=["integrations"])


class ImConfigRequest(BaseModel):
    provider: str  # wecom | dingtalk
    corp_id: str = ""      # 企微 CorpID / 钉钉 AppKey
    secret: str = ""       # 企微 CorpSecret / 钉钉 AppSecret
    agent_id: str = ""     # 企微 AgentId
    webhook_token: str = ""  # 回调签名 token（企微）/ 加签密钥（钉钉）
    encoding_aes_key: str = ""  # 企微消息 AES 解密密钥（可选）


@router.post("/im/config")
def set_im_config(req: ImConfigRequest, caller: CallerContext = Depends(get_caller)):
    """保存某租户的 IM 配置（仅平台管理员）。密钥仅存内存、list 时脱敏。"""
    if caller is None or caller.role != "platform_admin":
        raise HTTPException(403, "需要平台管理员权限")
    masked = im_store.save_config(
        caller.tenantId, req.provider, corp_id=req.corp_id, secret=req.secret,
        agent_id=req.agent_id, webhook_token=req.webhook_token,
        encoding_aes_key=req.encoding_aes_key,
    )
    return {"code": 0, "data": masked}


@router.get("/im/config")
def list_im_config(caller: CallerContext = Depends(get_caller)):
    if caller is None or caller.role != "platform_admin":
        raise HTTPException(403, "需要平台管理员权限")
    return {"code": 0, "data": im_store.list_configs(caller.tenantId)}


@router.delete("/im/config/{provider}")
def delete_im_config(provider: str, caller: CallerContext = Depends(get_caller)):
    if caller is None or caller.role != "platform_admin":
        raise HTTPException(403, "需要平台管理员权限")
    ok = im_store.delete_config(caller.tenantId, provider)
    return {"code": 0, "data": {"deleted": ok}}


# ── 企微回调 ────────────────────────────────────────────────────────────────
@router.get("/im/wecom/callback", response_class=PlainTextResponse)
def wecom_callback_verify(
    msg_signature: str, timestamp: str, nonce: str, echostr: str,
    tenant: str = Query(default=settings.DEFAULT_TENANT),
):
    """企微 URL 验证：签名相符原样回写 echostr，否则空。"""
    out = im_gateway.gateway.verify(
        tenant, "wecom", msg_signature=msg_signature, timestamp=timestamp,
        nonce=nonce, echostr=echostr,
    )
    return PlainTextResponse(out or "")


@router.post("/im/wecom/callback", response_class=PlainTextResponse)
async def wecom_callback_post(
    request: Request,
    msg_signature: str, timestamp: str, nonce: str,
    tenant: str = Query(default=settings.DEFAULT_TENANT),
):
    """企微消息回调：验签 + 解析（AES 解密需 EncodingAESKey，未配则占位）。"""
    body = (await request.body()).decode("utf-8", "ignore")
    enc = re.search(r"<Encrypt><!\[CDATA\[(.*?)\]\]></Encrypt>", body)
    encrypt = enc.group(1) if enc else body
    ok = im_gateway.gateway.verify(
        tenant, "wecom", msg_signature=msg_signature, timestamp=timestamp,
        nonce=nonce, encrypt=encrypt,
    )
    if not ok:
        raise HTTPException(401, "企微回调签名校验失败")
    parsed = im_gateway.WeComAdapter("", "", "", "").parse_inbound(encrypt)
    return PlainTextResponse("success")  # 企微要求 200 + 非空响应


# ── 钉钉回调 ────────────────────────────────────────────────────────────────
@router.post("/im/dingtalk/callback")
async def dingtalk_callback(
    request: Request,
    timestamp: str, nonce: str, signature: str,
    tenant: str = Query(default=settings.DEFAULT_TENANT),
):
    """钉钉事件回调：加签校验 + 归一化解析。"""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    ok = im_gateway.gateway.verify(
        tenant, "dingtalk", timestamp=timestamp, nonce=nonce, signature=signature,
    )
    if not ok:
        raise HTTPException(401, "钉钉回调签名校验失败")
    parsed = im_gateway.DingTalkAdapter("", "", "").parse_inbound(payload)
    return {"code": 0, "data": parsed}


# ── 主动下发 ────────────────────────────────────────────────────────────────
class ImSendRequest(BaseModel):
    provider: str
    to_user: str
    text: str


@router.post("/im/send")
def im_send(req: ImSendRequest, caller: CallerContext = Depends(get_caller)):
    """向某租户的 IM 下发文本消息（需平台管理员）。"""
    if caller is None or caller.role != "platform_admin":
        raise HTTPException(403, "需要平台管理员权限")
    try:
        return {"code": 0, "data": im_gateway.gateway.send(
            caller.tenantId, req.provider, req.to_user, req.text)}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class ImApprovalRequest(BaseModel):
    provider: str
    to_user: str
    title: str = ""
    url: str = ""
    summary: str = ""            # 可选：自定义卡片摘要（覆盖自动摘要）
    approval_id: str = ""        # 可选：传审批单号 → 后端自动解析标题/链接/助手摘要
    base_url: str = ""           # 可选：审批详情页绝对前缀；缺省回退 APPROVAL_UI_BASE_URL


@router.post("/im/notify-approval")
def im_notify_approval(req: ImApprovalRequest, caller: CallerContext = Depends(get_caller)):
    """审批回推 OA：把待审批推到 IM（需平台管理员）。

    - 传 ``approval_id``：后端按单据号自动解析标题、详情链接与审批助手摘要（推荐）。
    - 仅传 ``title/url/summary``：沿用旧的纯手动模式（向后兼容）。
    最终审批始终由管理员在审批台 commit，本接口只做回推展示。
    """
    if caller is None or caller.role != "platform_admin":
        raise HTTPException(403, "需要平台管理员权限")
    try:
        if req.approval_id:
            base = req.base_url or settings.APPROVAL_UI_BASE_URL
            return {"code": 0, "data": im_gateway.gateway.push_approval_with_assistant(
                caller.tenantId, req.provider, req.to_user, req.approval_id, base)}
        if not req.title:
            raise HTTPException(400, "未传 approval_id 时 title 必填")
        return {"code": 0, "data": im_gateway.gateway.notify_approval(
            caller.tenantId, req.provider, req.to_user, req.title, req.url,
            summary=req.summary)}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except approval_store.TicketNotFound:
        raise HTTPException(404, f"审批单不存在：{req.approval_id}") from None
