import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";

/** P1-9 · 子代理 / 专家执行台（#304）。
 *  把 203 业务包 + 28 公共库当作可被委派的「专家」：单人委派 / 批量并发委派 /
 *  执行台账回看。委派是一次无状态推理，不写主会话 turns、不落产出物。
 *  模型通道不可用时后端如实降级（ok=false），这里如实展示而不是伪造结论。 */
type Any = Record<string, any>;

const small: React.CSSProperties = { fontSize: 12, color: "var(--muted)" };
const box: React.CSSProperties = { border: "1px solid var(--line)", borderRadius: 8, padding: 14, background: "var(--card)" };
const input: React.CSSProperties = { width: "100%", boxSizing: "border-box" };

const STATUS: Record<string, { label: string; color: string }> = {
  ok: { label: "成功", color: "var(--ok)" },
  unavailable: { label: "模型不可用", color: "var(--warn)" },
  denied: { label: "被拒", color: "var(--err)" },
  error: { label: "异常", color: "var(--err)" },
};

function statusTag(s: string) {
  const m = STATUS[s] || { label: s || "—", color: "var(--muted)" };
  return <span className="tag" style={{ color: m.color, borderColor: m.color }}>{m.label}</span>;
}

function ts(v: number) {
  if (!v) return "—";
  return new Date(v * 1000).toLocaleString("zh-CN", { hour12: false });
}

