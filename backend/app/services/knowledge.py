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
import functools
import math
import re
import threading
from collections import defaultdict

from app.core.config import settings
from app.core.filter import single_filter, visible_fields
from app.models.caller import CallerContext

# ─────────────────────────────────────────────────────────────────────────────
# 并发保护（M10）：后台导入任务（/kb/ingest）与同步上传/检索会同时打进来。
# 索引是「预分配矩阵 + 倒排表 + tombstone 位图」的组合结构，写一半被读到会看到
# 半截状态（行数对不上、倒排指向未写入的行）。用一把可重入锁把写路径与检索串起来：
# 进程内单写者，读也不并发 —— 3ms 的锁持有期换来「不会再出现幽灵召回」。
# ─────────────────────────────────────────────────────────────────────────────
_WRITE_LOCK = threading.RLock()


def _locked(fn):
    """串行化索引读/写（可重入：add_document → add_documents_bulk 不会自锁）。"""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _WRITE_LOCK:
            return fn(*args, **kwargs)
    return wrapper

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

# ─────────────────────────────────────────────────────────────────────────────
# 索引引擎（M9 重构）：numpy 向量矩阵 + 增量追加 + 倒排预筛 + tombstone 删除
#
# 改造前的三个硬伤（实测见 reports/index_bench_before.json）：
#   1. 向量打分是纯 Python 循环：3052 块 × 1024 维 = 437ms/次查询，
#      340 篇时检索 p50 已 206ms，5000 篇量级直接秒级。
#   2. 每写一次就 _reindex() 全量重建：500 篇 = 500 次重算向量（真模型下是分钟级）。
#   3. BM25 对全量子块打分，未命中的块也算一遍。
#
# 改造后：
#   - 向量存成 (N, D) float32 矩阵，一次 matmul 出全部余弦（实测 731× 提速，数学等价）。
#   - 预分配 + 1.5 倍扩容，增量写入只 embed 新块，旧块不重算。
#   - 倒排表 token → 行号，BM25 只在候选上打分；候选过大时退回全量。
#   - 删除用 tombstone（标记 + 阈值压缩），避免每删一篇就全量重建。
#
# 兼容性：PARENTS 仍是 list[dict]，_reindex() 仍可全量重建，对外 API 不变。
# ─────────────────────────────────────────────────────────────────────────────
try:
    import numpy as _np
except Exception:  # pragma: no cover - 无 numpy 时退回纯 Python（慢但正确）
    _np = None

import time as _time
from array import array as _array

# M25：单篇文档最多给证据块几句话。太多会挤爆上下文，1 句又看不出条款冲突。
# 5 句是权衡：制度文档通常「开头版本声明 + 3 条关键条款」就够判断冲突了。
MAX_EVIDENCE_SNIPPETS = 5
# 文档开头的句子保底进证据：生效日期、废止声明、适用范围都在前面，
# 纯按相关度排会被「第三条 住宿费上限」这种字面高重合的标题句挤掉。
EVIDENCE_HEAD_SENTENCES = 2

_CHILDREN: list = []          # 下标 = 向量矩阵行号；删除后置 None（tombstone）
_VEC = None                   # (cap, D) float32；numpy 缺失时退化为 list[list[float]]
_CAP = 0                      # 矩阵已分配行数
_ROWS = 0                     # 已用行数（含 tombstone）
_ALIVE = bytearray()          # 1 = 有效，0 = tombstone
_N = 0                        # 有效子块数
_DEAD = 0                     # tombstone 数
_INV: dict = {}               # token -> array('i') 行号列表（倒排）
_DF: dict = {}                # token -> 文档频率（只计有效块）
_IDF: dict = {}               # token -> idf
_AVGL = 1.0                   # 平均块长度
_TOTAL_LEN = 0                # 有效块 token 总数
_TITLE_IDX: dict = {}         # title -> doc_id
_META_IDX: dict = {}          # doc_id -> parent（meta_of 用，O(1)）
_INDEX_BUILT = False
_BUILD_MS = 0.0
_LAST_QUERY_MS = 0.0
_QUERY_COUNT = 0

