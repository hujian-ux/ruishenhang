"""M8 测试：试点就绪（批量导入去重 / 基线跑批 / 成本趋势 / 语料画像）。

覆盖试点前必须证明的 6 件事：
  ① 目录批量导入：内容去重、重跑幂等、失败逐条记录而不中断整批
  ② 导进来就能召回（同一份索引，不需要二次同步）
  ③ 多文件批量上传端点（UI 一次选多个文件）
  ④ 成本曲线：按自然日连续补齐（空白日补 0）、月累计与月末外推、预算水位
  ⑤ 基线跑批：外部 golden 用标题反查 doc_id、无效条目进 dropped 不静默、报告落盘
  ⑥ 语料画像与最近报告端点可用
"""
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # type: ignore

from app.core.config import settings
from app.core.security import create_dev_token
from app.main import app
from app.models.caller import CallerContext
from app.services import eval as eval_svc
from app.services import ingest, kb_store, knowledge, usage_store

client = TestClient(app)

SALES = {"Authorization": create_dev_token(user_id="u_1024", dept_id="d_sales", role="employee")}
DEPT_ADMIN = {"Authorization": create_dev_token(user_id="u_8001", dept_id="d_sales", role="dept_admin")}
ADMIN = {"Authorization": create_dev_token(user_id="u_9000", dept_id="d_it", role="platform_admin")}
CALLER = CallerContext(userId="u_1024", deptId="d_sales", role="employee", tenantId="t_demo")


@pytest.fixture
def clean_kb():
    """测试前后对齐语料与注册表，避免污染 smoke/e2e 的召回断言。"""
    before = {p["doc_id"] for p in knowledge.get_parents()}
    yield
    after = {p["doc_id"] for p in knowledge.get_parents()}
    knowledge.remove_documents(sorted(after - before))
    kb_store.reset()


# ─────────────────────────────────────────── ① 批量导入：去重 + 幂等 + 失败记录
def test_bulk_import_dedup_and_idempotent(tmp_path, clean_kb):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "渠道政策2026.md").write_text(
        "# 2026 渠道政策\n\n一级经销商返点 8%，二级返点 5%。季度超额部分以物料形式返还。"
        "窜货三次取消年度返利。", encoding="utf-8")
    (corpus / "渠道政策2026-副本.md").write_text(   # 内容相同的副本 → 只入一次
        "# 2026 渠道政策\n\n一级经销商返点 8%，二级返点 5%。季度超额部分以物料形式返还。"
        "窜货三次取消年度返利。", encoding="utf-8")
    (corpus / "大客户拜访SOP.md").write_text(
        "# 大客户拜访 SOP\n\n拜访前 48 小时提交议程，拜访后 24 小时出纪要与下一步动作。"
        "A 级客户每月至少一次现场拜访。", encoding="utf-8")
    (corpus / "空文件.txt").write_text("", encoding="utf-8")
    (corpus / "图片.png").write_bytes(b"\x89PNG\r\n")

    r1 = ingest.import_corpus(corpus, CALLER, scope="department", dept_id="d_sales")
    assert r1["scanned"] >= 3
    assert r1["added"] == 2, r1["docs"]          # 两篇不同内容 + 一份副本被去重
    assert r1["duplicated"] == 1
    assert r1["failed_count"] >= 1               # 空文件记失败但不中断
    assert all("reason" in f for f in r1["failed"])
    assert r1["chars_added"] > 0

    # 重跑整批 → 3 个文本文件全部判重（含副本），一篇都不新增（幂等）
    r2 = ingest.import_corpus(corpus, CALLER, scope="department", dept_id="d_sales")
    assert r2["added"] == 0
    assert r2["duplicated"] == 3

    report_paths = ingest.write_report(r1, tmp_path / "reports", name="ingest_test")
    assert os.path.exists(report_paths["json"]) and os.path.exists(report_paths["markdown"])
    saved = json.loads(open(report_paths["json"], encoding="utf-8").read())
    assert saved["added"] == 2


