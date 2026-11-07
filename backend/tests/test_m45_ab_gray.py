"""M45 · P1 反馈闭环 A/B 实验 + 灰度发布（蓝图第 20/30/41 章）。

纯内存态 + 确定性分桶，不依赖外部 LLM / PG：
- A/B 实验：建实验 → 确定性分桶分配 → 记录产出 → 报告选优（样本不足标 insufficient）
- 灰度发布：set_rollout_weight 渐进放量（其余按比例缩放，总和保持 1）
- promote：结束实验并落一条 pending 演化建议（人在环，绝不静默改线上）
"""
from app.services import feedback as svc


class _Caller:
    def __init__(self, role="platform_admin", uid="u1", dept="d1", tenant="t1"):
        self.role = role
        self.userId = uid
        self.deptId = dept
        self.tenantId = tenant


def _reset():
    svc.reset_state()
    svc._AB_MIN_SAMPLES = 3  # 测试加速：单变体 3 样本即给结论


def _make_caller(role="platform_admin"):
    return _Caller(role=role)


def test_create_ab_experiment_validates_weights():
    _reset()
    admin = _make_caller()
    # ab 模式权重和必须≈1
    exp = svc.create_experiment("agent_x", "标题优化", [
        {"name": "A", "prompt_override": "pA", "weight": 0.5},
        {"name": "B", "prompt_override": "pB", "weight": 0.5},
    ], admin, mode="ab")
    assert exp["mode"] == "ab"
    assert exp["status"] == "running"
    assert len(exp["variants"]) == 2
    # 权重和≈1 被接受；非法：权重和≠1 应报错
    try:
        svc.create_experiment("a", "t", [
            {"name": "A", "weight": 0.3}, {"name": "B", "weight": 0.3}],
            admin, mode="ab")
        assert False, "权重和≠1 应被拒"
    except ValueError:
        pass


def test_assign_variant_is_deterministic_and_respects_weights():
    _reset()
    admin = _make_caller()
    exp = svc.create_experiment("agent_x", "t", [
        {"name": "A", "prompt_override": "pA", "weight": 0.7},
        {"name": "B", "prompt_override": "pB", "weight": 0.3},
    ], admin, mode="ab")
    # 同一 user_key 永远同一 variant
    v1 = svc.assign_variant(exp, "user-42")
    v2 = svc.assign_variant(exp, "user-42")
    assert v1["variant"] == v2["variant"]
    assert v1["prompt_override"] in ("pA", "pB")
    # 分布接近权重（100 个 key）
    counts = {"A": 0, "B": 0}
    for i in range(100):
        counts[svc.assign_variant(exp, f"user-{i}")["variant"]] += 1
    assert 50 <= counts["A"] <= 90  # 0.7 权重附近，宽松区间
    # 非 running / 不存在 → None
    assert svc.assign_variant(None, "x") is None
    exp["status"] = "ended"
    assert svc.assign_variant(exp, "user-42") is None


def test_experiment_report_winner_and_insufficient():
    _reset()
    admin = _make_caller()
    exp = svc.create_experiment("agent_x", "t", [
        {"name": "A", "weight": 0.5}, {"name": "B", "weight": 0.5},
    ], admin, mode="ab")
    # 样本不足 → insufficient_data=True，winner=None
    svc.record_outcome(exp["id"], "A", {"adopted": True})
    rep = svc.experiment_report(exp["id"])
    assert rep["insufficient_data"] is True
    assert rep["winner"] is None
    # 灌足样本：A 采纳率远高于 B
    for _ in range(5):
        svc.record_outcome(exp["id"], "A", {"adopted": True})
        svc.record_outcome(exp["id"], "B", {"adopted": False})
    rep = svc.experiment_report(exp["id"])
    assert rep["insufficient_data"] is False
    assert rep["winner"] == "A"
    assert rep["variants"][0]["adoption_rate"] >= rep["variants"][1]["adoption_rate"]


def test_gray_rollout_weight_scales_others():
    _reset()
    admin = _make_caller()
    exp = svc.create_experiment("agent_x", "灰度", [
        {"name": "base", "weight": 0.5},
        {"name": "new", "prompt_override": "pNew", "weight": 0.5},
    ], admin, mode="gray")
    # 把 new 放量到 0.8 → base 缩到 0.2
    upd = svc.set_rollout_weight(exp["id"], "new", 0.8, admin)
    wmap = {v["name"]: v["weight"] for v in upd["variants"]}
    assert abs(wmap["new"] - 0.8) < 1e-6
    assert abs(wmap["base"] - 0.2) < 1e-6
    # 非管理员拒绝
    try:
        svc.set_rollout_weight(exp["id"], "new", 0.5, _make_caller(role="employee"))
        assert False, "非管理员应被拒"
    except PermissionError:
        pass


def test_promote_ends_experiment_and_creates_pending_proposal():
    _reset()
    admin = _make_caller()
    exp = svc.create_experiment("agent_x", "t", [
        {"name": "A", "prompt_override": "pA", "weight": 0.5},
        {"name": "B", "prompt_override": "pB", "weight": 0.5},
    ], admin, mode="ab")
    res = svc.promote_variant(exp["id"], "A", admin)
    # 实验结束
    assert svc.list_experiments(status="running") == []
    # 落一条 pending 演化建议（不直接改 prompt）
    prop = res["proposal"]
    assert prop["status"] == "pending"
    assert prop["payload"]["prompt_override"] == "pA"
    assert res["requires_approval"] is True
    assert res["silent_db_write"] is False
