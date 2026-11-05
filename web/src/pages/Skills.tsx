import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { useRole } from "../role";

type Agent = {
  agent_id: string;
  name: string;
  status: string;
  adapter_type: string;
  description?: string;
  tags?: string[];
  core?: boolean;
};

type Cat = {
  category: string;
  category_cn: string;
  count: number;
  groups: { sub_function: string; count: number; agents: Agent[] }[];
};

type Role = {
  id: string;
  name: string;
  initial: string;
  desc: string;
  color: string;
  count: number;
  core_count: number;
  agents: Agent[];
};

const ADAPTER_LABEL: Record<string, string> = {
  llm: "生成",
  llm_retrieval: "查制度",
  rule: "规则",
  ml_serving: "模型",
  ml_llm_retrieval: "混合",
};

function SkillCard({ a, onUse, highlight }: { a: Agent; onUse?: (a: Agent) => void; highlight?: boolean }) {
  const clickable = a.status === "published";
  // 岗位视图下不要再显示「办公角色 / 财务」——岗位已经在页面顶部选中了，重复且会让人以为还有别的维度
  const tags = (a as any).category_cn ? [(a as any).category_cn] : (a.tags || []);
  return (
    <div
      onClick={() => clickable && onUse?.(a)}
      style={{
        border: highlight ? "1px solid #cfcfc8" : "1px solid var(--line)",
        borderRadius: 14, padding: "14px 16px",
        background: highlight ? "#fcfcfa" : "#fff",
        cursor: clickable ? "pointer" : "not-allowed",
        opacity: clickable ? 1 : .55, transition: ".16s",
        display: "flex", flexDirection: "column", gap: 6,
      }}
      onMouseEnter={(e) => {
        if (!clickable) return;
        e.currentTarget.style.borderColor = "#cfcfc8";
        e.currentTarget.style.boxShadow = "0 1px 2px rgba(27,27,25,.04),0 8px 24px rgba(27,27,25,.05)";
        e.currentTarget.style.transform = "translateY(-1px)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderColor = highlight ? "#cfcfc8" : "var(--line)";
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
        {tags.slice(0, 3).map((t: string) => (
          <span key={t} style={{ fontSize: 10.5, color: "var(--muted)", background: "#f6f6f2",
                                 borderRadius: 5, padding: "1px 6px" }}>{t}</span>
        ))}
        {a.status !== "published" && (
          <span style={{ fontSize: 10.5, color: "var(--warn)", background: "#fffbf0",
                         borderRadius: 5, padding: "1px 6px" }}>待评审</span>
        )}
      </div>
    </div>
  );
}

const GRID = { display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(268px,1fr))", gap: 10 };
// 开箱即用区固定成 2 列（420px 下限，窄屏自动落 1 列）：4 个核心包正好 2×2，
// 比 3+1 的残行整齐；卡片也更大，视觉上把"先配这几个"和下面的"其他 13 个"分开。
const GRID_CORE = { display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(420px,1fr))", gap: 10 };

export function SkillsPage({ onUse }: { onUse?: (a: Agent) => void }) {
  const [cats, setCats] = useState<Cat[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [total, setTotal] = useState(0);
  // ?mode=industry 直落行业视图（给培训/演示一条链接就能到指定视图）
  const [mode, setMode] = useState<"role" | "industry">(
    () => (new URLSearchParams(window.location.search).get("mode") === "industry" ? "industry" : "role"),
  );
  const [roleId, setRoleId] = useRole();
  const [activeCat, setActiveCat] = useState("全部");
  const [activeSub, setActiveSub] = useState("全部");
  const [q, setQ] = useState("");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getAgentCatalog()
      .then((r) => {
        setCats(r.data.categories || []);
        setRoles(r.data.roles || []);
        setTotal(r.data.total || 0);
        setErr("");
      })
      .catch((e: any) => setErr("技能库加载失败：" + e.message))
      .finally(() => setLoading(false));
  }, []);

  // 没选过岗位就落在第一个（财务/人事这类通用岗），避免进来是空白
  const cur = useMemo(
    () => roles.find((r) => r.id === roleId) || roles[0],
    [roles, roleId],
  );

  const match = (a: Agent) =>
    !q || `${a.name}${a.description || ""}`.toLowerCase().includes(q.trim().toLowerCase());

  const roleCore = useMemo(() => (cur?.agents || []).filter((a) => a.core && match(a)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [cur, q]);
  const roleRest = useMemo(() => (cur?.agents || []).filter((a) => !a.core && match(a)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [cur, q]);

  const subs = useMemo(() => {
    const c = cats.find((x) => x.category_cn === activeCat);
    return c ? c.groups.map((g) => g.sub_function).filter(Boolean) : [];
  }, [cats, activeCat]);

  const industryList = useMemo(() => {
    const out: Agent[] = [];
    for (const c of cats) {
      if (activeCat !== "全部" && c.category_cn !== activeCat) continue;
      for (const g of c.groups) {
        if (activeSub !== "全部" && g.sub_function !== activeSub) continue;
        for (const a of g.agents) {
          if (!match(a)) continue;
          out.push({ ...a, tags: [c.category_cn, g.sub_function].filter(Boolean) });
        }
      }
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cats, activeCat, activeSub, q]);

  return (
    <>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, fontWeight: 600, margin: "0 0 6px", letterSpacing: "-.4px" }}>
          技能库
          <span style={{ fontSize: 13, fontWeight: 400, color: "var(--muted)", marginLeft: 8 }}>
            {total} 个业务技能包 · {roles.length} 个岗位
          </span>
        </h1>
        <div className="hint">每张卡片都是一套完整的执行手册：输入契约 → 工作流程 → 产出契约 → 硬规则。点开即用。</div>
      </div>

      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginBottom: 14 }}>
        <div style={{ display: "inline-flex", border: "1px solid var(--line)", borderRadius: 9,
                      overflow: "hidden", background: "#fff" }}>
          {([["role", "按岗位"], ["industry", "按行业"]] as const).map(([k, label]) => (
            <div key={k} onClick={() => setMode(k)}
                 style={{ padding: "7px 14px", fontSize: 12.5, cursor: "pointer",
                          background: mode === k ? "var(--ink)" : "#fff",
                          color: mode === k ? "#fff" : "var(--ink-2)" }}>
              {label}
            </div>
          ))}
        </div>
        <input
          placeholder="搜索技能包…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          style={{ flex: "1 1 220px", padding: "8px 12px", border: "1px solid var(--line)",
                   borderRadius: 9, fontSize: 13, outline: "none", fontFamily: "inherit" }}
        />
      </div>

      {err && <div className="err">{err}</div>}
      {loading && <div className="hint">加载中…</div>}

      {/* 空态必须说人话：技能库空白时用户会以为是系统坏了，
          实际多半是包没导入 / 没审批发布 / 账号不在可见范围。 */}
      {!loading && !err && total === 0 && (
        <div style={{ border: "1px solid var(--line)", borderRadius: 14, padding: "34px 24px",
                      background: "#fcfcfa", textAlign: "center" }}>
          <div style={{ fontSize: 14, fontWeight: 550, color: "var(--ink)", marginBottom: 6 }}>
            当前身份看不到技能包
          </div>
          <div className="hint" style={{ maxWidth: 460, margin: "0 auto", lineHeight: 1.75 }}>
            常见三种原因：技能包还没导入；导入了但未在「审批台」通过；或者你的账号不在包的可见范围内。
            找管理员确认这三点即可。
          </div>
        </div>
      )}

      {mode === "role" && total > 0 && (
        <>
          {/* 岗位横排：11 个一屏看全，带数量，方便判断哪个岗装得多 */}
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 18 }}>
            {roles.map((r) => {
              const on = cur?.id === r.id;
              return (
                <div key={r.id} onClick={() => setRoleId(r.id)}
                     title={r.desc}
                     style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "5px 11px 5px 5px",
                              borderRadius: 999, cursor: "pointer", fontSize: 12.5,
                              border: `1px solid ${on ? "var(--ink)" : "var(--line)"}`,
                              background: on ? "var(--ink)" : "#fff",
                              color: on ? "#fff" : "var(--ink-2)" }}>
                  <span className="role-ini" style={{ background: on ? "rgba(255,255,255,.22)" : r.color }}>
                    {r.initial}
                  </span>
                  {r.name}
                  <span style={{ fontSize: 11, opacity: .62, marginLeft: 1 }}>{r.count}</span>
                </div>
              );
            })}
          </div>

          {cur && (
            <>
              <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 10 }}>
                <b style={{ fontSize: 14, fontWeight: 600 }}>{cur.name}岗 · 开箱即用</b>
                <span className="hint" style={{ fontSize: 12 }}>{cur.desc}</span>
                <span className="grow" style={{ flex: 1 }} />
                <span className="hint" style={{ fontSize: 12 }}>{cur.core_count} 个默认配置</span>
              </div>
              <div style={GRID_CORE}>
                {roleCore.map((a) => <SkillCard key={a.agent_id} a={a} onUse={onUse} highlight />)}
              </div>

              {roleRest.length > 0 && (
                <>
                  <div style={{ display: "flex", alignItems: "baseline", gap: 8, margin: "22px 0 10px" }}>
                    <b style={{ fontSize: 13.5, fontWeight: 550, color: "var(--ink-2)" }}>
                      {cur.name}岗 · 其他 {roleRest.length} 个
                    </b>
                    <span className="hint" style={{ fontSize: 12 }}>用到再点，不必先配</span>
                  </div>
                  <div style={GRID}>
                    {roleRest.map((a) => <SkillCard key={a.agent_id} a={a} onUse={onUse} />)}
                  </div>
                </>
              )}

              {roleCore.length + roleRest.length === 0 && (
                <div style={{ textAlign: "center", padding: "40px 0", color: "var(--muted)", fontSize: 13 }}>
                  没有匹配的技能包
                </div>
              )}
            </>
          )}
        </>
      )}

      {mode === "industry" && (
        <>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 12 }}>
            {["全部", ...cats.map((c) => c.category_cn)].map((c) => (
              <div key={c} className={"seg" + (activeCat === c ? " on" : "")}
                   style={{ flex: "0 0 auto", padding: "7px 13px" }}
                   onClick={() => { setActiveCat(c); setActiveSub("全部"); }}>
                {c}{c !== "全部" && <span style={{ opacity: .6, marginLeft: 4 }}>
                  {cats.find((x) => x.category_cn === c)?.count ?? 0}</span>}
              </div>
            ))}
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

          <div style={GRID}>
            {industryList.map((a) => <SkillCard key={a.agent_id} a={a} onUse={onUse} />)}
          </div>

          {!loading && industryList.length === 0 && (
            <div style={{ textAlign: "center", padding: "50px 0", color: "var(--muted)", fontSize: 13 }}>
              没有匹配的技能包
            </div>
          )}
        </>
      )}
    </>
  );
}
