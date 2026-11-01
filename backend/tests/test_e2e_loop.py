"""最小闭环端到端走查（任务①）：选型 → 建智能体 → 提问 → 过滤 → 评测。

用 FastAPI TestClient 在进程内串起 P0 三章的真实逻辑，证明闭环可独立验证：
  ① 选型服务：POST /selection/recommend 得到 adapter_type 与落地章节
  ② 建智能体：POST /agents 落库（服务端强制 registered_by，缺选型不可发布）
  ③ 提问：     POST /rag/retrieve 走混合检索 + 父子聚合
  ④ 过滤：     第18章单一过滤点——跨部文档不进上下文（filtered_out≥1 且不泄漏）
  ⑤ 评测：     eval 客观门禁（Recall@100 / objective 均 passed，不依赖 LLM-as-judge）
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # type: ignore

from app.core.security import create_dev_token
from app.main import app
from app.services import eval as eval_svc

client = TestClient(app)

# 销售部员工（拥有 d_sales 的 department 文档）
SALES_TOKEN = create_dev_token(user_id="u_1024", dept_id="d_sales", role="employee")
SALES_AUTH = {"Authorization": SALES_TOKEN}
# 设计部员工（拥有 d_design 的 department 文档）
DESIGN_TOKEN = create_dev_token(user_id="u_3000", dept_id="d_design", role="employee")
DESIGN_AUTH = {"Authorization": DESIGN_TOKEN}


def test_e2e_full_loop():
    # ── ① 选型服务化（第35章）──
    r = client.post("/api/v1/selection/recommend",
                    json={"input_type": "text", "deterministic": False, "need": "knowledge"},
                    headers=SALES_AUTH)
    assert r.status_code == 200, r.text
    sel = r.json()["data"]
    assert sel["adapter_type"] == "llm_retrieval", sel
    assert "landing_chapters" in sel and sel["landing_chapters"]
    adapter = sel["adapter_type"]

    # ── ② 建智能体（第35章红线：缺选型不可发布；registered_by 服务端强制）──
    r = client.post("/api/v1/agents",
                    json={"name": "销售知识助手", "adapter_type": adapter,
                          "description": f"选型={adapter}", "knowledge_scope": "department"},
                    headers=SALES_AUTH)
    assert r.status_code == 200, r.text
    ag = r.json()["data"]
    assert ag["status"] == "pending_review", ag          # 进入待发布评审
    assert ag["registered_by"] == "u_1024", ag           # 服务端强制，前端不可伪造
    assert ag["selection"]["recommended"] == adapter

    # ── ③ 提问（第36章：混合检索 + 父子聚合）──
    r = client.post("/api/v1/rag/retrieve",
                    json={"query": "到店礼 满减 销售话术", "kb_scope": "all",
                          "top_k": 6, "tenant_id": "t_demo"},
                    headers=SALES_AUTH)
    assert r.status_code == 200, r.text
    ret = r.json()["data"]
    assert len(ret["hits"]) > 0, "销售部员工应召回本部门知识"
    # 不变量：进入上下文的命中项全部可访问（单一过滤点保证无越权泄漏）
    for h in ret["hits"]:
        assert h["scope"] in ("global", "department", "owner", "personal")
    sales_hit_ids = {h["doc_id"] for h in ret["hits"]}
    assert "doc_8831" in sales_hit_ids or "doc_9001" in sales_hit_ids, "应可见销售部文档"

    # ── ④ 过滤（第18章单一过滤点，不可绕过）──
    # 以销售部员工检索"设计部品牌规范"，设计部 department 文档应被剔除，且不出现在命中
    r = client.post("/api/v1/rag/retrieve",
                    json={"query": "设计部 品牌规范 主色 科技蓝", "kb_scope": "all",
                          "top_k": 6, "tenant_id": "t_demo"},
                    headers=SALES_AUTH)
    assert r.status_code == 200, r.text
    ret_sales = r.json()["data"]
    assert ret_sales["filtered_out"] >= 1, "跨部 department 文档应被过滤"
    assert "doc_7002" not in {h["doc_id"] for h in ret_sales["hits"]}, "设计部文档不应泄漏给销售部"

    # 同一查询以设计部员工检索 → 该文档可见（过滤是 caller 相关，而非一刀切）
    r = client.post("/api/v1/rag/retrieve",
                    json={"query": "设计部 品牌规范 主色 科技蓝", "kb_scope": "all",
                          "top_k": 6, "tenant_id": "t_demo"},
                    headers=DESIGN_AUTH)
    assert r.status_code == 200, r.text
    ret_design = r.json()["data"]
    assert "doc_7002" in {h["doc_id"] for h in ret_design["hits"]}, "设计部员工应可见本部门文档"

    # ── ⑤ 评测（第37章客观门禁，不依赖 LLM-as-judge）──
    recall_score = eval_svc.run_recall_gate(k=100)  # 不达标会 SystemExit（门禁阻断）
    assert recall_score >= eval_svc.RECALL_GATE_THRESHOLD, recall_score
    obj = eval_svc.run_objective_gate()
    assert obj["status"] == "passed", obj