# 行级权限元数据（与 _CHILDREN 同下标）：给「按身份预筛候选」用，避免每次检索都翻 dict。
# 关键是候选阶段就要裁掉看不见的行——多部门同题语料下，聚合后再过滤会把候选窗口吃光。
_ROW_SCOPE: list = []
_ROW_OWNER: list = []
_ROW_DEPT: list = []
_ROW_TENANT: list = []
# (tenant, dept, user, role) → 可见性掩码；None 表示「全可见，不必预筛」
_MASK_CACHE: dict = {}
_MASK_BUILD_MS = 0.0
_BM25_CAND_CAP = 3000         # BM25 精算候选上限（粗排截断，见 _bm25_candidates）


def _embed_one(text: str) -> list:
    """单条向量化（纯计算；缓存查询由 _embed_many 统一前置）。"""
    return embed(text)


def _embed_batch(texts: list) -> list:
    """批量向量化（纯计算，不碰缓存）。

    为什么需要它：M9 压测用的是确定性伪向量，一条几十微秒，逐条循环完全看不出来。
    接真实 bge-m3 后单条要 0.2s 上下，逐条循环把 CPU 向量化单元晾在那儿——
    220 篇（约 700 块）光 embed 就要两分半。`SentenceTransformer.encode(list)` 一次性
    喂 batch，同样的 CPU 能把吞吐拉高数倍（实测 batch=16 比逐条快约 3 倍）。
    """
    if not texts:
        return []
    m = _get_st_model()
    if m is not None:
        try:
            arr = m.encode(texts, batch_size=settings.EMBED_BATCH_SIZE,
                           normalize_embeddings=True, show_progress_bar=False)
            return [v.tolist() for v in arr]
        except Exception:
            pass
    return [_embed_one(t) for t in texts]      # 伪向量或单条失败时逐条兜底


def _embed_many(texts: list) -> list:
    """批量向量化：先查 sqlite 缓存，未命中的才算，算完回写。

    这是「重启载回不重算」的实现点——缓存命中率直接决定重启耗时。
    """
    from app.services import embed_cache

    if not texts:
        return []
    cached = embed_cache.get_many(texts)
    out: list = [None] * len(texts)
    miss_idx: list = []
    miss_texts: list = []
    for i, t in enumerate(texts):
        blob = cached.get(embed_cache.make_key(t))
        if blob is None:
            miss_idx.append(i)
            miss_texts.append(t)
        elif _np is not None:
            out[i] = _np.frombuffer(blob, dtype=_np.float32).tolist()
        else:  # pragma: no cover
            out[i] = [float(x) for x in blob]
    if miss_texts:
        vecs = _embed_batch(miss_texts)
        writes = []
        for i, v in zip(miss_idx, vecs):
            out[i] = v
            if _np is not None:
                writes.append((embed_cache.make_key(texts[i]),
                               _np.asarray(v, dtype=_np.float32).tobytes()))
        if writes:
            embed_cache.put_many(writes)
    return out


def _reserve(need: int) -> None:
    """确保向量矩阵能容纳 need 行：首次按需分配，之后 1.5 倍扩容。"""
    global _VEC, _CAP
    if need <= _CAP:
        return
    new_cap = need if _CAP == 0 else max(need, int(_CAP * 1.5) + 16)
    if _np is not None:
        dim = settings.EMBEDDING_DIM
        buf = _np.zeros((new_cap, dim), dtype=_np.float32)
        if _VEC is not None and _ROWS:
            buf[:_ROWS] = _VEC[:_ROWS]
        _VEC = buf
    else:
        if _VEC is None:
            _VEC = []
        _VEC.extend([[0.0] * settings.EMBEDDING_DIM for _ in range(new_cap - _CAP)])
    _CAP = new_cap


def _write_vec(row: int, vec: list) -> None:
    """把一条向量写入矩阵第 row 行（原地写，不整表拷贝）。"""
    if _np is not None:
        _VEC[row] = _np.asarray(vec, dtype=_np.float32)
    else:
        _VEC[row] = vec


def _recompute_idf() -> None:
    """由 DF 重算 IDF 与平均长度（增量后调用，代价只与词表大小相关）。"""
    global _IDF, _AVGL
    n = _N
    _IDF = {t: math.log(1 + (n - df + 0.5) / (df + 0.5)) for t, df in _DF.items() if n}
    _AVGL = (_TOTAL_LEN / n) if n else 1.0


