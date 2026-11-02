"""知识库服务（第5/18/36章）：混合检索 + 父子切分 + 重排 + 单一过滤编排。

三元存储：PostgreSQL 权限元数据 + Qdrant 向量 + MinIO 文件，由本服务统一编排。
决策：中文 Embedding 选 bge（bge-m3，维度 1024）——见 settings.EMBEDDING_DIM。

M2 业务填充（相对 M0 骨架）：
  - 真实混合检索：BM25（字符 bigram 分词）+ 向量余弦 + RRF 融合，不再依赖伪向量单路。
  - 父子文档切分：parent 文档按句切为 child chunk，检索在 child 上展开，结果聚合回 parent。
  - 重排层（W2）：cross-encoder 懒加载，未安装时退化为「词重叠+向量」启发式打分。
  - 内存语料覆盖 4 种 scope / 多部门 / 跨租户，演示第18章单一过滤点。
重客户端（qdrant/sqlalchemy/minio/sentence_transformers）均懒加载；未安装时提供确定性
实现，保证应用可导入、可演示且可跑真实检索质量评估。
"""
import math
import re
from collections import defaultdict

from app.core.config import settings
from app.core.filter import single_filter
from app.models.caller import CallerContext

# ─────────────────────────────────────────────────────────────────────────────
# Embedding：bge 懒加载（单例缓存），否则确定性伪向量（与 bge-m3 维度对齐，CI/离线用）。
# 安全阀：加载失败一次即永久回退，绝不反复重试（避免测试/演示被网络下载挂住）。
# ─────────────────────────────────────────────────────────────────────────────
_st_model = None
_st_failed = False
_ce_model = None
_ce_failed = False


def _get_ce_model():
    """单例加载 CrossEncoder；未开关/已失败/加载异常均返回 None。"""
    global _ce_model, _ce_failed
    if _ce_model is not None or _ce_failed or not settings.USE_LOCAL_EMBEDDING:
        return _ce_model
    try:
        from sentence_transformers import CrossEncoder  # type: ignore
        _ce_model = CrossEncoder(settings.RERANK_MODEL)
    except Exception:
        _ce_failed = True
    return _ce_model


def _get_st_model():
    """单例加载 SentenceTransformer；未开关/已失败/加载异常均返回 None。"""
    global _st_model, _st_failed
    if _st_model is not None or _st_failed or not settings.USE_LOCAL_EMBEDDING:
        return _st_model
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
        _st_model = SentenceTransformer(settings.EMBEDDING_MODEL)
    except Exception:
        _st_failed = True  # 一次失败永久回退，不阻塞调用方
    return _st_model


def embed(text: str) -> list[float]:
    m = _get_st_model()
    if m is not None:
        try:
            return m.encode(text, normalize_embeddings=True).tolist()
        except Exception:
            pass
    dim = settings.EMBEDDING_DIM
    vec = [0.0] * dim
    for i, ch in enumerate(text.encode("utf-8")):
        vec[(ch + i) % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _tokenize(text: str) -> set[str]:
    """中文友好分词：去标点 → 单字 + 相邻 bigram + 英文/数字词。"""
    text = re.sub(r"[\s\W]+", " ", text).lower().strip()
    chars = [c for c in text if c.strip()]
    toks = set(chars)
    for i in range(len(chars) - 1):
        toks.add(chars[i] + chars[i + 1])
    for w in re.findall(r"[a-z0-9]+", text):
        toks.add(w)
    return toks


def _split_sentences(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"[。！？；\n]+", text) if p.strip()]


