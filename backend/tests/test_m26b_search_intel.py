"""M26 P1 · 检索词拆解与来源可信度打分（确定性，零外部依赖）。"""
import pytest

from app.services import search_intel, web_search


def test_decompose_competitive_query_three_rounds():
    q = "半成品菜 供应链 品牌 报价 广东 渠道"
    d = search_intel.decompose_query(q)
    # 第一轮实体/品类
    assert "半成品菜" in d["entity"] or "供应链" in d["entity"]
    # 第二轮价格/规格
    assert "报价" in d["price_spec"]
    # 第三轮渠道/地域
    assert "广东" in d["channel_region"] and "渠道" in d["channel_region"]
    # 合并子查询 1-4 条，且含裸实体基线
    assert 1 <= len(d["sub_queries"]) <= 4
    assert d["sub_queries"][0] in d["entity"] or d["entity"] in d["sub_queries"][0]


def test_decompose_strips_question_words():
    d = search_intel.decompose_query("广东 独立储能 容量补偿 政策 2026 怎么查")
    assert "怎么查" not in d["entity"]
    assert "政策" in d["entity"]


def test_decompose_llm_extension_opt_in():
    called = {}

    def fake_llm(q):
        called["q"] = q
        return ["储能 招标 价格", "储能 补贴 政策"]

    d = search_intel.decompose_query("储能 价格", llm_fn=fake_llm)
    assert d["llm_used"] is True
    assert any("招标" in s for s in d["sub_queries"])
    assert called["q"] == "储能 价格"


def test_credibility_tiers():
    official = search_intel.score_credibility({"url": "https://www.gov.cn/xx", "published_at": "2026-01"})
    ugc = search_intel.score_credibility({"url": "https://www.weibo.com/x", "published_at": "2026-01"})
    assert official["score"] >= 0.9
    assert official["tier"].startswith("官方")
    assert ugc["score"] <= 0.35
    assert ugc["tier"].startswith("自媒体")


def test_credibility_recency_old_lowers_score():
    new = search_intel.score_credibility({"url": "https://www.caixin.com/a", "published_at": "2026-03"})
    old = search_intel.score_credibility({"url": "https://www.caixin.com/a", "published_at": "2018-03"})
    assert old["score"] < new["score"]


def test_decomposed_search_merges_dedupes_scores_and_sorts():
    web_search.set_mock_results([
        {"title": "政府政策", "url": "https://www.gov.cn/p1", "snippet": "储能政策", "source": "mock", "published_at": "2026-01"},
        {"title": "自媒体说", "url": "https://www.weibo.com/p2", "snippet": "储能价格", "source": "mock", "published_at": "2026-01"},
    ])
    out = search_intel.search_decomposed("储能 价格 政策", top_k=10, force_provider="mock")
    assert out["ok"] is True
    assert out["mode"] == "decomposed"
    assert len(out["hits"]) == 2
    # 按可信度降序：gov.cn 应在 weibo 之前
    assert out["hits"][0]["credibility"]["score"] > out["hits"][1]["credibility"]["score"]
    assert "sub_queries" in out["decomposed"]
    # 每条都带 credibility 与 matched_sub_query
    assert all("credibility" in h and "matched_sub_query" in h for h in out["hits"])
    web_search.set_mock_results([])


def test_decomposed_search_degenerate_single_token():
    web_search.set_mock_results([
        {"title": "储能", "url": "https://www.gov.cn/x", "snippet": "储能", "source": "mock", "published_at": "2026-01"},
    ])
    out = search_intel.search_decomposed("储能", top_k=5, force_provider="mock")
    # 单 token 退化为单次检索，但响应形状一致（含 credibility）
    assert out["hits"][0]["credibility"]["score"] > 0
    web_search.set_mock_results([])
