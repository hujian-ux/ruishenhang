"""M27 · 运维路由（prefix=/ops）：指标 / 健康 / 告警 / 就绪度 / 审计导出 / 自助删数据。

全部端点需登录（Depends(get_caller)）；成功响应统一 {"code":0,"data":{...}}。
/ops/metrics 例外：text/plain 的 Prometheus 文本。
/ops/audit-export 与 /ops/me/data 的结果按 caller 收敛（复用既有 RBAC / userId）。
"""
from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
import os

from app.deps import get_caller
from app.models.caller import CallerContext
from app.services import agent_store, fewshot, observability

router = APIRouter(prefix="/ops", tags=["ops"])


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(caller: CallerContext = Depends(get_caller)):
    """Prometheus exposition（text/plain）；仍需登录。"""
    return PlainTextResponse(content=observability.prometheus_text(), media_type="text/plain")


@router.get("/health")
def health(caller: CallerContext = Depends(get_caller)):
    return {"code": 0, "data": observability.health(caller)}


@router.get("/alerts")
def get_alerts(caller: CallerContext = Depends(get_caller)):
    items = observability.alerts()
    h = observability.health(caller)
    return {
        "code": 0,
        "data": {
            "alerts": items,
            "count": len(items),
            # 样本不足时 alerts 为空，说明落在这里（reason），不误报也不失语
            "insufficient_data": h["insufficient_data"],
        },
    }


@router.get("/readiness")
def readiness(caller: CallerContext = Depends(get_caller)):
    return {"code": 0, "data": observability.readiness()}


@router.get("/audit-export")
def audit_export(caller: CallerContext = Depends(get_caller)):
    # 只读取，范围由 audit.get_logs(caller) 的既有 RBAC 收敛
    return {"code": 0, "data": observability.audit_export(caller)}


@router.delete("/me/data")
def delete_my_data(caller: CallerContext = Depends(get_caller)):
    # 只删 caller.userId 名下数据；其它 store 的数据列 manual_review，不谎称全删
    return {"code": 0, "data": observability.delete_my_data(caller)}


@router.post("/backfill-hard-rules")
def backfill_hard_rules(caller: CallerContext = Depends(get_caller)):
    """M15/M25 P1 · 一键补齐存量业务包的平台硬规则（仅平台管理员）。

    对未含平台硬规则头的业务包追加上下文一致的红线块、并刷新 hard_rules_version；
    已含的不动（幂等）。新导入的业务包由 import_solutions 在解析时自动带上，
    本端点只负责「历史存量」一次性自愈。
    """
    if caller.role != "platform_admin":
        return {"code": 3, "msg": "需要平台管理员权限"}
    return {"code": 0, "data": agent_store.backfill_hard_rules()}


@router.post("/build-fewshot")
def build_fewshot(caller: CallerContext = Depends(get_caller)):
    """M15/M17 P1 · 把方案库 166 个 xlsx 交付物样例灌成 few-shot（仅平台管理员）。

    遍历 SOLUTIONS_ROOT，按 solution_dir 匹配运行中的业务包，把（原始数据 ↔ 交付物）
    成对 xlsx 压成紧凑样例块挂到 agent（幂等、可重跑）。FEWSHOT_ENABLED=1 时运行时
    把样例拼进对应技能的 system_prompt，提升产出物格式一致性。
    """
    if caller.role != "platform_admin":
        return {"code": 3, "msg": "需要平台管理员权限"}
    root = os.environ.get("SOLUTIONS_ROOT", r"D:/workbuddy_解决方案")
    pairs = fewshot.scan_solution_pairs(root)
    by_dir = {p["solution_dir"]: p for p in pairs}
    attached = 0
    for a in list(agent_store._AGENTS.values()):
        if a.get("builtin") or a.get("category") == "public":
            continue
        src = a.get("source_path") or ""
        key = a.get("solution_dir") or (src.rsplit("/", 1)[0] if src else "")
        p = by_dir.get(key)
        if not p:
            continue
        block = fewshot.build_fewshot_block(p["input_path"], p["output_path"])
        fewshot.attach_fewshot(a, block)
        agent_store.save(a)
        attached += 1
    return {"code": 0, "data": {"pairs_found": len(pairs), "attached": attached}}