# ─────────────────────────────────────────────────────────────────────────────
# 内存语料（parent 文档）：覆盖 4 种 scope / 多部门 / 跨租户
# ─────────────────────────────────────────────────────────────────────────────
_DEMO_PARENTS: list[dict] = [
    {"doc_id": "doc_8831", "tenant_id": "t_demo", "title": "五一营销活动方案 v3",
     "content": "建议分三档：① 到店礼 ② 满减 ③ 老带新。到店礼为小样赠品，满减门槛设 199 元，老带新返 20 元券。活动周期 5 月 1 日至 5 月 7 日。",
     "scope": "department", "owner": "u_1024", "dept": "d_sales", "source": "产品手册 v3"},
    {"doc_id": "doc_9001", "tenant_id": "t_demo", "title": "销售部话术集",
     "content": "到店礼：到店即赠小样，引导加企微。满减：满 199 减 30，与会员折扣不可叠加。异议处理：价格贵可用赠品对冲。",
     "scope": "department", "owner": "u_1024", "dept": "d_sales", "source": "内部"},
    {"doc_id": "doc_7002", "tenant_id": "t_demo", "title": "设计部品牌规范",
     "content": "品牌主色使用规范：主色为科技蓝，辅色为浅灰。字体统一使用思源黑体。对外物料须通过设计部审核。",
     "scope": "department", "owner": "u_3000", "dept": "d_design", "source": "内部"},
    {"doc_id": "doc_5005", "tenant_id": "t_demo", "title": "全员考勤制度",
     "content": "弹性工作制说明：核心工时 10:00-16:00 须在岗。加班需提前审批。年假按司龄递增，最高 15 天。",
     "scope": "global", "owner": "u_admin_01", "dept": "d_hr", "source": "制度"},
    {"doc_id": "doc_6006", "tenant_id": "t_demo", "title": "张三的私人笔记",
     "content": "我的客户跟进：A 客户倾向赠品方案，B 客户关注价格。下周约 C 客户面谈。",
     "scope": "personal", "owner": "u_1024", "dept": "d_sales", "source": "个人"},
    {"doc_id": "doc_8008", "tenant_id": "t_other", "title": "友商租户文档",
     "content": "竞品定价策略与渠道分布，属于外部租户，不应被本租户看到。",
     "scope": "global", "owner": "u_other", "dept": "d_other", "tenant_id": "t_other", "source": "外部"},
    {"doc_id": "doc_9102", "tenant_id": "t_demo", "title": "财务报销流程",
     "content": "报销需在系统中提交发票与事由。差旅费上限按职级设定。审批链：直属主管→财务。每月 25 日截止。",
     "scope": "global", "owner": "u_admin_01", "dept": "d_fin", "source": "制度"},
    {"doc_id": "doc_9203", "tenant_id": "t_demo", "title": "销售目标拆解方法",
     "content": "季度目标按区域与产品线拆解到周。使用漏斗模型监控转化。周会复盘转化率与客单价。",
     "scope": "department", "owner": "u_1024", "dept": "d_sales", "source": "内部"},
    {"doc_id": "doc_9304", "tenant_id": "t_demo", "title": "设计稿交付标准",
     "content": "交付需包含源文件与切图。命名规范按项目编号。标注图层与尺寸。交付前自检清单。",
     "scope": "department", "owner": "u_3000", "dept": "d_design", "source": "内部"},
    {"doc_id": "doc_9405", "tenant_id": "t_demo", "title": "招聘面试指引",
     "content": "面试分三轮：业务、主管、HR。使用结构化评分表。背调在 offer 前完成。薪酬范围按级别。",
     "scope": "global", "owner": "u_admin_01", "dept": "d_hr", "source": "制度"},
    {"doc_id": "doc_9506", "tenant_id": "t_demo", "title": "客户分级管理规则",
     "content": "客户按贡献分 A/B/C 三级。A 级配备专属顾问。C 级走自助服务。季度复核等级。",
     "scope": "department", "owner": "u_1024", "dept": "d_sales", "source": "内部"},
    {"doc_id": "doc_9607", "tenant_id": "t_demo", "title": "合同审批权限表",
     "content": "合同金额分级审批：10 万以下主管批，10-50 万总监批，50 万以上 CEO 批。法务必审。",
     "scope": "global", "owner": "u_admin_01", "dept": "d_fin", "source": "制度"},
    {"doc_id": "doc_9708", "tenant_id": "t_demo", "title": "私域运营规范",
     "content": "企微社群每日推送不超过 2 条。禁止硬广刷屏。引导至小程序成交。话术须经合规审核。",
     "scope": "department", "owner": "u_1024", "dept": "d_sales", "source": "内部"},
    {"doc_id": "doc_9809", "tenant_id": "t_demo", "title": "视觉素材库使用说明",
     "content": "素材库按主题分类。商用需授权。禁止外发未定稿。下载记录可追溯。",
     "scope": "department", "owner": "u_3000", "dept": "d_design", "source": "内部"},
    {"doc_id": "doc_9900", "tenant_id": "t_demo", "title": "信息安全红线",
     "content": "禁止外发客户数据。密码定期更换。钓鱼邮件上报安全组。离职务必回收权限。",
     "scope": "global", "owner": "u_admin_01", "dept": "d_hr", "source": "制度"},
    {"doc_id": "doc_9101", "tenant_id": "t_demo", "title": "市场费用预算模板",
     "content": "预算按渠道拆分：投流、地推、物料。月度滚动预测。超预算预警。负责人签字。",
     "scope": "department", "owner": "u_1024", "dept": "d_sales", "source": "内部"},
    {"doc_id": "doc_9202", "tenant_id": "t_demo", "title": "设计评审 checklist",
     "content": "评审关注对比度、可达性、品牌一致。移动端适配检查。多语言预留。交付前过 checklist。",
     "scope": "department", "owner": "u_3000", "dept": "d_design", "source": "内部"},
    {"doc_id": "doc_9303", "tenant_id": "t_demo", "title": "新人入职指引",
     "content": "入职首日领设备与账号。一周内完成合规培训。导师制带教。首月目标设定。",
     "scope": "global", "owner": "u_admin_01", "dept": "d_hr", "source": "制度"},
]

