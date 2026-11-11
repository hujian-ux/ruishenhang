"""M55 · 把方案库里的「提示词 / 验收清单 / 领域规范」揉进对应技能。

来源（每个方案目录 ``solution_dir``）：
  - ``完整提示词.md``：给用户看的调用指南，含「关键追问」「验收清单」两节——这是
    金子，skill.md 里没有。抽成 ``agent.acceptance``（验收标准），让模型出完活按
    清单逐项自检；UI 可展示「这条技能的交付标准」。
  - 各包专属 ``*.md``（非 ``skill.md``/``完整提示词.md``/``zhu-ti-shi-ci.md``/
    ``页面内容.md``/``readme.md``）：技能真正依赖的**领域标准 / 边界 / 规范 / 参考**
    （如 ``赛道清单与边界.md``）。登记为 ``agent.reference_specs``（标准模板参考）：
    UI 展示「这份技能要传的标准长这样」，运行时作为**格式范本**注入（明确标注非本次
    用户数据），让模型校验用户上传文件的格式；**不破坏** skill.md 的「缺料必拒」硬规则。

设计取舍（对齐项目「零外部依赖、opt-in 默认关」）：
  - 提取纯文件系统遍历；``zhu-ti-shi-ci.md`` 经核查是「主提示词」的命名错位冗余副本
    （143/143 含『主提示词』标记），直接跳过，不当术语用。
  - 注入受 ``MATERIALS_ENABLED`` 控制，默认关；开启后把 acceptance + reference_specs
    拼进 system_prompt，对未启用路径零行为变更（与 fewshot 同一范式）。
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Dict, List, Optional

from app.core.config import settings

# 元信息 md（不算领域规范，跳过）：skill 已导入；完整提示词/主题词是用户侧调用指南
# （含冗余副本）；页面内容是营销稿；readme 是说明。
_META_MD = {"skill.md", "完整提示词.md", "zhu-ti-shi-ci.md", "页面内容.md", "readme.md"}

# 单份范本注入字数上限、最多注入文件数（控制 prompt 预算，避免超大规范撑爆上下文）。
_MAX_SPEC_CHARS = 2500
_MAX_SPEC_FILES = 6


def _read(path: str) -> str:
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            with open(path, encoding=enc) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    return ""


def _find_file(solution_dir: str, basename: str) -> Optional[str]:
    cand = os.path.join(solution_dir, basename)
    if os.path.isfile(cand):
        return cand
    for dp, _, fs in os.walk(solution_dir):
        if basename in fs:
            return os.path.join(dp, basename)
    return None


def extract_acceptance(solution_dir: str) -> Optional[str]:
    """从 ``完整提示词.md`` 抽「关键追问 + 验收清单」两节，压成验收标准块。

    返回 ``None`` 表示无可用内容（该包完整提示词未含这两节）。
    """
    path = _find_file(solution_dir, "完整提示词.md")
    if not path:
        return None
    text = _read(path)
    if not text.strip():
        return None
    # 按 `## 标题` 切分，抓取标题含「关键追问」「验收」「追问」的节。
    sections = re.split(r"(?m)^##\s+", text)
    picked: List[str] = []
    for sec in sections:
        head = sec.splitlines()[0] if sec.splitlines() else ""
        if ("验收" in head) or ("追问" in head):
            picked.append("## " + sec.strip())
    if not picked:
        return None
    block = "\n\n".join(picked).strip()
    if len(block) < 20:
        return None
    # 强制剔除 ``## `` 以免下游渲染器误切页（与 fewshot 一致）。
    return block.replace("##", "＃＃")


def collect_reference_specs(solution_dir: str) -> List[Dict[str, Any]]:
    """收集方案目录里的领域标准 / 规范 md（排除元信息文件），作为标准模板参考。

    返回 ``[{name, content, chars}]``，content 已截断到 ``_MAX_SPEC_CHARS``。
    """
    specs: List[Dict[str, Any]] = []
    for dp, _, fs in os.walk(solution_dir):
        for f in sorted(fs):
            if not f.lower().endswith(".md"):
                continue
            if f in _META_MD:
                continue
            full = os.path.join(dp, f)
            text = _read(full).strip()
            if not text:
                continue
            if len(specs) >= _MAX_SPEC_FILES:
                break
            chars = len(text)
            content = text[:_MAX_SPEC_CHARS]
            if chars > _MAX_SPEC_CHARS:
                content += f"\n…（已截断，原文 {chars} 字）"
            specs.append({"name": f, "content": content, "chars": chars})
    return specs


def attach_materials(agent: Dict[str, Any], acceptance: Optional[str],
                     reference_specs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """幂等把验收标准 + 标准模板参考挂到 agent（同步版本号）。"""
    if acceptance:
        agent["acceptance"] = acceptance
        agent["acceptance_version"] = hashlib.md5(acceptance.encode("utf-8")).hexdigest()[:10]
    else:
        agent.pop("acceptance", None)
        agent.pop("acceptance_version", None)
    if reference_specs:
        agent["reference_specs"] = reference_specs
        agent["reference_specs_version"] = hashlib.md5(
            "|".join(s["name"] for s in reference_specs).encode("utf-8")).hexdigest()[:10]
    else:
        agent.pop("reference_specs", None)
        agent.pop("reference_specs_version", None)
    return agent


def compose_materials(system_prompt: str, agent: Dict[str, Any]) -> str:
    """运行时把验收标准 + 标准模板参考拼进 system_prompt（opt-in，默认关）。"""
    if not settings.MATERIALS_ENABLED:
        return system_prompt
    parts = [system_prompt]
    acc = agent.get("acceptance")
    if acc:
        parts.append("## 交付验收自检（出活后按以下清单逐项核对，未达标项在开头明确说明）\n" + acc)
    specs = agent.get("reference_specs") or []
    if specs:
        refs = "\n\n".join(f"### 标准范本：{s['name']}\n{s['content']}" for s in specs)
        parts.append(
            "## 标准模板参考（以下为格式范本，**非本次用户数据**；用户未提供实际对应文件时，"
            "须按技能硬规则拒绝或明确标注假设口径，不得当作真实数据使用）\n" + refs)
    return "\n\n".join(parts) if len(parts) > 1 else system_prompt
