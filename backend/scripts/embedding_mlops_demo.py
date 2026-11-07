"""M27 · 非 LLM MLOps 闭环演示（嵌入模型 bge-m3）。

离线可跑：未装 sentence-transformers / 无模型路径时自动走确定性 fallback 嵌入，
明确标 backend=fallback，不谎报真模型指标。装了且 D:/models/bge-m3 存在则自动切真模型。

用法：
    .venv/Scripts/python.exe scripts/embedding_mlops_demo.py
"""
from app.services import embedding_mlops as em


def _golden() -> list:
    # 中文相似对：正例与 anchor 共享大量字符（fallback 也能拉开差距）。
    return [
        {"anchor": "储能电站 容量补偿 政策 2026",
         "candidates": ["储能电站 容量补偿 政策 2026 广东", "半成品菜 供应链 品牌 报价 广东"],
         "positive_idx": 0},
        {"anchor": "差旅住宿 标准 600元 超标审批",
         "candidates": ["差旅住宿 标准 600元 超标 分管副总 审批", "广东 储能 容量 补偿 政策"],
         "positive_idx": 0},
        {"anchor": "报销 办法 每月 25日 截止",
         "candidates": ["费用报销 管理办法 每月 25日 截止", "跨境 电商 退税 流程"],
         "positive_idx": 0},
    ]


def main() -> int:
    print("== 1. 登记嵌入模型（指纹 + 维度 + 后端）==")
    rec = em.register_embedding_model()
    print(f"  模型：{rec['name']}  版本：{rec['version']}  指纹：{rec['metrics']['fingerprint']}")
    print(f"  后端：{rec['metrics']['backend']}  维度：{rec['metrics']['dim']}")

    print("\n== 2. 数据集版本化（知识库快照）==")
    snap = em.snapshot_dataset("kb_demo", ["doc1 内容", "doc2 内容"])
    print(f"  版本：{snap['version']}  文档数：{snap['count']}")

    print("\n== 3. 回归基准（golden 相似对）==")
    model = em._load_real_model()
    res = em.benchmark(_golden(), model=model)
    print(f"  后端：{res['backend']}  recall@1={res['recall_at_1']}  "
          f"recall@k={res['recall_at_k']}  正负分离={res['separation']}")

    print("\n== 4. 回归判定（vs 基线）==")
    base = {"recall_at_1": 1.0}
    rc = em.regression_check(res, base)
    print(f"  regression={rc['regression']}  drop={rc['drop']}  threshold={rc['threshold']}")

    print("\n== 5. 闭环状态 ==")
    st = em.pipeline_status_v2()
    el = st["embedding_loop"]
    print(f"  模型已登记={el['registered']}  后端={el['backend']}  评估已跑={el['embedding_eval_done']}")
    print(f"  MLOps 整体 trained={st['trained']}（诚实：非 LLM 训练不参与此字段）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
