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

  // 6) M5 审批红线：未发布不可调用
  const agentId = ag.data.agent_id;
  const approvalId = ag.data.approval_id;
  const blocked = await fetch(`${BASE}/agent/invoke`, {
    method: "POST", headers: H,
    body: JSON.stringify({ agent_id: agentId, input: "hi", stream: false }),
  });
  assert(blocked.status === 403, `未发布 invoke 被拦（403：${(await blocked.json()).detail?.slice(0, 30)}…）`);

  // 7) M5 审批流：员工无权审批 → 管理员通过 → 发布可调用
  const empTry = await fetch(`${BASE}/approval/${approvalId}/decide`, {
    method: "POST", headers: H, body: JSON.stringify({ decision: "approve" }),
  });
  assert(empTry.status === 403, "员工决策审批被拒（403）");

  const adminLogin = await fetch(`${BASE}/auth/sso/callback`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider: "wecom", code: "dev_admin", device_id: "dev", state: "x" }),
  }).then((r) => r.json());
  assert(adminLogin.data.user.role === "platform_admin", "dev_admin 登录 → platform_admin（王总）");
  const AH = { "Content-Type": "application/json", Authorization: adminLogin.data.token };

  const ap = await fetch(`${BASE}/approval/${approvalId}/decide`, {
    method: "POST", headers: AH, body: JSON.stringify({ decision: "approve", comment: "契约脚本评审" }),
  }).then((r) => r.json());
  assert(ap.data.status === "approved" && ap.data.agent_status === "published",
    `审批通过 → 智能体 ${ap.data.agent_status}`);

  // 8) M4 智能体调用 + 会话记忆（发布后）
  const inv1 = await fetch(`${BASE}/agent/invoke`, {
    method: "POST", headers: H,
    body: JSON.stringify({ agent_id: agentId, input: "五一活动方案有哪些", stream: false }),
  });
  const inv1Body = await inv1.json();
  assert(inv1.status === 200 && !!inv1Body.data.answer, "调用智能体首轮 (POST /agent/invoke) 返回答案");
  const sid = inv1Body.data.session.session_id;
  assert(inv1Body.data.session.turn_count === 2, "首轮自动建会话（turn_count=2）");

  const inv2 = await fetch(`${BASE}/agent/invoke`, {
    method: "POST", headers: H,
    body: JSON.stringify({ agent_id: agentId, input: "预算多少", session_id: sid, stream: false }),
  });
  const inv2Body = await inv2.json();
  assert(inv2.status === 200 && inv2Body.data.session.query_rewritten === true,
    "第二轮记忆改写（query_rewritten=true，指代消解生效）");

  const sess = await fetch(`${BASE}/agent/sessions/${sid}`, { headers: H });
  const sessBody = await sess.json();
  assert(sess.status === 200 && sessBody.data.turn_count === 4, `会话记忆查询 (GET /agent/sessions) turn_count=${sessBody.data?.turn_count}`);

  // ── 9) M7 计量：调用后立即出账（token 真值 + 成本） ──
  const recs = await fetch(`${BASE}/usage/records?limit=20`, { headers: H }).then((r) => r.json());
  const rec = recs.data.records.find((x) => x.agent_id === agentId);
  assert(!!rec, "计量明细含本次调用的账单 (GET /usage/records)");
  assert(rec.prompt_tokens > 0 && rec.completion_tokens > 0,
    `token 落账：入 ${rec.prompt_tokens} / 出 ${rec.completion_tokens}`);
  assert(typeof rec.cost === "number" && rec.cost >= 0, `成本按单价折算：¥${rec.cost}`);
  assert(typeof rec.real === "boolean", `生成方式可判别：${rec.real ? "真实 LLM" : "stub 回退"}`);

  const sum = await fetch(`${BASE}/usage/summary?dimension=model`, { headers: H }).then((r) => r.json());
  assert(sum.data.rows.length > 0 && sum.data.total.total_tokens > 0,
    `按模型聚合 (GET /usage/summary) 共 ${sum.data.total.total_tokens} tokens`);

  const quota = await fetch(`${BASE}/usage/quota`, { headers: H }).then((r) => r.json());
  assert(quota.data.used_tokens > 0 && quota.data.exceeded === false,
    `配额水位 ${quota.data.used_pct}%（未超限，超限则 invoke 429）`);

  // ── 10) M7 会话管理：列表可见 + 他人不可删（403） ──
  const sessList = await fetch(`${BASE}/agent/sessions`, { headers: H }).then((r) => r.json());
  assert(sessList.data.sessions.some((s) => s.session_id === sid), "会话列表含刚建的会话 (GET /agent/sessions)");
  const delByOther = await fetch(`${BASE}/agent/sessions/${sid}`, { method: "DELETE", headers: AH });
  assert(delByOther.status === 403, "跨用户删除会话被拒（403，归属校验）");

  // ── 11) M7 知识库上传 → 可召回 → 删除 ──
  const marker = "契约验收标记词";
  const fd = new FormData();
  fd.append("file", new Blob([`${marker}：契约脚本上传的验收文档正文。`], { type: "text/plain" }), "contract.txt");
  fd.append("title", marker);
  fd.append("scope", "department");
  const up = await fetch(`${BASE}/kb/documents/upload`, { method: "POST", headers: { Authorization: token }, body: fd });
  const upBody = await up.json();
  assert(up.status === 200 && upBody.data.indexed === true,
    `文件上传入库 (POST /kb/documents/upload) doc=${upBody.data?.doc_id} indexed=${upBody.data?.indexed}`);
  const docId = upBody.data.doc_id;

  const kbHits = await fetch(`${BASE}/rag/retrieve`, {
    method: "POST", headers: H,
    body: JSON.stringify({ query: marker, kb_scope: "all", top_k: 6, tenant_id: "t_demo" }),
  }).then((r) => r.json());
  assert(kbHits.data.hits.some((h) => h.doc_id === docId), "上传后立即可被召回（同一份索引，无二次同步）");

  const del = await fetch(`${BASE}/kb/documents/${docId}`, { method: "DELETE", headers: H });
  assert(del.status === 200, "删除知识文档 (DELETE /kb/documents/{id}) 三处同步清理");

  // ── 12) M7 模型管理：员工无权启停（403）→ 管理员可停/启 ──
  const toggleByEmp = await fetch(`${BASE}/models/qwen-turbo`, {
    method: "PATCH", headers: H, body: JSON.stringify({ status: "inactive" }),
  });
  assert(toggleByEmp.status === 403, "员工启停模型被拒（403）");
  const off = await fetch(`${BASE}/models/qwen-turbo`, {
    method: "PATCH", headers: AH, body: JSON.stringify({ status: "inactive" }),
  }).then((r) => r.json());
  assert(off.data.model.status === "inactive", "管理员停用模型 (PATCH /models/{id}) 生效");
  const activeList = await fetch(`${BASE}/models`, { headers: H }).then((r) => r.json());
  assert(!activeList.data.models.some((m) => m.model_id === "qwen-turbo"), "停用后默认列表不再返回该模型");
  await fetch(`${BASE}/models/qwen-turbo`, { method: "PATCH", headers: AH, body: JSON.stringify({ status: "active" }) });
  const gw = await fetch(`${BASE}/models/gateway`, { headers: H }).then((r) => r.json());
  assert(typeof gw.data.available === "boolean", `网关探活 (GET /models/gateway) available=${gw.data.available}`);

  // ── 13) 收尾：删除本次会话（验证归属内可删） ──
  const delOwn = await fetch(`${BASE}/agent/sessions/${sid}`, { method: "DELETE", headers: H });
  assert(delOwn.status === 200, "本人删除会话成功（PG 副本同步删除）");

  console.log("─".repeat(56));
  console.log(process.exitCode ? "契约验证存在失败项 ✗" : "前端联调契约全部通过 ✓（浏览器经 Vite 代理等价）");
}

main().catch((e) => { console.error(e); process.exit(1); });