# 演示语料可关（M8）：真实语料进场后设 SEED_DEMO_CORPUS=0，
# 避免 18 篇演示文档与真实文档抢排名、把召回指标撑成假象。
PARENTS: list[dict] = list(_DEMO_PARENTS) if settings.SEED_DEMO_CORPUS else []


_CHILDREN: list[dict] = []
_DF: dict[str, int] = {}
_IDF: dict[str, float] = {}
_AVGL: float = 1.0
_N: int = 0
_INDEX_BUILT = False


def _reindex() -> None:
    """（重）构建内存索引：切分 children、向量化、BM25 统计。"""
    global _CHILDREN, _DF, _IDF, _AVGL, _N, _INDEX_BUILT
    _CHILDREN = []
    for p in PARENTS:
        for i, sent in enumerate(_split_sentences(p["content"])):
            _CHILDREN.append({
                "child_id": f"{p['doc_id']}_c{i}",
                "parent_id": p["doc_id"],
                "text": sent,
                "title": p["title"],
                "scope": p["scope"],
                "owner": p["owner"],
                "dept": p["dept"],
                "tenant_id": p["tenant_id"],
                "source": p["source"],
                "_vec": embed(sent),
                "_toks": _tokenize(sent),
            })
    _N = len(_CHILDREN)
    _DF = defaultdict(int)
    for c in _CHILDREN:
        for t in c["_toks"]:
            _DF[t] += 1
    _IDF = {t: math.log(1 + (_N - df + 0.5) / (df + 0.5)) for t, df in _DF.items()}
    _AVGL = (sum(len(c["_toks"]) for c in _CHILDREN) / _N) if _N else 1.0
    _INDEX_BUILT = True


def get_parents() -> list[dict]:
    """公开语料，供评测集生成（golden）复用，避免双份定义。"""
    return PARENTS


def corpus_size() -> dict:
    """语料规模画像（评测报告与试点看板共用）：父文档数 / 子块数 / 字节量。"""
    if not _INDEX_BUILT:
        _reindex()
    return {
        "documents": len(PARENTS),
        "children": len(_CHILDREN),
        "chars": sum(len(p.get("content") or "") for p in PARENTS),
        "depts": len({p.get("dept") for p in PARENTS if p.get("dept")}),
        "demo_documents": demo_corpus_size(),
    }


def demo_corpus_size() -> int:
    """当前生效的演示语料篇数（用于区分「演示环境指标」与「真实语料指标」）。"""
    return len(_DEMO_PARENTS) if settings.SEED_DEMO_CORPUS else 0


def find_by_title(title: str) -> str | None:
    """按标题找 doc_id（真实语料 doc_id 随机，外部 golden 用标题更可写）。"""
    t = (title or "").strip()
    if not t:
        return None
    for p in PARENTS:
        if p["title"] == t:
            return p["doc_id"]
    for p in PARENTS:
        if t in p["title"]:
            return p["doc_id"]
    return None


def meta_of(doc_id: str) -> dict | None:
    """取父文档的权限 meta（外部 golden 未给 viewer 时按文档归属推导）。"""
    for p in PARENTS:
        if p["doc_id"] == doc_id:
            return p
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 检索核心
# ─────────────────────────────────────────────────────────────────────────────
def _bm25(qtoks: set[str], doc_toks: set[str]) -> float:
    k1, b = 1.5, 0.75
    dl = len(doc_toks)
    tf = defaultdict(int)
    for t in doc_toks:
        tf[t] += 1
    score = 0.0
    for t in qtoks:
        idf = _IDF.get(t)
        if idf is None:
            continue
        f = tf[t]
        score += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / _AVGL))
    return score


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


RRF_K = 60


