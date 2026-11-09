import { useEffect, useState } from "react";
import { api } from "../api/client";

/** M27 · 平台运营台：把蓝图第 19/20/21/26/27/28/29/30/31/34/38/39/40/41/42/43/44 章
 *  收口后的能力集中在一屏。原则：只展示真实返回值，样本不足就写「样本不足」，
 *  外部依赖没配就写「未就绪」，不把 SKIP 画成绿色 PASS。 */
type Any = Record<string, any>;

const box: React.CSSProperties = { border: "1px solid var(--line)", borderRadius: 8, padding: 14, background: "var(--card)" };
const small: React.CSSProperties = { fontSize: 12, color: "var(--muted)" };
const mono: React.CSSProperties = { fontFamily: "ui-monospace, Consolas, monospace", fontSize: 12 };

function KV({ label, value, hint }: { label: string; value: any; hint?: string }) {
  return (
    <div style={{ ...box, padding: 12 }}>
      <div style={small}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, color: "var(--navy)", marginTop: 4 }}>{String(value)}</div>
      {hint && <div style={{ ...small, marginTop: 2 }}>{hint}</div>}
    </div>
  );
}

function Json({ data }: { data: any }) {
  return <pre style={{ ...mono, whiteSpace: "pre-wrap", margin: 0, maxHeight: 220, overflow: "auto" }}>{JSON.stringify(data, null, 2)}</pre>;
}

function pct(v: any) { return v == null ? "—" : `${Math.round(Number(v) * 100)}%`; }

function KVGrid({ rows }: { rows: [string, any][] }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
      {rows.map(([k, v], i) => (
        <div key={i} style={{ ...box, padding: 10 }}>
          <div style={small}>{k}</div>
          <div style={{ fontSize: 16, fontWeight: 700, color: "var(--navy)", marginTop: 2 }}>{String(v)}</div>
        </div>
      ))}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8 }}>
      <div style={{ ...small, width: 130, flexShrink: 0 }}>{label}</div>
      {children}
    </div>
  );
}

function QualityView({ q }: { q: Any }) {
  return (
    <div>
      {q.insufficient_data && <span className="badge" style={{ background: "#fff4e0", color: "#9a6700" }}>样本不足（{q.window_days}天）</span>}
      <div style={{ marginTop: 8 }}>
        <KVGrid rows={[
          ["样本量", q.sample_size], ["成功率", pct(q.success_rate)],
          ["幻觉率", pct(q.hallucination_rate)], ["转人工率", pct(q.handoff_rate)],
          ["低置信率", pct(q.low_confidence_rate)], ["满意度", q.satisfaction],
          ["p95 延迟", q.latency_p95 != null ? `${Math.round(q.latency_p95)}ms` : "—"],
          ["p99 延迟", q.latency_p99 != null ? `${Math.round(q.latency_p99)}ms` : "—"],
        ]} />
      </div>
    </div>
  );
}

function AdoptionView({ ad }: { ad: Any }) {
  return (
    <div>
      {ad.insufficient_data && <span className="badge" style={{ background: "#fff4e0", color: "#9a6700" }}>样本不足（{ad.window_days}天）</span>}
      <div style={{ marginTop: 8 }}>
        <KVGrid rows={[
          ["DAU", ad.dau], ["WAU", ad.wau],
          ["任务完成率", pct(ad.task_completion_rate)], ["部门渗透", pct(ad.dept_penetration)],
          ["采纳率", pct(ad.adoption_rate)], ["ROI", ad.roi],
        ]} />
      </div>
      {ad.note && <div style={{ ...small, marginTop: 6 }}>{ad.note}</div>}
    </div>
  );
}

function PolicyView({ p }: { p: Any }) {
  const flags: [string, any][] = [
    ["护栏启用", p.enabled], ["严格模式", p.strict],
    ["工具鉴权", p.tool_auth], ["输出脱敏", p.output_mask],
  ];
  return (
    <div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {flags.map(([k, v], i) => (
          <span key={i} className="badge" style={v ? { background: "#e6f4ea", color: "#1b7f4b" } : { background: "#fdecea", color: "#b03a3a" }}>{k}: {v ? "开" : "关"}</span>
        ))}
      </div>
      <div style={{ ...small, marginTop: 8 }}>注入规则 {p.injection_rules} · 脱敏规则 {p.masking_rules} · 最大命中 {p.max_injection_hits}</div>
      <div style={{ ...small, marginTop: 4 }}>放行工具：{(p.allowed_tools || []).map((t: string) => `<${t}>`).join(" ") || "无"}</div>
      {p.stats && <div style={{ ...small, marginTop: 4 }}>扫描 输入{p.stats.scan_input}/输出{p.stats.scan_output} · 接地{p.stats.grounding} · 工具{p.stats.tool_check}</div>}
    </div>
  );
}

