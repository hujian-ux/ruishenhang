"""接口数据模型：与 openapi.yaml 8 paths / 22 schemas 对齐（契约同源）。

仅定义脚手架阶段所需字段；业务扩展字段后续按契约补充，不破坏现有结构。
"""
from typing import Any, Optional
from pydantic import BaseModel, Field

# ── auth ──
class SsoCallbackRequest(BaseModel):
    provider: str = Field(..., description="wecom|dingtalk|feishu")
    code: str
    device_id: str
    state: Optional[str] = None

class UserOut(BaseModel):
    userId: str
    name: str
    deptId: str
    role: str

class SsoCallbackResponse(BaseModel):
    code: int = 0
    data: dict = Field(default_factory=dict)  # token/expires_in/user/device_bound/need_stepup

# ── rag ──
class RetrieveRequest(BaseModel):
    query: str
    kb_scope: str = "all"  # all|department|personal，仍受 caller 约束
    top_k: int = 6
    tenant_id: str

class RetrieveHit(BaseModel):
    doc_id: str
    title: str
    snippet: str
    scope: str
    kb_tier: str
    score: float
    source: str
    can_access: bool = True

class RetrieveResponse(BaseModel):
    code: int = 0
    data: dict = Field(default_factory=dict)  # query/hits/filtered_out/trace_id

# ── agent invoke ──
class AgentInvokeRequest(BaseModel):
    agent_id: str
    input: str
    stream: bool = True

class Citation(BaseModel):
    doc_id: str
    title: str
    scope: str

class AgentInvokeResponse(BaseModel):
    code: int = 0
    data: dict = Field(default_factory=dict)  # answer/citations/tool_calls

# ── agents (第35章选型落库) ──
class AgentRegisterRequest(BaseModel):
    agent_id: Optional[str] = None
    name: str
    adapter_type: str  # rule|ml_serving|llm|llm_retrieval|ml_llm_retrieval
    description: str
    knowledge_scope: str  # personal|owner|department|global
    model_id: Optional[str] = None
    selection_rationale: Optional[dict] = None

class AgentRegisterResponse(BaseModel):
    code: int = 0
    data: dict = Field(default_factory=dict)

# ── selection (第35章选型服务化) ──
class SelectionRequest(BaseModel):
    input_type: str  # text|image|table|structured
    deterministic: bool
    need: str  # knowledge|creative|accuracy|explain

# ── approval ──
class ApprovalDecisionRequest(BaseModel):
    decision: str  # approve|reject
    comment: Optional[str] = None

class ApprovalDecisionResponse(BaseModel):
    code: int = 0
    data: dict = Field(default_factory=dict)

# ── kb ──
class KbDocumentRequest(BaseModel):
    title: str
    content: str
    scope: str  # personal|department|global
    dept_id: Optional[str] = None

class KbDocumentResponse(BaseModel):
    code: int = 0
    data: dict = Field(default_factory=dict)

# ── models (第25章) ──
class ModelInfo(BaseModel):
    model_id: str
    vendor: str
    adapter: str
    capabilities: list[str] = Field(default_factory=list)
    context_window: Optional[int] = None
    status: str = "active"
    price_per_1k_prompt: float = 0
    price_per_1k_completion: float = 0

class ModelListResponse(BaseModel):
    code: int = 0
    data: dict = Field(default_factory=dict)  # models[]

class ModelRegisterRequest(BaseModel):
    model_id: str
    vendor: str
    adapter: str
    capabilities: list[str]
    context_window: int
    price_per_1k_prompt: float = 0
    price_per_1k_completion: float = 0
    default_quota: Optional[int] = None

class ModelRegisterResponse(BaseModel):
    code: int = 0
    data: dict = Field(default_factory=dict)

# ── usage ──
class UsageRow(BaseModel):
    key: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0
    quota: float = 0
    quota_used_pct: float = 0

class UsageSummary(BaseModel):
    code: int = 0
    data: dict = Field(default_factory=dict)  # dimension/period/rows[]

# ── error ──
class ErrorOut(BaseModel):
    code: int
    message: str
