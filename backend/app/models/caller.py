"""调用者上下文模型（X-Caller-Context 的数据结构）。"""
from pydantic import BaseModel


class CallerContext(BaseModel):
    userId: str
    deptId: str
    role: str  # employee | dept_admin | platform_admin
    tenantId: str
