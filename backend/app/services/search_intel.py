"""M26 · 检索词智能（P1 欠账落地）。

补 M26 文档第八节两条明确写下的 P1 短板：
  1. **检索词自动拆解**：竞品调研现在一次查询，理想是「品类 + 价格 + 渠道」三轮
     再合并，能显著改善聚合页（阿里巴巴商品聚合页）问题。
  2. **来源可信度落地**：目前只在提示词里让模型标「官方/行业/媒体/自媒体」，
     没有代码级打分与排序。

设计取舍（对齐项目「零外部依赖、能立刻真交付、opt-in 默认关」）：
  - ``decompose_query`` 是**确定性**三轮拆解（实体/品类 → 价格/规格 → 渠道/地域），
    不依赖 LLM；可选传入 ``llm_fn`` 在「真模型可用」时做一次 LLM 改写扩展（opt-in）。
  - ``score_credibility`` 纯域名 + 时效启发式，零依赖、可单测。
  - ``search_decomposed`` 复用 web_search.search（含降级链），只是把一次查询拆成
    多轮再合并去重、按可信度排序；**默认不启用**，仅 ``WEB_SEARCH_DECOMPOSE=1`` 时
    由路由切换，对现有 ``search()`` 调用零行为变更。
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from app.core.config import settings
from app.services import web_search

# --------------------------------------------------------------------------- 拆解词典
# 三类触发词：去掉它们剩下「实体/品类」核心；后两类单独成轮。
_QUESTION_STOPS = set(
    "怎么 如何 怎样 什么 哪些 哪个 哪里 为什么 为何 吗 呢 是否 哪家 多少 有哪些 "
    "推荐 对比 比较 分析 调研 查一下 查查 帮我 我想 了解 一下 有啥 什么价 多少钱 "
    "介绍 说说 给我 整理 列一下 看看".split()
)

_PRICE_KW = ["价格", "报价", "多少钱", "单价", "费用", "收费", "成本", "预算",
             "规格", "参数", "型号", "尺寸", "功率", "容量", "配置", "标准", "等级"]
_REGION_KW = ["全国", "国内", "海外", "出口", "内销", "华东", "华南", "华北", "华中",
              "西南", "东北", "广东", "江苏", "浙江", "山东", "北京", "上海", "深圳",
              "广州", "东莞", "长三角", "珠三角", "一线", "二线"]
_CHANNEL_KW = ["渠道", "线上", "线下", "电商", "门店", "经销商", "代理", "批发",
               "淘宝", "京东", "拼多多", "抖音", "天猫", "1688", "美团", "快手"]

_PRICE_TOK_RE = re.compile(r"(\d+\.?\d*\s*(?:元|万元|千元|万|w|W|k|K|￥|¥|美元|美金|欧|\s*%|折))")
_UNIT_RE = re.compile(r"(\d+(?:\.\d+)?\s*(?:kw|KW|Wh|wh|kWh|KWh|度|吨|t|方|平米|㎡|ml|L|升|cm|mm|寸|英寸|人|家|个|套|台))")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w一-鿿%./]", " ", s)).strip()


def decompose_query(query: str, *, llm_fn: Optional[Callable[[str], List[str]]] = None) -> Dict[str, Any]:
    """确定性三轮拆解，可选 LLM 扩展。

    返回 ``{"entity","price_spec","channel_region","sub_queries","llm_used"}``。
    - entity：去掉疑问词/标点后的核心实体/品类。
    - price_spec：命中的价格/规格片段（原样保留，便于拼子查询）。
    - channel_region：命中的渠道/地域片段。
    - sub_queries：合并后的 1–4 条子查询（去重、保留可读性）。
    """
    q = _norm(query)
    tokens = [t for t in re.split(r"[\s,，。、；;]+", q) if t]
    # 第一轮：实体/品类（去疑问词 + 去价格/规格/渠道/地域修饰）
    clean = q
    for s in _QUESTION_STOPS:          # 子串剔除，覆盖「怎么查」这类未空格分词
        clean = clean.replace(s, " ")
    clean = _norm(clean)
    for kw in _PRICE_KW + _REGION_KW + _CHANNEL_KW:
        clean = clean.replace(kw, " ")
    for m in list(_PRICE_TOK_RE.finditer(clean)) + list(_UNIT_RE.finditer(clean)):
        clean = clean.replace(m.group(0), " ")
    entity = _norm(clean)[:60] or q[:60]

    # 第二轮：价格/规格
    price_spec = " ".join(dict.fromkeys(
        [m.group(0) for m in _PRICE_TOK_RE.finditer(q)] +
        [t for t in tokens if any(k in t for k in _PRICE_KW)] +
        [m.group(0) for m in _UNIT_RE.finditer(q)]
    ))

    # 第三轮：渠道/地域
    channel_region = " ".join(dict.fromkeys(
        [t for t in tokens if any(k in t for k in _REGION_KW + _CHANNEL_KW)] +
        [k for k in (_REGION_KW + _CHANNEL_KW) if k in q]
    ))

    sub: List[str] = []
    base = entity
    if price_spec:
        sub.append(f"{base} {price_spec}".strip())
    if channel_region:
        sub.append(f"{base} {channel_region}".strip())
    if not sub:
        sub = [base]
    # 补齐一个不带修饰的裸实体查询，保证召回基线
    if base not in sub:
        sub.insert(0, base)
    # 去重保序、截断 4 条
    seen, out = set(), []
    for s in sub:
        s = s.strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
        if len(out) >= 4:
            break

    llm_used = False
    if llm_fn:
        try:
            extra = [s for s in (llm_fn(query) or []) if isinstance(s, str) and s.strip()]
            for s in extra:
                s = s.strip()
                if s and s not in seen:
                    seen.add(s)
                    out.append(s)
                if len(out) >= 6:
                    break
            llm_used = bool(extra)
        except Exception:
            llm_used = False

    return {
        "entity": entity,
        "price_spec": price_spec,
        "channel_region": channel_region,
        "sub_queries": out,
        "llm_used": llm_used,
    }


# --------------------------------------------------------------------------- 可信度
# 域名后缀/关键词 → 基础分（0.05–1.0）；越高越可信。
_TIER_OFFICIAL = 0.95      # 政府/监管/交易所/央行
_TIER_ACADEMIC = 0.85      # 高校/研究院/标准组织
_TIER_BRAND = 0.78         # 企业官方站（命中品牌白名单）
_TIER_MAINMEDIA = 0.70     # 主流权威媒体
_TIER_INDUSTRY = 0.60      # 行业/垂直媒体
_TIER_REFERENCE = 0.50     # 百科/问答类参考
_TIER_UGC = 0.30           # 论坛/社交/自媒体 UGC

def _domain_score(host: str) -> tuple:
    h = (host or "").lower()
    if not h:
        return _TIER_REFERENCE, "参考"
    if h.endswith(".gov.cn") or ".gov." in h or "gov.cn" in h:
        return _TIER_OFFICIAL, "官方·政府"
    if any(k in h for k in ("sse.com.cn", "szse.cn", "binexchange", "csrc.gov.cn",
                            "pbc.gov.cn", "samr.gov.cn", "ndrc.gov.cn", "stats.gov.cn")):
        return _TIER_OFFICIAL, "官方·监管/交易所"
    if h.endswith(".edu.cn") or ".edu." in h or any(k in h for k in
            ("cas.cn", "ac.cn", "std.org.cn", "iso.org", "ieee.org")):
        return _TIER_ACADEMIC, "学术/标准"
    if any(k in h for k in ("people.com.cn", "xinhuanet.com", "xinhua", "cctv.com",
                            "cntv.cn", "chinanews.com", "gov.cn")):
        return _TIER_MAINMEDIA, "主流媒体"
    if any(k in h for k in ("caixin.com", "21jingji.com", "stcn.com", "cs.com.cn",
                            "yicai.com", "21finance", "thepaper.cn", "qq.com", "sina.com.cn",
                            "163.com", "sohu.com", "ifeng.com", "cnstock.com")):
        return _TIER_MAINMEDIA, "主流媒体"
    if any(k in h for k in ("36kr.com", "ivdnet", "ofweek.com", "qianzhan.com",
                            "askci.com", "chinairn.com", "cbndata", "iiMedia", "analysys")):
        return _TIER_INDUSTRY, "行业媒体"
    if any(k in h for k in ("wikipedia", "wiki", "baike", "zhihu.com")):
        return _TIER_REFERENCE, "百科/问答"
    if any(k in h for k in ("weibo.com", "douyin.com", "xiaohongshu.com", "tieba",
                            "bilibili.com", "kuaishou.com", "toutiao.com", "weixin")):
        return _TIER_UGC, "自媒体/UGC"
    if any(k in h for k in ("taobao.com", "tmall.com", "jd.com", "pinduoduo.com",
                            "1688.com", "aliexpress", "amazon", "alibaba.com")):
        return _TIER_UGC, "电商聚合（非调研级）"
    return _TIER_REFERENCE, "参考"


def score_credibility(hit: Dict[str, Any]) -> Dict[str, Any]:
    """给单条命中打分：域名基础分 + 时效修正 → 0.05–1.0，附分级标签。"""
    try:
        host = urlparse(str(hit.get("url") or "")).hostname or ""
    except Exception:
        host = ""
    base, tier = _domain_score(host)

    # 时效修正：发布时间越新越可信
    recency = 0.0
    pub = str(hit.get("published_at") or hit.get("fetched_at") or "")
    m = re.search(r"(20\d{2})[-/年.]?(\d{1,2})?", pub)
    if m:
        year = int(m.group(1))
        cur = 2026
        age = cur - year
        if age <= 1:
            recency = 0.05
        elif age >= 3:
            recency = -0.10
    score = round(max(0.05, min(1.0, base + recency)), 3)
    return {"score": score, "tier": tier, "domain": host, "recency_adj": recency}


# --------------------------------------------------------------------------- 拆解检索
def search_decomposed(query: str, top_k: Optional[int] = None, *,
                      force_provider: Optional[str] = None,
                      fetch_top: int = 0,
                      llm_fn: Optional[Callable[[str], List[str]]] = None) -> Dict[str, Any]:
    """三轮拆解后分别检索、合并去重、按可信度排序。永不抛异常。

    与 ``web_search.search`` 字段兼容，额外返回 ``decomposed`` 与每条 hit 的
    ``credibility``。未拆解（单 token / 拆解后仅 1 条）自动退化为单次检索。
    """
    from app.services.web_search import _clean_query, _dedup_key  # 同包私有，复用去重

    t0 = __import__("time").time()
    q = _clean_query(query)
    k = min(max(1, top_k or settings.WEB_SEARCH_MAX_RESULTS), 10)
    dec = decompose_query(q, llm_fn=llm_fn)
    subs = dec["sub_queries"]

    if len(subs) <= 1:
        # 退化：与单次检索等价，但补上 credibility 字段保持一致响应形状
        r = web_search.search(q, top_k=k, force_provider=force_provider, fetch_top=fetch_top)
        for h in r["hits"]:
            h["credibility"] = score_credibility(h)
        return {**r, "decomposed": dec, "elapsed_ms": int((__import__("time").time() - t0) * 1000)}

    merged, seen = [], set()
    per_round = max(2, k // len(subs)) + 1
    for sq in subs:
        try:
            r = web_search.search(sq, top_k=per_round, force_provider=force_provider, fetch_top=0)
        except Exception:
            continue
        for h in r.get("hits", []):
            key = _dedup_key(h.get("url", ""))
            if key in seen:
                continue
            seen.add(key)
            h["credibility"] = score_credibility(h)
            h["matched_sub_query"] = sq
            merged.append(h)

    merged.sort(key=lambda h: (h.get("credibility", {}).get("score", 0.5), h.get("provider", "")),
                reverse=True)
    merged = merged[:k]

    ok = bool(merged)
    provider = (force_provider or web_search.configured_provider())
    return {
        "query": q,
        "top_k": k,
        "hits": merged,
        "origin": web_search.ORIGIN,
        "ok": ok,
        "degraded": False,
        "mode": "decomposed",
        "provider": provider,
        "fallback_from": "",
        "error": "",
        "decomposed": dec,
        "elapsed_ms": int((__import__("time").time() - t0) * 1000),
        "fetched_at": web_search.datetime.now(web_search.timezone.utc).isoformat(timespec="seconds"),
    }
