"""M40 · 语义缓存（真 bge-m3 语义层）+ 模型路由 applied + 网关节流。

不依赖真 bge-m3 模型权重：用 monkeypatch 注入确定性 embed，验证
「同义改写/跨语言」能被余弦命中（语义层真生效），而非仅靠字面 3-gram。
"""
import types

import pytest

from app.core import config as cfg
from app.services import semantic_cache as sc
from app.services import model_gateway as mg
from app.services import agent_runtime as ar


# ── 确定性 embed 桩（模拟 bge-m3 把同主题近义句映射到相近向量） ──────────────────
_TOPIC = {
    "leave": [1.0, 0.0, 0.0, 0.0],   # 年假/年休假
    "price": [0.0, 1.0, 0.0, 0.0],   # 价格/报价
}


def _fake_embed(text: str):
    if "年假" in text or "年休假" in text:
        return list(_TOPIC["leave"])
    if "价格" in text or "报价" in text:
        return list(_TOPIC["price"])
    return [0.0, 0.0, 0.0, 1.0]


@pytest.fixture
def embed_on(monkeypatch):
    monkeypatch.setattr(cfg.settings, "USE_LOCAL_EMBEDDING", True)
    monkeypatch.setattr("app.services.knowledge.embed", _fake_embed)
    sc.reset_state()
    yield
    sc.reset_state()
    monkeypatch.setattr(cfg.settings, "USE_LOCAL_EMBEDDING", False)


@pytest.fixture
def embed_off():
    sc.reset_state()
    yield
    sc.reset_state()


# ── 语义缓存：字面层 ────────────────────────────────────────────────────────
def test_literal_hit_char_3gram(embed_off):
    sc.put("公司年假怎么请", "年假需提前 3 天在 OA 提交")
    hit = sc.get("公司年假怎么请")
    assert hit is not None
    assert hit["answer"] == "年假需提前 3 天在 OA 提交"
    assert hit["sim_mode"] == "char_3gram"
    # 极小改写（标点/空格）仍字面命中
    assert sc.get("公司 年假 怎么 请") is not None


# ── 语义缓存：真语义层（同义改写命中，字面层不中） ───────────────────────────
def test_semantic_hit_synonym(embed_on):
    sc.put("公司年假怎么请", "年假需提前 3 天在 OA 提交")
    # 同义改写，字面 3-gram 远低于阈值，但主题向量一致 → 余弦命中
    hit = sc.get("如何申请公司的年休假")
    assert hit is not None, "同义改写应被 bge-m3 余弦层命中"
    assert hit["sim_mode"] == "bge-m3_cosine"
    assert hit["answer"] == "年假需提前 3 天在 OA 提交"


def test_semantic_miss_different_topic(embed_on):
    sc.put("公司年假怎么请", "年假需提前 3 天在 OA 提交")
    # 不同主题 → 余弦远低于阈值 → 不命中
    assert sc.get("这个产品报价多少") is None


def test_stats_reflects_semantic_layer(embed_on):
    sc.put("公司年假怎么请", "x")
    s = sc.stats()
    assert s["semantic_layer"] == "bge-m3"
    assert "bge-m3_cosine" in s["similarity"]
    assert s["hot_entries"] + s["cold_entries"] == s["entries"]


# ── 冷热分层：命中累计升 hot，FIFO 优先丢 cold ───────────────────────────────
def test_hot_tier_promotion_and_eviction_priority(embed_off, monkeypatch):
    # 把容量调到很小，制造淘汰
    monkeypatch.setattr(cfg.settings, "SEMANTIC_CACHE_MAX_ENTRIES", 3)
    monkeypatch.setattr(cfg.settings, "SEMANTIC_CACHE_HOT_HITS", 2)
    # cold 三条
    sc.put("q1", "a1")
    sc.put("q2", "a2")
    sc.put("q3", "a3")
    # 把 q1 打热（命中 2 次）
    sc.get("q1")
    sc.get("q1")
    s = sc.stats()
    assert s["hot_entries"] == 1
    # 再写一条，超出容量 3 → 应淘汰 cold（q2 或 q3），保留 hot(q1)
    sc.put("q4", "a4")
    remaining = {r["query"] for r in sc._CACHE.list(limit=10 ** 6)}
    assert "q1" in remaining, "hot 条目应在淘汰中保活"
    assert len(remaining) == 3


# ── 模型路由 applied=true ──────────────────────────────────────────────────
def test_resolve_model_routes_by_signal():
    # 复杂 + 检索引用 → 大模型
    m, dec = mg.resolve_model("请分析这次客诉的归因并引用制度",
                              {"adapter_type": "llm_retrieval", "require_citation": True})
    assert dec is not None and dec["tier"] == "large"
    assert m == cfg.settings.ROUTING_LARGE_MODEL
    # 简单任务 → 小模型
    m2, dec2 = mg.resolve_model("把这句话翻译成英文")
    assert dec2 is not None and dec2["tier"] == "small"
    assert m2 == cfg.settings.ROUTING_SMALL_MODEL
    # 显式指定 → 跳过路由
    m3, dec3 = mg.resolve_model("随便", requested="glm-4.6")
    assert dec3 is None and m3 == "glm-4.6"


def test_routing_applied_in_runtime(monkeypatch):
    """agent_runtime._call_llm 把 user_input/agent 透给网关，路由真生效。

    不连真模型：把 llm_available 置 False 且 LLM_STRICT=False，让 chat_sync_ex 走
    stub 分支（仍会执行 resolve_model，routed_model 被如实带回），由此验证路由 applied。
    """
    monkeypatch.setattr(mg, "llm_available", lambda force=False: False)
    strict = cfg.settings.LLM_STRICT
    monkeypatch.setattr(cfg.settings, "LLM_STRICT", False)
    try:
        # 复杂 + 引用 → 应路由到 LARGE 模型
        out = ar._call_llm("prompt", "sys", user_input="请分析这次客诉的归因并引用制度",
                           agent={"adapter_type": "llm_retrieval", "require_citation": True})
        assert out["routed_model"] == cfg.settings.ROUTING_LARGE_MODEL
        assert out["routed_tier"] == "large"
        # 简单 → SMALL
        out2 = ar._call_llm("prompt", "sys", user_input="把这句话翻译成英文")
        assert out2["routed_model"] == cfg.settings.ROUTING_SMALL_MODEL
        assert out2["routed_tier"] == "small"
    finally:
        monkeypatch.setattr(cfg.settings, "LLM_STRICT", strict)


# ── 网关节流（令牌桶） ──────────────────────────────────────────────────────
def test_gateway_throttle():
    orig_rps = cfg.settings.GATEWAY_THROTTLE_RPS
    orig_burst = cfg.settings.GATEWAY_THROTTLE_BURST
    mg._BUCKET.configure(0.1, 1.0)   # 极低限速，burst=1
    try:
        # 第一次在突发余量内放行
        mg.apply_throttle()
        # 耗尽后抛限流
        with pytest.raises(mg.RateLimited):
            mg.apply_throttle()
    finally:
        mg._BUCKET.configure(orig_rps, orig_burst)


def test_throttle_disabled_by_default():
    assert cfg.settings.GATEWAY_THROTTLE_RPS == 0.0
    # 默认关闭：连续多次都不应抛
    for _ in range(5):
        mg.apply_throttle()
