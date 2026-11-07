"""IM/OA 消息网关（P1-173）：企业微信 / 钉钉 收发适配器 + 审批回推 OA。

设计原则：
- 零厂商 SDK 依赖：直接走标准 REST + 官方签名公式，避免锁死版本。
- 签名校验用官方公式，确定性、可单测（不触网）。
- 网络调用（get_access_token / send）通过模块级 ``_http_post`` 注入，测试可替换为假实现。
- 凭据来自 im_store（租户级），opt-in：未配置则返回明确的「未配置」而非报错崩链。
- 消息体 AES 解密（企微回调）需 EncodingAESKey，本模块提供签名校验真逻辑；
  AES 解密给出清晰占位与指引，不谎称已实现。
"""
import base64
import hashlib
import hmac
import json
from typing import Any, Callable, Dict, Optional

import httpx

from app.services import im_store

# 可注入的 HTTP POST（测试替换为假实现）。签名校验不依赖它。
_http_post: Callable[[str, dict, dict], dict] = lambda url, data, headers: (
    httpx.post(url, data=data, headers=headers, timeout=5).json()
)


def set_http_post(fn: Callable[[str, dict, dict], dict]) -> None:
    """测试钩子：替换底层 HTTP POST 实现。"""
    global _http_post
    _http_post = fn


# ── 企业微信 ────────────────────────────────────────────────────────────────
class WeComAdapter:
    """企业微信适配器：回调签名校验 + 文本/卡片下发。

    回调签名（官方）：把 [token, timestamp, nonce, echostr/encrypt] 字典序排序后拼接，
    做 SHA1，得到 msg_signature。URL 验证时第 4 参是 echostr，消息回调时是 encrypt。
    """

    def __init__(self, corp_id: str, secret: str, agent_id: str,
                 token: str, encoding_aes_key: str = ""):
        self.corp_id = corp_id
        self.secret = secret
        self.agent_id = agent_id
        self.token = token
        self.encoding_aes_key = encoding_aes_key

    @staticmethod
    def _sign(*parts: str) -> str:
        s = "".join(sorted(parts))
        return hashlib.sha1(s.encode("utf-8")).hexdigest()

    def verify_url(self, msg_signature: str, timestamp: str, nonce: str,
                   echostr: str) -> Optional[str]:
        """URL 验证：签名相符则返回 echostr（原样回写），否则 None。"""
        if self._sign(self.token, timestamp, nonce, echostr) != msg_signature:
            return None
        return echostr

    def verify_message(self, msg_signature: str, timestamp: str, nonce: str,
                       encrypt: str) -> bool:
        """消息回调签名校验（AES 解密前的第一道关）。"""
        return self._sign(self.token, timestamp, nonce, encrypt) == msg_signature

    def get_access_token(self) -> str:
        data = _http_post(
            "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
            {"corpid": self.corp_id, "corpsecret": self.secret},
            {"Content-Type": "application/x-www-form-urlencoded"},
        )
        tok = data.get("access_token")
        if not tok:
            raise RuntimeError(f"企微获取 access_token 失败：{data}")
        return tok

    def send_text(self, to_user: str, text: str, access_token: str = "") -> dict:
        tok = access_token or self.get_access_token()
        body = {
            "touser": to_user, "msgtype": "text", "agentid": int(self.agent_id or 0),
            "text": {"content": text},
        }
        return _http_post(
            f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={tok}",
            body, {"Content-Type": "application/json"},
        )

    def send_approval_card(self, to_user: str, title: str, url: str,
                           access_token: str = "") -> dict:
        """审批回推 OA：用 markdown 卡片推送待审批链接（企微原生审批可后续接）。"""
        tok = access_token or self.get_access_token()
        md = f"# 待审批\n**{title}**\n\n[点击处理]({url})"
        body = {
            "touser": to_user, "msgtype": "markdown", "agentid": int(self.agent_id or 0),
            "markdown": {"content": md},
        }
        return _http_post(
            f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={tok}",
            body, {"Content-Type": "application/json"},
        )

    def parse_inbound(self, encrypt: str) -> Dict[str, Any]:
        """解析入站消息。签名已校验后调用。

        完整实现需 AES-256-CBC 解密（EncodingAESKey 派生 43 位 → 补 '=' 成 44 位 base64）。
        未配置 EncodingAESKey 时返回占位结构，不谎称已解密。
        """
        if not self.encoding_aes_key:
            return {"provider": "wecom", "raw": encrypt,
                    "note": "需要 EncodingAESKey 才能 AES 解密消息体"}
        # TODO(M27-OA)：用 EncodingAESKey 派生 key/iv，AES 解密后去 16B 随机前缀 +
        # msg_len(4B) + msg + receiveid，再 XML 解析。需 pycryptodome。
        raise NotImplementedError("企微消息 AES 解密待接入（需 EncodingAESKey + pycryptodome）")


