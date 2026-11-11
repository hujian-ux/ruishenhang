"""M15/M17 P1 · 把方案库里的 166 个 xlsx 交付物样例变成 few-shot。

M15/M17 反复点名的性价比最高的产出一致性手段：每个方案目录都带「原始数据 +
交付物」成对 xlsx，这是**最好的格式样例**。把这对样例压成紧凑文本，注入对应技能
的 system_prompt，模型输出的表头/列序/粒度就会向真实交付物对齐（M17 财务两处
7B 措辞细节即源于此）。

设计取舍（对齐项目「零外部依赖、opt-in 默认关」）：
  - ``scan_solution_pairs`` 纯文件系统遍历，按文件名关键词把 xlsx 配成
    （原始数据 ↔ 交付物）对；无法判断的不强行配对，避免乱配。
  - ``build_fewshot_block`` 用既有 ``tabular._parse_xlsx`` 读表头 + ≤3 样本行，
    压成紧凑文本；强制剔除 ``## `` 以免下游渲染器误切页。
  - ``attach_fewshot / compose_with_fewshot`` 幂等；``FEWSHOT_ENABLED`` 默认关，
    开启后才在运行时把样例拼进 system_prompt，对未启用路径零行为变更。
  - 真实灌库走 ``scripts/build_fewshot.py`` → ``POST /ops/build-fewshot``（管理员），
    对运行中的 115 包按 solution_dir 匹配注入；与硬规则 backfill 同一自愈范式。
"""
from __future__ import annotations

import csv
import hashlib
import os
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.services import tabular

# 中文关键词（原）：直接命中文件名里的中文字。
_INPUT_KW = ["原始", "输入", "数据", "明细", "台账", "源数据", "样本", "基础",
             "期初", "底稿", "明细表", "原材料", "进销存"]
_OUTPUT_KW = ["交付", "输出", "成果", "报告", "结果", "终稿", "成品", "分析",
              "结算", "预审", "波动", "报表", "汇总", "测算", "对比", "产出",
              "评审", "建议", "方案", "导出"]
# 拼音标记层（M17 P2）：D:/workbuddy_解决方案 的样例文件名是拼音（如
# ``ying-shou-zhang-kuan-zhang-ling-fen-xi-biao``），中文关键词在中文字符串里
# 命中不到，必须用拼音片段识别「输入/交付物」。仅收录**无歧义**的片段，避免误配。
_INPUT_PINYIN = ["yuan-shi-shu-ju", "shu-ju", "ming-xi", "liu-shui", "ji-lu",
                 "yu-e", "di-gao", "tai-zhang", "jin-xiao-cun", "yuan-cai-liao",
                 "ji-chu", "qi-chu", "yang-ben", "ming-dan", "su-cai"]
_OUTPUT_PINYIN = ["jiao-fu-wu", "fen-xi", "yu-shen", "bao-biao", "hui-zong",
                  "ce-suan", "cheng-guo", "jie-guo", "fang-an", "dao-chu",
                  "ping-shen", "jian-yi", "dui-bi", "chan-chu", "bao-gao",
                  "jie-suan", "bo-dong", "zuo-zhan-ka", "zhen-duan", "yu-ce",
                  "jue-ce"]

# few-shot 样例文件扩展名（xlsx/xlsm 为原交付物；csv 为 D 盘方案库另存的表格素材，
# 全量 348 个，覆盖 05–11 七个新行业，是补齐样例覆盖的主来源）。
_SAMPLE_EXTS = (".xlsx", ".xlsm", ".csv")


def _is_input(name: str) -> bool:
    return any(k in name for k in _INPUT_KW + _INPUT_PINYIN)


def _is_output(name: str) -> bool:
    return any(k in name for k in _OUTPUT_KW + _OUTPUT_PINYIN)


