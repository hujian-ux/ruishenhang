"""P1-173 · 企业微信回调消息体 AES-256-CBC 解密（确定性、不触网）。

测试策略：用与企微官方一致的加密算法造一个合法密文（随机 16B + 4B 大端长度 + XML + receiveid
+ PKCS7 填充），再验证 parse_inbound 能正确解密并解析出字段。同时覆盖「无 EncodingAESKey」
与「receive_id 不符」两个边界。
"""
import base64
import os
import struct

from Crypto.Cipher import AES

from app.services.im_gateway import WeComAdapter


def _make_encoding_aes_key() -> str:
    # 32 随机字节 → base64（44 字符，末位 '='）→ 取前 43 字符即企微 EncodingAESKey
    return base64.b64encode(os.urandom(32)).decode("ascii")[:43]


def _wecom_encrypt(corp_id: str, encoding_aes_key: str, xml_msg: str) -> str:
    aes_key = base64.b64decode(encoding_aes_key + "=")
    assert len(aes_key) == 32
    msg_bytes = xml_msg.encode("utf-8")
    body = os.urandom(16) + struct.pack(">I", len(msg_bytes)) + msg_bytes + corp_id.encode("utf-8")
    pad = 32 - (len(body) % 32)
    if pad == 0:
        pad = 32
    body += bytes([pad]) * pad
    cipher = AES.new(aes_key, AES.MODE_CBC, aes_key[:16])
    return base64.b64encode(cipher.encrypt(body)).decode("ascii")


def test_wecom_parse_inbound_decrypts_text():
    corp_id = "wwdemo123456"
    key = _make_encoding_aes_key()
    xml_msg = (
        "<xml><ToUserName><![CDATA[wwdemo123456]]></ToUserName>"
        "<FromUserName><![CDATA[zhangsan]]></FromUserName>"
        "<MsgType><![CDATA[text]]></MsgType>"
        "<Content><![CDATA[你好，这是测试]]></Content>"
        "<AgentID>1000002</AgentID></xml>"
    )
    enc = _wecom_encrypt(corp_id, key, xml_msg)
    ad = WeComAdapter(corp_id=corp_id, secret="s", agent_id="1000002",
                      token="t", encoding_aes_key=key)
    out = ad.parse_inbound(enc)
    assert out["provider"] == "wecom"
    assert out["msg_type"] == "text"
    assert out["from_user"] == "zhangsan"
    assert out["to_user"] == corp_id
    assert out["content"] == "你好，这是测试"


def test_wecom_parse_inbound_event():
    corp_id = "wwdemo123456"
    key = _make_encoding_aes_key()
    xml_msg = (
        "<xml><ToUserName><![CDATA[wwdemo123456]]></ToUserName>"
        "<FromUserName><![CDATA[zhangsan]]></FromUserName>"
        "<MsgType><![CDATA[event]]></MsgType>"
        "<Event><![CDATA[subscribe]]></Event></xml>"
    )
    enc = _wecom_encrypt(corp_id, key, xml_msg)
    ad = WeComAdapter(corp_id=corp_id, secret="s", agent_id="1",
                      token="t", encoding_aes_key=key)
    out = ad.parse_inbound(enc)
    assert out["msg_type"] == "event"
    assert out["event"] == "subscribe"


def test_wecom_parse_inbound_without_aes_key_returns_placeholder():
    ad = WeComAdapter(corp_id="wwx", secret="s", agent_id="1", token="t")
    out = ad.parse_inbound("someciphertext")
    assert out["provider"] == "wecom"
    assert out["note"]  # 提示需要 EncodingAESKey


def test_wecom_parse_inbound_receive_id_mismatch_rejected():
    corp_id = "wwreal"
    key = _make_encoding_aes_key()
    # 密文里的 receiveid 写成别的 corp，解密后校验应失败
    xml_msg = "<xml><MsgType><![CDATA[text]]></MsgType><Content><![CDATA[hi]]></Content></xml>"
    enc = _wecom_encrypt("wwattacker", key, xml_msg)
    ad = WeComAdapter(corp_id=corp_id, secret="s", agent_id="1",
                      token="t", encoding_aes_key=key)
    try:
        ad.parse_inbound(enc)
        assert False, "receive_id 不符应抛错"
    except ValueError as e:
        assert "receive_id" in str(e)
