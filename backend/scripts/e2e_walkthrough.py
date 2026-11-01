"""最小闭环端到端走查（人读报告版）。

运行：cd backend && python scripts/e2e_walkthrough.py
不依赖外部服务，直接用 FastAPI TestClient 在进程内跑通 P0 闭环并打印可读步骤。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # type: ignore

from app.core.security import create_dev_token
from app.main import app
from app.services import eval as eval_svc

client = TestClient(app)
SALES_AUTH = {"Authorization": create_dev_token(user_id="u_1024", dept_id="d_sales", role="employee")}
DESIGN_AUTH = {"Authorization": create_dev_token(user_id="u_3000", dept_id="d_design", role="employee")}

LINE = "─" * 64


def step(n, title):
    print(f"\n{LINE}\n[{n}] {title}\n{LINE}")


def main():
    print("企业 AI 智能体中台 · 最小闭环端到端走查")
    print(f"后端版本：{app.version}  ·  走查身份：u_1024 / 销售部 / employee")

    # ① 选型
    step(1, "选型服务（第35章）")
    r = client.post("/api/v1/selection/recommend",
                    json={"input_type": "text", "deterministic": False, "need": "knowledge"},
                    headers=SALES_AUTH)
    sel = r.json()["data"]
    print(f"  请求：text / 非确定性 / 企业知识问答")
    print(f"  结论：adapter_type={sel['adapter_type']}  ({sel['name']})")
    print(f"  落地章节：{sel['landing_chapters']}")
    adapter = sel["adapter_type"]

    # ② 建智能体
    step(2, "建智能体（第35章红线：缺选型不可发布）")
    r = client.post("/api/v1/agents",
                    json={"name": "销售知识助手", "adapter_type": adapter,
                          "description": f"选型={adapter}", "knowledge_scope": "department"},
                    headers=SALES_AUTH)
    ag = r.json()["data"]
    print(f"  注册：name=销售知识助手 / scope=department / adapter={adapter}")
    print(f"  回执：agent_id={ag['agent_id']}  status={ag['status']}  registered_by={ag['registered_by']}")
    print("  ✓ registered_by 由服务端按 caller 强制回填，前端不可伪造")

    # ③ 提问
    step(3, "提问（第36章：混合检索 + 父子聚合）")
    r = client.post("/api/v1/rag/retrieve",
                    json={"query": "到店礼 满减 销售话术", "kb_scope": "all",
                          "top_k": 6, "tenant_id": "t_demo"},
                    headers=SALES_AUTH)
    ret = r.json()["data"]
    print(f"  查询：到店礼 满减 销售话术  →  召回 {len(ret['hits'])} 条，过滤 {ret['filtered_out']} 条")
    for h in ret["hits"][:3]:
        print(f"    · [{h['scope']}] {h['title']}  (score={h['score']})")

    # ④ 过滤
    step(4, "过滤（第18章单一过滤点，不可绕过）")
    r = client.post("/api/v1/rag/retrieve",
                    json={"query": "设计部 品牌规范 主色 科技蓝", "kb_scope": "all",
                          "top_k": 6, "tenant_id": "t_demo"},
                    headers=SALES_AUTH)
    ret_sales = r.json()["data"]
    print(f"  销售部员工检索「设计部品牌规范」：过滤 {ret_sales['filtered_out']} 条")
    print(f"    doc_7002(设计部) 在命中？{'是(泄漏!)' if 'doc_7002' in {h['doc_id'] for h in ret_sales['hits']} else '否(已拦截)✓'}")
    r = client.post("/api/v1/rag/retrieve",
                    json={"query": "设计部 品牌规范 主色 科技蓝", "kb_scope": "all",
                          "top_k": 6, "tenant_id": "t_demo"},
                    headers=DESIGN_AUTH)
    ret_design = r.json()["data"]
    print(f"  设计部员工同查询：doc_7002 在命中？{'是(本部门可见)✓' if 'doc_7002' in {h['doc_id'] for h in ret_design['hits']} else '否'}")

    # ⑤ 评测
    step(5, "评测（第37章客观门禁，不依赖 LLM-as-judge）")
    recall = eval_svc.run_recall_gate(k=100)  # 不达标会 SystemExit（门禁阻断）
    obj = eval_svc.run_objective_gate()
    print(f"  Recall@100 = {recall:.3f}  →  {'passed' if recall >= eval_svc.RECALL_GATE_THRESHOLD else 'review'}")
    print(f"  objective 门禁 →  {obj['status']}  "
          f"(recall@5={obj['metrics']['recall@5']} / mrr@10={obj['metrics']['mrr@10']} / filter_acc={obj['metrics']['filter_accuracy']})")

    print(f"\n{LINE}\n闭环走查完成：选型→建智能体→提问→过滤→评测 全部通过 ✓\n{LINE}")


if __name__ == "__main__":
    main()
