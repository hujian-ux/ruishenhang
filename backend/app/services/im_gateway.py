"""IM/OA 消息网关（P1-173）：企业微信 / 钉钉 收发适配器 + 审批回推 OA。

设计原则：
- 零厂商 SDK 依赖：直接走标准 REST + 官方签名公式，避免锁死版本。
- 签名校验用官方公式，确定性、可单测（不触网）。
- 网络调用（get_access_token / send）通过模块级 ``_http_post`` 注入，测试可替换为假实现。
- 凭据来自 im_store（租户级），opt-in：未配置则返回明确的「未配置」而非报错崩链。
- 消息体 AES 解密（企微回调）用 EncodingAESKey 派生 32B key + AES-256-CBC（IV=key[:16]），
  依赖 ``pycryptodome``（已进 pyproject 主依赖）；未配 EncodingAESKey 时返回占位、不谎称已解密。
"""
import base64
import hashlib
import hmac
import json
import struct
import xml.etree.ElementTree as ET
from typing import Any, Callable, Dict, Optional

from Crypto.Cipher import AES

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

        企微消息体加密格式（官方）：
        - ``AESKey = base64decode(EncodingAESKey + "=")``（43 位 → 补 "=" 成 44 位 → 32B）。
        - AES-256-CBC，``IV = AESKey[:16]``；明文 = 随机(16B) + 消息长度(4B 大端) + 消息 + receiveid。
        - 消息为 XML（文本/事件），本方法解密后解析出常用字段。
        """
        if not self.encoding_aes_key:
            return {"provider": "wecom", "raw": encrypt,
                    "note": "需要 EncodingAESKey 才能 AES 解密消息体"}
        try:
            aes_key = base64.b64decode(self.encoding_aes_key + "=")
            if len(aes_key) != 32:
                raise ValueError(f"EncodingAESKey 派生 key 长度异常：{len(aes_key)}B")
            cipher = AES.new(aes_key, AES.MODE_CBC, aes_key[:16])
            raw = cipher.decrypt(base64.b64decode(encrypt))
            # 去 PKCS7 填充（块大小 32）
            pad = raw[-1]
            if 1 <= pad <= 32:
                raw = raw[:-pad]
            if len(raw) < 20:
                raise ValueError("解密后长度不足（缺随机前缀+长度头）")
            msg_len = struct.unpack(">I", raw[16:20])[0]
            msg = raw[20:20 + msg_len]
            receive_id = raw[20 + msg_len:].decode("utf-8", "ignore")
            xml_text = msg.decode("utf-8", "ignore")
        except Exception as e:  # noqa: BLE001 — 解密/解析失败统一报，供调用方 400
            raise ValueError(f"企微消息 AES 解密失败：{e}")
        # receive_id 应为 corp_id（企微回填），不一致视为被篡改
        if receive_id and self.corp_id and receive_id != self.corp_id:
            raise ValueError(f"企微消息 receive_id({receive_id}) 与 corp_id({self.corp_id}) 不符")
        return {
            "provider": "wecom",
            "msg_type": _wecom_xml(xml_text, "MsgType"),
            "from_user": _wecom_xml(xml_text, "FromUserName"),
            "to_user": _wecom_xml(xml_text, "ToUserName"),
            "content": _wecom_xml(xml_text, "Content"),
            "event": _wecom_xml(xml_text, "Event"),
            "raw": xml_text,
        }


def _wecom_xml(xml_text: str, tag: str) -> Optional[str]:
    """从企微 XML 消息里取文本字段（CDATA 由 ElementTree 自动处理）。"""
    try:
        root = ET.fromstring(xml_text)
        el = root.find(tag)
        return el.text if el is not None else None
    except ET.ParseError:
        return None


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