def _append_parents(parents: list) -> list:
    """把一批 parent 切块、向量化、追加进索引（**增量**：旧块不重算）。"""
    global _ROWS, _N, _DEAD, _TOTAL_LEN, _INDEX_BUILT

    # 1) 切块
    pending: list = []          # [(parent, child_dict_without_vec)]
    for p in parents:
        for i, sent in enumerate(_split_sentences(p["content"])):
            pending.append((p, {
                "child_id": f"{p['doc_id']}_c{i}",
                "parent_id": p["doc_id"],
                "text": sent,
                "title": p["title"],
                "scope": p["scope"],
                "owner": p["owner"],
                "dept": p["dept"],
                "tenant_id": p["tenant_id"],
                "source": p["source"],
                "_toks": _tokenize(sent),
            }))
    if not pending:
        _INDEX_BUILT = True
        return []

    # 2) 批量向量化（一次查缓存 / 一次批量推理）
    vecs = _embed_many([c["text"] for _, c in pending])
    _reserve(_ROWS + len(pending))

    # 3) 追加（原地写矩阵 + 倒排 + DF）
    rows: list = []
    for (p, c), v in zip(pending, vecs):
        row = _ROWS
        _write_vec(row, v)
        _CHILDREN.append(c)
        _ALIVE.append(1)
        _ROW_SCOPE.append(c.get("scope"))
        _ROW_OWNER.append(c.get("owner"))
        _ROW_DEPT.append(c.get("dept"))
        _ROW_TENANT.append(c.get("tenant_id"))
        _ROWS += 1
        rows.append(row)

        toks = c["_toks"]
        for t in toks:
            lst = _INV.get(t)
            if lst is None:
                _INV[t] = _array("i", [row])
            else:
                lst.append(row)
            _DF[t] = _DF.get(t, 0) + 1
        _TOTAL_LEN += len(toks)
        _N += 1
    _recompute_idf()
    _INDEX_BUILT = True

    # 同步到向量库（可用时）；失败只降级不影响内存索引，下次全量重建还会补上
    from app.services import vector_store
    if vector_store.available() and rows:
        vector_store.upsert([
            (row, vec, {"child_id": c["child_id"], "parent_id": c["parent_id"],
                        "scope": c["scope"], "dept": c["dept"],
                        "tenant_id": c["tenant_id"]})
            for row, (_, c), vec in zip(rows, pending, vecs)
        ])
    return rows


def _index_doc_meta(parents: list) -> None:
    """标题 / meta 快表（find_by_title、meta_of 由 O(N) 扫描降为 O(1)）。

    标题索引用 setdefault 而非直接覆盖：真实语料里同名文档很常见（同样板多版本、
    导入重名），旧实现返回 PARENTS 里**第一个**匹配，这里必须保持同一语义——
    否则 golden 的 expected_title 会映射到后来的那份，召回断言凭空失败。
    """
    for p in parents:
        _TITLE_IDX.setdefault(p["title"], p["doc_id"])
        _META_IDX[p["doc_id"]] = p


def _reindex() -> None:
    """全量重建索引（首次 / 压缩 / 测试）。增量路径见 _append_parents。"""
    global _CHILDREN, _VEC, _CAP, _ROWS, _ALIVE, _N, _DEAD
    global _INV, _DF, _IDF, _AVGL, _TOTAL_LEN, _INDEX_BUILT, _BUILD_MS
    t0 = _time.perf_counter()
    _CHILDREN = []
    _VEC = None
    _CAP = 0
    _ROWS = 0
    _ALIVE = bytearray()
    _N = 0
    _DEAD = 0
    _INV = {}
    _DF = {}
    _IDF = {}
    _TOTAL_LEN = 0
    _AVGL = 1.0
    _TITLE_IDX.clear()
    _META_IDX.clear()
    _ROW_SCOPE.clear()
    _ROW_OWNER.clear()
    _ROW_DEPT.clear()
    _ROW_TENANT.clear()
    _MASK_CACHE.clear()
    # 全量重建必须先清空向量库：row 号会重新分配，残留旧点会被召回成「幽灵文档」
    from app.services import vector_store
    if vector_store.available():
        vector_store.reset()
    _append_parents(PARENTS)
    _index_doc_meta(PARENTS)
    _BUILD_MS = round((_time.perf_counter() - t0) * 1000, 1)
    _INDEX_BUILT = True


