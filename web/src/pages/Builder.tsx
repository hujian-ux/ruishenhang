import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { Session } from "../auth/session";

/* ============================================================
 * P1-171 搭建台：模板中心 / L2 零代码建智能体 / L3 工作流编排画布
 * 后端底座：routers/builder.py（agents / workflows / departments / templates）
 * 设计对齐 theme.ts，零额外依赖（画布为自绘 SVG + 指针拖拽）。
 * ============================================================ */

type Tab = "templates" | "agent" | "workflow";

export function BuilderPage({ session }: { session: Session }) {
  const [tab, setTab] = useState<Tab>("templates");
  const [prefill, setPrefill] = useState<any>(null);
  const [prefillNonce, setPrefillNonce] = useState(0);

  const useAgentTemplate = (tpl: any) => {
    setPrefill(tpl);
    setPrefillNonce((n) => n + 1);
    setTab("agent");
  };

  return (
    <>
      <div className="builder-tabs">
        <div className={"tool" + (tab === "templates" ? " on" : "")} onClick={() => setTab("templates")}>模板中心</div>
        <div className={"tool" + (tab === "agent" ? " on" : "")} onClick={() => setTab("agent")}>L2 建智能体</div>
        <div className={"tool" + (tab === "workflow" ? " on" : "")} onClick={() => setTab("workflow")}>L3 工作流画布</div>
      </div>

      {tab === "templates" && <TemplateCenter onUseAgentTemplate={useAgentTemplate} />}
      {tab === "agent" && (
        <AgentBuilder
          session={session}
          prefill={prefill}
          prefillNonce={prefillNonce}
        />
      )}
      {tab === "workflow" && <WorkflowCanvas />}
    </>
  );
}

/* ───────────────────────── 模板中心 ───────────────────────── */

function TemplateCenter({ onUseAgentTemplate }: { onUseAgentTemplate: (tpl: any) => void }) {
  const [tpl, setTpl] = useState<any>(null);
  const [deptOpen, setDeptOpen] = useState<string | null>(null);
  const [deptName, setDeptName] = useState("");
  const [msg, setMsg] = useState<string>("");

  useEffect(() => {
    api.listBuilderTemplates().then((r) => setTpl(r.data)).catch((e) => setMsg("加载模板失败：" + e.message));
  }, []);

  if (!tpl) return <div className="hint">{msg || "加载模板中心…"}</div>;

  const deptEntries: [string, any][] = Object.entries(tpl.department_templates || {});
  const agentTpls: any[] = tpl.agent_templates || [];

  const buildDept = async (key: string) => {
    try {
      const r = await api.createDepartment({ name: deptName || key, template: key, domain: "", description: "", parent: "" });
      setMsg(`已发起部门「${r.data.name}」（${r.data.id}），状态=${r.data.status}，需平台管理员在「审批台→部门」批准。`);
      setDeptOpen(null);
      setDeptName("");
    } catch (e: any) { setMsg("建部门失败：" + e.message); }
  };

  return (
    <>
      <div className="card">
        <h3>① 智能体模板（L2 零代码脚手架）</h3>
        <div className="hint" style={{ marginBottom: 12 }}>
          点「用此模板」即跳到「L2 建智能体」并自动填好三件契约与步骤，你只改名字就能产出草稿。
        </div>
        <div className="tpl-grid">
          {agentTpls.map((a) => (
            <div className="tpl-card" key={a.key}>
              <h4>{a.name}</h4>
              <div className="note">{a.goal}</div>
              <div className="hint">输入：{a.inputs_contract}</div>
              <div className="hint">输出：{a.outputs_contract}</div>
              <ol>{a.steps.map((s: string, i: number) => <li key={i}>{s}</li>)}</ol>
              <button className="btn blue" style={{ marginTop: 4 }} onClick={() => onUseAgentTemplate(a)}>用此模板建智能体</button>
            </div>
          ))}
        </div>
      </div>

      <div className="card">
        <h3>② 部门模板（自助建部门，需管理员审批）</h3>
        <div className="hint" style={{ marginBottom: 12 }}>
          每类模板预置可见边界与示例智能体；发起后状态为 pending，平台管理员在「审批台」批准后才 active。
        </div>
        <div className="tpl-grid">
          {deptEntries.map(([key, v]) => (
            <div className="tpl-card" key={key}>
              <h4>{v.name} <span className="tag">{key}</span></h4>
              <div className="note">{v.visible_boundary?.note}</div>
              <div className="row" style={{ flexWrap: "wrap" }}>
                {(v.example_agents || []).map((g: string, i: number) => (
                  <span className="tag" key={i}>{g}</span>
                ))}
                {(v.example_agents || []).length === 0 && <span className="hint">空白模板，从零开始</span>}
              </div>
              {deptOpen === key ? (
                <div className="field" style={{ marginTop: 8 }}>
                  <label>部门名称</label>
                  <div className="row">
                    <input value={deptName} placeholder={v.name} onChange={(e) => setDeptName(e.target.value)} />
                    <button className="btn blue" onClick={() => buildDept(key)}>确认发起</button>
                    <button className="btn ghost" onClick={() => setDeptOpen(null)}>取消</button>
                  </div>
                </div>
              ) : (
                <button className="btn ghost" style={{ marginTop: 4 }} onClick={() => { setDeptOpen(key); setDeptName(""); }}>用此模板建部门</button>
              )}
            </div>
          ))}
        </div>
        {msg && <div className="ok">{msg}</div>}
      </div>
    </>
  );
}

