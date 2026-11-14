# -*- coding: utf-8 -*-
"""真模型端到端联调冒烟脚本（在「出网放行」环境运行）。

目的：验证「真实 LLM 调用 → 会话上下文窗口 → 压缩注入 → 生成器整链路」。

⚠️ 本脚本在本 WorkBuddy 沙箱无法执行——沙箱安全策略拦截了对 LLM API 的出网调用
（实测：请求打到 api.siliconflow.cn 后被拦截，且提示不得重试等价方式）。
请在以下环境运行：
    cd backend
    ./.venv/Scripts/python.exe scripts/real_e2e_smoke.py

前置（.env 需具备且不要加 EAP_SKIP_ENV_LOCAL=1，那会桩掉模型）：
    LLM_PROVIDER=direct
    LLM_VENDOR=siliconflow
    LLM_API_KEY=sk-...                       # 真实硅基流动 key
    LLM_DEFAULT_MODEL=<硅基流动真实存在的模型 id>   # 见下方「已知坑」

已知坑（2026-11-14 实测）：
    .env 里的 LLM_DEFAULT_MODEL=Qwen/Qwen2.5-7B-Instruct 在硅基流动免费池返回
    HTTP 400「Model does not exist」。请在硅基流动控制台「模型」页确认免费池实际
    可用的 Qwen2.5-7B id（常见为 Qwen/Qwen2.5-7B-Instruct 或带版本后缀），改对再跑。
"""
import os
import sys
import time


def _load_env():
    # 与 app 的 pydantic-settings 行为一致：显式加载 .env，确保 LLM_API_KEY 注入
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass


def main() -> int:
    _load_env()

    from app.core.config import settings
    from app.services import model_gateway as m
    from app.services import session_store as ss
    from app.models.caller import CallerContext
    from app.services import agent_runtime as ar

    print("PROVIDER =", settings.LLM_PROVIDER)
    print("VENDOR   =", settings.LLM_VENDOR)
    print("MODEL    =", settings.LLM_DEFAULT_MODEL)
    key = getattr(settings, "LLM_API_KEY", "") or ""
    print("KEY      =", (key[:6] + "…") if key else "EMPTY ⚠")

    if not m.llm_available(force=True):
        print("✗ llm_available=False：检查 LLM_API_KEY / LLM_PROVIDER / 出网是否放行")
        return 2

    # ── Tier1：裸模型调用（最小依赖，先证明真模型可达）──
    t0 = time.time()
    r1 = m.chat_sync_ex(
        None,
        [{"role": "user", "content": "用一句话回答：东莞在哪个省？"}],
        timeout=30, max_tokens=40,
    )
    print(f"[Tier1] elapsed={time.time()-t0:.2f}s real={r1.get('real')} "
          f"model={r1.get('model_id')} error={r1.get('error')}")
    print("        text:", (r1.get("text") or "")[:120])
    if not r1.get("real"):
        print("✗ 真模型未返回 real=True（见 error），终止")
        return 3

    # ── Tier2：端到端 run_agent + 上下文窗口 + 压缩注入 ──
    # 触发压缩：开启 CTX_COMPRESS_ENABLED 并造一段会超窗的历史
    os.environ["CTX_COMPRESS_ENABLED"] = "1"
    caller = CallerContext(userId="u_e2e", deptId="d_sales",
                           role="employee", tenantId="t_demo")
    history = [
        {"role": "user" if i % 2 == 0 else "assistant",
         "content": f"历史第{i}轮：关于企业战略六维框架的市场分析要点{i}"}
        for i in range(20)
    ]
    t1 = time.time()
    res = ar.run_agent(
        "", "结合上面的历史，给我一句总结性建议。", caller,
        history=history, adapter_type="llm", context_window=4,
    )
    print(f"[Tier2] elapsed={time.time()-t1:.2f}s answer_len={len(res.get('answer',''))}")
    print("        answer:", res.get("answer", "")[:200])

    # P1-12 新增的上下文窗口观测字段
    ctx = res.get("context") or {}
    print("        context.window_turns =", ctx.get("window_turns"),
          "dropped =", ctx.get("dropped_turns"),
          "summary_provider =", (ctx.get("summary") or {}).get("provider"))

    print("✓ 真模型端到端联调通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