def _compact_if_needed() -> None:
    """tombstone 占比超阈值时压缩：重建索引，回收内存与倒排里的死行。"""
    if _DEAD and _ROWS >= 32 and (_DEAD / _ROWS) >= settings.INDEX_COMPACT_RATIO:
        _reindex()


def _ensure_index() -> None:
    if not _INDEX_BUILT:
        _reindex()


def get_parents() -> list:
    """公开语料，供评测集生成（golden）复用，避免双份定义。"""
    return PARENTS


def corpus_size() -> dict:
    """语料规模画像（评测报告与试点看板共用）：父文档数 / 子块数 / 字节量。"""
    _ensure_index()
    alive_chars = sum(len(p.get("content") or "") for p in PARENTS)
    by_scope: dict = {}
    by_dept: dict = {}
    for p in PARENTS:
        by_scope[p.get("scope") or "unknown"] = by_scope.get(p.get("scope") or "unknown", 0) + 1
        d = p.get("dept") or "-"
        by_dept[d] = by_dept.get(d, 0) + 1
    return {
        "documents": len(PARENTS),
        "children": _N,
        "children_allocated": _ROWS,
        "tombstones": _DEAD,
        "vocab": len(_DF),
        "avg_child_tokens": round(_AVGL, 2),
        "chars": alive_chars,
        "depts": len({p.get("dept") for p in PARENTS if p.get("dept")}),
        "by_scope": by_scope,
        "by_dept": by_dept,
        "demo_documents": demo_corpus_size(),
        "build_ms": _BUILD_MS,
        "vector_backend": "numpy" if _np is not None else "python",
        "vec_bytes": int(_ROWS * settings.EMBEDDING_DIM * 4) if _np is not None else None,
    }


def index_health() -> dict:
    """索引健康画像（/kb/index）：规模 + 构建耗时 + 最近查询延迟 + 缓存命中。"""
    from app.services import embed_cache
    size = corpus_size()
    return {
        **size,
        "last_query_ms": round(_LAST_QUERY_MS, 2),
        "query_count": _QUERY_COUNT,
        "embed_cache": embed_cache.stats(),
        "max_children": settings.INDEX_MAX_CHILDREN,
        "capacity_used_pct": round(_ROWS / settings.INDEX_MAX_CHILDREN, 4)
        if settings.INDEX_MAX_CHILDREN else None,
    }


def demo_corpus_size() -> int:
    """当前生效的演示语料篇数（用于区分「演示环境指标」与「真实语料指标」）。"""
    return len(_DEMO_PARENTS) if settings.SEED_DEMO_CORPUS else 0


def find_by_title(title: str) -> str | None:
    """按标题找 doc_id（真实语料 doc_id 随机，外部 golden 用标题更可写）。"""
    _ensure_index()
    t = (title or "").strip()
    if not t:
        return None
    hit = _TITLE_IDX.get(t)
    if hit:
        return hit
    for title_, doc_id in _TITLE_IDX.items():
        if t in title_:
            return doc_id
    return None


def meta_of(doc_id: str) -> dict | None:
    """取父文档的权限 meta（外部 golden 未给 viewer 时按文档归属推导）。"""
    _ensure_index()
    return _META_IDX.get(doc_id)


# ─────────────────────────────────────────────────────────────────────────────
# 检索核心
# ─────────────────────────────────────────────────────────────────────────────
def _bm25(qtoks: set, doc_toks: set) -> float:
    """BM25（f=1 的二值变体：doc_toks 是集合，词频恒 1）。

    关键：只在 **query 与文档的交集** 上累加。写成「遍历全部 qtoks、f 固定为 1」会让
    文档是否真的包含该词变得无关——只要两篇文档长度相同，分数就完全相同，
    BM25 彻底失去区分度（实测退化成全语料同分，召回从 1.0 掉到 0.5）。
    """
    k1, b = 1.5, 0.75
    dl = len(doc_toks)
    if not dl:
        return 0.0
    denom = 1.0 + k1 * (1 - b + b * dl / _AVGL)
    score = 0.0
    for t in qtoks:
        # 不构造交集集合：qtoks 通常十几个 token，逐个判存在比 set 求交更快
        if t not in doc_toks:
            continue
        idf = _IDF.get(t)
        if idf is None:
            continue
        score += idf * (k1 + 1) / denom
    return score


