"""P1-173b IM/OA 网关：WeCom/DingTalk 签名校验（官方公式）、配置脱敏、下发与审批回推。

签名校验用官方公式自验，不触网；网络调用经 set_http_post 注入假实现。
"""
import base64
import hashlib
import hmac

import app.services.im_gateway as gw
from app.services import im_store
from app.services.im_gateway import DingTalkAdapter, WeComAdapter, gateway, set_http_post


def test_wecom_url_signature_roundtrip():
    token, ts, nonce, echo = "mytoken", "1700000000", "abc", "HELLO"
    # 复刻官方公式：sorted([token,ts,nonce,echo]) 拼接后 SHA1
    expected = hashlib.sha1("".join(sorted([token, ts, nonce, echo])).encode()).hexdigest()
    ad = WeComAdapter("", "", "", token)
    assert ad._sign(token, ts, nonce, echo) == expected
    assert ad.verify_url(expected, ts, nonce, echo) == echo          # 验过返回 echostr
    assert ad.verify_url("wrong", ts, nonce, echo) is None            # 验败返回 None


def test_wecom_message_signature():
    ad = WeComAdapter("", "", "", "tok")
    enc = "<encrypted-blob>"
    sig = ad._sign("tok", "t", "n", enc)
    assert ad.verify_message(sig, "t", "n", enc) is True
    assert ad.verify_message("bad", "t", "n", enc) is False


def test_dingtalk_signature_roundtrip():
    secret, ts = "signsecret", "1700000000"
    string_to_sign = f"{ts}\n{secret}"
    expected = base64.b64encode(
        hmac.new(secret.encode(), string_to_sign.encode(), hashlib.sha256).digest()
    ).decode()
    ad = DingTalkAdapter("", "", secret)
    assert ad._sign(ts, secret) == expected
    assert ad.verify_callback(ts, "nonce", expected) is True
    assert ad.verify_callback(ts, "nonce", "bad") is False


def test_im_store_masks_secret():
    im_store.save_config("t1", "wecom", corp_id="cid", secret="SUPERSECRET123",
                         webhook_token="tok")
    cfg = im_store.get_config("t1", "wecom")
    assert cfg["secret"] == "SUPERSECRET123"  # 内部取用返回真密钥（网关下发需要）
    assert cfg["corp_id"] == "cid"
    listed = im_store.list_configs("t1")
    assert listed and listed[0]["secret"] == "SUPE****"  # 列表/API 给人看才脱敏
    assert im_store.delete_config("t1", "wecom") is True


def test_gateway_send_uses_injected_http():
    captured = {}

    def fake_post(url, data, headers):
        captured["url"] = url
        captured["data"] = data
        # gettoken 调用也需要 access_token 字段，否则触发真实获取逻辑
        return {"access_token": "TOK", "errcode": 0, "ok": True}

    set_http_post(fake_post)
    try:
        im_store.save_config("t2", "wecom", corp_id="cid", secret="s",
                             agent_id="1000002", webhook_token="tok")
        res = gateway.send("t2", "wecom", "zhangsan", "hi")
        assert res["ok"] is True
        assert "message/send" in captured["url"]
        assert captured["data"]["touser"] == "zhangsan"
        assert captured["data"]["text"]["content"] == "hi"
    finally:
        set_http_post(lambda u, d, h: __import__("httpx").post(u, data=d, headers=h).json())
        im_store.delete_config("t2", "wecom")


def test_gateway_notify_approval_uses_card():
    captured = {}

    def fake_post(url, data, headers):
        captured["data"] = data
        return {"access_token": "TOK", "errcode": 0}

    set_http_post(fake_post)
    try:
        im_store.save_config("t3", "dingtalk", corp_id="key", secret="appsec",
                             webhook_token="signkey")
        gateway.notify_approval("t3", "dingtalk", "lisi", "报销审批", "https://x/approve/1")
        assert captured["data"]["msgtype"] == "markdown"
        assert "报销审批" in captured["data"]["markdown"]["text"]
        assert "https://x/approve/1" in captured["data"]["markdown"]["text"]
    finally:
        set_http_post(lambda u, d, h: __import__("httpx").post(u, data=d, headers=h).json())
        im_store.delete_config("t3", "dingtalk")


def test_gateway_send_unconfigured_raises():
    try:
        gateway.send("no-such-tenant", "wecom", "u", "x")
    except ValueError as e:
        assert "未配置" in str(e)
    else:
        raise AssertionError("未配置时应抛 ValueError")
