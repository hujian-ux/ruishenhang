"""识别一串模型 API key 属于哪家厂商，并判断它现在能不能真跑。

背景：接入期最常见的浪费是「key 拿错厂商」。智谱 key 形如 `id.secret`，
DeepSeek/Moonshot/硅基流动都是 `sk-` 开头，肉眼几乎分不出来；而各家的 401 原文
都是英文，只会告诉你"认证失败"，不会告诉你"你打错门了"。

用法：
    .venv/Scripts/python.exe scripts/check_key.py --key sk-xxx
    .venv/Scripts/python.exe scripts/check_key.py --key sk-xxx --json

判定口径（关键在区分「不是这家」和「是这家但没钱」）：
    200                → 这家可用，直接能跑
    402 / 30001 余额不足 → **是这家的**，key 有效，账户没钱（认证已通过才会报余额）
    403 模型未开通/下线   → **是这家的**，key 有效，只是探测用的模型不可用
    401 认证失败        → 不是这家
    其它               → 按原文归类，不猜

退出码：0 = 找到可用厂商；1 = 找到了厂商但不可用（缺钱/缺权限）；2 = 全不匹配。
"""
import argparse
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.model_gateway import PROVIDER_PRESETS  # noqa: E402

# 每家的"最便宜探测模型"：只要能出字就够，不追求效果。
# 单独放这里而不是塞进 PROVIDER_PRESETS —— 产品代码不该知道验收用哪个模型。
PROBE_MODELS = {
    "zhipu": "glm-4-flash",
    "dashscope": "qwen-turbo",
    "openai": "gpt-4o-mini",
    "deepseek": "deepseek-chat",
    "moonshot": "moonshot-v1-8k",
    "siliconflow": "Qwen/Qwen2.5-7B-Instruct",
}

# 认证失败的典型原文。「认证没过」意味着这串 key 不属于该厂商。
AUTH_FAIL_MARKS = (
    "invalid_api_key", "invalid authentication", "incorrect api key",
    "authentication fails", "invalid token", "令牌已过期或验证不正确",
    "unauthorized", "login fail",
)


def _classify(status: int, body: str) -> tuple[str, str]:
    """→ (归属判定, 人话结论)。归属判定：yes / no / unknown。"""
    low = body.lower()
    if status == 200:
        return "yes", "可用，直接能跑"
    if status == 402 or '"30001"' in body or "balance is insufficient" in low:
        return "yes", "key 有效，但账户余额不足（去充值即可）"
    if status == 403 and ("model disabled" in low or "no permission" in low
                          or '"30003"' in body):
        return "yes", "key 有效，但探测模型已下线/未开通（换模型即可）"
    if status in (401, 403) or any(m in low for m in AUTH_FAIL_MARKS):
        return "no", "不是这家（认证失败）"
    if status == 400 and "model does not exist" in low:
        # 认证已过，只是模型名不在清单里
        return "yes", "key 有效，但模型名不存在"
    if status == 429:
        return "yes", "key 有效，但触发限流（稍后重试）"
    if status >= 500:
        return "unknown", "厂商服务异常，无法判定"
    return "unknown", f"未知响应 HTTP {status}"


def scan(key: str, timeout: float = 25.0) -> list[dict]:
    rows = []
    for vendor, preset in PROVIDER_PRESETS.items():
        model = PROBE_MODELS.get(vendor)
        if not model:
            continue
        base = preset["base_url"].rstrip("/")
        row = {"vendor": vendor, "label": preset["label"], "model": model,
               "base_url": base, "status": 0, "owns": "unknown", "verdict": "", "reply": ""}
        try:
            r = httpx.post(f"{base}/chat/completions",
                           headers={"Authorization": f"Bearer {key}",
                                    "Content-Type": "application/json"},
                           json={"model": model, "max_tokens": 12,
                                 "messages": [{"role": "user", "content": "只回复：OK"}]},
                           timeout=timeout)
            row["status"] = r.status_code
            body = r.text
            row["owns"], row["verdict"] = _classify(r.status_code, body)
            if r.status_code == 200:
                try:
                    row["reply"] = (r.json()["choices"][0]["message"]["content"] or "")[:20]
                except Exception:
                    row["reply"] = ""
        except Exception as exc:
            row["verdict"] = f"网络不可达：{type(exc).__name__}"
        rows.append(row)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="识别 key 归属厂商并判断可用性")
    ap.add_argument("--key", required=True)
    ap.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    a = ap.parse_args()

    key = a.key.strip()
    rows = scan(key)

    if a.json:
        print(json.dumps({"key_len": len(key), "results": rows}, ensure_ascii=False, indent=2))
    else:
        print(f"key 长度 {len(key)}，前缀 {key[:7]}…，后缀 …{key[-4:]}")
        print("-" * 78)
        for r in rows:
            mark = {"yes": "✓", "no": "·", "unknown": "?"}[r["owns"]]
            print(f"{mark} {r['label']:<16} {r['status'] or 'ERR':>4}  {r['verdict']}"
                  f"{('  → ' + r['reply']) if r['reply'] else ''}")
        print("-" * 78)

    hits = [r for r in rows if r["owns"] == "yes"]
    usable = [r for r in hits if r["status"] in (200, 429)]
    if usable:
        r = usable[0]
        print(f"结论：这串 key 属于【{r['label']}】，现在可用。")
        print(f"接入参数： LLM_VENDOR={r['vendor']}   LLM_DEFAULT_MODEL={r['model']}")
        return 0
    if hits:
        r = hits[0]
        print(f"结论：这串 key 属于【{r['label']}】，但当前跑不了 —— {r['verdict']}")
        return 1
    print("结论：没有任何厂商接受这串 key（全部认证失败）。"
          "请确认复制完整、未被重置，且不是别的服务（如对象存储/GitHub）的 token。")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