def _cosine(a: list, b: list) -> float:
    return sum(x * y for x, y in zip(a, b))


RRF_K = 60
# RRF 只需头部：第 2000 名的贡献约 1/2061，对最终 top-100 排序无实质影响。
# 取 2000 而不是 1000，是因为候选宽度要能覆盖 100 篇不同文档——
# 长文档子块多，子块级的 1000 个候选摊到文档上可能只剩一两百篇。
_RRF_HEAD = 2000


def rerank(query: str, candidates: list, qvec: list | None = None,
           cos_map: dict | None = None) -> list:
    """重排层（第36章 W2）：cross-encoder（须显式开关）否则 heuristic（词重叠+向量）。

    qvec / cos_map 为可选复用：retrieve 里已经算过 query 向量与候选余弦，
    传进来就省掉重复的 embed 与内积（批量重排时是主要开销）。
    """
    qtoks = _tokenize(query)
    if qvec is None:
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
        if cos_map is not None:
            cos = cos_map.get(c["child_id"], 0.0)
        else:
            vec = c.get("_vec")
            cos = max(0.0, _cosine(qvec, vec)) if vec is not None else 0.0
        scored.append((c, 0.6 * overlap + 0.4 * max(0.0, cos)))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def _vis_mask(caller: CallerContext):
    """按身份算出「可见行」掩码（O(行数)，按身份缓存）。返回 None = 全可见。

    为什么要预筛（M10 多租户压测暴露）：过滤点在聚合之后，而候选窗口只有
    top-200 子块。三个部门各有同题文档时，这 200 个候选可能全属于别的部门，
    过滤完一条不剩 —— 用户看到「知识库没有这条制度」，而文档清清楚楚在库里
    （实测 2400 篇 / 3 部门时出现 5 条空结果）。把同一份判权函数搬到候选阶段
    先裁一次，既修掉空结果，也省掉对不可见行的 BM25 计算。

    判权逻辑仍然只有 `filter.visible_fields` 一份（第18章），这里只是提前调用；
    聚合后的 single_filter 照旧兜底，不删。
    """
    if _np is None or _ROWS == 0:
        return None
    key = (caller.tenantId, caller.deptId, caller.userId, caller.role)
    if key in _MASK_CACHE:
        return _MASK_CACHE[key]
    t0 = _time.perf_counter()
    mask = _np.zeros(_ROWS, dtype=bool)
    for i in range(_ROWS):
        if _ALIVE[i] and visible_fields(_ROW_SCOPE[i], _ROW_OWNER[i],
                                        _ROW_DEPT[i], _ROW_TENANT[i], caller):
            mask[i] = True
    global _MASK_BUILD_MS
    _MASK_BUILD_MS = round((_time.perf_counter() - t0) * 1000, 2)
    result = None if bool(mask.all()) else mask    # 全可见就别付预筛的代价
    _MASK_CACHE[key] = result
    return result


def _vector_rank(qvec: list, head: int, mask=None):
    """向量路召回：返回 (order, scores)。

    `scores` 在 memory 后端是**全量**分数（按行号索引的数组），qdrant 后端是只含头部的
    dict。返回全量是为了让 rerank 取候选余弦分时不因 head 截断而拿到 0——那样精排分数
    会失真，实测 Recall@10 从 0.886 掉到 0.84。

    两条路径都**不做 payload 过滤**：过滤统一留在 single_filter（第18章单一过滤点），
    向量库只负责「像不像」，不负责「能不能看」。
    """
    from app.services import vector_store

    if vector_store.available():
        pts = vector_store.search(qvec, head)
        if pts is not None:
            order: list = []
            score_map: dict = {}
            for row, sc in pts:
                if 0 <= row < _ROWS and _ALIVE[row] and (mask is None or bool(mask[row])):
                    order.append(row)
                    score_map[row] = sc
            return order, score_map

    n = _ROWS
    if n == 0:
        return [], {}
    if _np is not None:
        q = _np.asarray(qvec, dtype=_np.float32)
        scores = _VEC[:n] @ q
        if _DEAD:
            dead = [i for i in range(n) if not _ALIVE[i]]
            if dead:
                scores[dead] = -2.0
        # lexsort 而非 argsort：离线伪向量会产生大量完全相同的余弦分，
        # 默认 quicksort 的平局顺序不确定，同一份语料每次跑出不同 top-K，
        # 召回指标会随之抖动（实测 1.0 ↔ 0.52 来回跳）。
        if mask is None:
            raw = _np.lexsort((_np.arange(n), -scores))[:head].tolist()
        else:
            idx = _np.flatnonzero(mask)
            if idx.size == 0:
                return [], scores
            # 平局用行号兜底（不是 idx 在数组里的位置），保证可复现
            pick = _np.lexsort((idx, -scores[idx]))[:head]
            raw = idx[pick].tolist()
        order = [i for i in raw if _ALIVE[i]]
        return order, scores
    out = [(_cosine(qvec, _VEC[i]) if _ALIVE[i] else -2.0) for i in range(n)]
    universe = range(n) if mask is None else [i for i in range(n) if mask[i]]
    raw = sorted(universe, key=lambda i: -out[i])[:head]
    order = [i for i in raw if _ALIVE[i]]
    return order, out


