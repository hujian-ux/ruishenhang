// 轻量 API 客户端：对接后端单体 /api/v1，自动携带 dev token（由 /auth/sso/callback 签发）
import { getSession } from "../auth/session";

// 基座 URL：
// - Web 开发/自托管：留空 → 走相对路径 "/api/v1"（同源，经 Vite/Caddy 反代）。
// - 原生包（Capacitor iOS/安卓）：编译时注入 VITE_API_BASE 指向部署后的后端，
//   例如 https://ai.your-domain.com/api/v1（必须含 /api/v1 后缀）。
const BASE: string = import.meta.env.VITE_API_BASE || "/api/v1";

let token = "";

export function setToken(t: string) {
  token = t;
}

const UNAUTHORIZED_EVENT = "wb-unauthorized";

// 登录态不能只存内存：token 是模块级变量，Vite 热更新（或任何模块重载）会把它
// 清空，而 main.tsx 不会跟着重跑。表现就是改完代码后每个请求都不带
// Authorization，界面上一片 "unauthorized: missing caller context"。
// 所以每次请求都从 localStorage 兜一次。
function authToken(): string {
  if (token) return token;
  const t = getSession()?.idToken || "";
  if (t) token = t;
  return t;
}

function handle(res: Response, path: string) {
  if (res.status === 401 && !path.startsWith("/auth/")) {
    // 会话过期：通知上层清掉登录态回登录页，别让用户对着一堆"加载失败"猜原因
    window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT));
    throw new Error("登录已过期，请重新登录");
  }
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const t = authToken();
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(t ? { Authorization: t } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  handle(res, path);
  if (!res.ok) {
    let detail = `${res.status}`;
    try { detail = (await res.json())?.detail || detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

// 文件上传走 multipart，不能带 Content-Type（浏览器自动补 boundary）
async function uploadFile(path: string, form: FormData): Promise<any> {
  const t = authToken();
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: t ? { Authorization: t } : {},
    body: form,
  });
  handle(res, path);
  if (!res.ok) {
    let detail = `${res.status}`;
    try { detail = (await res.json())?.detail || detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  // 开发模式（dev token）：本地/演示用；生产走下方 OIDC
  login: (code = "dev") => req<{ code: number; data: { token: string; user: any } }>("POST", "/auth/sso/callback", {
    provider: "wecom", code, device_id: "dev", state: "x",
  }),

  // ── M12 OIDC：后端代换码（避开浏览器 CORS）+ 可选一键写入 Casdoor ──
  oidcExchange: (body: {
    issuer: string; client_id: string; redirect_uri: string; code: string; code_verifier: string;
  }) => req<{ code: number; data: { id_token: string; expires_in: number; user: any } }>(
    "POST", "/auth/oidc/exchange", body,
  ),
  oidcProvision: (body: {
    issuer: string; admin_user: string; admin_password: string;
    owner?: string; app_name?: string;
    wecom?: { corp_id: string; secret: string; agent_id: string };
    dingtalk?: { corp_id: string; secret: string; agent_id: string };
  }) => req<{ code: number; msg: string; steps?: string[]; providers?: any[]; detail?: any }>(
    "POST", "/auth/oidc/provision", body,
  ),
  listModels: () => req<{ code: number; data: { models: any[] } }>("GET", "/models"),
  retrieve: (q: string) => req<any>("POST", "/rag/retrieve", {
    query: q, kb_scope: "all", top_k: 6, tenant_id: "t_demo",
  }),
  registerAgent: (payload: any) => req<any>("POST", "/agents", payload),
  // 第35章选型服务（后端 selection/framework.py 服务化；联调真源）
  recommend: (inputType: string, deterministic: boolean, need: string) =>
    req<any>("POST", "/selection/recommend", {
      input_type: inputType, deterministic, need,
    }),
  // role 传了就只回本岗 + 公共库 + 通用助手（岗位边界的服务端保障）
  listAgents: (role?: string) =>
    req<{ code: number; data: { agents: any[] } }>(
      "GET", "/agents" + (role ? `?role=${encodeURIComponent(role)}` : "")),
  // ── M15 技能包融合：分类目录 + 批量导入 ──
  getAgentCatalog: (role?: string) => req<{
    code: number;
    data: {
      categories: any[]; roles: any[]; public: any[]; public_groups: any[];
      total: number; role_total: number; public_total: number;
    };
  }>("GET", "/agents/catalog" + (role ? `?role=${encodeURIComponent(role)}` : "")),
  // M19 职业工作台：不传 role 返回职业目录；传了返回该岗位的 core + 全部
  getRoles: () => req<{ code: number; data: { roles: any[]; total: number; public: any[] } }>(
    "GET", "/agents/roles"),
  getRoleWorkspace: (role: string) =>
    req<{ code: number; data: { role: any; count: number; core: any[]; rest: any[]; public: any[] } }>(
      "GET", `/agents/roles?role=${encodeURIComponent(role)}`),
  importAgents: (body: { items: any[]; batch_name?: string; batch_approval?: boolean }) =>
    req<any>("POST", "/agents/import", body),
  // M4 会话记忆：session_id 传入续聊，缺省新建
  // M16 文件进出：fileIds 为已上传附件的 file_id（先调 uploadAgentFile）
  // M17 产出格式：outputFormats 可多选（html/xlsx/docx/pptx/pdf），不传=html（平台优先 HTML）
  invokeAgent: (agentId: string, input: string, sessionId?: string,
                fileIds?: string[], outputFormats?: string[], useSamples?: boolean) =>
    req<any>("POST", "/agent/invoke", {
      agent_id: agentId, input, stream: false,
      ...(sessionId ? { session_id: sessionId } : {}),
      ...(fileIds && fileIds.length ? { file_ids: fileIds } : {}),
      ...(outputFormats && outputFormats.length ? { output_formats: outputFormats } : {}),
      // M52：勾上就由服务端去技能包目录取原始输入样例当附件
      ...(useSamples ? { use_samples: true } : {}),
    }),

  // ── M52 技能包的输入清单与自带样例 ──
  // checklist 是包作者写在 skill.md 里的「要给什么」；samples 是包目录里的文件，
  // input 类可一键挂载试跑（can_run_with_samples）。
  getSkillInputs: (agentId: string) =>
    req<{ code: number; data: {
      agent_id: string; name: string; checklist: string[]; source_path: string;
      pack_found: boolean; input_sample_count: number; can_run_with_samples: boolean;
      hint: string;
      samples: { filename: string; display_name: string; kind: "input" | "output" | "meta";
                 size: number; ext: string; from_readme: boolean }[];
    } }>("GET", `/agents/${encodeURIComponent(agentId)}/inputs`),

  // ── M16 文件进出（P0）：xlsx 上传 → 按技能包执行 → 产出物下载 ──
  uploadAgentFile: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return uploadFile("/agent/files", form) as Promise<{
      code: number; data: {
        file_id: string; filename: string; size: number; kind: string;
        meta: { parse_ok: boolean; parse_error?: string; sheets?: number; rows?: number; store?: string };
        preview: { ok: boolean; sheet_count?: number; total_rows?: number;
                   sheets?: { name: string; rows: number; cols: number; headers: string[] }[] };
        download_url: string; object_store_available: boolean;
      };
    }>;
  },
  listAgentFiles: (kind = "", runId = "") =>
    req<{ code: number; data: { files: any[]; count: number; stats: any } }>(
      "GET", `/agent/files?kind=${kind}${runId ? `&run_id=${runId}` : ""}`,
    ),
  deleteAgentFile: (fileId: string) => req<any>("DELETE", `/agent/files/${fileId}`),

  getSession: (sessionId: string) =>
    req<any>("GET", `/agent/sessions/${sessionId}`),
  // M5/M46 审批流
  pendingApprovals: (onlyMyStage = false) =>
    req<{ code: number; data: { count: number; tickets: any[] } }>(
      "GET", `/approval/pending${onlyMyStage ? "?only_my_stage=true" : ""}`),
  decideApproval: (approvalId: string, decision: "approve" | "reject", comment?: string) =>
    req<any>("POST", `/approval/${approvalId}/decide`, { decision, comment }),
  // M46 审批助手（辅助层，只建议不落账）；enrich=true 走真模型通道增强一句话提示
  assistApproval: (approvalId: string, enrich = false) =>
    req<any>("POST", `/approval/${approvalId}/assist${enrich ? "?enrich=true" : ""}`),

  // ── M7 计量（第25章）：真实落账 + 多维聚合 + 配额 ──
  getUsage: (dimension: string, days = 30) =>
    req<any>("GET", `/usage/summary?dimension=${dimension}&days=${days}`),
  getUsageRecords: (limit = 50) => req<any>("GET", `/usage/records?limit=${limit}`),
  getQuota: () => req<any>("GET", "/usage/quota"),

  // ── M7 知识库：上传 / 列表 / 读回 / 删除 ──
  listKbDocuments: () => req<{ code: number; data: { documents: any[]; count: number; object_store_available: boolean } }>("GET", "/kb/documents"),
  uploadKbDocument: (file: File, title: string, scope: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("title", title);
    form.append("scope", scope);
    return uploadFile("/kb/documents/upload", form);
  },
  getKbContent: (docId: string) => req<any>("GET", `/kb/documents/${docId}/content`),
  deleteKbDocument: (docId: string) => req<any>("DELETE", `/kb/documents/${docId}`),

  // ── M8 试点：批量上传（服务端去重 + 只重建一次索引）/ 语料画像 ──
  bulkUploadKbDocuments: (files: File[], scope: string) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    form.append("scope", scope);
    return uploadFile("/kb/documents/bulk", form);
  },
  getKbStats: () => req<{ code: number; data: any }>("GET", "/kb/stats"),
  getKbIndex: () => req<{ code: number; data: any }>("GET", "/kb/index"),

  // ── M10 异步导入（万篇级：提交即返回，轮询进度） ──
  createKbIngest: (body: { documents: { title?: string; content: string; filename?: string }[]; scope: string }) =>
    req<{ code: number; data: any }>("POST", "/kb/ingest", body),
  getKbIngest: (jobId: string) => req<{ code: number; data: any }>("GET", `/kb/ingest/${jobId}`),
  listKbIngest: (limit = 10) => req<{ code: number; data: { jobs: any[]; count: number } }>("GET", `/kb/ingest?limit=${limit}`),
  cancelKbIngest: (jobId: string) => req<{ code: number; data: any }>("POST", `/kb/ingest/${jobId}/cancel`),

  // ── M8 试点：成本曲线 + 基线评测 ──
  getUsageTrend: (days = 14) => req<{ code: number; data: any }>("GET", `/usage/trend?days=${days}`),
  getEvalLatest: () => req<{ code: number; data: any; source?: string }>("GET", "/eval/latest"),
  getEvalHistory: (limit = 14) => req<{ code: number; data: { rows: any[]; count: number } }>("GET", `/eval/history?limit=${limit}`),
  runEval: (limit = 0) => req<any>("POST", `/eval/run${limit ? `?limit=${limit}` : ""}`),

  // ── M7 会话管理 ──
  listSessions: (limit = 20) => req<{ code: number; data: { sessions: any[]; count: number } }>("GET", `/agent/sessions?limit=${limit}`),
  deleteSession: (sessionId: string) => req<any>("DELETE", `/agent/sessions/${sessionId}`),

  // ── M7 模型管理 ──
  listModelsAll: (status = "all") => req<{ code: number; data: { models: any[]; gateway: any } }>("GET", `/models?status=${status}`),
  gatewayHealth: () => req<any>("GET", "/models/gateway"),
  toggleModel: (modelId: string, status: "active" | "inactive") =>
    req<any>("PATCH", `/models/${modelId}`, { status }),

  // ── M49 模型在线配置（改完即时生效 + 落盘 .env.local） ──
  getModelConfig: () => req<any>("GET", "/models/config"),
  saveModelConfig: (body: {
    provider: "direct" | "litellm"; vendor?: string; base_url?: string;
    api_key?: string; model: string;
    chain?: string[];
    routing?: { enabled?: boolean; small?: string; large?: string };
    temperature?: number; max_tokens?: number;
  }) => req<any>("PUT", "/models/config", body),
  // 真生成探活：花一次极短调用换确定结论（401/402/404 会原样回显）
  probeModel: (modelId = "") =>
    req<any>("GET", `/models/probe${modelId ? `?model_id=${encodeURIComponent(modelId)}` : ""}`),
  // 池子体检：链上每个模型都打一次（免费额度按模型算，要看还剩几个能顶）
  probeModelChain: () => req<any>("GET", "/models/probe?all=1"),
  // M51：清空轮换链冷却（额度按天重置，运维想立刻重试时不用干等）
  resetModelChain: () => req<any>("POST", "/models/chain/reset"),

  // ── P1-173 身份与集成：OIDC 状态 + IM 运行时接入 ──
  // 注意：/auth/oidc/health 返回裸 JSON（无 code/data 包裹），调用方用 r.data || r 兜底
  getOidcHealth: () => req<any>("GET", "/auth/oidc/health"),
  getAuthSettings: () => req<any>("GET", "/auth/settings"),
  saveImConfig: (body: {
    provider: "wecom" | "dingtalk"; corp_id: string; secret: string;
    agent_id: string; webhook_token: string; encoding_aes_key?: string;
  }) => req<any>("POST", "/integrations/im/config", body),
  listImConfigs: () => req<any>("GET", "/integrations/im/config"),
  deleteImConfig: (provider: string) => req<any>("DELETE", `/integrations/im/config/${provider}`),
  sendImMessage: (body: { provider: string; to_user: string; text: string }) =>
    req<any>("POST", "/integrations/im/send", body),
  imApprove: (body: {
    provider: string; to_user: string; title?: string; url?: string; summary?: string;
    approval_id?: string; base_url?: string;
  }) => req<any>("POST", "/integrations/im/notify-approval", body),

  // ── M43/M48 租户配置（platform_admin）──
  listTenants: () => req<any>("GET", "/tenants"),
  getTenant: (id: string) => req<any>("GET", `/tenants/${id}`),
  putTenantApprovalConfig: (id: string, body: {
    stages: { name: string; approver_role: string }[];
    allow_global_scope: boolean;
    notifiers?: Record<string, string[]>;
  }) => req<any>("PUT", `/tenants/${id}/approval-config`, body),
  // M49：把某租户审批模板（stages + notifiers）整体复制到目标租户
  copyTenantApprovalConfig: (sourceId: string, targetId: string) =>
    req<any>("POST", `/tenants/${sourceId}/copy-approval-config`, { target_tenant_id: targetId }),

  // ── M27：蓝图缺口收口 ──
  getReadiness: () => req<any>("GET", "/ops/readiness"),
  configureExternalInput: (key: string, fields: Record<string, any>) =>
    req<any>("POST", "/ops/configure", { key, fields }),
  getOpsHealth: () => req<any>("GET", "/ops/health"),
  getOpsAlerts: () => req<any>("GET", "/ops/alerts"),
  getOpsMetrics: () => fetch(`${BASE}/ops/metrics`, {
    headers: authToken() ? { Authorization: authToken() } : {},
  }).then((r) => r.text()),
  getQualityMetrics: () => req<any>("GET", "/metrics/quality"),
  getAdoptionMetrics: () => req<any>("GET", "/metrics/adoption"),
  getGuardPolicy: () => req<any>("GET", "/guardrails/policy"),
  getMyRole: () => req<any>("GET", "/agents/my-role"),
  getEvolutionGaps: () => req<any>("GET", "/evolution/gaps"),
  listProposals: (status = "") => req<any>("GET", `/evolution/proposals${status ? `?status=${status}` : ""}`),
  decideProposal: (pid: string, decision: "approve" | "reject") =>
    req<any>("POST", `/evolution/proposals/${pid}/decide`, { decision }),
  listHandoffs: (status = "") => req<any>("GET", `/handoff${status ? `?status=${status}` : ""}`),
  resolveHandoff: (hid: string, resolution: string) =>
    req<any>("POST", `/handoff/${hid}/resolve`, { resolution }),
  listBuilderTemplates: () => req<any>("GET", "/builder/templates"),
  createDepartment: (body: any) => req<any>("POST", "/builder/departments", body),
  listDepartments: () => req<any>("GET", "/builder/departments"),
  approveDepartment: (did: string) => req<any>("POST", `/builder/departments/${did}/approve`),
  createAgentDraft: (body: any) => req<any>("POST", "/builder/agents", body),
  listAgentDrafts: () => req<any>("GET", "/builder/agents"),
  publishAgentDraft: (agentId: string) => req<any>("POST", `/builder/agents/${agentId}/publish`),
  createWorkflow: (body: any) => req<any>("POST", "/builder/workflows", body),
  listWorkflows: () => req<any>("GET", "/builder/workflows"),
  getWorkflow: (wid: string) => req<any>("GET", `/builder/workflows/${wid}`),
  updateWorkflow: (wid: string, body: any) => req<any>("PUT", `/builder/workflows/${wid}`, body),
  runWorkflow: (wid: string, input: string) => req<any>("POST", `/builder/workflows/${wid}/run`, { input }),
  getModelTiers: () => req<any>("GET", "/models/tiers"),
  getCacheStats: () => req<any>("GET", "/ops/cache/stats"),
  getMlopsPipeline: () => req<any>("GET", "/mlops/pipeline"),
};

// 下载必须带鉴权头，所以不能直接用 <a href>（浏览器不会补 Authorization）。
// 这里走 fetch→blob→临时链接，保证「谁能下」由后端 RBAC 决定，而不是靠隐藏地址。
export async function downloadAgentFile(fileId: string, filename: string) {
  const res = await fetch(`${BASE}/agent/files/${fileId}`, {
    headers: token ? { Authorization: token } : {},
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try { detail = (await res.json())?.detail || detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename || "download.xlsx";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// 取文件 Blob（不触发下载）：右侧看板内联预览 HTML/PDF 用。
export async function fetchAgentFile(fileId: string): Promise<Blob> {
  const res = await fetch(`${BASE}/agent/files/${fileId}`, {
    headers: token ? { Authorization: token } : {},
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try { detail = (await res.json())?.detail || detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  return res.blob();
}

// 第35章选型规则（本地兜底，与后端 selection/framework.py 同源；后端不可达时使用）
export function recommendLocal(inputType: string, deterministic: boolean, need: string): any {
  let adapter: string;
  if (deterministic && (need === "accuracy" || need === "explain")) {
    adapter = inputType === "table" || inputType === "structured" ? "ml_serving" : "rule";
  } else if (need === "knowledge") {
    adapter = "llm_retrieval";
  } else if (need === "creative") {
    adapter = "llm";
  } else if ((inputType === "table" || inputType === "structured") && need === "accuracy") {
    adapter = "ml_llm_retrieval";
  } else {
    adapter = "llm";
  }
  const meta: Record<string, any> = {
    rule: { name: "规则引擎 / 传统 ML", tag: "RULE" },
    ml_serving: { name: "传统 ML 服务", tag: "ML" },
    llm: { name: "大模型（LLM）", tag: "LLM" },
    llm_retrieval: { name: "RAG（检索增强）", tag: "RAG" },
    ml_llm_retrieval: { name: "数据分析 + RAG 混合", tag: "HYBRID" },
  };
  return { adapter_type: adapter, ...meta[adapter] };
}

// 联调入口：优先后端选型服务，失败回退本地规则（保证选型向导永远可用）
export async function recommendWithBackend(inputType: string, deterministic: boolean, need: string): Promise<any> {
  try {
    const r = await api.recommend(inputType, deterministic, need);
    return r.data; // 后端返回 { adapter_type, name, tag, why, link, landing_chapters }
  } catch {
    return recommendLocal(inputType, deterministic, need);
  }
}

