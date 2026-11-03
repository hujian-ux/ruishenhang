// 轻量 API 客户端：对接后端单体 /api/v1，自动携带 dev token（由 /auth/sso/callback 签发）
const BASE = "/api/v1";

let token = "";

export function setToken(t: string) {
  token = t;
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: token } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try { detail = (await res.json())?.detail || detail; } catch { /* ignore */ }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

// 文件上传走 multipart，不能带 Content-Type（浏览器自动补 boundary）
async function uploadFile(path: string, form: FormData): Promise<any> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: token ? { Authorization: token } : {},
    body: form,
  });
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
  listAgents: () => req<{ code: number; data: { agents: any[] } }>("GET", "/agents"),
  // ── M15 技能包融合：分类目录 + 批量导入 ──
  getAgentCatalog: () => req<{ code: number; data: { categories: any[]; total: number } }>("GET", "/agents/catalog"),
  importAgents: (body: { items: any[]; batch_name?: string; batch_approval?: boolean }) =>
    req<any>("POST", "/agents/import", body),
  // M4 会话记忆：session_id 传入续聊，缺省新建
  // M16 文件进出：fileIds 为已上传附件的 file_id（先调 uploadAgentFile）
  invokeAgent: (agentId: string, input: string, sessionId?: string, fileIds?: string[]) =>
    req<any>("POST", "/agent/invoke", {
      agent_id: agentId, input, stream: false,
      ...(sessionId ? { session_id: sessionId } : {}),
      ...(fileIds && fileIds.length ? { file_ids: fileIds } : {}),
    }),

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
  // M5 审批流
  pendingApprovals: () => req<{ code: number; data: { count: number; tickets: any[] } }>("GET", "/approval/pending"),
  decideApproval: (approvalId: string, decision: "approve" | "reject", comment?: string) =>
    req<any>("POST", `/approval/${approvalId}/decide`, { decision, comment }),

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