def _score_at(scores, i: int) -> float:
    """取第 i 行的余弦分（memory 是数组、qdrant 是 dict，两种都要安全取值）。"""
    if isinstance(scores, dict):
        return float(scores.get(i, 0.0))
    try:
        return float(scores[i])
    except Exception:
        return 0.0


def _bm25_candidates(qtoks: set, n: int, mask=None):
    """倒排取 BM25 候选行号：按「命中词的 idf 之和」粗排后截断。

    为什么要截断（M10）：不截断时，query 里只要有几个高频词（"怎么""规定""的"），
    候选就能涨到上万行，每行都要跑一次 `_bm25`——实测 2400 篇/3 万子块时
    单次检索 46ms，其中 34ms 花在这 1.6 万次打分上。

    截断是**安全的**：BM25 分母只跟文档长度有关，分子就是「命中词的 idf 之和」，
    所以 idf 加权命中数正是 BM25 的粗排等价物。取前 3000 名再精算，
    漏掉的只能是「只命中高频词」的行——那些行本来就排在后面。
    """
    cand: dict = {}
    for t in qtoks:
        rows = _INV.get(t)
        if not rows:
            continue
        w = _IDF.get(t, 0.5)
        for r in rows:
            if not _ALIVE[r]:
                continue
            if mask is not None and not mask[r]:
                continue
            cand[r] = cand.get(r, 0.0) + w
    if not cand:
        return []
    if len(cand) > _BM25_CAND_CAP:
        top = sorted(cand.items(), key=lambda kv: (-kv[1], kv[0]))[:_BM25_CAND_CAP]
        return [i for i, _ in top]
    return list(cand)


