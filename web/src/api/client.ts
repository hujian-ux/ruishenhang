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
  if (!res.ok) throw new Error(`${method} ${path} -> ${res.status}`);
  return res.json() as Promise<T>;
}

export const api = {
  login: () => req<{ code: number; data: { token: string } }>("POST", "/auth/sso/callback", {
    provider: "wecom", code: "dev", device_id: "dev", state: "x",
  }),
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
};

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

