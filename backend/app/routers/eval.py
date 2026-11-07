"""M8 评测路由：把「基线跑批」变成页面能看的一屏。

  - GET  /eval/latest   最近一次基线报告（先读 reports/latest.json；没有就现算一遍内置 golden）
  - GET  /eval/history  基线漂移序列（试点每天跑一次，看召回随语料增长是否掉下来）
  - POST /eval/run      现场跑一次基线并落盘（limit 控制条数，避免页面等太久）

只读 + 触发跑批，不提供改 golden 的接口：评测集属受控资产，改动走 Git 评审。
"""
from fastapi import APIRouter, Depends, HTTPException, Query

from app.deps import get_caller
from app.models.caller import CallerContext
from app.services import eval as eval_svc
from app.services import judge as judge_svc
from app.services import model_gateway

router = APIRouter(prefix="/eval", tags=["eval"])


@router.get("/latest")
async def latest(caller: CallerContext = Depends(get_caller)):
    """最近一次基线报告；尚无报告时用内置 golden 现算（不落盘，仅供页面首屏）。"""
    rep = eval_svc.latest_report()
    if rep:
        return {"code": 0, "data": rep, "source": "report"}
    rep = eval_svc.evaluate(with_filter=False)
    return {"code": 0, "data": rep, "source": "computed"}


@router.get("/history")
async def history(limit: int = Query(30, ge=1, le=200),
                  caller: CallerContext = Depends(get_caller)):
    rows = eval_svc.history(limit=limit)
    return {"code": 0, "data": {"rows": rows, "count": len(rows)}}


@router.post("/run")
async def run(limit: int = Query(0, ge=0, le=2000,
                                 description="只跑前 N 条 golden（0=全部）"),
              caller: CallerContext = Depends(get_caller)):
    """现场跑一次基线并落盘（页面「跑一次基线」按钮）。

    放开给部门管理员以上：员工点不动评测（避免误跑把 latest 覆盖成残缺报告）。
    """
    if caller.role not in ("dept_admin", "platform_admin"):
        raise HTTPException(status_code=403, detail="forbidden: 仅部门管理员/平台管理员可触发基线跑批")
    golden = eval_svc.load_golden() or eval_svc.build_golden_entries()
    golden, dropped = eval_svc.normalize_golden(golden)
    if limit:
        golden = golden[:limit]
    try:
        report = eval_svc.evaluate(golden=golden, with_filter=True)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    report["dropped"] = dropped
    paths = eval_svc.write_report(report)
    return {"code": 0, "data": report, "paths": paths}


@router.post("/judge")
async def judge(caller: CallerContext = Depends(get_caller)):
    """现场跑一次 LLM-as-Judge（第28章）。

    仅平台管理员可触发（判官要调真模型，逐条消耗 token）。无真模型时如实返回
    ``llm_real=false`` 与 gate 不通过，**绝不拿 stub 当真判分**。
    """
    if caller.role != "platform_admin":
        raise HTTPException(status_code=403, detail="forbidden: 仅平台管理员可触发 LLM-as-Judge")
    if not model_gateway.llm_available():
        return {"code": 0, "data": {"llm_real": False,
                                    "hint": "未配置真模型密钥，判官无法运行（LLM_API_KEY / 厂商环境变量）",
                                    "gate": {"passed": False, "reason": "判官未接真模型"}}}
    try:
        report = judge_svc.run_judge()
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    baseline = judge_svc.load_baseline()
    reg = judge_svc.regression_gate(report, baseline)
    return {"code": 0, "data": {**report, "regression": reg}}