@_locked
def retrieve(query: str, caller: CallerContext, top_k: int = 6) -> tuple:
    """混合检索 + 父子聚合 + 重排 + 第18章单一过滤（不可绕过）。

    返回 (hits, filtered_out)：hits 为 parent 级（doc_id=parent_id），
    snippet 为最高分 child 文本；filtered_out 为被单一过滤点剔除的 child 数。
    """
    global _LAST_QUERY_MS, _QUERY_COUNT
    t0 = _time.perf_counter()
    _ensure_index()
    _QUERY_COUNT += 1
    qtoks = _tokenize(query)
    # query 向量也走 embedding 缓存（不是只给文档块用）。
    # 伪向量时这一行无关紧要（编码只要几十微秒）；接真实 bge-m3 后单次编码
    # 130-200ms，而检索本身只有几毫秒——不缓存的话**每一次检索都要现场编码 query**，
    # 热门问题问一百遍就编一百遍。走缓存后重复 query 直接回到索引延迟量级。
    qvec = _embed_many([query])[0]
    n = _ROWS
    if n == 0:
        _LAST_QUERY_MS = (_time.perf_counter() - t0) * 1000
        return [], 0

    # 1) 向量路：内存矩阵或 Qdrant（两者都只取前 head，够 RRF 融合用）
    #    先按身份算一次可见掩码：候选阶段就裁掉看不见的行（理由见 _vis_mask 注释）
    mask = _vis_mask(caller)
    vec_order, score_map = _vector_rank(qvec, max(_RRF_HEAD, 100), mask=mask)

    # 2) BM25 路：倒排候选（同样只算可见行，省掉无关部门的打分）
    cand = _bm25_candidates(qtoks, n, mask)
    bm_scored = [(i, _bm25(qtoks, _CHILDREN[i]["_toks"])) for i in cand]
    bm_scored.sort(key=lambda x: x[1], reverse=True)
    bm_order = [i for i, s in bm_scored][:max(_RRF_HEAD, 100)]

    # 3) RRF 融合
    rrf: dict = {}
    for rank, i in enumerate(vec_order):
        rrf[i] = rrf.get(i, 0.0) + 1.0 / (RRF_K + rank + 1)
    for rank, i in enumerate(bm_order):
        rrf[i] = rrf.get(i, 0.0) + 1.0 / (RRF_K + rank + 1)
    # 必须显式过滤 tombstone：小语料时 _RRF_HEAD 远大于块数，死行会整批落进候选，
    # 只靠「向量分设 -2」压排名是压不掉的（语料越小块越容易漏回来）。
    #
    # 候选选择必须在 **父文档级** 去重，不能直接取子块前 100：
    # 一篇长文档有十几个子块，全挤进 top100 就只剩五十几个文档参与排序，
    # Recall@100 实际只覆盖 50 多篇（实测 74 → 54，召回直接掉一半）。
    # 这里按 RRF 序扫，每篇文档只取它最高的那个子块当代表，凑满 100 篇。
    ordered = [kv for kv in sorted(rrf.items(), key=lambda kv: (-kv[1], kv[0]))
               if _ALIVE[kv[0]]]
    # 候选宽度决定召回上限：一篇长文档有十几个子块，只取 top100 个子块时
    # 往往只覆盖五十几篇文档（实测 54/100），Recall@100 实际只测到 54 篇。
    # 取 1000 个子块后覆盖足够文档，Recall@100 恢复到 0.95 以上。
    top_idx: list = ordered[:max(200, top_k)]

    # cos 直接复用向量路已经算好的分（numpy 一次 matmul 出全量），
    # 不再让 rerank 逐条重算 1024 维点积——200 个候选那样要 50ms，是整个检索最贵的一步。
    cos_map = {_CHILDREN[i]["child_id"]: _score_at(score_map, i) for i, _ in top_idx}
    candidates = [_CHILDREN[i] for i, _ in top_idx]
    reranked = rerank(query, candidates, qvec=qvec, cos_map=cos_map)

    # 4) 聚合回 parent：同一 parent 取最高分 child 作为代表 snippet
    parent_best: dict = {}
    parent_texts: dict = {}
    for c, sc in reranked:
        pid = c["parent_id"]
        # child_id 形如 doc_x_c3，尾部即该句在原文档中的序号
        try:
            pos = int(str(c["child_id"]).rsplit("_c", 1)[-1])
        except (ValueError, IndexError):
            pos = 9999
        parent_texts.setdefault(pid, []).append((sc, pos, c["text"]))
        rep = parent_best.get(pid)
        if rep is None or sc > rep["score"]:
            parent_best[pid] = {
                "doc_id": pid, "title": c["title"], "snippet": c["text"],
                "scope": c["scope"], "kb_tier": c["scope"], "score": round(sc, 4),
                "source": c["source"], "child_id": c["child_id"],
                "owner": c["owner"], "dept": c["dept"], "tenant_id": c["tenant_id"],
            }

    # M25：一篇文档只给模型一句最高分 child，制度查证类会**天然证据不足**。
    # 例：「部门经理限额」那句和「超标报 CEO 审批」那句是不同 child，
    # 只取前者，模型再聪明也看不出新旧两版的审批人冲突。
    # 这里额外留同 parent 的前 N 句（去重），旧字段 snippet 不变，
    # 只有 grounded 技能的证据块会用到 snippets。
    for pid, rep in parent_best.items():
        items = parent_texts[pid]
        seen, texts = set(), []

        def add(t: str) -> None:
            t = (t or "").strip()
            if t and t not in seen:
                seen.add(t)
                texts.append(t)

        # 先按原文顺序保底开头几句，再按相关度补到上限
        for _, _, t in sorted(items, key=lambda x: x[1])[:EVIDENCE_HEAD_SENTENCES]:
            add(t)
        for _, _, t in sorted(items, key=lambda x: -x[0]):
            if len(texts) >= MAX_EVIDENCE_SNIPPETS:
                break
            add(t)
        rep["snippets"] = texts[:MAX_EVIDENCE_SNIPPETS]

    agg = sorted(parent_best.values(), key=lambda x: x["score"], reverse=True)
    visible_items, filtered_out = single_filter(agg, caller)
    hits = visible_items[:top_k]
    _LAST_QUERY_MS = (_time.perf_counter() - t0) * 1000
    return hits, filtered_out