def scan_solution_pairs(root: str) -> List[Dict[str, Any]]:
    """遍历方案库，配对（原始数据 xlsx ↔ 交付物 xlsx）。

    返回 ``[{solution_dir, input_path, output_path, input_name, output_name}]``。
    规则（保守，宁可不配也不乱配）：
      - 有可识别的输入/输出各至少一个 → 取各自首个配对；
      - 无输入但能识别唯一交付物且文件数≥2 → 取首个非交付物作输入样例、交付物作输出
        （多文件财务包常见：多份原始数据 + 一份分析表）；
      - 目录恰好 2 个 xlsx 且无法识别 → 按顺序（前=输入，后=输出）配对；
      - 单个 xlsx → 作为 output-only 配对（input 留空）；
      - 多于 2 个且无法识别（多为「全原始数据无交付物」）→ 跳过。
    """
    pairs: List[Dict[str, Any]] = []
    for dirpath, _, files in os.walk(root):
        xs = [f for f in files if f.lower().endswith(_SAMPLE_EXTS)]
        if not xs:
            continue
        rel = os.path.relpath(dirpath, root).replace("\\", "/")
        ins = [f for f in xs if _is_input(f)]
        outs = [f for f in xs if _is_output(f)]
        if ins and outs:
            pairs.append(_mk(rel, dirpath, ins[0], outs[0]))
        elif outs and len(xs) >= 2:
            # 有明确交付物 + 其他文件 → 取首个非交付物作输入样例（多原始数据+单交付物常见）
            rest = [f for f in xs if f not in outs]
            pairs.append(_mk(rel, dirpath, rest[0] if rest else "", outs[0]))
        elif len(xs) == 1:
            # 单文件：多半是交付物样例，作为 output-only 配对（input 留空）
            pairs.append(_mk(rel, dirpath, "", xs[0]))
        # 恰好 2 个且无法区分为输入/交付物 → 不瞎猜（避免两份原始数据被教成交付物模板）；
        # 多于 2 个且无法识别 → 跳过。两类均保守不配对。
    return pairs


def _mk(rel: str, dirpath: str, in_name: str, out_name: str) -> Dict[str, Any]:
    return {
        "solution_dir": rel,
        "input_path": os.path.join(dirpath, in_name) if in_name else None,
        "output_path": os.path.join(dirpath, out_name),
        "input_name": in_name,
        "output_name": out_name,
    }


def _sheet_desc(path: str, max_cols: int = 12, max_rows: int = 3) -> str:
    try:
        if path.lower().endswith(".csv"):
            return _csv_desc(path, max_cols, max_rows)
        raw = open(path, "rb").read()
        sheets = tabular._parse_xlsx(raw)
    except Exception:
        return f"（无法解析：{os.path.basename(path)}）"
    lines: List[str] = []
    for s in sheets[:1]:
        if s.get("empty"):
            lines.append(f"（空表 {s.get('name', '')}）")
            continue
        headers = [str(h) for h in (s.get("headers") or [])[:max_cols]]
        lines.append("表头：" + "｜".join(headers))
        for row in (s.get("sample") or [])[:max_rows]:
            cells = [str(c) for c in row[:max_cols]]
            lines.append("样本：" + "｜".join(cells))
    return "\n".join(lines)


def _csv_desc(path: str, max_cols: int = 12, max_rows: int = 3) -> str:
    """读 CSV 表头 + ≤3 样本行（与 xlsx 同款紧凑格式）。编码按 utf-8-sig→gbk→utf-8 兜底。"""
    rows: List[List[str]] = []
    for enc in ("utf-8-sig", "gbk", "utf-8"):
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                for i, row in enumerate(csv.reader(f)):
                    if i > max_rows:
                        break
                    rows.append(row)
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    else:
        return f"（无法解析：{os.path.basename(path)}）"
    if not rows:
        return f"（空表 {os.path.basename(path)}）"
    headers = [str(h).strip() for h in rows[0][:max_cols]]
    lines = ["表头：" + "｜".join(headers)]
    for row in rows[1:max_rows + 1]:
        cells = [str(c).strip() for c in row[:max_cols]]
        lines.append("样本：" + "｜".join(cells))
    return "\n".join(lines)


def build_fewshot_block(input_path: Optional[str], output_path: str) -> str:
    """把一对 xlsx 压成紧凑 few-shot 文本。input_path 可空（单交付物样例）。

    强制剔除 ``## `` 以免下游渲染器误切页。
    """
    inp = _sheet_desc(input_path) if input_path else "（无原始数据样例）"
    out = _sheet_desc(output_path)
    block = f"【输入样例】\n{inp}\n\n【交付物样例】\n{out}"
    return block.replace("##", "＃＃")


def attach_fewshot(agent: Dict[str, Any], block: str) -> Dict[str, Any]:
    """幂等把 few-shot 块挂到 agent（同步版本号）。"""
    if not block:
        return agent
    agent["fewshot"] = block
    agent["fewshot_version"] = hashlib.md5(block.encode("utf-8")).hexdigest()[:10]
    return agent


def compose_with_fewshot(system_prompt: str, agent: Dict[str, Any]) -> str:
    """运行时把 few-shot 拼进 system_prompt（opt-in，默认关）。"""
    if not settings.FEWSHOT_ENABLED:
        return system_prompt
    fs = agent.get("fewshot")
    if not fs:
        return system_prompt
    return (f"{system_prompt}\n\n"
            f"## 产出物样例（few-shot，仅作格式与结构参考，不代表真实数据）\n{fs}")
