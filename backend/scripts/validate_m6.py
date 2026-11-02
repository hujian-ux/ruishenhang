#!/usr/bin/env python3
"""M6 基础设施实机验证脚本。

逐项检查 PG / MinIO / LiteLLM 三项底座是否真正生效，并验证核心能力：
  - 会话 / 审批单写穿 PostgreSQL，模拟"应用重启"（新进程 load_from_pg）不丢失；
  - 知识文档原文落 MinIO，上传后可回读且内容一致；
  - 智能体调用走真实 LLM 生成（无密钥时优雅回退 stub，并明确标注）。

用法（在 backend 目录下，使用项目 venv 运行）：
  ../.venv/Scripts/python.exe scripts/validate_m6.py

设计原则：基础设施未启动时各项 SKIP，绝不让脚本崩溃；启动后逐项 PASS/FAIL。
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


def banner(title: str) -> None:
    print("\n" + "=" * 66)
    print(title)
    print("=" * 66)


def check(name: str, fn):
    try:
        ok, detail = fn()
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] {name}: 异常 {e!r}")
        return False
    if ok is None:
        print(f"[SKIP] {name}: {detail}")
        return None
    if ok:
        print(f"[PASS] {name}: {detail}")
    else:
        print(f"[FAIL] {name}: {detail}")
    return ok


# ──────────────────────────────────────────────────────────────
# 子进程模式：模拟"应用重启"，从 PG 载回刚才写的数据
# ──────────────────────────────────────────────────────────────
if "--restart-check" in sys.argv:
    sid = sys.argv[sys.argv.index("--restart-check") + 1]
    aid = sys.argv[sys.argv.index("--restart-check") + 2]
    from app.services import session_store, approval_store  # noqa: E402

    ns = session_store.load_from_pg()
    na = approval_store.load_from_pg()
    sess_ok = sid in session_store._SESSIONS
    tick_ok = aid in approval_store._TICKETS
    print(f"RESTART agent_sessions 载回 {ns} 条（含目标={sess_ok}）")
    print(f"RESTART approval_tickets 载回 {na} 条（含目标={tick_ok}）")
    sys.exit(0 if (sess_ok and tick_ok) else 1)


def main() -> None:
    from app.core.config import settings
    from app.models.caller import CallerContext
    from app.services import (agent_runtime, approval_store, model_gateway,
                              object_store, pg_store, session_store)

    banner("M6 基础设施实机验证")
    print(f"PG      : {settings.PG_HOST}:{settings.PG_PORT}/{settings.PG_DB}  user={settings.PG_USER}")
    print(f"MinIO   : {settings.MINIO_ENDPOINT}  bucket={settings.MINIO_BUCKET}")
    print(f"LiteLLM : {settings.LITELLM_BASE_URL}  default_model={settings.LLM_DEFAULT_MODEL}")

    caller = CallerContext(userId="u_val", deptId="d_val", tenantId="t_demo", role="employee")

    # ① PG 持久化 + 重启不丢
    def pg_test():
        if not pg_store.available():
            return None, "PG 不可达（docker 未启动或端口不通；接口仍可运行，仅内存兜底）"
        sess = session_store.create_session("val_agent", caller)
        session_store.append_turn(sess["session_id"], "user", "验证问题一")
        session_store.append_turn(sess["session_id"], "assistant", "验证回答一")
        ticket = approval_store.create_ticket("val_agent", caller.userId, caller.deptId)
        # 新进程 = 应用重启：load_from_pg 应从 PG 载回，内存丢失也能恢复
        env = dict(os.environ, PYTHONPATH=BACKEND)
        r = subprocess.run(
            [sys.executable, __file__, "--restart-check", sess["session_id"], ticket["approval_id"]],
            capture_output=True, text=True, cwd=BACKEND, env=env,
        )
        ok = r.returncode == 0
        detail = (r.stdout.strip() or r.stderr.strip() or "无输出") + (" → 重启载回成功" if ok else " → 重启载回失败")
        return ok, detail

    check("PG 写穿 + 重启载回会话/审批单", pg_test)

    # ② MinIO 文档原文上传 + 回读一致
    def minio_test():
        if not object_store.available():
            return None, "MinIO 不可达（docker 未启动或端口不通；接口仍可运行，仅内存兜底）"
        key = "val/kb_sample.txt"
        content = "企业知识库：混合检索 = BM25 + 向量余弦 + RRF 融合；父子切分聚合；单一过滤点防越权。"
        loc = object_store.put_text(key, content, metadata={"source": "validate_m6"})
        if not loc.startswith("minio://"):
            return False, f"未写入 MinIO（落内存兜底：{loc}）"
        back = object_store.get_text(key)
        return back == content, f"写入 {loc}；回读一致={'是' if back == content else '否'}"

    check("MinIO 文档原文上传 + 回读一致", minio_test)

    # ③ LiteLLM 可达 + 智能体调用（真/伪生成判别）
    def llm_test():
        up = model_gateway.llm_available(force=True)
        if not up:
            return None, "LiteLLM 代理不可达（docker 未启动 / 端口 4000 不通；调用将走 stub 回退）"
        ans = agent_runtime.run_agent("val_agent", "用一句话说明知识库混合检索的原理。", caller, history=[])
        answer = ans["answer"]
        is_real = not answer.startswith("[stub")
        tag = ("真实LLM生成" if is_real
               else "调用失败→stub回退（查 D:\\enterprise-ai-platform\\logs\\litellm.err.log："
                    "常见原因＝密钥含中文/占位文字、或模型名被误设成密钥）")
        return True, f"模型={settings.LLM_DEFAULT_MODEL}；{tag}：{answer[:64]}"

    check("LiteLLM 可达 + 智能体调用（真/伪生成判别）", llm_test)

    banner("结论")
    print("SKIP = 对应底座未启动（接口仍可用，仅内存兜底）；PASS/FAIL = 真实底座生效验证。")
    print("要跑出【真实 LLM 生成】：在 .env 或终端设置 DASHSCOPE_API_KEY（通义千问）或")
    print("OPENAI_API_KEY，并把 LLM_DEFAULT_MODEL 设为 qwen-plus / gpt-4o-mini，再重跑本脚本。")


if __name__ == "__main__":
    main()
