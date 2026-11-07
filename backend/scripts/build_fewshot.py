"""M15/M17 P1 · 把方案库 166 个 xlsx 交付物灌成 few-shot（一键自愈）。

遍历 D:/workbuddy_解决方案，按 solution_dir 匹配运行中的 115 业务包，把
（原始数据 ↔ 交付物）成对 xlsx 压成紧凑样例块挂到 agent。FEWSHOT_ENABLED=1 时
运行时自动拼进对应技能提示词。幂等、可重跑。

用法（先起后端）：
    .venv/Scripts/python.exe scripts/build_fewshot.py
    .venv/Scripts/python.exe scripts/build_fewshot.py --base http://127.0.0.1:8080
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
    with httpx.Client(timeout=300) as c:
        token = _login(c, a.base, a.code)
        H = {"Authorization": token}
        r = c.post(f"{a.base}/api/v1/ops/build-fewshot", headers=H)
        r.raise_for_status()
        d = r.json()
        if d.get("code") != 0:
            print(f"失败：{d.get('msg')}（需平台管理员）")
            return 1
        data = d["data"]
        print(f"扫描到 xlsx 配对 {data['pairs_found']} 对｜成功注入 few-shot 的技能 {data['attached']} 个")
        print("提示：运行时需 FEWSHOT_ENABLED=1 才把样例拼进提示词。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