def rerank(query: str, candidates: list[dict]) -> list[tuple[dict, float]]:
    """重排层（第36章 W2）：cross-encoder（须显式开关）否则 heuristic（词重叠+向量）。"""
    qtoks = _tokenize(query)
    qvec = embed(query)
    ce = _get_ce_model()
    if ce is not None:
        try:
            pairs = [(query, c["text"]) for c in candidates]
            scores = ce.predict(pairs)
            return [(c, float(s)) for c, s in zip(candidates, scores)]
        except Exception:
            pass
    scored = []
    for c in candidates:
        overlap = len(qtoks & c["_toks"]) / (len(qtoks | c["_toks"]) or 1)
        cos = max(0.0, _cosine(qvec, c["_vec"]))
        scored.append((c, 0.6 * overlap + 0.4 * cos))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def retrieve(query: str, caller: CallerContext, top_k: int = 6) -> tuple[list[dict], int]:
    """混合检索 + 父子聚合 + 重排 + 第18章单一过滤（不可绕过）。

    返回 (hits, filtered_out)：hits 为 parent 级（doc_id=parent_id），
    snippet 为最高分 child 文本；filtered_out 为被单一过滤点剔除的 child 数。
    """
    if not _INDEX_BUILT:
        _reindex()
    qtoks = _tokenize(query)
    qvec = embed(query)

    vec_rank = sorted(range(_N), key=lambda i: _cosine(qvec, _CHILDREN[i]["_vec"]), reverse=True)
    bm25_scores = [_bm25(qtoks, c["_toks"]) for c in _CHILDREN]
    bm25_rank = sorted(range(_N), key=lambda i: bm25_scores[i], reverse=True)

    rrf = [0.0] * _N
    for rank, i in enumerate(vec_rank):
        rrf[i] += 1.0 / (RRF_K + rank + 1)
    for rank, i in enumerate(bm25_rank):
        rrf[i] += 1.0 / (RRF_K + rank + 1)

    top_idx = sorted(range(_N), key=lambda i: rrf[i], reverse=True)[:100]
    candidates = [_CHILDREN[i] for i in top_idx]

    reranked = rerank(query, candidates)

    # 聚合回 parent：同一 parent 取最高分 child 作为代表 snippet
    parent_best: dict[str, dict] = {}
    for c, sc in reranked:
        pid = c["parent_id"]
        rep = parent_best.get(pid)
        if rep is None or sc > rep["score"]:
            parent_best[pid] = {
                "doc_id": pid, "title": c["title"], "snippet": c["text"],
                "scope": c["scope"], "kb_tier": c["scope"], "score": round(sc, 4),
                "source": c["source"], "child_id": c["child_id"],
                "owner": c["owner"], "dept": c["dept"], "tenant_id": c["tenant_id"],
            }

    agg = sorted(parent_best.values(), key=lambda x: x["score"], reverse=True)
    visible_items, filtered_out = single_filter(agg, caller)
    hits = visible_items[:top_k]
    return hits, filtered_out


# ─────────────────────────────────────────────────────────────────────────────
# 写入（M2：内存实现；真实后端经 ensure_qdrant_collection 接入）
# ─────────────────────────────────────────────────────────────────────────────
def add_document(title: str, content: str, scope: str, owner: str, dept: str, tenant_id: str) -> str:
    """写入内存语料并重建索引（服务端强制回填 owner/dept/tenant）。M2 内存版。"""
    doc_id = "doc_" + _short_uuid()
    PARENTS.append({
        "doc_id": doc_id, "tenant_id": tenant_id, "title": title, "content": content,
        "scope": scope, "owner": owner, "dept": dept, "source": "user_input",
    })
    _reindex()
    return doc_id


def add_documents_bulk(rows: list[dict]) -> list[str]:
    """批量写入并**只重建一次索引**（M8 试点：一次灌 200–500 篇）。

    单篇 add_document 每次都会 _reindex()，500 篇 = 500 次全量重建，实测分钟级；
    批量入口把重建压到 1 次，是批量导入能跑起来的前提。
    rows: [{title, content, scope, owner, dept, tenant_id, source?, doc_id?}]
    """
    ids: list[str] = []
    for r in rows:
        doc_id = r.get("doc_id") or ("doc_" + _short_uuid())
        PARENTS.append({
            "doc_id": doc_id,
            "tenant_id": r["tenant_id"],
            "title": r.get("title") or "(无标题)",
            "content": r.get("content") or "",
            "scope": r.get("scope") or "personal",
            "owner": r.get("owner") or "",
            "dept": r.get("dept") or "",
            "source": r.get("source") or "bulk",
        })
        ids.append(doc_id)
    if ids:
        _reindex()
    return ids


def remove_documents(doc_ids: list[str]) -> int:
    """批量移除（批量导入回滚 / 测试清理），同样只重建一次索引。"""
    drop = set(doc_ids)
    before = len(PARENTS)
    PARENTS[:] = [p for p in PARENTS if p["doc_id"] not in drop]
    removed = before - len(PARENTS)
    if removed:
        _reindex()
    return removed


def remove_document(doc_id: str) -> bool:
    """从内存语料移除文档并重建索引（M7：KB 删除同步检索侧，避免"删了还能召回"）。"""
    before = len(PARENTS)
    PARENTS[:] = [p for p in PARENTS if p["doc_id"] != doc_id]
    if len(PARENTS) == before:
        return False
    _reindex()
    return True


def _short_uuid() -> str:
    import uuid
    return uuid.uuid4().hex[:8]


def ensure_qdrant_collection():
    from qdrant_client import QdrantClient  # type: ignore
    client = QdrantClient(host=settings.QDRANT_HOST, port=settings.QDRANT_PORT)
    # 生产接入：client.recreate_collection(..., vectors_config=VectorParams(size=settings.EMBEDDING_DIM))
    return client
