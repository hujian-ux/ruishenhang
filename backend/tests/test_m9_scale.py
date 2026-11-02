"""M9 规模化测试：增量索引 / tombstone / 向量缓存 / 多部门隔离 / 向量后端降级。

与 M8 的区别：M8 验证「能不能跑两周」，M9 验证「规模涨十倍还成不成立」——
写入是否真的是增量、删除是否真的不召回、跨部门在 2000+ 篇下是否还零泄露、
向量库不可达时会不会把业务带崩。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.security import create_dev_token  # noqa: E402
from app.main import app  # noqa: E402
from app.models.caller import CallerContext  # noqa: E402
from app.services import embed_cache, kb_store, knowledge, vector_store  # noqa: E402

client = TestClient(app)
SALES = {"Authorization": create_dev_token(user_id="u_1024", dept_id="d_sales", role="employee")}

SALES_CALLER = CallerContext(userId="u_1024", deptId="d_sales", role="employee", tenantId="t_demo")
DESIGN_CALLER = CallerContext(userId="u_3000", deptId="d_design", role="employee", tenantId="t_demo")
OTHER_TENANT = CallerContext(userId="u_other", deptId="d_other", role="employee", tenantId="t_other")


@pytest.fixture
def clean_kb():
    """测试前后对齐语料与注册表。"""
    before = {p["doc_id"] for p in knowledge.get_parents()}
    yield
    after = {p["doc_id"] for p in knowledge.get_parents()}
    knowledge.remove_documents(sorted(after - before))
    kb_store.reset()


def _bulk(rows):
    return knowledge.add_documents_bulk(rows)


def _rows(specs, scope="department", dept="d_sales", tenant="t_demo"):
    return [{"title": t, "content": c, "scope": scope, "owner": "u_1024",
             "dept": dept, "tenant_id": tenant, "source": "m9"} for t, c in specs]


# ── ① 增量写入：只处理新增块，不重算全量 ──
def test_incremental_write_does_not_rebuild_whole_index(clean_kb):
    """新增一批只应追加新子块；行号从旧的总数开始接。"""
    _bulk(_rows([("M9甲", "甲文档内容：渠道返点与价格政策说明。"),
                 ("M9乙", "乙文档内容：门店陈列与物料投放规范。")]))
    rows_before = knowledge._ROWS
    children_before = knowledge._N

    _bulk(_rows([("M9丙", "丙文档内容：售后工单分级与响应时效。")]))
    assert knowledge._ROWS > rows_before, "增量写入应追加行，而不是原地重建"
    assert knowledge._N == children_before + (knowledge._ROWS - rows_before)
    # 旧块仍可召回（没有被重建打乱）
    hits, _ = knowledge.retrieve("甲文档 渠道返点", SALES_CALLER, 5)
    assert any("M9甲" == h["title"] for h in hits)


# ── ② 删除：tombstone 后立刻不再召回 ──
def test_deleted_document_is_not_recalled(clean_kb):
    ids = _bulk(_rows([("M9待删", "临时制度：本周内网维护，暂停外部访问。")]))
    hits, _ = knowledge.retrieve("内网维护 暂停外部访问", SALES_CALLER, 5)
    assert any(h["doc_id"] == ids[0] for h in hits)

    assert knowledge.remove_document(ids[0]) is True
    hits2, _ = knowledge.retrieve("内网维护 暂停外部访问", SALES_CALLER, 5)
    assert all(h["doc_id"] != ids[0] for h in hits2), "删掉的文档不应再被召回"
    assert knowledge._DEAD >= 1, "删除应留下 tombstone 供后续压缩"


# ── ③ 多部门隔离：三个部门同一租户下互不可见 ──
def test_cross_department_isolation_at_scale(clean_kb):
    _bulk(_rows([("M9销售制度", "销售部：经销商返点 8%，窜货取消返利。")], dept="d_sales"))
    _bulk(_rows([("M9设计规范", "设计部：品牌主色科技蓝，对外物料须设计部审核。")], dept="d_design"))
    _bulk(_rows([("M9人事考勤", "人事部：核心工时 10:00-16:00，加班需审批。")], dept="d_hr"))
    _bulk(_rows([("M9全员红线", "全员：禁止外发客户数据，离职务必回收权限。")], scope="global", dept="d_hr"))

    # 销售查设计部内容 → 不得出现设计部部门级文档
    hits, _ = knowledge.retrieve("设计部 品牌主色 物料审核", SALES_CALLER, 20)
    assert all(not (h["scope"] == "department" and h["dept"] == "d_design") for h in hits), \
        "跨部门泄露：销售部召回到了设计部部门级文档"

    # 设计部查销售部内容 → 同样不得越界
    hits2, _ = knowledge.retrieve("经销商返点 窜货 取消返利", DESIGN_CALLER, 20)
    assert all(not (h["scope"] == "department" and h["dept"] == "d_sales") for h in hits2)

    # global 全员可见
    hits3, _ = knowledge.retrieve("禁止外发客户数据 离职回收权限", SALES_CALLER, 20)
    assert any(h["title"] == "M9全员红线" for h in hits3), "global 文档应对全员可见"


# ── ④ 跨租户隔离：别的租户一条都不能进来 ──
def test_cross_tenant_zero_leak(clean_kb):
    _bulk(_rows([("M9友商机密", "友商租户内部定价策略，不应被本租户看到。")],
                dept="d_other", tenant="t_other"))
    hits, _ = knowledge.retrieve("友商 内部定价策略", SALES_CALLER, 20)
    assert all(h["tenant_id"] == "t_demo" for h in hits)
    # 反向：友商租户也看不到我们的
    _bulk(_rows([("M9我方机密", "本租户渠道底价，仅内部可见。")]))
    hits2, _ = knowledge.retrieve("渠道底价 仅内部可见", OTHER_TENANT, 20)
    assert all(h["tenant_id"] == "t_other" for h in hits2)


# ── ⑤ Embedding 缓存：同文本二次入库应命中，不再重算 ──
def test_embedding_cache_hits_on_repeat(tmp_path, monkeypatch, clean_kb):
    monkeypatch.setattr(settings, "EMBED_CACHE", True)
    monkeypatch.setattr(settings, "EMBED_CACHE_PATH", str(tmp_path / "cache.db"))
    monkeypatch.setattr(embed_cache, "_init_failed", False)
    monkeypatch.setattr(embed_cache, "_conn", None)
    monkeypatch.setattr(embed_cache, "_pending", [])
    embed_cache.reset_stats()

    text = "缓存验证专用文本：月度经营分析口径与复盘节奏。"
    vecs1 = knowledge._embed_many([text])
    vecs2 = knowledge._embed_many([text])
    # 缓存以 float32 落盘（与索引矩阵同精度），回读会有 1e-8 级差异，用近似比较
    assert vecs1[0] == pytest.approx(vecs2[0], abs=1e-6), "缓存命中应返回同样的向量"
    st = embed_cache.stats()
    assert st["hits"] >= 1, st
    assert st["available"] is True, st


# ── ⑥ 向量库不可达时静默降级，检索仍可用 ──
def test_vector_backend_falls_back_to_memory(monkeypatch, clean_kb):
    monkeypatch.setattr(settings, "VECTOR_BACKEND", "qdrant")
    monkeypatch.setattr(settings, "QDRANT_MODE", "http")
    monkeypatch.setattr(settings, "QDRANT_PORT", 1)        # 必然连不上
    monkeypatch.setattr(settings, "QDRANT_TIMEOUT_S", 0.2)
    vector_store.reset_state()
    try:
        assert vector_store.available() is False, "不可达时应判定为不可用"
        st = vector_store.stats()
        assert st["backend"] == "memory"
        assert st["error"], "降级必须留下原因，不能静默假装成功"
        # 业务不受影响
        _bulk(_rows([("M9降级验证", "向量库不可达时的兜底检索内容：价格审批流程。")]))
        hits, _ = knowledge.retrieve("价格审批流程", SALES_CALLER, 5)
        assert hits, "向量库不可达不应导致检索整体失效"
    finally:
        vector_store.reset_state()


# ── ⑦ 索引健康端点：试点排障要看的字段都在 ──
def test_index_health_endpoint(clean_kb):
    _bulk(_rows([("M9健康检查", "索引健康检查用内容：语料规模与延迟画像。")]))
    knowledge.retrieve("索引健康", SALES_CALLER, 3)
    r = client.get("/api/v1/kb/index", headers=SALES)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    for key in ("documents", "children", "build_ms", "last_query_ms", "query_count",
                "vector_backend", "embed_cache", "vector_store", "capacity_used_pct"):
        assert key in d, f"索引健康缺少字段 {key}"
    assert d["query_count"] >= 1
    assert d["last_query_ms"] >= 0
    assert d["vector_store"]["backend"] in ("memory",) or d["vector_store"]["backend"].startswith("qdrant")


# ── ⑧ 规模上界：500 篇写入与检索都在可接受范围 ──
def test_scale_500_docs_latency(clean_kb):
    import time
    specs = [(f"M9规模{i:04d}", f"规模测试文档 {i}：区域政策、返点规则、审批流程与执行说明。"
                                f"编号 {i}，归属部门 d_sales。") for i in range(500)]
    t0 = time.perf_counter()
    _bulk(_rows(specs))
    write_s = time.perf_counter() - t0

    t0 = time.perf_counter()
    for _ in range(5):
        knowledge.retrieve("区域政策 返点规则 审批流程", SALES_CALLER, 6)
    avg_ms = (time.perf_counter() - t0) / 5 * 1000

    assert write_s < 30, f"500 篇批量写入耗时 {write_s:.1f}s，超出预期"
    assert avg_ms < 300, f"500 篇下检索 {avg_ms:.1f}ms，明显退化"