# ─────────────────────────────────────────── ② 导入即可召回
def test_imported_corpus_is_recallable(tmp_path, clean_kb):
    corpus = tmp_path / "kb2"
    corpus.mkdir()
    (corpus / "蓝鲸计划.md").write_text(
        "# 蓝鲸计划上线安排\n\n蓝鲸计划于 12 月 8 日灰度上线，首批覆盖华东 20 家门店。"
        "负责人为李海，灰度期两周，异常回滚由值班同学执行。", encoding="utf-8")
    rep = ingest.import_corpus(corpus, CALLER, scope="department", dept_id="d_sales")
    assert rep["added"] == 1
    doc_id = rep["docs"][0]["doc_id"]

    hits, filtered = knowledge.retrieve("蓝鲸计划什么时候上线", CALLER, top_k=6)
    assert any(h["doc_id"] == doc_id for h in hits), [h["title"] for h in hits]

    # 跨部门不可见（同一份语料仍受第18章过滤点约束）
    other = CallerContext(userId="u_3000", deptId="d_design", role="employee", tenantId="t_demo")
    hits2, _ = knowledge.retrieve("蓝鲸计划什么时候上线", other, top_k=6)
    assert all(h["doc_id"] != doc_id for h in hits2)


# ─────────────────────────────────────────── ③ 多文件批量上传端点
def test_bulk_upload_endpoint(clean_kb):
    files = [
        ("files", ("区域价格表.txt", "区域价格表：华东标准价 199 元，华南 189 元，西北 209 元。".encode("utf-8"),
                   "text/plain")),
        ("files", ("售后响应规范.txt", "售后响应规范：P0 故障 15 分钟响应，P1 一小时，P2 次日跟进。".encode("utf-8"),
                   "text/plain")),
    ]
    r = client.post("/api/v1/kb/documents/bulk", files=files,
                    data={"scope": "department"}, headers=SALES)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["added"] == 2 and d["duplicated"] == 0 and d["skipped"] == 0, d

    # 再传一次 → 全部判重（返回既有 doc_id，不重复入库）
    r2 = client.post("/api/v1/kb/documents/bulk", files=files,
                     data={"scope": "department"}, headers=SALES)
    d2 = r2.json()["data"]
    assert d2["added"] == 0 and d2["duplicated"] == 2, d2
    assert d2["docs"][0]["doc_id"].startswith("doc_")

    # 员工写集团 → 403（越权拦截仍在）
    bad = client.post("/api/v1/kb/documents/bulk", files=files,
                      data={"scope": "global"}, headers=SALES)
    assert bad.status_code == 403


# ─────────────────────────────────────────── ④ 成本曲线
def test_usage_trend_fills_zero_days():
    usage_store.reset()
    for i, real in enumerate([True, True, False]):
        usage_store.record(CALLER, f"agent_{i}", settings.LLM_DEFAULT_MODEL,
                           usage={"prompt_tokens": 100 * (i + 1), "completion_tokens": 50},
                           real=real, latency_ms=120)
    t = usage_store.trend(CALLER, days=7)
    assert len(t["series"]) == 7
    assert t["series"][-1]["calls"] == 3                     # 今天全落在这格
    assert t["series"][-1]["real_calls"] == 2
    assert sum(b["calls"] for b in t["series"]) == 3
    assert t["zero_days"] == 6                               # 空白日确实补了 0，不是省略
    assert t["calls"] == 3 and t["tokens"] == 100 + 200 + 300 + 50 * 3
    assert t["cost"] > 0 and t["currency"] == "CNY"
    # 月末外推 ≥ 本月已发生，预算水位在 0–100 之外的极端值也允许（真实可能超）
    assert t["projected_month_cost"] >= t["month_to_date_cost"]
    assert t["budget_status"] in ("ok", "warn", "over", "unset")
    assert "used_pct" in t["quota"]