# ── 钉钉 ────────────────────────────────────────────────────────────────────
class DingTalkAdapter:
    """钉钉适配器：事件回调加签校验（官方公式）+ 文本/卡片下发。

    加签（官方）：signature = Base64( HMAC-SHA256( key=secret, msg=timestamp+"\\n"+secret ) )。
    """

    def __init__(self, app_key: str, app_secret: str, secret: str):
        self.app_key = app_key
        self.app_secret = app_secret
        self.secret = secret  # 事件订阅「加签密钥」

    @staticmethod
    def _sign(timestamp: str, secret: str) -> str:
        string_to_sign = f"{timestamp}\n{secret}"
        return base64.b64encode(
            hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"),
                     hashlib.sha256).digest()
        ).decode("ascii")

    def verify_callback(self, timestamp: str, nonce: str, signature: str) -> bool:
        """事件回调签名校验（nonce 透传不参与计算，与官方一致）。"""
        return self._sign(timestamp, self.secret) == signature

    def get_access_token(self) -> str:
        data = _http_post(
            "https://oapi.dingtalk.com/gettoken",
            {"appkey": self.app_key, "appsecret": self.app_secret},
            {"Content-Type": "application/x-www-form-urlencoded"},
        )
        tok = data.get("access_token")
        if not tok:
            raise RuntimeError(f"钉钉获取 access_token 失败：{data}")
        return tok

    def send_text(self, to_user: str, text: str, access_token: str = "") -> dict:
        tok = access_token or self.get_access_token()
        body = {"msgtype": "text", "text": {"content": text},
                "touser": to_user}
        return _http_post(
            f"https://oapi.dingtalk.com/topapi/message/corpconversation/asyncsend_v2?access_token={tok}",
            body, {"Content-Type": "application/json"},
        )

    def send_approval_card(self, to_user: str, title: str, url: str,
                           access_token: str = "") -> dict:
        tok = access_token or self.get_access_token()
        md = f"# 待审批\n**{title}**\n\n[点击处理]({url})"
        body = {"msgtype": "markdown", "markdown": {"title": "待审批", "text": md},
                "touser": to_user}
        return _http_post(
            f"https://oapi.dingtalk.com/topapi/message/corpconversation/asyncsend_v2?access_token={tok}",
            body, {"Content-Type": "application/json"},
        )

    def parse_inbound(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """归一化入站事件。钉钉事件为 JSON（与企微 XML/AES 不同），此处直接透出关键字段。"""
        return {
            "provider": "dingtalk",
            "event_type": payload.get("EventType") or payload.get("type"),
            "sender": payload.get("senderId") or payload.get("staffId"),
            "text": payload.get("text") or "",
            "raw": payload,
        }


# ── 网关调度 ────────────────────────────────────────────────────────────────
class IMGateway:
    """按租户 + provider 取适配器并分发。"""

    def _adapter(self, tenant: str, provider: str):
        cfg = im_store.get_config(tenant, provider)
        if not cfg:
            raise ValueError(f"租户 {tenant} 未配置 {provider}（请先用 /integrations/im/config 配置）")
        if provider == "wecom":
            return WeComAdapter(
                corp_id=cfg.get("corp_id", ""), secret=cfg.get("secret", ""),
                agent_id=cfg.get("agent_id", ""), token=cfg.get("webhook_token", ""),
                encoding_aes_key=cfg.get("encoding_aes_key", ""),
            )
        if provider == "dingtalk":
            return DingTalkAdapter(
                app_key=cfg.get("corp_id", ""), app_secret=cfg.get("secret", ""),
                secret=cfg.get("webhook_token", ""),
            )
        raise ValueError(f"不支持的 provider：{provider}")

    def send(self, tenant: str, provider: str, to_user: str, text: str) -> dict:
        return self._adapter(tenant, provider).send_text(to_user, text)

    def notify_approval(self, tenant: str, provider: str, to_user: str,
                        title: str, url: str) -> dict:
        """审批回推 OA：把待审批推到对应 IM。返回发送结果。"""
        return self._adapter(tenant, provider).send_approval_card(to_user, title, url)

    def verify(self, tenant: str, provider: str, **params) -> Any:
        """回调验签：wecom 用 verify_url/verify_message，dingtalk 用 verify_callback。"""
        ad = self._adapter(tenant, provider)
        if provider == "wecom":
            if "echostr" in params:
                return ad.verify_url(params["msg_signature"], params["timestamp"],
                                     params["nonce"], params["echostr"])
            return ad.verify_message(params["msg_signature"], params["timestamp"],
                                     params["nonce"], params["encrypt"])
        if provider == "dingtalk":
            return ad.verify_callback(params["timestamp"], params["nonce"],
                                      params["signature"])
        raise ValueError(f"不支持的 provider：{provider}")


# 单例（路由层共用）
gateway = IMGateway()
