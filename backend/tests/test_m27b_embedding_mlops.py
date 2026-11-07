"""M27 · 非 LLM MLOps 闭环（嵌入模型：登记/版本/基准/回归/状态）。"""
import pytest

from app.services import embedding_mlops as em


@pytest.fixture(autouse=True)
def _isolate():
    em.reset_state()
    yield
    em.reset_state()


def test_register_embedding_model_fingerprint():
    rec = em.register_embedding_model(path="D:/nonexistent_model_dir")
    assert rec["name"].startswith("embedding:")
    assert rec["metrics"]["backend"] == "fallback"
    assert "fingerprint" in rec["metrics"]


def test_snapshot_dataset_version_changes_with_content():
    s1 = em.snapshot_dataset("kb", ["a", "b"])
    s2 = em.snapshot_dataset("kb", ["a", "c"])
    assert s1["version"] != s2["version"]
    assert s1["hash"] != s2["hash"]


def test_benchmark_recall_with_fallback():
    items = [
        {"anchor": "储能电站 容量补偿 政策 2026",
         "candidates": ["储能电站 容量补偿 政策 2026 广东", "半成品菜 供应链 品牌 报价 广东"],
         "positive_idx": 0},
        {"anchor": "差旅住宿 标准 600元 超标审批",
         "candidates": ["差旅住宿 标准 600元 超标 分管副总 审批", "广东 储能 容量 补偿 政策"],
         "positive_idx": 0},
    ]
    res = em.benchmark(items)  # 默认 fallback
    assert res["backend"] == "fallback"
    assert res["recall_at_1"] == 1.0
    assert res["separation"] > 0


def test_regression_check_flags_drop():
    base = {"recall_at_1": 1.0}
    assert em.regression_check({"recall_at_1": 0.8}, base)["regression"] is True
    assert em.regression_check({"recall_at_1": 0.97}, base)["regression"] is False
    assert em.regression_check({"recall_at_1": 0.9}, base, drop_threshold=0.05)["regression"] is True


def test_pipeline_status_v2_reflects_registration():
    assert em.pipeline_status_v2()["embedding_loop"]["registered"] is False
    em.register_embedding_model()
    assert em.pipeline_status_v2()["embedding_loop"]["registered"] is True
