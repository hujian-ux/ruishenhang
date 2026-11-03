import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";

type Agent = {
  agent_id: string;
  name: string;
  status: string;
  adapter_type: string;
  description?: string;
  tags?: string[];
};

type Cat = {
  category: string;
  category_cn: string;
  count: number;
  groups: { sub_function: string; count: number; agents: Agent[] }[];
};

const ADAPTER_LABEL: Record<string, string> = {
  llm: "LLM",
  llm_retrieval: "RAG",
  rule: "RULE",
  ml_serving: "ML",
  ml_llm_retrieval: "HYBRID",
};

const STATUS_LABEL: Record<string, [string, string]> = {
  published: ["已发布", "#1b7f3b"],
  pending_review: ["待评审", "#b26a00"],
  rejected: ["已驳回", "#c62828"],
};

export function SkillsPage() {
  const [cats, setCats] = useState<Cat[]>([]);
  const [total, setTotal] = useState(0);
  const [activeCat, setActiveCat] = useState("全部");
  const [activeSub, setActiveSub] = useState("全部");
  const [q, setQ] = useState("");
  const [picked, setPicked] = useState<Agent | null>(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);

  // 即时调用
  const [task, setTask] = useState("");
  const [answer, setAnswer] = useState<any>(null);
  const [running, setRunning] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const r = await api.getAgentCatalog();
      setCats(r.data.categories || []);
      setTotal(r.data.total || 0);
      setErr("");
    } catch (e: any) {
      setErr("目录加载失败：" + e.message);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); }, []);

  const subs = useMemo(() => {
    const c = cats.find((x) => x.category_cn === activeCat);
    if (!c) return [];
    return c.groups.map((g) => g.sub_function).filter(Boolean);
  }, [cats, activeCat]);

  const list = useMemo(() => {
    const out: Agent[] = [];
    for (const c of cats) {
      if (activeCat !== "全部" && c.category_cn !== activeCat) continue;
      for (const g of c.groups) {
        if (activeSub !== "全部" && g.sub_function !== activeSub) continue;
        for (const a of g.agents) {
          if (q && !(`${a.name}${a.description || ""}`.toLowerCase().includes(q.toLowerCase()))) continue;
          out.push(a);
        }
      }
    }
    return out;
  }, [cats, activeCat, activeSub, q]);

  const run = async () => {
    if (!picked || !task.trim()) return;
    setRunning(true);
    setAnswer(null);
    try {
      const r = await api.invokeAgent(picked.agent_id, task, undefined);
      setAnswer(r.data);
    } catch (e: any) {
      setAnswer({ error: e.message });
    } finally {
      setRunning(false);
    }
  };

  const pubCount = list.filter((a) => a.status === "published").length;

  return (
    <>
      <div className="card">
        <h3>技能包目录（M15 · 融合 {total} 个业务智能体）</h3>
        <div className="hint" style={{ marginBottom: 10 }}>
          每个技能包都是一套「输入契约 → 工作流程 → 产出契约 → 硬规则」的执行手册。
          未发布（待评审）的技能包不能调用 —— 这是第35章红线，批量导入也不放宽。
        </div>
        <div className="row" style={{ flexWrap: "wrap", gap: 6, marginBottom: 8 }}>
          {["全部", ...cats.map((c) => c.category_cn)].map((c) => (
            <div key={c} className={"seg " + (activeCat === c ? "on" : "")}
              onClick={() => { setActiveCat(c); setActiveSub("全部"); }}>
              {c}{c !== "全部" && ` ${cats.find((x) => x.category_cn === c)?.count ?? 0}`}
            </div>
          ))}
        </div>
        {subs.length > 0 && (
          <div className="row" style={{ flexWrap: "wrap", gap: 6, marginBottom: 8 }}>
            {["全部", ...subs].map((s) => (
              <div key={s} className={"seg " + (activeSub === s ? "on" : "")} onClick={() => setActiveSub(s)}>{s}</div>
            ))}
          </div>
        )}
        <div className="field" style={{ marginBottom: 8 }}>
          <input placeholder="搜索技能包名称或描述…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <div className="hint">当前筛选 {list.length} 个 · 其中已发布 {pubCount} 个</div>
        {err && <div className="hint" style={{ color: "#c62828" }}>{err}</div>}
        {loading && <div className="hint">加载中…</div>}
      </div>

      {picked && (
        <div className="card">
          <h3>使用：{picked.name}
            <span className="tag" style={{ marginLeft: 8 }}>{ADAPTER_LABEL[picked.adapter_type] || picked.adapter_type}</span>
          </h3>
          <div className="hint" style={{ marginBottom: 8 }}>
            状态：{STATUS_LABEL[picked.status]?.[0] || picked.status}
            {picked.status !== "published" && " —— 需先在「审批台」通过评审才可调用"}
          </div>
          <div className="field">
            <label>本次任务输入（技能包会按自己的输入契约逐项核对，缺什么会明确列出来）</label>
            <textarea rows={5} value={task} onChange={(e) => setTask(e.target.value)}
              placeholder="把原始数据/情况贴进来，例如：五条线本月与上月数字、目标值、异常项…" />
          </div>
          <div className="row">
            <button className="btn" disabled={running || !task.trim() || picked.status !== "published"} onClick={run}>
              {running ? "执行中…" : "按技能包流程执行"}
            </button>
            <button className="btn ghost" onClick={() => { setPicked(null); setAnswer(null); setTask(""); }}>收起</button>
          </div>
          {answer?.error && <div className="hint" style={{ color: "#c62828", marginTop: 8 }}>{answer.error}</div>}
          {answer && !answer.error && (
            <div style={{ marginTop: 10 }}>
              <div className="hint">
                技能包已生效：{answer.agent?.skill_applied ? "是" : "否（该智能体未绑定提示词）"}
                {" · "}adapter={answer.agent?.adapter_type}
                {" · "}tokens={answer.usage?.total_tokens} · ¥{answer.usage?.cost}
              </div>
              <pre style={{ whiteSpace: "pre-wrap", fontSize: 13, lineHeight: 1.7, marginTop: 6 }}>{answer.answer}</pre>
            </div>
          )}
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: 12 }}>
        {list.map((a) => {
          const [st, color] = STATUS_LABEL[a.status] || [a.status, "#555"];
          return (
            <div key={a.agent_id} className="card" style={{ margin: 0, cursor: "pointer" }}
              onClick={() => { setPicked(a); setAnswer(null); setTask(""); }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
                <b style={{ fontSize: 14 }}>{a.name}</b>
                <span className="tag">{ADAPTER_LABEL[a.adapter_type] || a.adapter_type}</span>
              </div>
              <div className="hint" style={{ marginTop: 6, display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
                {a.description || "（无描述）"}
              </div>
              <div style={{ marginTop: 8, fontSize: 11.5, color }}>● {st}</div>
            </div>
          );
        })}
      </div>
      {!loading && list.length === 0 && <div className="card"><div className="hint">没有匹配的技能包。</div></div>}
    </>
  );
}
