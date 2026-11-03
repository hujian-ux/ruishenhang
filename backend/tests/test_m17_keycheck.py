"""M17-2：模型接入排障的回归。

这一组用例全部来自真实踩坑，不是假想场景：
- 用户给的「智谱 key」其实是硅基流动的 → 401 原文只写 "Invalid Authentication"，
  看不出打错门，必须能被 check_key 识别成「不是这家」；
- 同一串 key 在硅基流动返回 402 → **认证已通过**，只是账户没钱，
  这个区分必须成立，否则会把「去充值」误判成「key 无效」，白折腾一轮。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.model_gateway import diagnose  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from check_key import _classify  # noqa: E402


# ── diagnose：状态码 → 可执行结论 ──
def test_diagnose_401_points_to_wrong_vendor():
    msg = diagnose(401, '{"error":{"code":"401","message":"令牌已过期或验证不正确"}}')
    assert "密钥无效" in msg
    assert "HTTP 401" in msg


def test_diagnose_402_says_balance_not_key():
    """402 必须明确「key 本身有效」—— 否则用户会去重新生成 key，白费功夫。"""
    msg = diagnose(402, '{"code":30001,"message":"Sorry, your account balance is insufficient"}')
    assert "余额不足" in msg
    assert "key 本身有效" in msg


def test_diagnose_403_and_429():
    assert "无权限" in diagnose(403, '{"code":30003,"message":"Model disabled."}')
    assert "限流" in diagnose(429, "")


def test_diagnose_5xx_and_unknown():
    assert "厂商侧服务异常" in diagnose(503, "")
    assert diagnose(418, "teapot") == ""        # 不瞎猜


# ── check_key._classify：归属判定 ──
def test_classify_accepts_402_as_this_vendor():
    owns, verdict = _classify(402, '{"code":30001,"message":"Sorry, your account balance is insufficient"}')
    assert owns == "yes" and "余额不足" in verdict


def test_classify_401_is_not_this_vendor():
    assert _classify(401, '{"error":{"message":"Invalid Authentication"}}')[0] == "no"
    assert _classify(401, '{"error":{"message":"令牌已过期或验证不正确"}}')[0] == "no"
    assert _classify(401, '{"error":{"message":"Incorrect API key provided"}}')[0] == "no"


def test_classify_model_disabled_still_owns_key():
    """403 Model disabled 说明认证已过、只是模型下线，不能判成「不是这家」。"""
    owns, verdict = _classify(403, '{"code":30003,"message":"Model disabled."}')
    assert owns == "yes" and "已下线" in verdict


def test_classify_ok_and_rate_limited():
    assert _classify(200, "")[0] == "yes"
    assert _classify(429, "")[0] == "yes"


def test_classify_server_error_is_unknown():
    assert _classify(502, "bad gateway")[0] == "unknown"
