"""M26 · 联网搜索通道（公网事实获取）。

为什么单独一层：
    M22–M25 的所有信息都来自「用户给的输入 / 上传的附件 / 企业内部知识库」。
    平台没有一条通往公网的正规通道，于是「联网查资料」「竞品调研」这两类
    每个岗位都会遇到的活只能靠模型凭常识编——比不答更危险。
    本模块把「查外网」做成与 RAG 平行、但**来源标签完全隔离**的工具。

三条不可退让的边界：
    1. **公网 ≠ 内部制度**。搜索结果永远带 `origin="public_web"`，在提示词里
       用 `[W1][W2]` 编号；内部知识库片段用 `[1][2]`。模型不许把竞品网页
       写成「公司规定」，也不许拿内部制度去佐证公网事实。
    2. **失败不许编**。通道禁用 / provider 挂了 / 零命中，都要给模型一个明确的
       空信号（compose_web_evidence 里），不允许拿行业惯例补一段像答案的话。
    3. **不把网页写进知识库**。公网结果只在本轮上下文里存活，不入库、不参与
       向量检索、不跨租户共享——避免把外部内容变成「授权片段」绕过 RBAC。

Provider 策略（auto）：
    有 key 用官方 API（博查 / Serper / Tavily / Bing v7 / 自建 SearXNG）；
    都没配走免费网页抓取，顺序 sogou_html → bing_html。
    中文长 query 实测搜狗相关性明显更好（同一句储能政策，Bing 前三条是
    「广东省_百度百科 / 省政府门户」，搜狗前三条全是政策与补偿标准原文），
    所以搜狗当前是免费首选、Bing 互为备份。
    provider 失败自动降级并标 `degraded=True`，绝不 500。
    生产建议配官方 key：网页抓取依赖对方页面结构，只作兜底与本地演示。

安全：
    - query 截断、去控制字符，结果条数上限 10；
    - URL 只认 http/https，拒绝私网/回环/保留地址字面 IP 与 .internal/.local；
    - 域名黑名单可配；跟踪参数归一化后去重；
    - 抓正文只允许「本进程刚搜到的 URL」（短期白名单），且二次做 DNS 解析校验，
      外部无法通过接口传任意 URL 触发内网请求（SSRF）。
"""
from __future__ import annotations

import base64
import html as html_lib
import ipaddress
import re
import socket
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx

from app.core.config import settings

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

ORIGIN = "public_web"

# 只在本轮允许抓取正文的 URL（url -> 登记时间）。接口不暴露自定义 URL，
# 因此即便搜索结果里混进恶意链接，也无法被外部主动指定去请求。
_FETCH_ALLOW: Dict[str, float] = {}
FETCH_ALLOW_TTL_S = 300
_FETCH_MAX_BYTES = 512 * 1024

_TRACK_PREFIXES = ("utm_", "spm", "from", "src", "ref", "fbclid", "gclid", "msclkid")


