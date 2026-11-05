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
  llm: "生成",
  llm_retrieval: "查制度",
  rule: "规则",
  ml_serving: "模型",
  ml_llm_retrieval: "混合",
};

export function SkillsPage({ onUse }: { onUse?: (a: Agent) => void }) {
  const [cats, setCats] = useState<Cat[]>([]);
  const [total, setTotal] = useState(0);
  const [activeCat, setActiveCat] = useState("全部");
  const [activeSub, setActiveSub] = useState("全部");
  const [q, setQ] = useState("");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getAgentCatalog()
      .then((r) => {
        setCats(r.data.categories || []);
        setTotal(r.data.total || 0);
        setErr("");
      })
      .catch((e: any) => setErr("技能库加载失败：" + e.message))
      .finally(() => setLoading(false));
  }, []);

  const subs = useMemo(() => {
    const c = cats.find((x) => x.category_cn === activeCat);
    return c ? c.groups.map((g) => g.sub_function).filter(Boolean) : [];
  }, [cats, activeCat]);

  const list = useMemo(() => {
    const out: Agent[] = [];
    for (const c of cats) {
      if (activeCat !== "全部" && c.category_cn !== activeCat) continue;
      for (const g of c.groups) {
        if (activeSub !== "全部" && g.sub_function !== activeSub) continue;
        for (const a of g.agents) {
          if (q && !`${a.name}${a.description || ""}`.toLowerCase().includes(q.toLowerCase())) continue;
          out.push({ ...a, tags: [c.category_cn, g.sub_function].filter(Boolean) });
        }
      }
    }
    return out;
  }, [cats, activeCat, activeSub, q]);

  return (
    <>
      <div style={{ marginBottom: 18 }}>
        <h1 style={{ fontSize: 22, fontWeight: 600, margin: "0 0 6px", letterSpacing: "-.4px" }}>
          技能库
          <span style={{ fontSize: 13, fontWeight: 400, color: "var(--muted)", marginLeft: 8 }}>
            {total} 个业务技能包
          </span>
        </h1>
        <div className="hint">每张卡片都是一套完整的执行手册：输入契约 → 工作流程 → 产出契约 → 硬规则。点开即用。</div>
      </div>

      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginBottom: 12 }}>
        <input
          placeholder="搜索技能包…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          style={{ flex: "1 1 240px", padding: "9px 12px", border: "1px solid var(--line)",
                   borderRadius: 10, fontSize: 13, outline: "none", fontFamily: "inherit" }}
        />
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {["全部", ...cats.map((c) => c.category_cn)].map((c) => (
            <div key={c} className={"seg" + (activeCat === c ? " on" : "")}
                 style={{ flex: "0 0 auto", padding: "7px 13px" }}
                 onClick={() => { setActiveCat(c); setActiveSub("全部"); }}>
              {c}{c !== "全部" && <span style={{ opacity: .6, marginLeft: 4 }}>
                {cats.find((x) => x.category_cn === c)?.count ?? 0}</span>}
            </div>
          ))}
        </div>
      </div>

      {subs.length > 0 && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 12 }}>
          {["全部", ...subs].map((s) => (
            <div key={s} className={"seg" + (activeSub === s ? " on" : "")}
                 style={{ flex: "0 0 auto", padding: "5px 11px", fontSize: 12 }}
                 onClick={() => setActiveSub(s)}>{s}</div>
          ))}
        </div>
      )}

      {err && <div className="err">{err}</div>}
      {loading && <div className="hint">加载中…</div>}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(268px,1fr))", gap: 10 }}>
        {list.map((a) => (
          <div key={a.agent_id}
               onClick={() => a.status === "published" && onUse?.(a)}
               style={{
                 border: "1px solid var(--line)", borderRadius: 14, padding: "14px 16px", background: "#fff",
                 cursor: a.status === "published" ? "pointer" : "not-allowed",
                 opacity: a.status === "published" ? 1 : .55, transition: ".16s",
                 display: "flex", flexDirection: "column", gap: 6,
               }}
               onMouseEnter={(e) => {
                 if (a.status !== "published") return;
                 e.currentTarget.style.borderColor = "#cfcfc8";
                 e.currentTarget.style.boxShadow = "0 1px 2px rgba(27,27,25,.04),0 8px 24px rgba(27,27,25,.05)";
                 e.currentTarget.style.transform = "translateY(-1px)";
               }}
               onMouseLeave={(e) => {
                 e.currentTarget.style.borderColor = "var(--line)";
                 e.currentTarget.style.boxShadow = "none";
                 e.currentTarget.style.transform = "none";
               }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "flex-start" }}>
              <b style={{ fontSize: 13.5, fontWeight: 550, lineHeight: 1.45 }}>{a.name}</b>
              <span className="tag">{ADAPTER_LABEL[a.adapter_type] || a.adapter_type}</span>
            </div>
            <div style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.6,
                          display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
                          overflow: "hidden", minHeight: 38 }}>
              {a.description || "（暂无描述）"}
            </div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 2 }}>
              {(a.tags || []).map((t) => (
                <span key={t} style={{ fontSize: 10.5, color: "var(--muted)", background: "#f6f6f2",
                                       borderRadius: 5, padding: "1px 6px" }}>{t}</span>
              ))}
              {a.status !== "published" && (
                <span style={{ fontSize: 10.5, color: "var(--warn)", background: "#fffbf0",
                               borderRadius: 5, padding: "1px 6px" }}>待评审</span>
              )}
            </div>
          </div>
        ))}
      </div>

      {!loading && list.length === 0 && (
        <div style={{ textAlign: "center", padding: "50px 0", color: "var(--muted)", fontSize: 13 }}>
          没有匹配的技能包
        </div>
      )}
    </>
  );
}
