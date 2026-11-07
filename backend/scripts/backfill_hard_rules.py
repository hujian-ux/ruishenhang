"""M15/M25 P1 · 存量 115 业务包补齐平台硬规则（一键自愈）。

新导入的业务包已由 import_solutions.build_system_prompt 自动带上硬规则；
本脚本负责「历史存量」：调用运行中的后端 ``POST /ops/backfill-hard-rules``
（仅平台管理员可用），对未含平台硬规则头的业务包追加并刷新版本。幂等、可重跑。

用法（先起后端）：
    .venv/Scripts/python.exe scripts/backfill_hard_rules.py
    .venv/Scripts/python.exe scripts/backfill_hard_rules.py --base http://127.0.0.1:8080
"""
import argparse

import httpx


def _login(client: httpx.Client, base: str, code: str) -> str:
    r = client.post(f"{base}/api/v1/auth/sso/callback",
                    json={"provider": "wecom", "code": code,
                          "device_id": "dev", "state": "x"})
    r.raise_for_status()
    return r.json()["data"]["token"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8080")
    ap.add_argument("--code", default="dev_admin")
    a = ap.parse_args()
    with httpx.Client(timeout=120) as c:
        token = _login(c, a.base, a.code)
        H = {"Authorization": token}
        r = c.post(f"{a.base}/api/v1/ops/backfill-hard-rules", headers=H)
        r.raise_for_status()
        d = r.json()
        if d.get("code") != 0:
            print(f"失败：{d.get('msg')}（需平台管理员）")
            return 1
        data = d["data"]
        print(f"业务包总数 {data['total']}｜已含硬规则 {data['already']}｜本次补齐 {data['patched']}")
        print(f"硬规则版本：{data['hard_rules_version']}")
        for cat, slot in sorted(data["by_category"].items()):
            print(f"  - {cat}: 共 {slot['total']} 已含 {slot['already']} 补齐 {slot['patched']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
