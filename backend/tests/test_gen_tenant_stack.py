"""gen_tenant_stack.py 单测：占位符替换 / 租户规范化 / 端口偏移 / 无残留。"""
import re

from scripts import gen_tenant_stack as g


def test_generate_basic():
    tenant, compose, env_text, cpath, epath = g.generate(
        "ACME-子公司", 1000, True, g.ROOT / "deploy")
    assert tenant == "acme"
    # 端口偏移应用
    assert "6432:5432" in compose           # PG 5432 + 1000
    assert "7333:6333" in compose           # Qdrant 6333 + 1000
    assert "9080:8080" in compose           # app 8080 + 1000
    # 服务名/卷名带租户前缀
    assert "acme_postgres:" in compose
    assert "acme_pgdata:" in compose
    assert "acme_net" in compose
    # 无残留占位符
    left = re.findall(r"\$\{[A-Z_]+}", compose)
    assert left == [], f"残留占位符：{left}"
    # 随机密钥已写入 env
    assert "PG_PASSWORD=" in env_text and "MINIO_PASSWORD=" in env_text


def test_generate_slug_invalid_becomes_valid():
    tenant, compose, env_text, _, _ = g.generate("  Bad/Tenant! ", 0, False, g.ROOT / "deploy")
    assert tenant == "bad-tenant"
    assert "bad-tenant_postgres:" in compose


def test_generate_offset_zero_uses_base_ports():
    _, compose, _, _, _ = g.generate("t1", 0, False, g.ROOT / "deploy")
    assert "5432:5432" in compose
    assert "8080:8080" in compose


def test_generate_raises_on_unknown_template(tmp_path):
    # 指向不存在的模板会抛 FileNotFoundError
    import pathlib
    old = g.TEMPLATE
    try:
        g.TEMPLATE = tmp_path / "nope.yml"
        try:
            g.generate("t", 0, False, tmp_path)
            assert False, "应当抛 FileNotFoundError"
        except FileNotFoundError:
            pass
    finally:
        g.TEMPLATE = old