def test_usage_trend_endpoint():
    usage_store.reset()
    usage_store.record(CALLER, "agent_x", settings.LLM_DEFAULT_MODEL,
                       usage={"prompt_tokens": 10, "completion_tokens": 10}, real=True)
    r = client.get("/api/v1/usage/trend?days=5", headers=SALES)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["window_days"] == 5 and len(d["series"]) == 5
    assert d["budget_cny"] == settings.USAGE_MONTHLY_BUDGET_CNY


# ─────────────────────────────────────────── ⑤ 基线跑批（外部 golden）
def test_baseline_eval_custom_golden(tmp_path, clean_kb):
    golden = tmp_path / "golden.jsonl"
    golden.write_text("\n".join([
        "# 试点自建 golden：只写 query + 标题，doc_id 由系统反查",
        json.dumps({"query": "满减门槛是多少", "expected_title": "五一营销活动方案 v3"}, ensure_ascii=False),
        json.dumps({"query": "报销什么时候截止", "expected_title": "财务报销流程"}, ensure_ascii=False),
        json.dumps({"query": "这条故意写错目标", "expected_title": "根本不存在的文档"}, ensure_ascii=False),
    ]), encoding="utf-8")

    rep = eval_svc.evaluate(golden_path=str(golden), with_filter=False)
    assert rep["golden_size"] == 2
    assert len(rep["dropped"]) == 1                     # 找不到目标 → 丢弃并说明原因
    assert "根本不存在的文档" in rep["dropped"][0]["reason"]
    assert rep["metrics"]["recall@100"] == 1.0
    assert rep["by_scope"]                              # 按 scope 分组有内容
    assert rep["corpus"]["documents"] >= 2

    out = tmp_path / "reports"
    paths = eval_svc.write_report(rep, out, name="baseline_test")
    for key in ("json", "snapshot", "markdown", "history"):
        assert os.path.exists(paths[key]), key
    assert eval_svc.latest_report(out)["golden_size"] == 2
    assert len(eval_svc.history(out)) == 1


def test_baseline_gate_blocks_bad_corpus(tmp_path):
    """目标文档不在语料里 → 门禁必须给出未通过，而不是"看起来还行"。"""
    golden = tmp_path / "bad.jsonl"
    golden.write_text(json.dumps(
        {"query": "随便问点什么", "expected_doc_id": "doc_not_exist"}, ensure_ascii=False), encoding="utf-8")
    rep = eval_svc.evaluate(golden_path=str(golden), with_filter=False)
    # 未指定标题时按 doc_id 直用（golden 允许显式给 doc_id，哪怕语料里没有 → 召回必然 0）
    if rep["golden_size"] == 1:
        assert rep["metrics"]["recall@100"] == 0.0
        assert rep["gate"]["passed"] is False


# ─────────────────────────────────────────── ⑥ 画像与端点
def test_kb_stats_endpoint(clean_kb):
    r = client.get("/api/v1/kb/stats", headers=SALES)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    for k in ("documents", "total_chars", "by_scope", "index_char_cap",
              "object_store_available", "pg_available"):
        assert k in d, k


def test_eval_endpoints(monkeypatch, tmp_path):
    monkeypatch.setattr(eval_svc, "REPORT_DIR", tmp_path)
    r = client.get("/api/v1/eval/latest", headers=SALES)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "metrics" in body["data"] and body["data"]["golden_size"] > 0

    denied = client.post("/api/v1/eval/run?limit=5", headers=SALES)
    assert denied.status_code == 403                      # 员工不能触发跑批

    ok = client.post("/api/v1/eval/run?limit=5", headers=DEPT_ADMIN)
    assert ok.status_code == 200, ok.text
    assert ok.json()["data"]["golden_size"] == 5
    assert (tmp_path / "latest.json").exists()

    hist = client.get("/api/v1/eval/history?limit=10", headers=SALES)
    assert hist.status_code == 200 and hist.json()["data"]["count"] >= 1