export function ExpertsPage() {
  const [err, setErr] = useState("");
  const [stats, setStats] = useState<Any>({});
  const [experts, setExperts] = useState<any[]>([]);
  const [runs, setRuns] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);

  // 委派表单
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [task, setTask] = useState("");
  const [context, setContext] = useState("");
  const [result, setResult] = useState<Any | null>(null);
  const [detail, setDetail] = useState<Any | null>(null);

  const load = useCallback(async () => {
    setLoading(true); setErr("");
    try {
      const qs = new URLSearchParams();
      if (q) qs.set("q", q);
      if (kind) qs.set("kind", kind);
      const [s, e, r] = await Promise.all([
        api.getExpertStats(),
        api.getExperts({ q, kind, limit: 200 }),
        api.getExpertRuns({ limit: 50 }),
      ]);
      setStats(s.data || {});
      setExperts(e.data?.items || []);
      setRuns(r.data?.items || []);
    } catch (ex: any) { setErr(ex.message); }
    setLoading(false);
  }, [q, kind]);

  useEffect(() => {
    const t = setTimeout(load, 250);
    return () => clearTimeout(t);
  }, [load]);

  const toggle = (id: string) => {
    setSelected((prev) => prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]);
  };

  const runDispatch = async () => {
    if (!selected.length || !task.trim()) { setErr("请至少选一个专家并填写子任务"); return; }
    setBusy(true); setErr(""); setResult(null);
    try {
      if (selected.length === 1) {
        const r = await api.dispatchExpert({
          agent_id: selected[0], task: task.trim(), context: context.trim(),
        });
        setResult({ mode: "single", item: r.data, items: [r.data] });
      } else {
        const r = await api.dispatchExpertBatch({
          tasks: selected.map((id) => ({ agent_id: id, task: task.trim() })),
          context: context.trim(),
        });
        setResult({ mode: "batch", ...r.data });
      }
      await load();
    } catch (ex: any) { setErr(ex.message); }
    setBusy(false);
  };

  const openRun = async (runId: string) => {
    setBusy(true); setErr("");
    try {
      const r = await api.getExpertRun(runId);
      setDetail(r.data || {});
    } catch (ex: any) { setErr(ex.message); }
    setBusy(false);
  };

  const items = result?.items || [];
  const nameOf = (id: string) => experts.find((e: Any) => e.agent_id === id)?.name || id;

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div>
          <h2 style={{ margin: 0 }}>子代理 / 专家执行台 · P1-9</h2>
          <div style={small}>把技能包当专家委派子任务：独立上下文、不写主会话、不落产出物；模型不可用如实降级，不伪造结论。</div>
        </div>
        <button className="btn" onClick={load}>刷新</button>
      </div>
      {err && <div className="card" style={{ borderColor: "#e57373", color: "#b03a3a", marginBottom: 12 }}>{err}</div>}

      {/* ── 看板 KPI ── */}
      <div className="grid4">
        <div className="kpi"><span>累计委派</span><b>{stats.total ?? 0}</b><i>按当前身份可见</i></div>
        <div className="kpi"><span>成功</span><b style={{ color: "var(--ok)" }}>{stats.ok ?? 0}</b><i>真模型率 {Math.round((stats.real_rate ?? 0) * 100)}%</i></div>
        <div className="kpi"><span>降级 / 异常</span><b style={{ color: "var(--warn)" }}>{(stats.unavailable ?? 0) + (stats.error ?? 0)}</b><i>被拒 {stats.denied ?? 0}</i></div>
        <div className="kpi"><span>平均耗时</span><b>{stats.avg_latency_ms ?? 0}<span style={{ fontSize: 13 }}> ms</span></b><i>token {(stats.usage?.prompt_tokens ?? 0) + (stats.usage?.completion_tokens ?? 0)}</i></div>
      </div>

      {/* ── 委派区 ── */}
      <div className="card" style={{ marginTop: 14 }}>
        <h3>① 委派子任务</h3>
        <div className="row" style={{ alignItems: "center", marginBottom: 10 }}>
          <input className="field" placeholder="搜索专家名称/描述/子职能"
            value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 240, margin: 0 }} />
          <button className={"seg" + (kind === "" ? " on" : "")} onClick={() => setKind("")}>全部</button>
          <button className={"seg" + (kind === "business" ? " on" : "")} onClick={() => setKind("business")}>业务包</button>
          <button className={"seg" + (kind === "public" ? " on" : "")} onClick={() => setKind("public")}>公共库</button>
          <span style={{ marginLeft: "auto", ...small }}>
            {loading ? "加载中…" : `可见专家 ${experts.length} 个 · 已选 ${selected.length} 个`}
          </span>
        </div>

        {/* 专家多选卡片 */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(210px, 1fr))", gap: 8, maxHeight: 210, overflowY: "auto", marginBottom: 12 }}>
          {experts.map((e: Any) => {
            const on = selected.includes(e.agent_id);
            return (
              <div key={e.agent_id} onClick={() => toggle(e.agent_id)}
                style={{ ...box, cursor: "pointer", padding: 10,
                         borderColor: on ? "var(--blue)" : "var(--line)",
                         background: on ? "rgba(47,110,235,.06)" : "var(--card)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 6 }}>
                  <b style={{ fontSize: 13, color: "var(--ink)" }}>{e.name}</b>
                  <span className="tag">{e.kind === "public" ? "公共库" : "业务包"}</span>
                </div>
                <div style={{ ...small, marginTop: 4, minHeight: 30 }}>
                  {e.description || "（无描述）"}
                </div>
                <div style={{ ...small, marginTop: 4 }}>
                  {e.role_name ? `${e.role_name} · ` : ""}{e.category_cn || "—"} · 人设 {e.persona_len} 字
                </div>
              </div>
            );
          })}
          {!experts.length && <div style={small}>无匹配专家</div>}
        </div>

        <div className="grid2">
          <div>
            <div style={{ ...small, marginBottom: 4 }}>子任务（必填）</div>
            <textarea className="field" rows={3} style={input} placeholder="例如：基于 6 月经营数据做量价分离与异常归因，给出三条可执行结论"
              value={task} onChange={(e) => setTask(e.target.value)} />
          </div>
          <div>
            <div style={{ ...small, marginBottom: 4 }}>上游上下文（可选：主任务背景 / 前序结论）</div>
            <textarea className="field" rows={3} style={input} placeholder="专家会把它与自身人设一并参考，缺料会明确标注"
              value={context} onChange={(e) => setContext(e.target.value)} />
          </div>
        </div>
        <div className="row" style={{ marginTop: 10, alignItems: "center" }}>
          <button className="btn primary" disabled={busy || !selected.length || !task.trim()} onClick={runDispatch}>
            {busy ? "执行中…" : selected.length > 1 ? `并发委派 ${selected.length} 个专家` : "委派执行"}
          </button>
          <span style={small}>选 1 个走单次委派，选多个走 batch 并发（上限 6 个）</span>
        </div>
      </div>

      {/* ── 本次结果 ── */}
      {result && (
        <div className="card" style={{ marginTop: 14, borderColor: "var(--blue)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h3 style={{ margin: 0 }}>
              ② 本次结果 · {result.mode === "batch" ? `并发 ${result.total} 个（成功 ${result.ok_count}）` : "单次委派"}
            </h3>
            <button className="btn" onClick={() => setResult(null)}>收起</button>
          </div>
          {result.mode === "batch" && (
            <div style={small}>
              batch_id {result.batch_id} · 耗时 {result.latency_ms} ms · 真模型 {result.real_count}/{result.total}
            </div>
          )}
          <div style={{ marginTop: 10, display: "grid", gap: 10 }}>
            {items.map((it: Any, i: number) => (
              <div key={it.run_id || i} style={box}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <b style={{ color: "var(--ink)" }}>{it.agent_name || nameOf(it.agent_id) || `专家 ${i + 1}`}</b>
                  <span>{statusTag(it.status)}{it.real === false && <span style={{ ...small, marginLeft: 6 }}>非真模型</span>}</span>
                </div>
                <div style={{ ...small, marginTop: 4 }}>{it.task}</div>
                {it.ok ? (
                  <div style={{ marginTop: 8, whiteSpace: "pre-wrap", fontSize: 13, lineHeight: 1.7 }}>{it.answer}</div>
                ) : (
                  <div style={{ marginTop: 8, color: "var(--err)", fontSize: 13 }}>
                    未产出结论：{it.error || "模型通道不可用"}（未配置模型密钥时如实降级，不会伪造专家结论）
                  </div>
                )}
                <div style={{ ...small, marginTop: 8 }}>
                  {it.model_id || "—"} · {it.latency_ms ?? 0} ms · run_id {it.run_id || "—"}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── 执行台账 ── */}
      <div className="card" style={{ marginTop: 14 }}>
        <h3>③ 执行台账（最近 50 条）</h3>
        <div style={{ overflowX: "auto" }}>
          <table className="tbl">
            <thead>
              <tr><th>时间</th><th>专家</th><th>子任务</th><th>状态</th><th>模型</th><th>耗时</th><th></th></tr>
            </thead>
            <tbody>
              {runs.map((r: Any) => (
                <tr key={r.run_id}>
                  <td style={small}>{ts(r.created_at)}</td>
                  <td><b style={{ color: "var(--ink)" }}>{r.agent_name || r.agent_id}</b>
                    <div style={small}>{r.source === "tool" ? "工具委派" : r.source === "batch" ? "并发委派" : "API 委派"}</div></td>
                  <td style={{ maxWidth: 320, ...small }}>{r.task}</td>
                  <td>{statusTag(r.status)}</td>
                  <td style={small}>{r.model_id || "—"}</td>
                  <td style={small}>{r.latency_ms} ms</td>
                  <td><button className="btn ghost" disabled={busy} onClick={() => openRun(r.run_id)}>明细</button></td>
                </tr>
              ))}
              {!runs.length && <tr><td colSpan={7} style={small}>暂无执行记录。选一个专家并填写子任务后点「委派执行」。</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── 明细抽屉 ── */}
      {detail && (
        <div className="card" style={{ marginTop: 14, borderColor: "var(--blue)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h3 style={{ margin: 0 }}>执行明细 · {detail.agent_name}</h3>
            <button className="btn" onClick={() => setDetail(null)}>关闭</button>
          </div>
          <div className="grid4" style={{ marginTop: 12 }}>
            <div className="kpi"><span>状态</span><b style={{ color: STATUS[detail.status]?.color }}>{STATUS[detail.status]?.label || detail.status}</b><i>真模型 {detail.real ? "是" : "否"}</i></div>
            <div className="kpi"><span>人设长度</span><b>{detail.persona_len}</b><i>上下文 {detail.context_len} 字</i></div>
            <div className="kpi"><span>结论长度</span><b>{detail.answer_len}</b><i>深度 {detail.depth} · {detail.source}</i></div>
            <div className="kpi"><span>耗时 / token</span><b>{detail.latency_ms}<span style={{ fontSize: 13 }}> ms</span></b>
              <i>{(detail.usage?.prompt_tokens || 0) + (detail.usage?.completion_tokens || 0)} tokens</i></div>
          </div>
          <div style={{ ...small, marginTop: 10 }}>子任务：{detail.task}</div>
          {detail.error && <div style={{ color: "var(--err)", marginTop: 8, fontSize: 13 }}>错误：{detail.error}</div>}
          <div style={{ ...box, marginTop: 10, whiteSpace: "pre-wrap", fontSize: 13, lineHeight: 1.7 }}>
            {detail.answer || "（无结论：模型通道不可用或未产出）"}
          </div>
        </div>
      )}
    </div>
  );
}