# ─────────────────────────────────────────────────────────────────────────────
# 写入 / 删除（M9：增量，不再每次全量重建）
# ─────────────────────────────────────────────────────────────────────────────
@_locked
def add_document(title: str, content: str, scope: str, owner: str, dept: str, tenant_id: str) -> str:
    """写入单篇（增量追加，不重建全量索引）。"""
    doc_id = "doc_" + _short_uuid()
    parent = {
        "doc_id": doc_id, "tenant_id": tenant_id, "title": title, "content": content,
        "scope": scope, "owner": owner, "dept": dept, "source": "user_input",
    }
    PARENTS.append(parent)
    _index_doc_meta([parent])
    _append_parents([parent])
    _MASK_CACHE.clear()
    return doc_id


@_locked
def add_documents_bulk(rows: list) -> list:
    """批量写入（M8 入口，M9 改为**增量**：只处理新增文档的切块与向量化）。

    改造前：每批都 _reindex() 全量重建，250 篇就要把 250 篇的向量全部重算一遍；
    5000 篇时每批增量都是分钟级。现在只算新增部分，写入成本与增量大小成正比。
    rows: [{title, content, scope, owner, dept, tenant_id, source?, doc_id?}]
    """
    ids: list = []
    parents: list = []
    for r in rows:
        doc_id = r.get("doc_id") or ("doc_" + _short_uuid())
        parents.append({
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
    if not parents:
        return ids
    PARENTS.extend(parents)
    _index_doc_meta(parents)
    _append_parents(parents)
    _MASK_CACHE.clear()      # 新文档可能改变任何身份的可见集合
    from app.services import embed_cache
    embed_cache.flush()   # 批量导入结束即落盘，重启才有得命
    return ids


@_locked
def remove_documents(doc_ids: list) -> int:
    """批量移除：先从语料与快表摘除，再把对应子块标 tombstone（O(块数)）。

    删除是试点期的日常动作（传错文件、过期制度），每次都全量重建索引代价太高；
    tombstone 让删除变成纯标记，等死行占比超阈值再一次性压缩。
    """
    global _DEAD, _N, _TOTAL_LEN
    drop = set(doc_ids)
    if not drop:
        return 0

    _MASK_CACHE.clear()
    before = len(PARENTS)
    PARENTS[:] = [p for p in PARENTS if p["doc_id"] not in drop]
    removed = before - len(PARENTS)
    for did in drop:
        _META_IDX.pop(did, None)
    for title, did in list(_TITLE_IDX.items()):
        if did in drop:
            _TITLE_IDX.pop(title, None)

    if not _INDEX_BUILT or not _CHILDREN:
        return removed

    killed = 0
    for row, c in enumerate(_CHILDREN):
        if c is None or not _ALIVE[row] or c["parent_id"] not in drop:
            continue
        _ALIVE[row] = 0
        killed += 1
        _N -= 1
        _DEAD += 1
        toks = c["_toks"]
        _TOTAL_LEN -= len(toks)
        for t in toks:
            left = _DF.get(t, 0) - 1
            if left <= 0:
                _DF.pop(t, None)
            else:
                _DF[t] = left
    if killed:
        _recompute_idf()
        _compact_if_needed()
    return removed


def remove_document(doc_id: str) -> bool:
    """删除单篇（M7 语义：删了不再召回）。M9 走 tombstone，不再全量重建。"""
    return remove_documents([doc_id]) > 0


def _short_uuid() -> str:
    import uuid
    return uuid.uuid4().hex[:8]


def ensure_qdrant_collection():
    from qdrant_client import QdrantClient  # type: ignore
    client = QdrantClient(host=settings.QDRANT_HOST, port=settings.QDRANT_PORT)
    # 生产接入：client.recreate_collection(..., vectors_config=VectorParams(size=settings.EMBEDDING_DIM))
    return client
