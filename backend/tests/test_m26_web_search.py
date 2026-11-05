"""M26 · 联网搜索通道。

不依赖真网络、不依赖真模型也必须成立的红线：
1. **内外来源隔离**：公网证据用 [W编号]，内部片段用 [编号]，双通道时分开标注；
   公网结果永不进 citations、永不写进知识库。
2. **失败不编**：通道禁用 / provider 抛错 / 零命中都给明确空信号，
   search() 不抛异常，模型被要求写「未检索到公开信息」。
3. **SSRF 面收死**：只认公网 http/https，抓正文必须来自本轮搜索结果白名单
   且二次做 DNS 校验，接口不接受自定义 URL。
4. 业务包行为不变：不带 web 适配器的 115 个包一次都不会触网。
5. 公共库新增「联网查证」3 条，口径 25 → 28，硬规则单一来源仍全量注入。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import create_dev_token
from app.main import app
from app.models.caller import CallerContext
from app.services import agent_runtime, agent_store, public_skills, web_search

client = TestClient(app)
ADMIN = {"Authorization": create_dev_token("p_admin", "d_platform", "platform_admin")}
EMP = {"Authorization": create_dev_token("u_1024", "d_sales", "employee")}
ROOT = Path(__file__).resolve().parents[2]
# autouse fixture 会把 search 换成离线桩；需要测真实 search() 时显式还原这一个引用
REAL_SEARCH = web_search.search

FAKE = [
    {"title": "广东省新型储能实施方案", "url": "https://drc.gd.gov.cn/policy/2026",
     "snippet": "独立储能电站可参与现货市场与容量补偿", "published_at": "2026-01-15",
     "origin": "public_web", "source": "mock"},
    {"title": "某行业媒体解读", "url": "https://news.example.com/a/1",
     "snippet": "容量补偿标准存在多种口径", "published_at": "2026-03-02",
     "origin": "public_web", "source": "mock"},
]


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """默认全部离线：任何测试都不允许真的发外网请求。"""
    monkeypatch.setitem(web_search.PROVIDERS, "sogou_html", lambda q, k: [])
    monkeypatch.setitem(web_search.PROVIDERS, "bing_html", lambda q, k: [])
    def _fake_search(query, top_k=None, *, force_provider=None, fetch_top=0):
        return {"query": query, "top_k": top_k or 6, "hits": [dict(h) for h in FAKE],
                "ok": True, "degraded": False, "mode": "mock", "provider": "mock",
                "error": "", "fetched_at": "2026-11-06 00:00 UTC", "elapsed_ms": 1,
                "origin": web_search.ORIGIN, "fallback_from": ""}
    monkeypatch.setattr(web_search, "search", _fake_search)


@pytest.fixture(scope="module")
def seeded():
    before = set(agent_store._AGENTS)
    agent_store.ensure_public_skills()
    yield
    for aid in list(agent_store._AGENTS):
        if aid not in before:
            agent_store._AGENTS.pop(aid, None)


# ---------------------------------------------------------------- 1. 配置与状态
def test_status_never_leaks_keys(monkeypatch):
    monkeypatch.setattr(settings, "BOCHA_API_KEY", "sk-super-secret")
    s = web_search.status()
    assert s["configured"]["BOCHA_API_KEY"] is True
    assert "sk-super-secret" not in str(s)
    assert s["origin"] == "public_web"
    assert "不写入" in s["note"]


def test_provider_selection(monkeypatch):
    for k in ("BOCHA_API_KEY", "SERPER_API_KEY", "TAVILY_API_KEY",
              "BING_SEARCH_KEY", "SEARXNG_BASE_URL", "WEB_SEARCH_PROVIDER"):
        monkeypatch.setattr(settings, k, "auto" if k == "WEB_SEARCH_PROVIDER" else "")
    assert web_search.configured_provider() == "sogou_html"
    monkeypatch.setattr(settings, "BOCHA_API_KEY", "k")
    assert web_search.configured_provider() == "bocha"
    monkeypatch.setattr(settings, "WEB_SEARCH_PROVIDER", "serper")
    assert web_search.configured_provider() == "serper"
    monkeypatch.setattr(settings, "WEB_SEARCH_ENABLED", False)
    assert web_search.status()["mode"] == "disabled"


# ---------------------------------------------------------------- 2. URL 安全
@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8080/admin", "http://169.254.169.254/latest/meta-data",
    "http://10.0.0.5/", "http://192.168.1.1/", "http://[::1]/",
    "file:///etc/passwd", "ftp://example.com/a", "http://localhost/",
    "http://db.internal/secret", "http://printer.local/", "http://8.8.8.8:9999/",
    "https://metadata.google.internal/computeMetadata/v1/",
])
def test_private_urls_are_blocked(url):
    assert not web_search._safe_public_url(url), f"私网/危险 URL 被放行：{url}"


def test_public_url_allowed_and_blocklist(monkeypatch):
    assert web_search._safe_public_url("https://www.gov.cn/zhengce/2026")
    monkeypatch.setattr(settings, "WEB_SEARCH_BLOCK_DOMAINS", "spam.example, tracker.io")
    assert not web_search._safe_public_url("https://x.spam.example/a")
    assert web_search._safe_public_url("https://www.gov.cn/a")


def test_dedup_strips_tracking_params():
    a = web_search._dedup_key("https://a.com/p?utm_source=bing&id=1")
    b = web_search._dedup_key("https://a.com/p/?id=1&utm_medium=cpc")
    assert a == b


def test_sogou_parser_prefers_data_url_and_strips_tags():
    """搜狗结果带 /link 跳转，但块内有 data-url 原始链接，必须优先取它。"""
    page = '''
    <div class="vrwrap" id="s1"><h3 class="vr-title"><!--awbg-->
      <a name="dttl" href="/link?url=encrypted">广东 <em>独立储能</em> 试行电费补偿</a></h3>
      <div class="fz-mid space-txt clamp2" data-url="https://news.bjx.com.cn/html/1.shtml">
        年度补偿标准 100 元/千瓦</div></div>
    <div class="vrwrap"><h3><a href="https://mp.weixin.qq.com/s?a=1">政策解读</a></h3>
      <div class="str_info">原文摘要在这里</div></div>
    <div id="pagebar"></div>
    '''
    hits = web_search._parse_sogou_html(page, 5)
    assert len(hits) == 2
    assert hits[0]["url"] == "https://news.bjx.com.cn/html/1.shtml"
    assert "独立储能" in hits[0]["title"] and "<em>" not in hits[0]["title"]
    assert "100 元/千瓦" in hits[0]["snippet"]
    assert hits[1]["url"].startswith("https://mp.weixin.qq.com/")
    assert all(h["origin"] == "public_web" for h in hits)


def test_hit_normalizes_and_drops_unsafe():
    good = web_search._hit("<b>标题</b>&amp;", "https://a.com/x", "  摘要&nbsp;内容 ", "mock")
    assert good and good["title"] == "标题&" and good["origin"] == "public_web"
    assert "  " not in good["snippet"]
    assert web_search._hit("t", "http://127.0.0.1/", "s", "mock") is None
    assert len(web_search._clean_query("  a\n\t b " * 100)) <= 200


# ---------------------------------------------------------------- 3. 失败不编
def test_search_never_raises_when_provider_fails(monkeypatch):
    import app.services.web_search as ws
    monkeypatch.setattr(ws, "search", REAL_SEARCH)
    monkeypatch.setattr(settings, "WEB_SEARCH_PROVIDER", "bing_html")
    monkeypatch.setattr(settings, "WEB_SEARCH_ENABLED", True)
    monkeypatch.setitem(ws.PROVIDERS, "bing_html",
                        lambda q, k: (_ for _ in ()).throw(RuntimeError("dns down")))
    monkeypatch.setitem(ws.PROVIDERS, "sogou_html",
                        lambda q, k: (_ for _ in ()).throw(RuntimeError("also down")))
    out = ws.search("储能政策", 5)
    assert out["ok"] is False and out["mode"] == "unavailable"
    assert "dns down" in out["error"] and out["hits"] == []


def test_api_failure_falls_back_to_free_html(monkeypatch):
    import app.services.web_search as ws
    monkeypatch.setattr(ws, "search", REAL_SEARCH)
    monkeypatch.setattr(settings, "WEB_SEARCH_PROVIDER", "serper")
    monkeypatch.setattr(settings, "SERPER_API_KEY", "k")
    monkeypatch.setitem(ws.PROVIDERS, "serper",
                        lambda q, k: (_ for _ in ()).throw(RuntimeError("402")))
    monkeypatch.setitem(ws.PROVIDERS, "sogou_html",
                        lambda q, k: [web_search._hit("兜底结果", "https://a.com/x", "摘要", "sogou_html")])
    monkeypatch.setitem(ws.PROVIDERS, "bing_html",
                        lambda q, k: [web_search._hit("Bing 兜底", "https://b.com/x", "摘要", "bing_html")])
    out = ws.search("储能政策", 5)
    assert out["ok"] and out["degraded"] and out["mode"] == "fallback_html"
    assert out["fallback_from"] == "serper" and "402" in out["error"]
    assert out["hits"][0]["source"] == "sogou_html", "降级应优先用中文相关性更好的搜狗"


def test_disabled_channel_signals_empty(monkeypatch):
    import app.services.web_search as ws
    monkeypatch.setattr(ws, "search", REAL_SEARCH)
    monkeypatch.setattr(settings, "WEB_SEARCH_ENABLED", False)
    out = ws.search("任意问题")
    assert out["ok"] is False and out["mode"] == "disabled"
    ev = agent_runtime.compose_web_evidence(out["hits"], out)
    assert "未检索到公开信息" in ev and "不许用常识" in ev


def test_fetch_page_rejects_url_not_from_search(monkeypatch):
    monkeypatch.setattr(settings, "WEB_SEARCH_ALLOW_FETCH", True)
    def _boom(*a, **k):
        raise AssertionError("不该发出任何请求")
    monkeypatch.setattr(web_search, "_client", _boom)
    out = web_search.fetch_page("https://not-in-allowlist.example/x")
    assert out["ok"] is False and out["error"] == "url_not_from_search_or_expired"


def test_search_registers_allowlist_and_dns_guard(monkeypatch):
    import app.services.web_search as ws
    monkeypatch.setattr(ws, "search", REAL_SEARCH)
    monkeypatch.setattr(settings, "WEB_SEARCH_PROVIDER", "mock")
    monkeypatch.setattr(settings, "WEB_SEARCH_ALLOW_FETCH", True)
    ws.set_mock_results(FAKE)
    out = ws.search("储能", 3)
    assert out["ok"] and out["hits"][0]["url"] in ws._FETCH_ALLOW
    monkeypatch.setattr(ws, "_dns_is_public", lambda host: False)
    blocked = ws.fetch_page(out["hits"][0]["url"])
    assert blocked["error"] == "unsafe_url"


# ---------------------------------------------------------------- 4. 证据与 prompt
def test_web_evidence_has_w_number_and_origin():
    ev = agent_runtime.compose_web_evidence(FAKE, {"provider": "mock", "mode": "api",
                                                   "fetched_at": "2026-11-06"})
    assert "[W1]" in ev and "[W2]" in ev
    assert "https://drc.gd.gov.cn/policy/2026" in ev
    assert "公网" in ev and "不是公司内部资料" in ev
    assert "来源" in ev and "[W编号]" in ev
    assert "[编号]" not in ev, "公网证据块不许用内部片段的编号"


def test_web_evidence_empty_is_explicit():
    ev = agent_runtime.compose_web_evidence([], {"mode": "unavailable", "error": "timeout"})
    assert "未检索到公开信息" in ev and "timeout" in ev
    assert "不许用常识" in ev


def test_dual_channel_prompt_keeps_two_number_systems():
    inner = agent_runtime.compose_evidence([
        {"title": "差旅制度", "snippet": "部门经理 600 元/晚"}])
    web = agent_runtime.compose_web_evidence(FAKE, {"provider": "mock"})
    p = agent_runtime._compose_prompt("住宿限额多少", inner, "", True,
                                      grounded=True, web_ctx=web, web_grounded=True)
    assert "授权检索依据" in p and "[编号]" in p
    assert "[W1]" in p and "【公开网页证据" in p
    assert "双通道边界" in p and "不得用内部制度证明外部事实" in p


def test_plain_skill_prompt_has_no_web_rules():
    p = agent_runtime._compose_prompt("写个通知", "", "", True)
    assert "公开信息红线" not in p and "【公开网页证据" not in p


# ---------------------------------------------------------------- 5. 运行时接线
def _caller():
    return CallerContext(userId="u_1", deptId="d_sales", role="employee", tenantId="t_demo")


def test_business_pack_never_touches_web(monkeypatch):
    calls = []
    monkeypatch.setattr(web_search, "search",
                        lambda *a, **k: (calls.append(1), {"hits": [], "ok": False})[1])
    agent_runtime.run_agent("ag_biz", "做个表", _caller(),
                            system_prompt="你是业务包\n## 结论\n输出表格", adapter_type="llm")
    assert calls == [], "普通业务包不该联网"


def test_web_skill_searches_web_not_internal(monkeypatch):
    out = agent_runtime.run_agent("ag_pub_web_lookup", "查储能政策", _caller(),
                                  system_prompt="你是查证助手\n## 结论\n输出",
                                  adapter_type="llm_web_search")
    tools = {t["tool"] for t in out["tool_calls"]}
    assert "web_search" in tools and "rag_retrieve" not in tools
    assert len(out["web_sources"]) == 2 and out["web_sources"][0]["origin"] == "public_web"
    assert out["citations"] == [], "公网结果不许混进内部 citations"
    assert out["web_search"]["provider"] == "mock"


def test_dual_channel_runs_both(monkeypatch):
    out = agent_runtime.run_agent("ag_pub_cross_check", "内部规定和外部政策打架吗", _caller(),
                                  system_prompt="你是核对助手\n## 结论\n输出",
                                  adapter_type="llm_web_search_rag")
    tools = {t["tool"] for t in out["tool_calls"]}
    assert {"web_search", "rag_retrieve"} <= tools
    assert out["web_sources"] and isinstance(out["citations"], list)


# ---------------------------------------------------------------- 6. 公共库与契约
def test_public_library_web_group(seeded):
    group = [s for s in public_skills.PUBLIC_SKILLS if s["sub_function"] == "联网查证"]
    assert len(group) == 3
    assert {s["adapter_type"] for s in group} == {"llm_web_search", "llm_web_search_rag"}
    assert len(public_skills.PUBLIC_SKILLS) == 28
    assert public_skills.PUBLIC_GROUPS[-1] == "联网查证"
    seq = [s["sub_function"] for s in public_skills.PUBLIC_SKILLS]
    compact = [g for i, g in enumerate(seq) if i == 0 or g != seq[i - 1]]
    assert not [g for g in compact if compact.count(g) > 1]


def test_web_skills_have_contract_and_real_prompt():
    for s in public_skills.PUBLIC_SKILLS:
        if s["sub_function"] != "联网查证":
            continue
        for f in ("when_to_use", "inputs_contract", "outputs_contract"):
            assert s.get(f), f"{s['name']} 缺 {f}"
        assert s["system_prompt"].count("## ") >= 4
        assert len(s["system_prompt"]) >= 350
        assert "[W编号]" in s["system_prompt"] or "[W1]" in s["system_prompt"]
    agent = next(a for a in public_skills.build_agents() if a["agent_id"] == "ag_pub_competitor_scan")
    assert agent["web_fetch_top"] == 2 and agent["default_format"] == "xlsx"


def test_hard_rules_cover_web_and_still_injected_everywhere():
    assert "[W编号]" in public_skills.HARD_RULES
    assert "未检索到公开信息" in public_skills.HARD_RULES
    assert "## " not in public_skills.HARD_RULES
    for a in public_skills.build_agents():
        assert public_skills.HARD_RULES in a["system_prompt"]
        assert a["hard_rules_version"] == "2026-11-06.m26"


def test_adapters_are_disjoint_from_plain_llm():
    assert "llm_web_search" in agent_runtime.WEB_ADAPTERS
    assert "llm_web_search_rag" in agent_runtime.WEB_ADAPTERS & agent_runtime.RETRIEVAL_ADAPTERS
    assert "llm_web_search" not in agent_runtime.RETRIEVAL_ADAPTERS
    assert "llm_retrieval" in agent_runtime.RETRIEVAL_ADAPTERS


# ---------------------------------------------------------------- 7. 路由与契约
def test_search_router_and_status(monkeypatch):
    assert client.post("/api/v1/search/web", json={"query": "储能"}).status_code == 401
    st = client.get("/api/v1/search/status", headers=EMP)
    assert st.status_code == 200 and st.json()["data"]["origin"] == "public_web"
    r = client.post("/api/v1/search/web", headers=EMP,
                    json={"query": "储能政策", "top_k": 3})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["ok"] and len(d["hits"]) == 2 and d["hits"][0]["origin"] == "public_web"
    assert client.post("/api/v1/search/web", headers=EMP,
                       json={"query": "x", "top_k": 99}).status_code == 422


def test_invoke_web_skill_returns_sources(seeded):
    r = client.post("/api/v1/agent/invoke", headers=ADMIN, json={
        "agent_id": "ag_pub_web_lookup", "input": "查一下储能容量补偿政策",
        "stream": False, "output_formats": ["docx"]})
    assert r.status_code == 200, r.text[:300]
    d = r.json()["data"]
    assert len(d["web_sources"]) == 2
    assert d["web_search"]["provider"] == "mock"
    assert {t["tool"] for t in d["tool_calls"]} >= {"web_search"}
    assert not d["citations"], "公网结果不该出现在内部引用里"


def test_openapi_documents_search_paths():
    text = (ROOT / "openapi.yaml").read_text(encoding="utf-8")
    assert "  /search/web:" in text and "  /search/status:" in text
    assert "WebSearchHit:" in text and "public_web" in text
