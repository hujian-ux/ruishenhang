"""提示词模板外置包（P1-4 / #299）。

运营侧可直接编辑 ``app/prompts/*.tpl`` 调整提示词，无需发版（auto_reload）。
from app.prompts import render, available_scenes, matrix, compose, template_source
"""
from app.prompts.loader import (
    COMPOSE_ORDER,
    DEFAULT_SCENES,
    available_modes,
    available_scenes,
    compose,
    matrix,
    render,
    template_source,
)

__all__ = [
    "COMPOSE_ORDER",
    "DEFAULT_SCENES",
    "available_modes",
    "available_scenes",
    "compose",
    "matrix",
    "render",
    "template_source",
]
