"""P0-2 / P0-3 工具运行时包。

导入本包即触发 builtin 工具注册（registry）。sandbox 与 loop 为纯逻辑，
由 agent_runtime 在需要时调用。P1-9 追加 experts（delegate_expert 委派工具）。
"""
from app.tools import registry, sandbox, loop, builtin, experts  # noqa: F401  (builtin/experts 触发注册)

__all__ = ["registry", "sandbox", "loop", "builtin", "experts"]
