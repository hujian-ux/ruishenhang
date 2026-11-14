"""提示词模板外置加载器（P1-4 / #299）。

把原 ``zh_prompts`` 内嵌的 7 个中文任务模板迁到 ``app/prompts/*.tpl``（Jinja2），
并支持「模式 × 场景」矩阵：``default/`` 为全量基础场景，``strict/`` 为模式覆盖层
（稀疏覆盖，命名 ``zh_<场景>.tpl``，未覆盖场景回退到 default）。

设计要点：
- 运营侧修改 ``.tpl`` 后无需发版（``auto_reload=True``，按 mtime 热加载）。
- ``StrictUndefined``：缺变量抛 ``UndefinedError``，统一转 ``KeyError``，兼容旧契约
  （``render`` 未知场景 / 缺变量必须抛 ``KeyError``）。
- ``compose()``：按固定组装序拼接完整提示词（主提示→系统提醒→模式提醒→身份与记忆→历史），
  顺序写死在 ``COMPOSE_ORDER``，避免运行时漂移；可经 ``_compose.tpl`` 外置检视。
"""
from __future__ import annotations

from pathlib import Path

import jinja2

_PROMPTS_ROOT = Path(__file__).resolve().parent
_DEFAULT_DIR = _PROMPTS_ROOT / "default"
_STRICT_DIR = _PROMPTS_ROOT / "strict"

# 基础场景（default 全量覆盖，运营不应新增/删除，仅改内容）
DEFAULT_SCENES = ("summarize", "extract", "classify", "qa", "rewrite", "compliance", "table")

# 固定组装序（compose）
COMPOSE_ORDER = ("main", "system_reminder", "mode_reminder", "identity_memory", "history")
_COMPOSE_TITLES = {
    "main": "主提示",
    "system_reminder": "系统提醒",
    "mode_reminder": "模式提醒",
    "identity_memory": "身份与记忆",
    "history": "历史",
}

# 文件系统加载器：searchpath 为 prompts 根，模板按子目录前缀引用
# （default/xxx.tpl、strict/zh_xxx.tpl、根级 _*.tpl 共享片段）。
# strict 覆盖层与 default 同处一个 searchpath，include 片段在根级统一复用。
_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(_PROMPTS_ROOT)),
    undefined=jinja2.StrictUndefined,
    auto_reload=True,
    keep_trailing_newline=True,
)


def available_modes() -> list[str]:
    """当前支持的模式：默认（default）/ 严格（strict）。"""
    return ["default", "strict"]


def available_scenes() -> list[str]:
    """全部可用场景（即 default 全量 7 个）。"""
    return sorted(DEFAULT_SCENES)


def _resolve_path(scene: str, mode: str) -> str:
    """把 (场景, 模式) 解析成模板路径；未知场景/模式抛 KeyError。"""
    if scene not in DEFAULT_SCENES:
        raise KeyError(
            f"未知中文提示词场景：{scene}（可选：{', '.join(DEFAULT_SCENES)}）"
        )
    if mode == "strict":
        strict_name = f"zh_{scene}.tpl"
        if (_STRICT_DIR / strict_name).exists():
            return f"strict/{strict_name}"
        return f"default/{scene}.tpl"  # 未覆盖 → 回退 default
    if mode == "default":
        return f"default/{scene}.tpl"
    raise KeyError(f"未知模式：{mode}（可选：default, strict）")


def render(scene: str, mode: str = "default", **vars) -> str:
    """渲染场景模板。未知场景/模式/缺变量均抛 KeyError（兼容旧契约）。"""
    path = _resolve_path(scene, mode)
    try:
        tpl = _env.get_template(path)
        return tpl.render(**vars)
    except jinja2.UndefinedError as exc:  # 缺变量 → 转 KeyError，兼容旧契约
        raise KeyError(f"提示词模板缺变量：{exc}") from exc


def template_source(scene: str, mode: str = "default") -> str:
    """返回模板原始源码（供运营预览 / 编辑）。"""
    path = _resolve_path(scene, mode)
    source, _, _ = _env.loader.get_source(_env, path)
    return source


def matrix() -> dict:
    """返回模式 × 场景矩阵结构。"""
    strict_scenes = sorted(
        p.stem[3:] for p in _STRICT_DIR.glob("zh_*.tpl")
        if p.stem.startswith("zh_") and p.stem[3:] in DEFAULT_SCENES
    )
    return {
        "modes": available_modes(),
        "scenes": available_scenes(),
        "matrix": {
            "default": available_scenes(),
            "strict": strict_scenes,  # 仅列出被覆盖的场景
        },
    }


def compose(*, main: str = "", system_reminder: str = "",
            mode_reminder: str = "", identity_memory: str = "",
            history: str = "") -> str:
    """按固定组装序拼接完整提示词：主提示→系统提醒→模式提醒→身份与记忆→历史。"""
    tpl = _env.get_template("_compose.tpl")
    return tpl.render(
        main=main,
        system_reminder=system_reminder,
        mode_reminder=mode_reminder,
        identity_memory=identity_memory,
        history=history,
    )
