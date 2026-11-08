"""接口数据模型：与 openapi.yaml 8 paths / 22 schemas 对齐（契约同源）。

仅定义脚手架阶段所需字段；业务扩展字段后续按契约补充，不破坏现有结构。
"""
from typing import Any, List, Optional
from pydantic import BaseModel, Field

# ── auth ──
class SsoCallbackRequest(BaseModel):
    provider: str = Field(..., description="wecom|dingtalk|feishu")
    code: str
    device_id: str
    state: Optional[str] = None
    # P1-173 收口：前端拿到 IdP 的 id_token 后直接带上，由后端验签换身份（不落 dev token）。
    # 收口开启时，dev_admin 捷径被拒，必须携带有效的 id_token。
    id_token: Optional[str] = None

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
    # M25：同一篇文档的前 N 句证据，供「制度查证」类技能并列比对条款；
    # 缺省空列表，老调用方不受影响。
    snippets: List[str] = Field(default_factory=list)

class RetrieveResponse(BaseModel):
    code: int = 0
    data: dict = Field(default_factory=dict)  # query/hits/filtered_out/trace_id

# ── agent invoke ──
class WebSearchRequest(BaseModel):
    """M26 公网检索：只允许传 query 与条数，不接受自定义 URL（SSRF 面收死）。"""
    query: str
    top_k: int = Field(6, ge=1, le=10)
    fetch_top: int = Field(0, ge=0, le=3,
                           description="抓前 N 条结果的正文，仅用于竞品调研这类需要看原文的活")


class AgentInvokeRequest(BaseModel):
    agent_id: str
    input: str
    stream: bool = True
    session_id: Optional[str] = None  # M4 会话记忆：传入则续聊，缺省新建会话
    # M16 文件进出：先 POST /agent/files 上传拿 file_id，再在这里引用；
    # 产出物由后端生成并通过 data.artifacts 返回下载地址。
    file_ids: List[str] = Field(default_factory=list)
    # M17 产出格式：html|xlsx|docx|pptx|pdf|auto，可多选（同一份答案渲多种格式，
    # 不重复调模型）。不传 = html（平台优先 HTML，对方要其它格式再显式传）。
    output_formats: List[str] = Field(default_factory=list,
                                      description="html|xlsx|docx|pptx|pdf|auto，可多选")

class Citation(BaseModel):
    doc_id: str
    title: str
    scope: str

class AgentInvokeResponse(BaseModel):
    code: int = 0
    data: dict = Field(default_factory=dict)  # answer/citations/tool_calls

# ── agents (第35章选型落库 + M15 技能包融合) ──
class AgentRegisterRequest(BaseModel):
    agent_id: Optional[str] = None
    name: str
    adapter_type: str  # rule|ml_serving|llm|llm_retrieval|ml_llm_retrieval
    description: str
    knowledge_scope: str  # personal|owner|department|global
    model_id: Optional[str] = None
    selection_rationale: Optional[dict] = None
    # M15：技能包（prompt-as-skill）承载字段。缺省时行为与旧版完全一致。
    system_prompt: Optional[str] = None      # 技能包执行提示词（无则退回平台默认助手）
    category: Optional[str] = None           # industry | office
    category_cn: Optional[str] = None        # 制造业 / 零售 / 办公角色 …
    sub_function: Optional[str] = None       # HR / 行政 / 财务 / 销售（办公角色才有）
    tags: Optional[list[str]] = None
    source_path: Optional[str] = None        # 技能包来源路径，便于版本回流与重导入
    inputs_contract: Optional[str] = None
    outputs_contract: Optional[str] = None


class AgentImportItem(BaseModel):
    """批量导入的单个技能包（导入器解析后直接投喂，无需再走选型向导）。"""
    name: str
    description: str
    system_prompt: str
    adapter_type: str = "llm"
    knowledge_scope: str = "department"
    category: Optional[str] = None
    category_cn: Optional[str] = None
    sub_function: Optional[str] = None
    tags: Optional[list[str]] = None
    source_path: Optional[str] = None
    inputs_contract: Optional[str] = None
    outputs_contract: Optional[str] = None
    model_id: Optional[str] = None


class AgentImportRequest(BaseModel):
    items: list[AgentImportItem]
    batch_name: Optional[str] = None
    # 115 个包逐个开单会把审批台压垮：默认合并为一张批次审批单，一次决策整批发布
    batch_approval: bool = True


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

# ── kb 异步导入（M10）──
class KbIngestDocument(BaseModel):
    content: str
    title: Optional[str] = None
    filename: Optional[str] = None

class KbIngestRequest(BaseModel):
    documents: list[KbIngestDocument]
    scope: str = "department"
    dept_id: Optional[str] = None
    source: str = "async_ingest"

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