function TiersView({ t }: { t: Any }) {
  const items = [t.small, t.large].filter(Boolean);
  return (
    <div>
      <div style={small}>默认 {t.default_model} · 规则路由 {t.enabled ? "开" : "关"}</div>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, marginTop: 6 }}>
        <tbody>{items.map((m: Any, i: number) => (
          <tr key={i} style={{ borderTop: "1px solid var(--line)" }}>
            <td style={{ padding: "4px" }}>{m.model}</td><td>{m.tier}</td><td>¥{m.unit_price}/次</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function CacheView({ c }: { c: Any }) {
  return (
    <div>
      <KVGrid rows={[["条目", c.entries], ["命中率", pct(c.hit_rate)], ["语义层", c.semantic_layer], ["相似度", c.similarity]]} />
      <div style={{ ...small, marginTop: 6 }}>{c.note}</div>
    </div>
  );
}

function PipelineView({ p }: { p: Any }) {
  const stages: [string, Any][] = [["特征库", p.feature_store], ["训练", p.training], ["模型仓库", p.registry], ["重训钩子", p.retrain]];
  return (
    <div>
      {stages.map(([n, s], i) => s && (
        <div key={i} style={{ ...small, borderTop: "1px solid var(--line)", padding: "5px 0" }}>
          {s.done ? "✓" : "✗"} {n} — {s.condition}
        </div>
      ))}
      <div style={{ ...small, marginTop: 6 }}>已训练模型 {p.models_registered} · 数据集 {p.datasets} · 运行 {p.runs} · 全部就绪 {p.done_all ? "是" : "否"}</div>
    </div>
  );
}

export function PlatformOpsPage({ isAdmin }: { isAdmin: boolean }) {
  const [err, setErr] = useState("");
  const [readiness, setReadiness] = useState<Any>({});
  const [health, setHealth] = useState<Any>({});
  const [alerts, setAlerts] = useState<any[]>([]);
  const [metrics, setMetrics] = useState("");
  const [quality, setQuality] = useState<Any>({});
  const [adoption, setAdoption] = useState<Any>({});
  const [proposals, setProposals] = useState<any[]>([]);
  const [gaps, setGaps] = useState<any[]>([]);
  const [handoffs, setHandoffs] = useState<any[]>([]);
  const [myRole, setMyRole] = useState<Any>({});
  const [policy, setPolicy] = useState<Any>({});
  const [tiers, setTiers] = useState<Any>({});
  const [cache, setCache] = useState<Any>({});
  const [pipeline, setPipeline] = useState<Any>({});
  const [depts, setDepts] = useState<any[]>([]);
  const [drafts, setDrafts] = useState<any[]>([]);
  const [flows, setFlows] = useState<any[]>([]);
  const [flowRun, setFlowRun] = useState<Any>({});
  const [deptName, setDeptName] = useState("华东项目组");
  const [draftName, setDraftName] = useState("部门问答助手");
  const [cfgKey, setCfgKey] = useState("");
  const [cfgFields, setCfgFields] = useState<Any>({});
  const [cfgMsg, setCfgMsg] = useState("");
  const [cfgBusy, setCfgBusy] = useState(false);

  const load = async () => {
    try {
      const [r, h, a, m, q, ad, p, g, ho, role, pol, ti, ca, pl, dp, dr, fl] = await Promise.all([
        api.getReadiness(), api.getOpsHealth(), api.getOpsAlerts(), api.getOpsMetrics(),
        api.getQualityMetrics(), api.getAdoptionMetrics(), api.listProposals(),
        api.getEvolutionGaps(), api.listHandoffs(), api.getMyRole(), api.getGuardPolicy(),
        api.getModelTiers(), api.getCacheStats(), api.getMlopsPipeline(),
        api.listDepartments(), api.listAgentDrafts(), api.listWorkflows(),
      ]);
      setReadiness(r.data || {}); setHealth(h.data || {});
      setAlerts(a.data?.alerts || a.data || []);
      setMetrics(m || ""); setQuality(q.data || {}); setAdoption(ad.data || {});
      setProposals(p.data?.proposals || p.data || []);
      setGaps(g.data?.gaps || g.data || []);
      setHandoffs(ho.data?.handoffs || ho.data?.items || []);
      setMyRole(role.data || {}); setPolicy(pol.data || {}); setTiers(ti.data || {});
      setCache(ca.data || {}); setPipeline(pl.data || {});
      setDepts(dp.data?.departments || dp.data || []);
      setDrafts(dr.data?.agents || dr.data?.drafts || dr.data || []);
      setFlows(fl.data?.workflows || fl.data || []);
      setErr("");
    } catch (e: any) { setErr(e.message); }
  };
  useEffect(() => { load(); }, []);

  const act = async (fn: () => Promise<any>, after?: (d: any) => void) => {
    try { const d = await fn(); after?.(d); await load(); setErr(""); }
    catch (e: any) { setErr(e.message); }
  };

  const openCfg = (k: string) => {
    setCfgKey(k);
    setCfgFields(k === "search_key" ? { provider: "tavily" } : k === "real_corpus" ? { enabled: false } : {});
    setCfgMsg("");
  };

  const submitCfg = async () => {
    if (!cfgKey) return;
    setCfgBusy(true); setCfgMsg("");
    try {
      const d = await api.configureExternalInput(cfgKey, cfgFields);
      if (d.code === 0) {
        setCfgMsg("OK：已写入并落盘 .env.local，就绪度已刷新（重启仍生效）");
        await load();
      } else {
        setCfgMsg("失败：" + (d.msg || "未知错误"));
      }
    } catch (e: any) { setCfgMsg("失败：" + e.message); }
    setCfgBusy(false);
  };

  const readyItems: Any = readiness.items || readiness.checks || readiness;
  const readyList = Array.isArray(readyItems) ? readyItems : Object.entries(readyItems).map(([k, v]) => ({ key: k, ...(v as Any) }));
  const notReady = readyList.filter((x: Any) => x.configured === false || x.status === "not_ready").length;

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div>
          <h2 style={{ margin: 0 }}>平台运营台 · M27</h2>
          <div style={small}>蓝图缺口收口：就绪度 / 安全 / 演化 / 质量 / 编排。所有数字来自真实返回值，不做估算。</div>
        </div>
        <button className="btn" onClick={load}>刷新</button>
      </div>
      {err && <div className="card" style={{ borderColor: "#e57373", color: "#b03a3a", marginBottom: 12 }}>加载或操作失败：{err}</div>}

      <div className="grid4">
        <KV label="外部输入未就绪" value={notReady} hint={`共 ${readyList.length} 项，需真实凭据或语料`} />
        <KV label="当前告警" value={alerts.length} hint={health.insufficient_data ? "样本不足，未误报" : "按阈值实时计算"} />
        <KV label="待批演化建议" value={proposals.filter((p: Any) => p.status === "pending").length} hint="默认不自动生效" />
        <KV label="人工工单" value={handoffs.filter((h: Any) => h.status === "open").length} hint="低置信转人工" />
      </div>

      <div className="grid2">
        <div className="card">
          <h3>① 外部输入就绪度（第 9/15/31 章）</h3>
          <div style={small}>configured 只表示检测到配置，不代表验收通过；免费网页源不算生产就绪。</div>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13, marginTop: 8 }}>
            <thead><tr style={{ textAlign: "left", color: "var(--muted)" }}>
              <th style={{ padding: "6px 4px" }}>项</th><th>状态</th><th>说明</th><th>操作</th></tr></thead>
            <tbody>{readyList.map((x: Any, i: number) => (
              <tr key={i} style={{ borderTop: "1px solid var(--line)" }}>
                <td style={{ padding: "6px 4px" }}>{x.key || x.name || x.id}</td>
                <td style={{ color: x.configured === false ? "#b03a3a" : "#1b7f4b" }}>
                  {x.configured === false ? "未就绪" : (x.status || "已配置/待验收")}</td>
                <td style={{ ...small }}>{x.note || x.verify_command || ""}</td>
                <td style={{ padding: "6px 4px" }}>
                  {x.configured === false && x.key && (
                    <button className="btn" onClick={() => openCfg(x.key)}>配置</button>
                  )}
                </td>
              </tr>
            ))}</tbody>
          </table>
          {cfgKey && (
            <div style={{ ...box, marginTop: 10, borderColor: "#1b7f4b" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <b>在线配置：{cfgKey}</b>
                <button className="btn" onClick={() => setCfgKey("")}>关闭</button>
              </div>
              {cfgKey === "llm_key" && (
                <>
                  <Field label="API Key"><input className="field" type="password" value={cfgFields.api_key || ""} onChange={(e) => setCfgFields({ ...cfgFields, api_key: e.target.value })} style={{ width: 320 }} /></Field>
                  <Field label="Base URL（可选）"><input className="field" value={cfgFields.base_url || ""} onChange={(e) => setCfgFields({ ...cfgFields, base_url: e.target.value })} style={{ width: 320 }} /></Field>
                  <Field label="模型（可选）"><input className="field" value={cfgFields.model || ""} onChange={(e) => setCfgFields({ ...cfgFields, model: e.target.value })} style={{ width: 320 }} /></Field>
                </>
              )}
              {cfgKey === "search_key" && (
                <>
                  <Field label="Provider">
                    <select className="field" value={cfgFields.provider || "tavily"} onChange={(e) => setCfgFields({ ...cfgFields, provider: e.target.value })}>
                      <option value="tavily">tavily</option>
                      <option value="bocha">bocha</option>
                      <option value="serper">serper</option>
                      <option value="bing">bing</option>
                    </select>
                  </Field>
                  <Field label="API Key"><input className="field" type="password" value={cfgFields.api_key || ""} onChange={(e) => setCfgFields({ ...cfgFields, api_key: e.target.value })} style={{ width: 320 }} /></Field>
                </>
              )}
              {cfgKey === "oidc_idp" && (
                <>
                  <Field label="Issuer"><input className="field" value={cfgFields.issuer || ""} onChange={(e) => setCfgFields({ ...cfgFields, issuer: e.target.value })} style={{ width: 320 }} /></Field>
                  <Field label="Audience"><input className="field" value={cfgFields.audience || ""} onChange={(e) => setCfgFields({ ...cfgFields, audience: e.target.value })} style={{ width: 320 }} /></Field>
                </>
              )}
              {cfgKey === "real_corpus" && (
                <Field label="真实语料模式">
                  <label style={{ fontSize: 13 }}>
                    <input type="checkbox" checked={!!cfgFields.enabled} onChange={(e) => setCfgFields({ ...cfgFields, enabled: e.target.checked })} />
                    {" "}启用真实语料（关闭演示语料）
                  </label>
                </Field>
              )}
              {cfgMsg && <div style={{ ...small, color: cfgMsg.startsWith("OK") ? "#1b7f4b" : "#b03a3a", marginTop: 8 }}>{cfgMsg}</div>}
              <button className="btn" style={{ marginTop: 10 }} disabled={cfgBusy} onClick={submitCfg}>
                {cfgBusy ? "保存中…" : "保存并刷新就绪度"}
              </button>
              <div style={{ ...small, marginTop: 6 }}>仅 platform_admin 可操作；明文密钥落盘 backend/.env.local（gitignore），生产应改用密钥管理服务。</div>
            </div>
          )}
        </div>

        <div className="card">
          <h3>② 运维可观测（第 26 章）</h3>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
            <span className="badge">health: {health.status || "unknown"}</span>
            <span className="badge">留存 {health.retention_days ?? "—"} 天</span>
            <span className="badge">告警 {alerts.length}</span>
          </div>
          <details style={{ marginTop: 8 }}>
            <summary style={{ ...small, cursor: "pointer" }}>Prometheus 原始指标（/ops/metrics，供 Grafana 抓取）</summary>
            <pre style={{ ...mono, background: "var(--soft)", padding: 8, borderRadius: 6, maxHeight: 160, overflow: "auto", marginTop: 6 }}>{metrics.slice(0, 1200) || "（无数据）"}</pre>
          </details>
          <div style={small}>告警阈值：错误率 1% / p95 3000ms / 模型 429 突增。样本不足时不报。</div>
        </div>

        <div className="card">
          <h3>③ 质量与运营度量（第 28/30/41 章）</h3>
          <div className="grid2">
            <div style={box}><div style={small}>质量指标</div><QualityView q={quality} /></div>
            <div style={box}><div style={small}>采纳度 / ROI</div><AdoptionView ad={adoption} /></div>
          </div>
          <div style={{ ...small, marginTop: 8 }}>空数据一律 0 + insufficient_data，不编造 DAU / ROI。</div>
        </div>

        <div className="card">
          <h3>④ 演化建议台（第 20 章 · 人在环）</h3>
          <div style={small}>知识缺口：{gaps.length} 条；建议：{proposals.length} 条。审批只管理员可操作。</div>
          <div style={{ maxHeight: 190, overflow: "auto", marginTop: 8 }}>
            {(gaps || []).slice(0, 3).map((g: Any, i: number) => (
              <div key={`g${i}`} style={{ ...small, padding: "4px 0" }}>缺口 · {g.agent_id || g.key} · {g.count || 0} 次 · {g.suggestion || ""}</div>
            ))}
            {(proposals || []).slice(0, 6).map((p: Any) => (
              <div key={p.id} style={{ display: "flex", alignItems: "center", gap: 8, borderTop: "1px solid var(--line)", padding: "6px 0" }}>
                <span style={{ flex: 1, fontSize: 13 }}>{p.title} <span style={small}>({p.status})</span></span>
                {isAdmin && p.status === "pending" && (
                  <>
                    <button className="btn" onClick={() => act(() => api.decideProposal(p.id, "approve"))}>批准</button>
                    <button className="btn" onClick={() => act(() => api.decideProposal(p.id, "reject"))}>驳回</button>
                  </>
                )}
              </div>
            ))}
            {!proposals.length && <div style={small}>暂无建议。负反馈与纠错会自动生成待批条目。</div>}
          </div>
        </div>

        <div className="card">
          <h3>⑤ 人工工单与安全策略（第 27/42/44 章）</h3>
          <div style={small}>岗位来源：{myRole.source || "—"} · {myRole.role || "未配置"}（enforced={String(!!myRole.enforced)}）</div>
          <div style={{ ...small, marginTop: 4 }}>护栏开关</div>
          <PolicyView p={policy} />
          <div style={{ ...small, marginTop: 8 }}>工单 {handoffs.length} 条</div>
          {(handoffs || []).slice(0, 4).map((h: Any) => (
            <div key={h.id} style={{ display: "flex", gap: 8, alignItems: "center", borderTop: "1px solid var(--line)", padding: "5px 0", fontSize: 13 }}>
              <span style={{ flex: 1 }}>{h.reason || h.id} <span style={small}>({h.status})</span></span>
              {isAdmin && h.status === "open" && <button className="btn" onClick={() => act(() => api.resolveHandoff(h.id, "运营台人工处理完成"))}>处理</button>}
            </div>
          ))}
        </div>

        <div className="card">
          <h3>⑥ 成本 / 缓存 / MLOps（第 34/38/40 章）</h3>
          <div className="grid2">
            <div style={box}><div style={small}>模型分档</div><TiersView t={tiers} /></div>
            <div style={box}><div style={small}>语义缓存</div><CacheView c={cache} /></div>
          </div>
          <div style={{ ...small, marginTop: 8 }}>MLOps 流水线状态（骨架不冒充已训练）</div>
          <PipelineView p={pipeline} />
        </div>
      </div>

      <div className="card" style={{ marginTop: 14 }}>
        <h3>⑦ 自助建部门 / L2 建包 / L3 编排（第 19/21 章）</h3>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <input className="field" value={deptName} onChange={(e) => setDeptName(e.target.value)} style={{ width: 170 }} />
          <button className="btn" onClick={() => act(() => api.createDepartment({
            name: deptName, parent: "dept_root", domain: "general", description: "运营台创建的试点部门", template: "blank",
          }))}>建部门（待审）</button>
          <input className="field" value={draftName} onChange={(e) => setDraftName(e.target.value)} style={{ width: 170 }} />
          <button className="btn" onClick={() => act(() => api.createAgentDraft({
            name: draftName, goal: "回答本部门常见问题并附出处",
            inputs_contract: "自然语言问题", outputs_contract: "带出处的简要回答",
            steps: ["检索授权片段", "按硬规则作答", "标注缺失项"],
          }))}>建技能草稿</button>
          <button className="btn" onClick={() => act(() => api.createWorkflow({
            name: "制度问答流程", nodes: [
              { type: "rag", name: "查制度", config: {} },
              { type: "approval", name: "人工复核", config: {} },
            ],
          }), (d) => { const id = d?.data?.id; if (id) act(() => api.runWorkflow(id, "差旅住宿标准是多少"), (r) => setFlowRun(r?.data || {})); })}>建并试跑工作流</button>
        </div>
        <div className="grid3">
          <div style={box}><div style={small}>部门 {depts.length}</div><Json data={(depts || []).slice(0, 3).map((d: Any) => ({ name: d.name, status: d.status }))} /></div>
          <div style={box}><div style={small}>技能草稿 {drafts.length}</div><Json data={(drafts || []).slice(0, 3).map((d: Any) => ({ name: d.name, status: d.status }))} /></div>
          <div style={box}><div style={small}>工作流 {flows.length} / 试跑</div><Json data={Object.keys(flowRun).length ? flowRun : (flows || []).slice(0, 2).map((f: Any) => ({ name: f.name }))} /></div>
        </div>
        <div style={{ ...small, marginTop: 6 }}>草稿不落 115 业务包口径；发布仍需走审批红线。工作流 LLM 节点为 dry_run，不冒充真模型。</div>
      </div>
    </div>
  );
}
