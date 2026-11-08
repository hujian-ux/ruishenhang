"""M27 · MLOps 训练流水线路由。

端点（统一前缀 /mlops，挂在 API_PREFIX 下）：
- GET    /mlops/pipeline            流水线状态（诚实标注未接项）
- POST   /mlops/register            登记模型元数据（兼容旧骨架签名）
- GET    /mlops/models               模型仓库列表（按租户隔离，platform_admin 看全部）
- POST   /mlops/drift               单变量 PSI + 均值漂移（兼容旧）
- POST   /mlops/datasets/export     导出训练数据集（本地特征库 materialize）
- GET    /mlops/datasets            数据集列表（租户隔离）
- POST   /mlops/trains              基线 sklearn 训练，返回 run + 登记模型(staging)
- GET    /mlops/trains/{run_id}     训练任务状态/指标
- POST   /mlops/models/{model_id}/promote  晋升 staging→production（platform_admin）
- POST   /mlops/drift/run           多维特征漂移（PSI + 均值漂移）
- POST   /mlops/retrain             真实自动重训入口（platform_admin；需 MLOPS_AUTO_RETRAIN=1）

鉴权：全部需登录；promote 需 platform_admin；数据集/模型/训练按 tenant_id 隔离。
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from app.core.config import settings
from app.deps import get_caller
from app.models.caller import CallerContext
from app.services import mlops

router = APIRouter(prefix="/mlops", tags=["mlops"])


def _require_keys(body: Dict[str, Any], *keys: str) -> None:
    for k in keys:
        if k not in body or body.get(k) in (None, ""):
            raise HTTPException(status_code=422, detail=f"缺少字段：{k}")


def _own_or_admin(caller: CallerContext, tenant_id: str) -> None:
    """资源必须属于当前租户，或调用者为 platform_admin；否则 404（不泄露存在性）。"""
    if caller.role == "platform_admin":
        return
    if tenant_id != caller.tenantId:
        raise HTTPException(status_code=404, detail="资源不存在或无权访问")


# ───────────────────────────── 状态 / 注册 / 列表 ─────────────────────────────
@router.get("/pipeline")
async def mlops_pipeline(caller: CallerContext = Depends(get_caller)):
    """流水线状态：四项已部分落地 + 诚实标注未接项。"""
    return {"code": 0, "data": mlops.pipeline_status()}


@router.post("/register")
async def mlops_register(body: dict, caller: CallerContext = Depends(get_caller)):
    """登记模型元数据（兼容旧骨架；新增 tenant_id/artifact/stage 透传）。"""
    _require_keys(body, "name", "version", "metrics")
    if not isinstance(body.get("metrics"), dict):
        raise HTTPException(status_code=422, detail="metrics 必须是对象")
    data = mlops.register_model(
        str(body.get("name") or ""), str(body.get("version") or ""),
        body.get("metrics") or {}, str(body.get("fingerprint") or ""),
        tenant_id=caller.tenantId,
        artifact=str(body.get("artifact") or ""),
        stage=str(body.get("stage") or "registered"),
        dataset_id=str(body.get("dataset_id") or ""),
        label_col=str(body.get("label_col") or ""),
        feature_cols=body.get("feature_cols") or [],
    )
    return {"code": 0, "data": data}


@router.get("/models")
async def mlops_models(caller: CallerContext = Depends(get_caller)):
    """模型仓库列表（按租户隔离，platform_admin 看全部）。"""
    items = mlops.list_models(caller.tenantId, caller.tenantId, caller.role)
    return {"code": 0, "data": {"items": items, "count": len(items)}}


@router.post("/drift")
async def mlops_drift(body: dict, caller: CallerContext = Depends(get_caller)):
    """单变量 PSI + 均值漂移（兼容旧骨架）。"""
    _require_keys(body, "baseline", "observed")
    if not isinstance(body.get("baseline"), list) or not isinstance(body.get("observed"), list):
        raise HTTPException(status_code=422, detail="baseline / observed 必须是列表")
    data = mlops.drift_check(body.get("baseline") or [], body.get("observed") or [])
    return {"code": 0, "data": data}


# ───────────────────────────── 数据集 ─────────────────────────────
@router.post("/datasets/export")
async def mlops_dataset_export(body: dict, caller: CallerContext = Depends(get_caller)):
    """导出训练数据集（本地特征库 materialize）。"""
    _require_keys(body, "records", "feature_cols", "label_col")
    if not isinstance(body.get("records"), list) or not body.get("records"):
        raise HTTPException(status_code=422, detail="records 必须是非空列表")
    if not isinstance(body.get("feature_cols"), list) or not body.get("feature_cols"):
        raise HTTPException(status_code=422, detail="feature_cols 必须是非空列表")
    try:
        data = mlops.export_training_dataset(
            caller.tenantId, body["records"], body["feature_cols"],
            str(body["label_col"]), name=str(body.get("name") or ""))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"code": 0, "data": data}


@router.get("/datasets")
async def mlops_datasets(caller: CallerContext = Depends(get_caller)):
    """数据集列表（租户隔离）。"""
    items = mlops.list_datasets(caller.tenantId, caller.tenantId, caller.role)
    return {"code": 0, "data": {"items": items, "count": len(items)}}


# ───────────────────────────── 训练 ─────────────────────────────
@router.post("/trains")
async def mlops_train(body: dict, caller: CallerContext = Depends(get_caller)):
    """基线 sklearn 训练，返回 run 记录（含登记模型，阶段 staging）。"""
    _require_keys(body, "dataset_id")
    dataset = mlops.list_datasets(caller.tenantId, caller.tenantId, caller.role)
    match = next((d for d in dataset if d["id"] == body["dataset_id"]), None)
    _own_or_admin(caller, (match or {}).get("tenant_id", ""))
    if not match:
        raise HTTPException(status_code=404, detail="数据集不存在或无权访问")
    try:
        run = mlops.train_model(
            caller.tenantId, body["dataset_id"],
            model_type=str(body.get("model_type") or "auto"),
            params=body.get("params") or None, caller=caller)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"code": 0, "data": run}


@router.get("/trains/{run_id}")
async def mlops_train_status(run_id: str, caller: CallerContext = Depends(get_caller)):
    """训练任务状态/指标。"""
    run = mlops.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="训练任务不存在")
    _own_or_admin(caller, run.get("tenant_id", ""))
    return {"code": 0, "data": run}


# ───────────────────────────── 晋升 / 漂移检测 ─────────────────────────────
@router.post("/models/{model_id}/promote")
async def mlops_promote(model_id: str, caller: CallerContext = Depends(get_caller)):
    """晋升 staging→production（需 platform_admin）。"""
    try:
        data = mlops.promote_model(model_id, caller)
    except PermissionError:
        raise HTTPException(status_code=403,
                            detail="forbidden: 仅 platform_admin 可晋升模型")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"code": 0, "data": data}


@router.post("/drift/run")
async def mlops_drift_run(body: dict, caller: CallerContext = Depends(get_caller)):
    """多维特征漂移（PSI + 均值漂移）。baseline 取数据集，observed 取记录或数据集。"""
    _require_keys(body, "baseline_dataset_id")
    observed = body.get("observed_records")
    observed_id = body.get("observed_dataset_id")
    if observed is None and observed_id is None:
        raise HTTPException(status_code=422,
                            detail="需提供 observed_records 或 observed_dataset_id")
    try:
        data = mlops.run_drift(caller.tenantId, body["baseline_dataset_id"],
                               observed_records=observed, observed_dataset_id=observed_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"code": 0, "data": data}


@router.post("/retrain")
async def mlops_retrain(body: dict, caller: CallerContext = Depends(get_caller)):
    """P2 真实自动重训入口（platform_admin）：漂移 → 真实训练 → 可选晋升。

    安全默认：需 MLOPS_AUTO_RETRAIN=1 才允许真正跑训练；否则返回 409 提示先开启，
    避免误触发训练/晋升。observed_records 为空时只返回漂移建议（不训练）。
    """
    if caller.role != "platform_admin":
        raise HTTPException(status_code=403, detail="forbidden: 仅 platform_admin 可触发重训")
    if not settings.MLOPS_AUTO_RETRAIN:
        raise HTTPException(status_code=409,
                            detail="MLOPS_AUTO_RETRAIN 未开启；开启后才允许自动重训（安全默认）")
    _require_keys(body, "baseline_dataset_id")
    try:
        data = mlops.scheduled_retrain(
            body["baseline_dataset_id"], body.get("observed_records") or [],
            tenant_id=caller.tenantId, caller=caller,
            auto_promote=bool(body.get("auto_promote", False)))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"code": 0, "data": data}
