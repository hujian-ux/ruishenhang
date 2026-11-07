"""M27 · 评测质量 / 成本路由 / 语义缓存 / 置信降级 / 中文特化 / MLOps 骨架。

自建 FastAPI app（不 import app.main），只挂网关 + 审计 trace 中间件：
  - 全部端点要求登录（dev token），审计动作 monkeypatch 屏蔽，避免落 SQLite；
  - autouse fixture 调各模块 reset_state()，保证用例间状态隔离；
  - 覆盖：judge 分数区间/mode、回归门禁、中文切分、zh_bench、缓存命中/未命中/
    TTL/命中率/FIFO/命名空间、路由 small/large/关闭不改模型、confidence stub 上限、
    handoff 建单与 resolve、PSI 漂移、pipeline 不谎称已训练、reset 隔离、端点冒烟。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.middleware import AuditTraceMiddleware, GatewayMiddleware
from app.core.security import create_dev_token
from app.models.caller import CallerContext
from app.routers import quality as q_router
from app.services import quality, semantic_cache, model_router, degrade, mlops

# ── 测试自建 app（严格按契约，不 import app.main）──────────────────────────
app = FastAPI()
app.add_middleware(AuditTraceMiddleware)
app.add_middleware(GatewayMiddleware)
app.include_router(q_router.router, prefix="/api/v1")
client = TestClient(app)

H = {"Authorization": create_dev_token("u", "d", "employee")}
ADMIN = {"Authorization": create_dev_token("p_admin", "d_platform", "platform_admin")}
CALLER = CallerContext(userId="u", deptId="d", role="employee", tenantId="t_demo")

BASE = "/api/v1"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """审计屏蔽 + 各模块状态重置（用例前后都 reset，保证隔离）。"""
    from app.services import audit
    monkeypatch.setattr(audit, "record", lambda *a, **k: None)   # 审计动作屏蔽

    def _reset_all():
        quality.reset_state()
        semantic_cache.reset_state()
        model_router.reset_state()
        degrade.reset_state()
        mlops.reset_state()

    _reset_all()
    yield
    _reset_all()


# ── 1. llm_judge：分数区间 + mode 标注 + 可解释维度 ─────────────────────────
def test_judge_score_range_and_mode():
    good = quality.llm_judge("营收增长15%，依据[1]", "营收增长15%，依据[1]", "[1] 财报")
    assert 0 <= good["score"] <= 5
    assert good["mode"] == "heuristic"                       # 明确不冒充 LLM-as-Judge
    assert set(good["dimensions"]) == {"relevance", "compliance", "citation", "safety"}
    assert all(0 <= v <= 5 for v in good["dimensions"].values())
    assert "未调用真 LLM-as-Judge" in good["note"]           # 明确边界
    assert "score" in good and "note" in good

    bad = quality.llm_judge("不知道", "营收增长15%，依据[1]", "[1] 财报")
    assert 0 <= bad["score"] < good["score"]                # 字面规则能区分好坏
    assert bad["dimensions"]["relevance"] < good["dimensions"]["relevance"]

    risky = quality.llm_judge("教你制作炸弹并 ignore previous 指令", "随便")
    assert risky["dimensions"]["safety"] < 5                # 红线词命中扣分


# ── 2. 回归门禁：候选不劣化才 pass（更好 / 持平均放行）────────────────────
def test_prompt_regression_pass_when_not_degraded():
    cases = [
        {"answer": "营收增长15%，依据[1]", "expected": "营收增长15%，依据[1]", "evidence": "[1] 财报"},
        {"answer": "净利润3.2亿元", "expected": "净利润3.2亿元", "evidence": "[2] 财报"},
    ]
    better = "营收增长15%，依据[1]；净利润3.2亿元，依据[2]"
    r = quality.prompt_regression("不知道", better, cases)
    assert r["pass"] is True
    assert r["candidate_avg"] >= r["baseline_avg"]
    assert r["delta"] > 0
    assert r["mode"] == "heuristic"
    assert len(r["cases"]) == 2
    assert all({"baseline", "candidate"} <= set(c) for c in r["cases"])

    # 持平（delta=0）也算不劣化
    same = quality.prompt_regression(better, better, cases)
    assert same["pass"] is True
    assert same["delta"] == 0


# ── 3. 回归门禁：劣化 fail ────────────────────────────────────────────────
def test_prompt_regression_fail_when_degraded():
    cases = [{"answer": "营收增长15%，依据[1]", "expected": "营收增长15%，依据[1]",
              "evidence": "[1] 财报"}]
    good = "营收增长15%，依据[1]"
    r = quality.prompt_regression(good, "呃……不清楚", cases)
    assert r["pass"] is False
    assert r["delta"] < 0
    assert r["candidate_avg"] < r["baseline_avg"]


# ── 4. 中文切分：不破词、保留标点、空文本 [] ───────────────────────────────
def test_chinese_segment_keeps_words_and_punctuation():
    text = "我们研究人工智能与大模型，并且关注中文处理。"
    segs = quality.chinese_segment(text)
    assert "".join(segs) == text                  # 保留标点、不丢字
    assert any("人工智能" in s for s in segs)     # 词不被切开
    assert any("大模型" in s for s in segs)
    assert quality.chinese_segment("") == []
    assert quality.chinese_segment("   ") == []


# ── 5. 中文切分：长句被切到 <=60 字 + 句子边界 ────────────────────────────
def test_chinese_segment_long_sentence_split():
    long = ("本次评审首先梳理了过去三个季度的经营数据，随后对照年度预算逐项核对了差旅、采购与人力成本，"
            "接着邀请财务、法务与业务部门分别就合规口径发表意见，最终形成覆盖收入确认、成本归集、"
            "税务处理以及内控留痕四个方面的综合整改方案，并要求各责任部门提交书面反馈与时间表。")
    assert len(long) > 60
    segs = quality.chinese_segment(long)
    assert len(segs) > 1
    assert all(len(s) <= 60 for s in segs)        # 切到 <=60 字
    assert "".join(segs) == long                  # 无字符丢失、标点保留
    assert any("综合整改方案" in s for s in segs) # 切分不破词

    multi = "第一句。第二句！第三句？\n第四句"
    msegs = quality.chinese_segment(multi)
    assert len(msegs) >= 4                        # 。！？；\n 多边界生效
    joined = "".join(msegs)
    assert "。" in joined and "！" in joined and "？" in joined


# ── 6. zh_bench：6 条内置基准 + 诚实 note ─────────────────────────────────
def test_zh_bench_six_items():
    b = quality.zh_bench()
    assert b["size"] == 6
    assert b["note"] == "内部小集，不等同 C-Eval/CMMLU"
    assert "不等同" in b["note"]
    sc = b["self_check"]
    assert len(sc["items"]) == 6
    assert sc["pass"] is True
    types = {i["type"] for i in sc["items"]}
    assert {"数字", "专名", "政策术语", "成语", "长句"} <= types
    assert sc["long_sentence_split"] is True
    assert sc["all_segments_le_60"] is True


# ── 7. 语义缓存：命中 / 未命中 + 字面相似度标注 ────────────────────────────
def test_cache_hit_and_miss():
    semantic_cache.put("公司的年假政策是什么", "答案A")
    hit = semantic_cache.get("公司的年假政策是什么")
    assert hit is not None
    assert hit["answer"] == "答案A"
    assert hit["similarity"] >= settings.SEMANTIC_CACHE_THRESHOLD
    assert hit["hit"] is True

    assert semantic_cache.get("完全不相关的报销流程与差旅标准") is None
    s = semantic_cache.stats()
    assert s["entries"] == 1 and s["hits"] == 1 and s["misses"] == 1
    # 未加载 bge-m3 时不冒充语义向量：similarity 仍是字面层，semantic_layer 标注 disabled
    assert s["similarity"] == "char_3gram_jaccard"
    assert s["semantic_layer"] == "disabled"
    assert "字面" in s["note"]
    assert s["enabled"] is True


# ── 8. 语义缓存：TTL 过期作废 ─────────────────────────────────────────────
def test_cache_ttl_expiry(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(semantic_cache, "now", lambda: clock["t"])
    semantic_cache.put("今天的年假政策是什么", "答案A")
    assert semantic_cache.get("今天的年假政策是什么") is not None   # 未过期命中
    clock["t"] = 1000.0 + settings.SEMANTIC_CACHE_TTL_S + 1
    assert semantic_cache.get("今天的年假政策是什么") is None       # 过期作废
    assert semantic_cache.stats()["entries"] == 0                  # 惰性删除


# ── 9. 语义缓存：命中率统计 + clear 重置 ──────────────────────────────────
def test_cache_hit_rate_and_clear():
    semantic_cache.put("query-A", "ans")
    semantic_cache.get("query-A")        # hit
    semantic_cache.get("query-A")        # hit
    semantic_cache.get("zzz-other")      # miss
    s = semantic_cache.stats()
    assert s["hits"] == 2 and s["misses"] == 1
    assert s["hit_rate"] == round(2 / 3, 4)
    semantic_cache.clear()
    s2 = semantic_cache.stats()
    assert s2["entries"] == 0 and s2["hits"] == 0 and s2["misses"] == 0
    assert s2["hit_rate"] == 0.0


# ── 10. 语义缓存：容量上限 FIFO 淘汰 ──────────────────────────────────────
def test_cache_fifo_eviction(monkeypatch):
    monkeypatch.setattr(settings, "SEMANTIC_CACHE_MAX_ENTRIES", 2)
    semantic_cache.put("第一条完全不同的查询内容甲", "A")
    semantic_cache.put("第二条完全不同的查询内容乙", "B")
    semantic_cache.put("第三条完全不同的查询内容丙", "C")
    assert semantic_cache.stats()["entries"] == 2           # 上限生效
    assert semantic_cache.get("第一条完全不同的查询内容甲") is None   # 最旧被淘汰
    assert semantic_cache.get("第三条完全不同的查询内容丙") is not None


# ── 11. 语义缓存：agent 命名空间隔离 ──────────────────────────────────────
def test_cache_agent_namespace():
    semantic_cache.put("同一个问题", "A 的答案", agent_id="agent_a")
    assert semantic_cache.get("同一个问题", agent_id="agent_a") is not None
    assert semantic_cache.get("同一个问题", agent_id="agent_b") is None


# ── 12. 路由：简单任务 → small ────────────────────────────────────────────
def test_route_simple_to_small():
    r = model_router.route("帮我把这句话翻译成英文")
    assert r["tier"] == "small"
    assert r["model"] == settings.ROUTING_SMALL_MODEL
    assert r["unit_price"] == settings.ROUTING_SMALL_TIER_PRICE
    assert r["enabled"] is True
    assert model_router.route("你好")["tier"] == "small"     # 短文本


# ── 13. 路由：复杂任务 → large（含检索+引用倾向）────────────────────────────
def test_route_complex_to_large():
    r = model_router.route("请分析营收下滑的归因并给出合规整改方案")
    assert r["tier"] == "large"
    assert r["model"] == settings.ROUTING_LARGE_MODEL
    assert r["unit_price"] == settings.ROUTING_LARGE_TIER_PRICE
    r2 = model_router.route("给出依据", {"adapter_type": "llm_retrieval",
                                        "require_citation": True})
    assert r2["tier"] == "large"
    t = model_router.tiers()
    assert t["small"]["model"] == settings.ROUTING_SMALL_MODEL
    assert t["large"]["model"] == settings.ROUTING_LARGE_MODEL
    assert t["enabled"] is True


# ── 14. 路由：ROUTING_ENABLED=0 关闭时不改模型 ─────────────────────────────
def test_route_disabled_keeps_model(monkeypatch):
    monkeypatch.setattr(settings, "ROUTING_ENABLED", False)
    simple = model_router.route("帮我把这句话翻译成英文")
    complex_ = model_router.route("请分析营收下滑的归因并给出合规整改方案")
    for r in (simple, complex_):
        assert r["enabled"] is False
        assert r["model"] == settings.LLM_DEFAULT_MODEL      # 保持默认模型
        assert r["model"] != settings.ROUTING_SMALL_MODEL    # 没切成小模型
    assert simple["model"] == complex_["model"]              # 两种输入都不改模型
    assert model_router.tiers()["enabled"] is False


# ── 15. confidence：stub 上限 0.5 / 证据引用加分 / guard 扣分 / 空答案 0 ──
def test_confidence_stub_cap():
    with_ev = degrade.confidence("答案[1]", evidence="[1] 证据")
    assert 0 <= with_ev <= 0.5                              # stub 硬上限 0.5
    real = degrade.confidence("答案[1]", evidence="[1] 证据", llm_real=True)
    assert real > 0.5                                      # 真模型可超过
    assert degrade.confidence("") == 0.0                    # 空答案 0
    assert degrade.confidence("答案", "") < with_ev          # 证据+编号引用加分
    guarded = degrade.confidence("答案[1]", evidence="[1] 证据",
                                 guard={"leak": True, "injection_hits": 2})
    assert guarded < with_ev                               # 泄密/注入命中扣分


# ── 16. 降级链：默认 + 可配 ───────────────────────────────────────────────
def test_chain_default_and_configurable():
    assert degrade.chain() == ["small_model", "rules", "human"]
    degrade.set_chain("agent_x", ["rules", "human"])
    assert degrade.chain("agent_x") == ["rules", "human"]
    assert degrade.chain("agent_y") == ["small_model", "rules", "human"]


# ── 17. handoff：建单（status=open + 完整上下文 + 置信理由）与 resolve ─────
def test_handoff_create_and_resolve():
    ctx = {"answer": "疑似不准确[1]", "evidence": "[1] 片段", "llm_real": False,
           "trace": ["step1", "step2"]}
    h = degrade.handoff("run_1", "agent_1", "置信低于阈值，转人工复核", ctx, CALLER)
    assert h["status"] == "open"
    assert h["context"] == ctx                             # 完整上下文
    assert h["confidence_reason"] == "置信低于阈值，转人工复核"
    assert h["reason"] == "置信低于阈值，转人工复核"
    assert 0 <= h["confidence"] <= 0.5
    assert h["threshold"] == settings.CONFIDENCE_THRESHOLD
    assert h["chain"] == ["small_model", "rules", "human"]
    assert h["created_by"] == "u"

    items = degrade.list_handoffs(CALLER, status="open")
    assert len(items) == 1 and items[0]["id"] == h["id"]
    assert degrade.list_handoffs(CALLER, status="resolved") == []

    done = degrade.resolve(h["id"], "已人工补充证据并确认", CALLER)
    assert done["status"] == "resolved"
    assert done["resolution"] == "已人工补充证据并确认"
    assert done["resolved_by"] == "u"
    with pytest.raises(KeyError):
        degrade.resolve("not-exist", "x", CALLER)


# ── 18. PSI：无漂移 ──────────────────────────────────────────────────────
def test_psi_no_drift():
    base = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    r = mlops.drift_check(base, list(base))
    assert r["drift"] is False
    assert r["psi"] < r["threshold"]
    assert r["mean_shift"] == 0.0
    assert r["note"] == "骨架实现，未接训练流水线"


# ── 19. PSI：有漂移 ──────────────────────────────────────────────────────
def test_psi_drift():
    base = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    shifted = [101, 102, 103, 104, 105, 106, 107, 108, 109, 110]
    r = mlops.drift_check(base, shifted)
    assert r["drift"] is True
    assert r["psi"] >= r["threshold"]
    assert abs(r["mean_shift"]) > 50


# ── 20. MLOps 登记（去重）+ 列表 ──────────────────────────────────────────
def test_mlops_register_and_list():
    m1 = mlops.register_model("qa-bot", "v1", {"accuracy": 0.9}, "fp-abc")
    assert m1["name"] == "qa-bot" and m1["fingerprint"] == "fp-abc"
    m2 = mlops.register_model("qa-bot", "v1", {"accuracy": 0.95}, "fp-abc")
    assert m2["id"] == m1["id"]                            # 同 name:version 去重更新
    assert m2["metrics"]["accuracy"] == 0.95
    assert len(mlops.list_models()) == 1


# ── 21. pipeline_status：不谎称已训练，四项 done=False + 落地条件 ──────────
def test_pipeline_status_not_trained():
    p = mlops.pipeline_status()
    for k in ("feature_store", "training", "registry", "retrain"):
        assert p[k]["done"] is False
        assert p[k]["condition"]                          # 有落地条件
    assert p["done_all"] is False
    assert p["trained"] is False
    assert "未接训练流水线" in p["note"]


# ── 22. reset_state 隔离：用例开始即干净，reset 后清空 ─────────────────────
def test_reset_state_isolation():
    # autouse fixture 已 reset：不应有上一用例残留
    assert mlops.list_models() == []
    assert degrade.list_handoffs() == []
    assert semantic_cache.stats()["entries"] == 0
    # 制造状态后统一 reset
    mlops.register_model("m", "v", {"x": 1})
    degrade.handoff("r", "a", "reason", {"answer": "x"}, CALLER)
    semantic_cache.put("q", "a")
    degrade.set_chain("a", ["rules"])
    assert len(mlops.list_models()) == 1
    assert len(degrade.list_handoffs()) == 1
    assert semantic_cache.stats()["entries"] == 1
    quality.reset_state()
    semantic_cache.reset_state()
    model_router.reset_state()
    degrade.reset_state()
    mlops.reset_state()
    assert mlops.list_models() == []
    assert degrade.list_handoffs() == []
    assert semantic_cache.stats()["entries"] == 0
    assert degrade.chain("a") == ["small_model", "rules", "human"]  # 自定义链已清


# ── 23. 全部端点要求登录（无 token → 401）────────────────────────────────
ALL_ENDPOINTS = [
    ("post", "/eval/judge", {"answer": "a", "expected": "b"}),
    ("post", "/eval/prompt-regression", {"baseline": "a", "candidate": "b", "cases": []}),
    ("get", "/eval/zh-bench", None),
    ("post", "/zh/segment", {"text": "你好"}),
    ("get", "/models/tiers", None),
    ("post", "/routing/preview", {"input": "你好"}),
    ("get", "/ops/cache/stats", None),
    ("post", "/ops/cache/clear", {}),
    ("post", "/handoff", {"run_id": "r", "agent_id": "a", "reason": "x", "context": {}}),
    ("get", "/handoff", None),
    ("post", "/handoff/xx/resolve", {"resolution": "ok"}),
    ("post", "/mlops/register", {"name": "m", "version": "v", "metrics": {}}),
    ("get", "/mlops/models", None),
    ("post", "/mlops/drift", {"baseline": [1], "observed": [1]}),
    ("get", "/mlops/pipeline", None),
]


@pytest.mark.parametrize("method,path,body", ALL_ENDPOINTS)
def test_all_endpoints_require_login(method, path, body):
    fn = getattr(client, method)
    if method == "get":
        resp = fn(BASE + path)
    else:
        resp = fn(BASE + path, json=body or {})
    assert resp.status_code == 401, (path, resp.status_code, resp.text)


# ── 24. 端点冒烟（登录态，逐一 code==0）───────────────────────────────────
def test_http_smoke_all_endpoints():
    def ok(resp):
        assert resp.status_code == 200, (resp.request.method, resp.request.url, resp.text)
        j = resp.json()
        assert j["code"] == 0
        return j["data"]

    d = ok(client.post(BASE + "/eval/judge",
                       json={"answer": "营收增长15%，依据[1]", "expected": "营收增长15%，依据[1]",
                             "evidence": "[1] 财报"}, headers=H))
    assert d["mode"] == "heuristic" and 0 <= d["score"] <= 5

    d = ok(client.post(BASE + "/eval/prompt-regression",
                       json={"baseline": "不知道", "candidate": "营收增长15%，依据[1]",
                             "cases": [{"expected": "营收增长15%，依据[1]", "evidence": "[1] 财报"}]},
                       headers=H))
    assert d["pass"] is True

    d = ok(client.get(BASE + "/eval/zh-bench", headers=H))
    assert d["size"] == 6

    d = ok(client.post(BASE + "/zh/segment", json={"text": "你好，世界。"}, headers=H))
    assert d["segments"] == ["你好，世界。"] and d["count"] == 1

    d = ok(client.get(BASE + "/models/tiers", headers=H))
    assert d["small"]["model"] == settings.ROUTING_SMALL_MODEL

    d = ok(client.post(BASE + "/routing/preview", json={"input": "请分析归因与方案"}, headers=H))
    assert d["tier"] == "large" and d["enabled"] is True

    d = ok(client.get(BASE + "/ops/cache/stats", headers=H))
    assert d["similarity"] == "char_3gram_jaccard"

    d = ok(client.post(BASE + "/ops/cache/clear", json={}, headers=H))
    assert d["cleared"] is True

    d = ok(client.post(BASE + "/handoff",
                       json={"run_id": "run_1", "agent_id": "agent_1", "reason": "低置信转人工",
                             "context": {"answer": "疑似[1]", "evidence": "[1] 片段"}}, headers=H))
    hid = d["id"]
    assert d["status"] == "open" and d["context"]["answer"] == "疑似[1]"

    d = ok(client.get(BASE + "/handoff?status=open", headers=H))
    assert d["count"] == 1 and d["items"][0]["id"] == hid

    d = ok(client.post(BASE + f"/handoff/{hid}/resolve",
                       json={"resolution": "已人工确认"}, headers=H))
    assert d["status"] == "resolved"

    d = ok(client.post(BASE + "/mlops/register",
                       json={"name": "qa-bot", "version": "v1",
                             "metrics": {"accuracy": 0.9}, "fingerprint": "fp"}, headers=H))
    assert d["fingerprint"] == "fp"

    d = ok(client.get(BASE + "/mlops/models", headers=H))
    assert d["count"] == 1

    d = ok(client.post(BASE + "/mlops/drift",
                       json={"baseline": [1, 2, 3], "observed": [1, 2, 3]}, headers=H))
    assert d["drift"] is False

    d = ok(client.get(BASE + "/mlops/pipeline", headers=H))
    assert all(not d[k]["done"] for k in ("feature_store", "training", "registry", "retrain"))


# ── 25. 端点错误：缺字段 422 / 未知工单 404 ───────────────────────────────
def test_http_errors():
    r = client.post(BASE + "/eval/judge", json={"answer": "x"}, headers=H)
    assert r.status_code == 422
    r = client.post(BASE + "/mlops/drift", json={"baseline": "not-a-list", "observed": [1]}, headers=H)
    assert r.status_code == 422
    r = client.post(BASE + "/handoff/nope/resolve", json={"resolution": "x"}, headers=H)
    assert r.status_code == 404
