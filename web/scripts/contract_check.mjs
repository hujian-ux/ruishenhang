// 前端联调契约验证：用 Node 原生 fetch 复刻 web 前端的真实调用链路
// （login → models → selection/recommend → rag/retrieve → agents 注册）
// 运行：node scripts/contract_check.mjs   （需后端已在 127.0.0.1:8080 启动）
const BASE = "http://127.0.0.1:8080/api/v1";

function assert(cond, msg) {
  if (!cond) { console.error("  ✗ " + msg); process.exitCode = 1; }
  else console.log("  ✓ " + msg);
}

async function main() {
  console.log("前端联调契约验证（复刻 web/src 的 fetch 调用）\n" + "─".repeat(56));

  // 1) login
  const login = await fetch(`${BASE}/auth/sso/callback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider: "wecom", code: "dev", device_id: "dev", state: "x" }),
  });
  const loginJson = await login.json();
  const token = loginJson.data.token;
  assert(!!token, "login 返回 dev token");
  const H = { "Content-Type": "application/json", Authorization: token };

  // 2) models
  const models = await fetch(`${BASE}/models`, { headers: H }).then((r) => r.json());
  assert(Array.isArray(models.data.models) && models.data.models.length > 0, `模型注册表有 ${models.data.models?.length} 条`);
  assert(models.data.models[0].model_id && models.data.models[0].adapter, "模型含 model_id / adapter 字段");

  // 3) selection/recommend（前端选型向导联调后端服务）
  const sel = await fetch(`${BASE}/selection/recommend`, {
    method: "POST", headers: H,
    body: JSON.stringify({ input_type: "text", deterministic: false, need: "knowledge" }),
  }).then((r) => r.json());
  assert(sel.data.adapter_type === "llm_retrieval", `选型服务返回 adapter_type=${sel.data.adapter_type}`);
  assert(!!sel.data.landing_chapters, `选型服务返回落地章节：${sel.data.landing_chapters}`);

  // 4) rag/retrieve
  const ret = await fetch(`${BASE}/rag/retrieve`, {
    method: "POST", headers: H,
    body: JSON.stringify({ query: "到店礼 满减 销售话术", kb_scope: "all", top_k: 6, tenant_id: "t_demo" }),
  }).then((r) => r.json());
  assert(ret.data.hits.length > 0, `召回 ${ret.data.hits.length} 条`);
  assert(typeof ret.data.filtered_out === "number", `过滤 ${ret.data.filtered_out} 条（单一过滤点）`);

  // 5) agents 注册（选型结论自动带入）
  const ag = await fetch(`${BASE}/agents`, {
    method: "POST", headers: H,
    body: JSON.stringify({
      name: "销售知识助手", adapter_type: sel.data.adapter_type,
      description: `选型=${sel.data.adapter_type}`, knowledge_scope: "department",
      selection_rationale: { recommended: sel.data.adapter_type, landing_chapters: sel.data.landing_chapters },
    }),
  }).then((r) => r.json());
  assert(ag.data.status === "pending_review", `智能体状态=${ag.data.status}（第35章待发布评审）`);
  assert(ag.data.registered_by === "u_1024", `registered_by 服务端强制=${ag.data.registered_by}`);

  console.log("─".repeat(56));
  console.log(process.exitCode ? "契约验证存在失败项 ✗" : "前端联调契约全部通过 ✓（浏览器经 Vite 代理等价）");
}

main().catch((e) => { console.error(e); process.exit(1); });