/* ───────────────────────── L2 建智能体 ───────────────────────── */

function composePreview(name: string, goal: string, inputs: string, outputs: string, steps: string[]) {
  const stepLines = (steps || []).filter(Boolean).map((s, i) => `  ${i + 1}. ${s}`).join("\n") || "  （未填写步骤）";
  return (
    `# 智能体：${name || "未命名"}\n\n` +
    `## 三件契约\n【什么时候用】${goal || "未提供"}\n` +
    `【要给什么（输入契约）】${inputs || "未提供"}\n` +
    `【会拿到什么（输出契约）】${outputs || "未提供"}\n\n` +
    `## 执行步骤\n${stepLines}`
  );
}

function AgentBuilder({ session, prefill, prefillNonce }: { session: Session; prefill: any; prefillNonce: number }) {
  const isAdmin = session.user.role === "platform_admin" || session.user.role === "dept_admin";
  const [form, setForm] = useState({ name: "", goal: "", inputs_contract: "", outputs_contract: "", scope: "department", steps: [""] as string[] });
  const [saved, setSaved] = useState<any>(null);
  const [drafts, setDrafts] = useState<any[]>([]);
  const [pubMsg, setPubMsg] = useState("");

  useEffect(() => {
    if (prefillNonce > 0 && prefill) {
      setForm({
        name: prefill.name || "",
        goal: prefill.goal || "",
        inputs_contract: prefill.inputs_contract || "",
        outputs_contract: prefill.outputs_contract || "",
        scope: prefill.scope || "department",
        steps: (prefill.steps && prefill.steps.length ? prefill.steps : [""]),
      });
      setSaved(null);
    }
  }, [prefillNonce]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadDrafts = () => api.listAgentDrafts().then((r) => setDrafts(r.data.agents || [])).catch(() => setDrafts([]));
  useEffect(() => { loadDrafts(); }, []);

  const setStep = (i: number, v: string) => {
    const s = [...form.steps]; s[i] = v; setForm({ ...form, steps: s });
  };
  const addStep = () => setForm({ ...form, steps: [...form.steps, ""] });
  const delStep = (i: number) => setForm({ ...form, steps: form.steps.filter((_, j) => j !== i) });

  const save = async () => {
    const steps = form.steps.map((s) => s.trim()).filter(Boolean);
    if (!form.name.trim()) { setSaved({ error: "名称必填" }); return; }
    try {
      const r = await api.createAgentDraft({
        name: form.name, goal: form.goal, inputs_contract: form.inputs_contract,
        outputs_contract: form.outputs_contract, steps, scope: form.scope,
      });
      setSaved(r.data);
      loadDrafts();
    } catch (e: any) { setSaved({ error: e.message }); }
  };

  const publish = async (id: string) => {
    try {
      const r = await api.publishAgentDraft(id);
      setPubMsg(`已提交发布：${r.data.agent_id} → status=${r.data.status}（待审批单通过后才能调用，未 published 调用返回 403）。`);
      loadDrafts();
    } catch (e: any) { setPubMsg("发布失败：" + e.message); }
  };

  const preview = composePreview(form.name, form.goal, form.inputs_contract, form.outputs_contract, form.steps);

  return (
    <>
      <div className="card">
        <h3>L2 零代码建智能体（草稿仅本人可见，发布需管理员）</h3>
        <div className="field"><label>名称</label><input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="如：合同初审助手" /></div>
        <div className="field"><label>什么时候用（目标）</label><textarea rows={2} value={form.goal} onChange={(e) => setForm({ ...form, goal: e.target.value })} /></div>
        <div className="grid2">
          <div className="field"><label>输入契约</label><textarea rows={2} value={form.inputs_contract} onChange={(e) => setForm({ ...form, inputs_contract: e.target.value })} /></div>
          <div className="field"><label>输出契约</label><textarea rows={2} value={form.outputs_contract} onChange={(e) => setForm({ ...form, outputs_contract: e.target.value })} /></div>
        </div>
        <div className="field">
          <label>执行步骤</label>
          <div className="steps-edit">
            {form.steps.map((s, i) => (
              <div className="step-row" key={i}>
                <span className="hint" style={{ width: 18, textAlign: "right" }}>{i + 1}</span>
                <input value={s} placeholder="一个步骤一句话" onChange={(e) => setStep(i, e.target.value)} />
                <button className="btn ghost" onClick={() => delStep(i)} disabled={form.steps.length <= 1}>×</button>
              </div>
            ))}
            <button className="btn ghost" style={{ alignSelf: "flex-start" }} onClick={addStep}>＋ 添加步骤</button>
          </div>
        </div>
        <div className="field"><label>可见范围</label>
          <select value={form.scope} onChange={(e) => setForm({ ...form, scope: e.target.value })}>
            <option value="personal">personal 本人</option>
            <option value="department">department 部门</option>
            <option value="global">global 集团</option>
          </select>
        </div>
        <button className="btn blue" onClick={save}>保存为草稿</button>
        {saved && saved.error && <div className="err">{saved.error}</div>}
        {saved && !saved.error && (
          <div className="ok">已存草稿：{saved.agent_id} · status={saved.status}（仅本人可见，发布后进入待审）</div>
        )}
      </div>

      <div className="card">
        <h3>系统提示词预览（保存后由后端按硬规则合成）</h3>
        <div className="copybox" style={{ maxHeight: 260 }}>{preview}</div>
      </div>

      <div className="card">
        <h3>我的草稿（{drafts.length}）</h3>
        {drafts.length === 0 && <div className="hint">还没有草稿，上方保存后即可在此管理。</div>}
        <table className="tbl">
          <thead><tr><th>名称</th><th>范围</th><th>状态</th><th>创建时间</th><th></th></tr></thead>
          <tbody>
            {drafts.map((d) => (
              <tr key={d.agent_id}>
                <td>{d.name}</td>
                <td><span className="tag">{d.scope}</span></td>
                <td>{d.status}</td>
                <td className="mono">{d.created_iso}</td>
                <td>{isAdmin && d.status === "draft" && (
                  <button className="btn" onClick={() => publish(d.agent_id)}>发布</button>
                )}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {pubMsg && <div className="ok">{pubMsg}</div>}
      </div>
    </>
  );
}

/* ───────────────────────── L3 工作流画布 ───────────────────────── */

type WfType = "llm" | "rag" | "tool" | "approval";
type WfNode = { id: string; type: WfType; name: string; config: any; x: number; y: number };
type WfEdge = { from: string; to: string };

const NODE_META: Record<WfType, { label: string; cls: string; desc: string }> = {
  llm: { label: "LLM 生成", cls: "nt-llm", desc: "占位节点：仅汇总上下文，不调模型（dry_run），不冒充真实生成" },
  rag: { label: "知识检索", cls: "nt-rag", desc: "检索授权知识片段，严格带调用者身份过滤" },
  tool: { label: "工具调用", cls: "nt-tool", desc: "经统一权限校验调用工具；拒绝/不可用时不冒充执行" },
  approval: { label: "人工审批", cls: "nt-approval", desc: "停下等人工审核，不自动跳过后续节点" },
};

const W = 210, H = 94;

function nodeSummary(n: WfNode): string {
  if (n.type === "rag") return n.config.query || "检索授权知识片段";
  if (n.type === "tool") return (n.config.tool_name || "未命名工具") + (n.config.args && n.config.args !== "{}" ? " · 已带参数" : "");
  if (n.type === "approval") return n.config.note || "停下待人工审核";
  return "dry_run 占位（不调模型）";
}

function connectPath(a: WfNode, b: WfNode): string {
  const x1 = a.x + W / 2, y1 = a.y + H;
  const x2 = b.x + W / 2, y2 = b.y;
  const dy = Math.max(36, Math.abs(y2 - y1) / 2);
  return `M ${x1} ${y1} C ${x1} ${y1 + dy} ${x2} ${y2 - dy} ${x2} ${y2}`;
}

function cleanConfig(n: WfNode): any {
  if (n.type === "tool") {
    let args: any = {};
    try { args = n.config.args ? JSON.parse(n.config.args) : {}; } catch { args = {}; }
    return { tool_name: n.config.tool_name || "", args };
  }
  if (n.type === "rag") return { query: n.config.query || "" };
  if (n.type === "approval") return { note: n.config.note || "" };
  return {};
}

function WorkflowCanvas() {
  const [name, setName] = useState("未命名工作流");
  const [nodes, setNodes] = useState<WfNode[]>([]);
  const [edges, setEdges] = useState<WfEdge[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [savedId, setSavedId] = useState<string | null>(null);
  const [run, setRun] = useState<any>(null);
  const [msg, setMsg] = useState<string>("");
  const [wfList, setWfList] = useState<any[]>([]);
  const [runInput, setRunInput] = useState("");
  const [linkFrom, setLinkFrom] = useState<string | null>(null);

  const canvasRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ id: string; dx: number; dy: number } | null>(null);

  const loadList = () => api.listWorkflows().then((r) => setWfList(r.data.workflows || [])).catch(() => setWfList([]));
  useEffect(() => { loadList(); }, []);

  const addNode = (t: WfType) => {
    setNodes((ns) => {
      const last = ns[ns.length - 1];
      const y = last ? last.y + H + 30 : 30;
      const node: WfNode = {
        id: "n" + Math.random().toString(36).slice(2, 8), type: t,
        name: NODE_META[t].label, config: {}, x: 40, y,
      };
      return [...ns, node];
    });
  };

  const onNodeDown = (e: React.PointerEvent, n: WfNode) => {
    e.preventDefault();
    e.stopPropagation();
    const el = canvasRef.current!;
    const rect = el.getBoundingClientRect();
    dragRef.current = {
      id: n.id,
      dx: e.clientX - rect.left + el.scrollLeft - n.x,
      dy: e.clientY - rect.top + el.scrollTop - n.y,
    };
    setSel(n.id);
  };
  const onMove = (e: React.PointerEvent) => {
    const d = dragRef.current; if (!d) return;
    const el = canvasRef.current!;
    const rect = el.getBoundingClientRect();
    const nx = e.clientX - rect.left + el.scrollLeft - d.dx;
    const ny = e.clientY - rect.top + el.scrollTop - d.dy;
    setNodes((ns) => ns.map((n) => n.id === d.id ? { ...n, x: Math.max(0, nx), y: Math.max(0, ny) } : n));
  };
  const onUp = () => { dragRef.current = null; };

  const patchConfig = (patch: any) => {
    if (!sel) return;
    setNodes((ns) => ns.map((n) => n.id === sel ? { ...n, config: { ...n.config, ...patch } } : n));
  };
  const rename = (v: string) => {
    if (!sel) return;
    setNodes((ns) => ns.map((n) => n.id === sel ? { ...n, name: v } : n));
  };
  const moveOrder = (dir: -1 | 1) => {
    if (!sel) return;
    setNodes((ns) => {
      const i = ns.findIndex((n) => n.id === sel);
      const j = i + dir;
      if (i < 0 || j < 0 || j >= ns.length) return ns;
      const copy = [...ns];
      [copy[i], copy[j]] = [copy[j], copy[i]];
      return copy;
    });
  };
  const removeNode = () => {
    if (!sel) return;
    setNodes((ns) => ns.filter((n) => n.id !== sel));
    setEdges((es) => es.filter((e) => e.from !== sel && e.to !== sel));
    setSel(null);
  };

  // ── 连线（端口拖拽）──
  const onPortDown = (id: string, isOut: boolean) => {
    if (isOut) { setLinkFrom((cur) => (cur === id ? null : id)); return; }
    setLinkFrom((cur) => {
      if (cur && cur !== id) {
        setEdges((es) => es.some((e) => e.from === cur && e.to === id)
          ? es : [...es, { from: cur, to: id }]);
      }
      return null;
    });
  };
  const removeEdge = (i: number) => setEdges((es) => es.filter((_, j) => j !== i));

  const buildPayload = () => ({
    name,
    nodes: nodes.map((n) => ({
      id: n.id, type: n.type, name: n.name,
      config: cleanConfig(n), position: { x: n.x, y: n.y },
    })),
    edges: edges.map((e) => ({ from: e.from, to: e.to })),
  });

  const doSave = async (): Promise<string | null> => {
    if (nodes.length === 0) { setMsg("画布为空，先添加节点"); return null; }
    try {
      const payload = buildPayload();
      if (savedId) {
        await api.updateWorkflow(savedId, payload);
        setMsg(`已更新工作流 ${savedId}（${name}）：布局、连线与节点一并保存。`);
      } else {
        const r = await api.createWorkflow(payload);
        setSavedId(r.data.id);
        setMsg(`已保存工作流：${r.data.id}（${r.data.name}），状态=${r.data.status}。`);
      }
      loadList();
      return savedId ?? payload.name;
    } catch (e: any) { setMsg("保存失败：" + e.message); return null; }
  };

  const doRun = async () => {
    setRun(null);
    let id = savedId;
    if (!id) { id = await doSave(); }
    if (!id) return;
    try {
      const r = await api.runWorkflow(id, runInput);
      setRun(r.data);
      setMsg(`运行完成：status=${r.data.status} · dry_run=${r.data.dry_run} · 步骤=${r.data.steps.length}`);
    } catch (e: any) { setMsg("运行失败：" + e.message); }
  };

  const loadWf = async (wid: string) => {
    try {
      const r = await api.getWorkflow(wid);
      const w = r.data;
      setName(w.name);
      setNodes((w.nodes || []).map((n: any) => ({
        id: n.id, type: n.type, name: n.name,
        config: n.config || {},
        x: n.position?.x ?? 40, y: n.position?.y ?? 30,
      })));
      setEdges((w.edges || []).map((e: any) => ({ from: e.from || e.source, to: e.to || e.target })));
      setSavedId(wid);
      setSel(null); setRun(null); setLinkFrom(null);
      setMsg(`已载入工作流 ${wid}（${w.nodes?.length || 0} 节点 · ${w.edges?.length || 0} 连线）。`);
    } catch (e: any) { setMsg("载入失败：" + e.message); }
  };

  const selNode = nodes.find((n) => n.id === sel) || null;
  const maxX = Math.max(600, ...nodes.map((n) => n.x + W), ...edges.map((e) => {
    const a = nodes.find((n) => n.id === e.from); return a ? a.x + W : 0;
  }));
  const maxY = Math.max(400, ...nodes.map((n) => n.y + H));

  return (
    <>
      <div className="card">
        <h3>L3 工作流编排画布（节点式 DAG，拖拽摆位）</h3>
        <div className="hint" style={{ marginBottom: 12 }}>
          点下方按钮添加节点 → 拖动卡片摆位 → 点卡片改配置 → 从节点底部<b>蓝点</b>拖到另一节点顶部<b>白点</b>连线（点连线可删除）。
          保存后布局与连线一并持久化，再载入会原样恢复。注意：当前为 dry_run 框架，rag/tool 真实行为由后端按身份过滤，不冒充执行。
        </div>
        {linkFrom && (
          <div className="hint" style={{ color: "var(--blue)", marginBottom: 8 }}>
            连线中：点目标节点顶部的输入端口完成；再次点同一输出端口取消。
          </div>
        )}
        <div className="wf-palette">
          <input style={{ width: 200, padding: "8px 11px", border: "1px solid var(--line)", borderRadius: 9, fontSize: 13, fontFamily: "inherit", background: "#fff", color: "var(--ink)" }} value={name}
            onChange={(e) => setName(e.target.value)} placeholder="工作流名称" />
          {(Object.keys(NODE_META) as WfType[]).map((t) => (
            <button key={t} className={"btn ghost"} onClick={() => addNode(t)}>
              ＋ {NODE_META[t].label}
            </button>
          ))}
          <span className="grow" />
          <button className="btn blue" onClick={doSave}>保存工作流</button>
          <button className="btn" onClick={doRun}>保存并运行</button>
        </div>

        <div className="wf-wrap">
          <div
            className="wf-canvas"
            ref={canvasRef}
            onPointerMove={onMove}
            onPointerUp={onUp}
            onPointerLeave={onUp}
          >
            <svg style={{ position: "absolute", left: 0, top: 0, width: maxX, height: maxY, pointerEvents: "none" }}>
              {edges.map((e, i) => {
                const a = nodes.find((n) => n.id === e.from);
                const b = nodes.find((n) => n.id === e.to);
                if (!a || !b) return null;
                return (
                  <path key={i} d={connectPath(a, b)} fill="none"
                    stroke={linkFrom === e.from ? "#1a5fd0" : "#b9b9b0"} strokeWidth={2}
                    style={{ pointerEvents: "visibleStroke", cursor: "pointer" }}
                    onClick={() => removeEdge(i)}>
                    <title>点击删除连线</title>
                  </path>
                );
              })}
            </svg>
            {nodes.length === 0 && <div className="wf-empty">画布为空 · 点上方「＋ 节点」开始编排</div>}
            {nodes.map((n) => (
              <div
                key={n.id}
                className={"wf-node" + (sel === n.id ? " sel" : "")}
                style={{ left: n.x, top: n.y }}
                onPointerDown={(e) => onNodeDown(e, n)}
              >
                <div className={"wf-port in" + (linkFrom && linkFrom !== n.id ? " arm" : "")}
                     onPointerDown={(e) => { e.stopPropagation(); onPortDown(n.id, false); }}
                     title="输入端口（连线终点）" />
                <div className={"wf-port out" + (linkFrom === n.id ? " on" : "")}
                     onPointerDown={(e) => { e.stopPropagation(); onPortDown(n.id, true); }}
                     title="输出端口（连线起点）" />
                <div className="nh">
                  <span className={"nt " + NODE_META[n.type].cls}>{NODE_META[n.type].label}</span>
                  <span className="nm">{n.name}</span>
                </div>
                <div className="nc">{nodeSummary(n)}</div>
              </div>
            ))}
          </div>

          {selNode && (
            <div className="wf-cfg">
              <h4>配置节点</h4>
              <div className="hint" style={{ marginBottom: 10 }}>{NODE_META[selNode.type].desc}</div>
              <div className="field"><label>名称</label>
                <input value={selNode.name} onChange={(e) => rename(e.target.value)} /></div>
              {selNode.type === "rag" && (
                <div className="field"><label>检索查询</label>
                  <textarea rows={3} value={selNode.config.query || ""} onChange={(e) => patchConfig({ query: e.target.value })} /></div>
              )}
              {selNode.type === "tool" && (
                <>
                  <div className="field"><label>工具名</label>
                    <input value={selNode.config.tool_name || ""} onChange={(e) => patchConfig({ tool_name: e.target.value })} /></div>
                  <div className="field"><label>参数（JSON）</label>
                    <textarea rows={3} value={selNode.config.args || "{}"} onChange={(e) => patchConfig({ args: e.target.value })} placeholder='{"key":"value"}' /></div>
                </>
              )}
              {selNode.type === "approval" && (
                <div className="field"><label>审核说明</label>
                  <textarea rows={3} value={selNode.config.note || ""} onChange={(e) => patchConfig({ note: e.target.value })} /></div>
              )}
              {selNode.type === "llm" && (
                <div className="hint">无需配置：运行到该节点时仅汇总前面各节点的输出上下文（dry_run）。</div>
              )}
              <div className="row" style={{ marginTop: 8 }}>
                <button className="btn ghost" onClick={() => moveOrder(-1)}>↑ 上移</button>
                <button className="btn ghost" onClick={() => moveOrder(1)}>↓ 下移</button>
                <button className="btn ghost" onClick={removeNode} style={{ color: "var(--err)" }}>删除</button>
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="card">
        <h3>运行输入与已有工作流</h3>
        <div className="field"><label>运行输入（传给第一个节点的 input）</label>
          <textarea rows={2} value={runInput} onChange={(e) => setRunInput(e.target.value)} placeholder="如：帮我查一下出差报销标准" /></div>
        <div className="field"><label>载入已有工作流</label>
          <select value="" onChange={(e) => e.target.value && loadWf(e.target.value)}>
            <option value="">— 选择 —</option>
            {wfList.map((w) => <option key={w.id} value={w.id}>{w.name}（{w.id} · {(w.nodes || []).length} 节点）</option>)}
          </select>
        </div>
        {msg && <div className="ok">{msg}</div>}

        {run && (
          <>
            <div className="hint" style={{ marginTop: 6 }}>
              运行结果：status=<b>{run.status}</b> · dry_run={String(run.dry_run)} · 上下文键={run.context_keys?.join(", ")}
            </div>
            <div className="run-steps">
              {run.steps.map((s: any, i: number) => (
                <div key={i} className={"run-step " + (s.ok ? "ok" : "bad")}>
                  <div className="rs-h">
                    <span className={"nt " + NODE_META[s.type as WfType]?.cls || "nt-llm"}>{NODE_META[s.type as WfType]?.label || s.type}</span>
                    <span>{s.name}</span>
                    <span className="tag">{s.ok ? "通过" : "异常"}</span>
                  </div>
                  <div className="mono" style={{ whiteSpace: "pre-wrap", maxHeight: 180, overflow: "auto" }}>
                    {JSON.stringify(s.output, null, 2)}
                  </div>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </>
  );
}