# --------------------------------------------------------------------------- 工具
def _clean_text(s: Any, limit: int = 400) -> str:
    """去标签 / 反转义 / 压空白 / 截断。搜索摘要里常混 <strong> 与 &nbsp;。"""
    t = html_lib.unescape(re.sub(r"<[^>]+>", "", str(s or "")))
    t = re.sub(r"[\x00-\x1f\x7f\u200b-\u200f\u202a-\u202e]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:limit]


def _clean_query(q: Any) -> str:
    t = re.sub(r"[\x00-\x1f\x7f]", " ", str(q or ""))
    return re.sub(r"\s+", " ", t).strip()[:200]


def _unwrap_bing_redirect(url: str) -> str:
    """bing.com/ck/a?u=a1<base64url> 形式的跳转换回真实目标链接。"""
    if "bing.com/ck/a" not in url:
        return url
    raw = parse_qs(urlparse(url).query).get("u", [""])[0]
    if not raw.startswith("a1"):
        return url
    body = raw[2:]
    try:
        return base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode("utf-8")
    except Exception:
        return url


def _blocked_domains() -> List[str]:
    raw = getattr(settings, "WEB_SEARCH_BLOCK_DOMAINS", "") or ""
    return [d.strip().lower().lstrip(".") for d in raw.split(",") if d.strip()]


def _safe_public_url(url: str) -> bool:
    """字面层 SSRF 校验：协议、主机名、私网字面 IP、黑名单、端口。"""
    try:
        p = urlparse(url)
    except Exception:
        return False
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    host = p.hostname.lower().strip(".")
    if (not host or host in ("localhost", "metadata", "metadata.google.internal")
            or host.endswith((".localhost", ".local", ".internal", ".localdomain"))):
        return False
    try:
        ip = ipaddress.ip_address(host)
        if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_loopback:
            return False
    except ValueError:
        pass  # 域名，抓正文时再做 DNS 校验
    try:
        if p.port is not None and p.port not in (80, 443):
            return False
    except ValueError:
        return False
    for bad in _blocked_domains():
        if host == bad or host.endswith("." + bad):
            return False
    return True


def _dedup_key(url: str) -> str:
    """去掉跟踪参数后的规范化 URL，用于同页去重。"""
    try:
        p = urlparse(url)
        q = [(k, v) for k, v in parse_qs(p.query, keep_blank_values=True).items()
             if not k.lower().startswith(_TRACK_PREFIXES)]
        return urlunparse((p.scheme, p.netloc.lower(), p.path.rstrip("/"), "",
                           urlencode(q, doseq=True), ""))
    except Exception:
        return url


def _hit(title: Any, url: Any, snippet: Any, source: str,
         published: Any = "") -> Optional[Dict[str, Any]]:
    u = _unwrap_bing_redirect(html_lib.unescape(str(url or "")))
    if not _safe_public_url(u):
        return None
    return {
        "title": _clean_text(title, 120) or "(无标题)",
        "url": u,
        "snippet": _clean_text(snippet, 400),
        "origin": ORIGIN,
        "source": source,
        "published_at": _clean_text(published, 40),
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


def _collect(raw: List[Optional[Dict[str, Any]]], top_k: int) -> List[Dict[str, Any]]:
    seen, out = set(), []
    for h in raw:
        if not h:
            continue
        k = _dedup_key(h["url"])
        if k in seen:
            continue
        seen.add(k)
        out.append(h)
        if len(out) >= top_k:
            break
    for u, t in list(_FETCH_ALLOW.items()):
        if time.time() - t > FETCH_ALLOW_TTL_S:
            _FETCH_ALLOW.pop(u, None)
    for h in out:
        h.setdefault("origin", ORIGIN)   # 自定义 provider 漏字段也不能丢掉来源标签
        _FETCH_ALLOW[h["url"]] = time.time()
    return out


def _client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(settings.WEB_SEARCH_TIMEOUT_S, connect=5.0),
                        follow_redirects=True, headers={"User-Agent": UA})


# --------------------------------------------------------------------------- providers
def _search_sogou_html(query: str, top_k: int) -> List[Dict[str, Any]]:
    """免费兜底首选：搜狗网页版。

    实测中文长 query 的相关性明显好于 Bing 网页版（同一句「广东 独立储能
    容量补偿 政策 2026」，Bing 前三条是「广东省_百度百科 / 省政府门户」，
    搜狗前三条全是政策与电费补偿原文）。搜狗结果块里通常带 `data-url`
    原始链接，优先取它，避免把 /link?url= 跳转页写进产出物来源表。
    """
    with _client() as c:
        r = c.get("https://www.sogou.com/web", params={"query": query})
        r.raise_for_status()
        text = r.text
    return _parse_sogou_html(text, top_k)


def _parse_sogou_html(text: str, top_k: int) -> List[Dict[str, Any]]:
    """纯解析函数：单独抽出以便离线回归，页面改版时改这一处即可。"""
    blocks = re.findall(
        r'<div class="vrwrap".*?(?=<div class="vrwrap"|<div id="pagebar"|$)',
        text, re.S)
    raw = []
    for b in blocks:
        h3 = re.search(r"<h3[^>]*>(.*?)</h3>", b, re.S)
        if not h3:
            continue
        a = re.search(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', h3.group(1), re.S)
        if not a:
            continue
        data = re.search(r'data-url="([^"]+)"', b)
        url = html_lib.unescape((data.group(1) if data else a.group(1)) or "")
        if url.startswith("/"):
            url = "https://www.sogou.com" + url
        m = re.search(
            r'<div class="[^"]*(?:space-txt|str_info|text-layout)[^"]*"[^>]*>(.*?)</div>',
            b, re.S)
        raw.append(_hit(a.group(2), url, m.group(1) if m else "", "sogou_html"))
    return _collect(raw, top_k)


def _search_bing_html(query: str, top_k: int) -> List[Dict[str, Any]]:
    """免费兜底：抓 cn.bing.com 结果页。无 key 可用，但受对方页面改版影响。"""
    with _client() as c:
        r = c.get("https://www.bing.com/search",
                  params={"q": query, "setlang": "zh-CN", "count": max(10, top_k)})
        r.raise_for_status()
        blocks = re.findall(r'<li class="b_algo".*?</li>', r.text, re.S)
    raw = []
    for b in blocks:
        m = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', b, re.S)
        if not m:
            continue
        paras = [_clean_text(x, 500) for x in re.findall(r"<p[^>]*>(.*?)</p>", b, re.S)]
        paras = [x for x in paras if len(x) > 15]
        raw.append(_hit(m.group(2), m.group(1), max(paras, key=len) if paras else "",
                        "bing_html"))
    return _collect(raw, top_k)


def _search_bing_api(query: str, top_k: int) -> List[Dict[str, Any]]:
    key = settings.BING_SEARCH_KEY
    with _client() as c:
        r = c.get("https://api.bing.microsoft.com/v7.0/search",
                  params={"q": query, "count": top_k, "mkt": "zh-CN"},
                  headers={"Ocp-Apim-Subscription-Key": key})
        r.raise_for_status()
        pages = (r.json().get("webPages") or {}).get("value") or []
    return _collect([_hit(x.get("name"), x.get("url"), x.get("snippet"), "bing_api",
                          x.get("dateLastCrawled")) for x in pages], top_k)


def _search_bocha(query: str, top_k: int) -> List[Dict[str, Any]]:
    """博查：中文网页检索，返回结构兼容 Bing v7。"""
    with _client() as c:
        r = c.post("https://api.bochaai.com/v1/web-search",
                   json={"query": query, "count": top_k, "freshness": "noLimit"},
                   headers={"Authorization": f"Bearer {settings.BOCHA_API_KEY}"})
        r.raise_for_status()
        pages = ((r.json().get("data") or {}).get("webPages") or {}).get("value") or []
    return _collect([_hit(x.get("name"), x.get("url"), x.get("snippet"), "bocha",
                          x.get("dateLastCrawled") or x.get("datePublished")) for x in pages], top_k)


def _search_serper(query: str, top_k: int) -> List[Dict[str, Any]]:
    with _client() as c:
        r = c.post("https://google.serper.dev/search",
                   json={"q": query, "num": top_k, "gl": "cn", "hl": "zh-cn"},
                   headers={"X-API-KEY": settings.SERPER_API_KEY})
        r.raise_for_status()
        rows = r.json().get("organic") or []
    return _collect([_hit(x.get("title"), x.get("link"), x.get("snippet"), "serper",
                          x.get("date")) for x in rows], top_k)


def _search_tavily(query: str, top_k: int) -> List[Dict[str, Any]]:
    with _client() as c:
        r = c.post("https://api.tavily.com/search",
                   json={"api_key": settings.TAVILY_API_KEY, "query": query,
                         "max_results": top_k, "search_depth": "basic",
                         "include_answer": False})
        r.raise_for_status()
        rows = r.json().get("results") or []
    return _collect([_hit(x.get("title"), x.get("url"), x.get("content"), "tavily",
                          x.get("published_date")) for x in rows], top_k)


def _search_searxng(query: str, top_k: int) -> List[Dict[str, Any]]:
    base = settings.SEARXNG_BASE_URL.rstrip("/")
    with _client() as c:
        r = c.get(f"{base}/search", params={"q": query, "format": "json",
                                            "language": "zh-CN", "safesearch": 1})
        r.raise_for_status()
        rows = r.json().get("results") or []
    return _collect([_hit(x.get("title"), x.get("url"), x.get("content"), "searxng",
                          x.get("publishedDate")) for x in rows], top_k)


_MOCK_RESULTS: List[Dict[str, Any]] = []


def set_mock_results(rows: List[Dict[str, Any]]) -> None:
    """测试 / 离线演示注入固定结果，不触网。"""
    global _MOCK_RESULTS
    _MOCK_RESULTS = list(rows)


def _search_mock(query: str, top_k: int) -> List[Dict[str, Any]]:
    return _collect([_hit(x.get("title"), x.get("url"), x.get("snippet"),
                          x.get("source", "mock"), x.get("published_at"))
                     for x in _MOCK_RESULTS], top_k)


PROVIDERS: Dict[str, Callable[[str, int], List[Dict[str, Any]]]] = {
    "sogou_html": _search_sogou_html,
    "bing_html": _search_bing_html,
    "bing_api": _search_bing_api,
    "bocha": _search_bocha,
    "serper": _search_serper,
    "tavily": _search_tavily,
    "searxng": _search_searxng,
    "mock": _search_mock,
}

API_PROVIDERS = ("bocha", "serper", "tavily", "bing_api", "searxng")


def configured_provider() -> str:
    """auto 解析结果：有 key 优先官方通道，否则免费网页抓取。"""
    chosen = (settings.WEB_SEARCH_PROVIDER or "auto").strip().lower()
    if chosen != "auto":
        return chosen if chosen in PROVIDERS else "sogou_html"
    for name, env_key in (("bocha", "BOCHA_API_KEY"), ("serper", "SERPER_API_KEY"),
                          ("tavily", "TAVILY_API_KEY"), ("bing_api", "BING_SEARCH_KEY"),
                          ("searxng", "SEARXNG_BASE_URL")):
        if (getattr(settings, env_key, "") or "").strip():
            return name
    return "sogou_html"


FREE_HTML_PROVIDERS = ("sogou_html", "bing_html")


def status() -> Dict[str, Any]:
    """给前端与运维看的状态：只说有没有配，不回显密钥。"""
    p = configured_provider()
    return {
        "enabled": bool(settings.WEB_SEARCH_ENABLED),
        "provider": p,
        "mode": ("disabled" if not settings.WEB_SEARCH_ENABLED
                 else "free_html" if p in FREE_HTML_PROVIDERS else "api"),
        "configured": {k: bool((getattr(settings, k, "") or "").strip())
                       for k in ("BOCHA_API_KEY", "SERPER_API_KEY", "TAVILY_API_KEY",
                                 "BING_SEARCH_KEY", "SEARXNG_BASE_URL")},
        "providers": sorted(PROVIDERS),
        "allow_fetch": bool(settings.WEB_SEARCH_ALLOW_FETCH),
        "timeout_s": settings.WEB_SEARCH_TIMEOUT_S,
        "max_results": settings.WEB_SEARCH_MAX_RESULTS,
        "origin": ORIGIN,
        "note": "公网结果只在当轮上下文使用，不写入企业内部知识库",
    }


# --------------------------------------------------------------------------- 正文抓取
def _dns_is_public(host: str) -> bool:
    """DNS 层复核：任一解析结果是私网/保留地址就整条拒绝。"""
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if not ip.is_global or ip.is_loopback or ip.is_reserved or ip.is_link_local:
            return False
    return True


def fetch_page(url: str, max_chars: Optional[int] = None) -> Dict[str, Any]:
    """抓一篇搜索结果的正文（只取纯文本，不执行脚本、不落库）。

    双重限制：URL 必须在最近一次 search() 登记的短期白名单里，且通过
    _safe_public_url + DNS 公网校验。任何一步不过都不发请求。
    """
    limit = max_chars or settings.WEB_SEARCH_FETCH_CHARS
    if not settings.WEB_SEARCH_ALLOW_FETCH:
        return {"ok": False, "text": "", "error": "fetch_disabled"}
    if time.time() - _FETCH_ALLOW.get(url, 0) > FETCH_ALLOW_TTL_S:
        return {"ok": False, "text": "", "error": "url_not_from_search_or_expired"}
    if not _safe_public_url(url) or not _dns_is_public(urlparse(url).hostname or ""):
        return {"ok": False, "text": "", "error": "unsafe_url"}
    try:
        with _client() as c:
            with c.stream("GET", url) as r:
                r.raise_for_status()
                ctype = r.headers.get("content-type", "").lower()
                if not any(t in ctype for t in ("text/html", "text/plain", "application/xhtml")):
                    return {"ok": False, "text": "", "error": "unsupported_content_type"}
                chunks, size = [], 0
                for chunk in r.iter_bytes(8192):
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= _FETCH_MAX_BYTES:
                        break
        raw = b"".join(chunks).decode(r.encoding or "utf-8", errors="ignore")
        raw = re.sub(r"<(script|style|noscript|svg)\b.*?</\1>", " ", raw,
                     flags=re.S | re.I)
        text = _clean_text(raw, limit)
        return {"ok": bool(text), "text": text, "chars": len(text),
                "truncated": size >= _FETCH_MAX_BYTES}
    except Exception as exc:
        return {"ok": False, "text": "", "error": f"{type(exc).__name__}: {exc}"[:200]}


# --------------------------------------------------------------------------- 对外主入口
def search(query: str, top_k: Optional[int] = None, *,
           force_provider: Optional[str] = None, fetch_top: int = 0) -> Dict[str, Any]:
    """执行一次公网检索。**永不抛异常**：失败以 ok=False / degraded 返回。"""
    t0 = time.time()
    q = _clean_query(query)
    k = min(max(1, top_k or settings.WEB_SEARCH_MAX_RESULTS), 10)
    base = {"query": q, "top_k": k, "hits": [], "origin": ORIGIN,
            "elapsed_ms": 0, "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if not q:
        return {**base, "ok": False, "degraded": True, "mode": "invalid_query",
                "provider": configured_provider(), "error": "empty query"}
    if not settings.WEB_SEARCH_ENABLED:
        return {**base, "ok": False, "degraded": True, "mode": "disabled",
                "provider": configured_provider(), "error": "web search disabled by config"}

    provider = (force_provider or configured_provider()).strip().lower()
    if provider not in PROVIDERS:
        provider = "sogou_html"
    mode = ("free_html" if provider in FREE_HTML_PROVIDERS
            else "mock" if provider == "mock" else "api")
    error, degraded, fallback_from = "", False, ""
    try:
        hits = PROVIDERS[provider](q, k)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:200]
        degraded = True
        hits, mode = [], "unavailable"
        # 两个免费源互为备份；官方通道挂了先试搜狗再试 Bing。
        # 降级必须标 degraded，不能把网页抓取结果伪装成 API 结果。
        if provider == "sogou_html":
            chain = ["bing_html"]
        elif provider == "bing_html":
            chain = ["sogou_html"]
        elif provider == "mock":
            chain = []
        else:
            chain = list(FREE_HTML_PROVIDERS)
        for alt in chain:
            try:
                alt_hits = PROVIDERS[alt](q, k)
                if alt_hits:
                    hits, mode, fallback_from = alt_hits, "fallback_html", provider
                    break
            except Exception as exc2:
                error = (error + f" | {alt}: {type(exc2).__name__}")[:200]

    if fetch_top > 0 and hits and settings.WEB_SEARCH_ALLOW_FETCH:
        for h in hits[:min(fetch_top, 3)]:
            page = fetch_page(h["url"])
            if page.get("ok"):
                h["content"] = page["text"]
            else:
                h["fetch_error"] = _clean_text(page.get("error", ""), 120)

    return {**base, "hits": hits, "ok": bool(hits), "degraded": degraded,
            "mode": mode, "provider": provider, "fallback_from": fallback_from,
            "error": error, "elapsed_ms": int((time.time() - t0) * 1000)}
