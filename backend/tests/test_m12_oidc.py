"""M12 真实身份：OIDC/JWT 校验层测试。

验证：标准 discovery + JWKS 验签（RS256）、claims→CallerContext 映射、
过期/audience/签名错误的拒绝、dev token 在 OIDC 开启时仍可用、生产环境拒绝 dev token。
测试注入 _JWKS_CACHE 绕过网络 discovery。
"""
import json
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core import security
from app.core.config import settings
from jwt.algorithms import RSAAlgorithm

ISS = "https://idp.example.com/realms/ruishenxing"
AUD = "ruishenxing-platform"


def _make_keys():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_jwk = json.loads(RSAAlgorithm.to_jwk(priv.public_key()))
    pub_jwk["kid"] = "1"  # 真实 IdP 的 JWKS 每个 key 都带 kid
    return priv, {"keys": [pub_jwk]}


def _seed_jwks():
    priv, jwks = _make_keys()
    security._JWKS_CACHE[ISS] = (jwks, time.time())
    return priv


def test_oidc_success_maps_claims():
    settings.OIDC_ISSUER = ISS
    settings.OIDC_AUDIENCE = AUD
    settings.OIDC_DEPT_CLAIM = "dept"
    settings.OIDC_ROLE_CLAIM = "role"
    priv = _seed_jwks()
    token = jwt.encode(
        {"sub": "u_oidc_1", "preferred_username": "li.ming", "dept": "d_sales",
         "role": "dept_admin", "name": "李明", "iss": ISS, "aud": AUD,
         "exp": int(time.time()) + 3600},
        priv, algorithm="RS256", headers={"kid": "1"},
    )
    caller = security._verify_oidc_token(token)
    assert caller is not None
    assert caller.userId == "li.ming"
    assert caller.deptId == "d_sales"
    assert caller.role == "dept_admin"
    assert caller.tenantId == settings.DEFAULT_TENANT


def test_oidc_expired_rejected():
    settings.OIDC_ISSUER = ISS
    settings.OIDC_AUDIENCE = AUD
    priv = _seed_jwks()
    token = jwt.encode(
        {"sub": "u_x", "iss": ISS, "aud": AUD, "exp": int(time.time()) - 10},
        priv, algorithm="RS256", headers={"kid": "1"},
    )
    assert security._verify_oidc_token(token) is None


def test_oidc_wrong_audience_rejected():
    settings.OIDC_ISSUER = ISS
    settings.OIDC_AUDIENCE = AUD
    priv = _seed_jwks()
    token = jwt.encode(
        {"sub": "u_x", "iss": ISS, "aud": "other-client", "exp": int(time.time()) + 3600},
        priv, algorithm="RS256", headers={"kid": "1"},
    )
    assert security._verify_oidc_token(token) is None


def test_oidc_bad_signature_rejected():
    settings.OIDC_ISSUER = ISS
    settings.OIDC_AUDIENCE = AUD
    priv = _seed_jwks()
    token = jwt.encode(
        {"sub": "u_x", "iss": ISS, "aud": AUD, "exp": int(time.time()) + 3600},
        priv, algorithm="RS256", headers={"kid": "1"},
    )
    bad = token + "tamper"
    assert security._verify_oidc_token(bad) is None


def test_decode_token_dev_still_works_when_oidc_on():
    settings.OIDC_ENABLED = True
    settings.OIDC_ISSUER = ISS
    dev = security.create_dev_token("u_dev", "d_it", "platform_admin")
    caller = security.decode_token(dev)
    # dev 分支优先于 OIDC，本地/CI 不依赖 IdP 即可用
    assert caller is not None and caller.userId == "u_dev"


def test_prod_rejects_dev_token():
    saved = settings.APP_ENV
    settings.APP_ENV = "prod"
    settings.OIDC_ENABLED = True
    dev = security.create_dev_token("u_dev", "d_it", "platform_admin")
    try:
        assert security.decode_token(dev) is None
    finally:
        settings.APP_ENV = saved
