#!/usr/bin/env python3
"""审计留存定期清理脚本 —— 闭环「180 天留存」策略的运维侧执行器。

平台已内置：
  - ``audit.purge_expired()``：按 ``AUDIT_RETENTION_DAYS`` 删除过期记录；
    法定保留动作（``AUDIT_LEGAL_HOLD_ACTIONS``：approval/download/handoff）永不过期。
  - ``POST /audit/purge`` 端点（platform_admin 鉴权）。

但**平台没有内置自动调度**——生产必须由 cron / systemd timer / K8s CronJob 每日调用本脚本，
否则审计表会无限增长（属于已确认的 D 层「审计 180 天留存」的运维闭环缺口）。

用法一 · 函数模式（直接在后端环境运行，最稳）：
  backend/.venv/Scripts/python.exe scripts/purge_audit.py
  backend/.venv/Scripts/python.exe scripts/purge_audit.py --days 180

用法二 · HTTP 模式（远程调用已运行的服务）：
  python scripts/purge_audit.py --http http://127.0.0.1:8080 --token <PLATFORM_ADMIN_TOKEN>

退出码：0=正常（含删除数为 0）；2=异常。
"""
import argparse
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description="审计留存定期清理（闭环 180 天留存）")
    ap.add_argument("--days", type=int, default=None,
                    help="覆盖留存天数（默认读 AUDIT_RETENTION_DAYS=180）")
    ap.add_argument("--http", type=str, default=None,
                    help="改为 HTTP 模式，指定服务基址，如 http://127.0.0.1:8080")
    ap.add_argument("--token", type=str, default=None,
                    help="HTTP 模式的管理员 token（裸 token，不带 Bearer）")
    ap.add_argument("--timeout", type=int, default=30)
    args = ap.parse_args()

    if args.http:
        import json
        import urllib.request
        url = f"{args.http.rstrip('/')}/api/v1/audit/purge"
        body = json.dumps({"retention_days": args.days or 0}).encode()
        # 平台鉴权头是裸 token（不含 Bearer）
        headers = {"Content-Type": "application/json"}
        if args.token:
            headers["Authorization"] = args.token
        req = urllib.request.Request(url, data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=args.timeout) as r:
                data = json.loads(r.read().decode())
            d = data.get("data", {})
            print(f"[purge] http ok: deleted={d.get('deleted')} "
                  f"retention_days={d.get('retention_days')} legal_hold={d.get('legal_hold')}")
            return 0
        except Exception as e:  # noqa: BLE001
            print(f"[purge] http failed: {e}", file=sys.stderr)
            return 2

    # 函数模式：直接在后端进程内调用，复用既有 DB 连接配置
    try:
        from app.services import audit as audit_svc
    except Exception as e:  # noqa: BLE001
        print(f"[purge] import failed（请在 backend 目录用 venv 运行本脚本）: {e}",
              file=sys.stderr)
        return 2
    try:
        deleted = audit_svc.purge_expired(args.days)
        print(f"[purge] function ok: deleted={deleted} "
              f"(retention_days={args.days or audit_svc.settings.AUDIT_RETENTION_DAYS}, "
              f"legal_hold={audit_svc._legal_hold()})")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"[purge] function failed: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
