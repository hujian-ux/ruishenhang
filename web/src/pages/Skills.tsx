import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { RoleChip, RoleGrid, type Role as RoleBase } from "../components/RolePicker";
import { useRole } from "../role";

type Agent = {
  agent_id: string;
  name: string;
  status: string;
  adapter_type: string;
  description?: string;
  tags?: string[];
  core?: boolean;
  sub_function?: string;
  default_format?: string;
  role_primary?: string;
  role_name?: string;
  category_cn?: string;
  // M24：技能契约（什么时候用 / 要给什么 / 会拿到什么）。公共库有，业务包可能没有。
  when_to_use?: string;
  inputs_contract?: string;
  outputs_contract?: string;
};

// 行业下的岗位节点（后端 categories[].roles）
type RoleNode = {
  id: string;
  name: string;
  initial: string;
  color?: string;
  note?: string;
  count: number;
  core_count: number;
  agents: Agent[];
};

type Cat = {
  category: string;
  category_cn: string;
  name?: string;
  note?: string;
  universal?: boolean;
  count: number;
  roles?: RoleNode[];
  groups: { sub_function: string; count: number; agents: Agent[] }[];
};

// 岗位节点比选择器里的 Role 多一截：带本岗全部包，技能库直接渲染
type Role = RoleBase & { count: number; core_count: number; agents: Agent[] };

const ADAPTER_LABEL: Record<string, string> = {
  llm: "生成",
  llm_retrieval: "查制度",
  llm_web_search: "联网",
  llm_web_search_rag: "内外双查",
  rule: "规则",
  ml_serving: "模型",
  ml_llm_retrieval: "混合",
};

const FORMAT_LABEL: Record<string, string> = {
  docx: "Word", pptx: "PPT", xlsx: "Excel", pdf: "PDF", html: "网页",
};

// 岗位页底部只放 6 张卡：优先「每天都要用」的四件套，再补总结与待办。
// 不按 sort 取前 6——那样会全是写作组，表格/纪要反而看不到。
const PUB_QUICK = ["pub_notice", "pub_ppt", "pub_sheet", "pub_minutes", "pub_summary", "pub_followup"];

// 公共库每组一句话，说明这组能干什么（23 条不分层会让人不知道从哪看）
const PUB_GROUP_NOTE: Record<string, string> = {
  "写作": "通知公告、制度办法、总结述职、方案、请示",
  "演示": "汇报 PPT、周报月报、内训课件",
  "表格": "规范台账、数据速览、比价选型、项目排期、上传已有表再清洗合并",
  "沟通与会议": "邮件消息、会议纪要、润色翻译、待办跟进、会前准备",
  "阅读": "合同审阅、文件提字段、长文速读、制度查证（带出处）",
  "方法与沉淀": "SOP 手册、复盘、语音转结构化记录",
  "联网查证": "带链接查资料、竞品对照、内部制度与公开信息交叉核对",
};

function SkillCard({ a, onUse, highlight, kind }: {
  a: Agent; onUse?: (a: Agent) => void; highlight?: boolean;
  // func = 岗位视图（不显示行业标签）；industry = 行业视图（显示岗位名）
  kind?: "func" | "industry";
}) {
  const clickable = a.status === "published";
  const isPub = !!(a as any).public || (a as any).category_cn === "公共库";
  // 已经按行业/岗位筛过一遍了，卡片上再写一遍"制造业"是噪音：
  // 公共库显示它属于哪一组（写作/演示/表格…），行业视图显示岗位名，岗位视图不显示。
  const tags = isPub
    ? [a.sub_function || ""].filter(Boolean)
    : kind === "industry"
      ? [a.role_name || a.role_primary || ""].filter(Boolean)
      : kind === "func"
        ? []
        : [a.category_cn || ""].filter(Boolean);
  return (
    <div
      onClick={() => clickable && onUse?.(a)}
      // 卡片不塞第四行文字：契约信息放在悬停里，需要的人一眼能查到，
      // 不需要的人看到的还是原来的密度（M23 定下的「重可读性轻装饰」）。
      title={[
        a.when_to_use && `什么时候用：${a.when_to_use}`,
        a.inputs_contract && `要给什么：${a.inputs_contract}`,
        a.outputs_contract && `会拿到：${a.outputs_contract}`,
      ].filter(Boolean).join("\n") || undefined}
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
        {a.default_format && (
          <span style={{ fontSize: 10.5, color: "var(--blue)", background: "var(--blue-l)",
                         borderRadius: 5, padding: "1px 6px" }}>
            出 {FORMAT_LABEL[a.default_format] || a.default_format}
          </span>
        )}
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

function Section({ title, note, right }: { title: string; note?: string; right?: string }) {
  return (
    <div style={{ display: "flex", alignItems: "baseline", gap: 8, margin: "22px 0 10px" }}>
      <b style={{ fontSize: 13.5, fontWeight: 550, color: "var(--ink-2)" }}>{title}</b>
      {note && <span className="hint" style={{ fontSize: 12 }}>{note}</span>}
      <span style={{ flex: 1 }} />
      {right && <span className="hint" style={{ fontSize: 12 }}>{right}</span>}
    </div>
  );
}

// 行业选择卡：先选行业，再看这个行业下面有哪些岗位
function IndustryCard({ c, onPick }: { c: Cat; onPick: () => void }) {
  const roles = (c.roles || []).filter((r) => r.count > 0);
  return (
    <div
      onClick={onPick}
      data-testid={`industry-${c.category_cn}`}
      style={{
        border: "1px solid var(--line)", borderRadius: 14, padding: "16px 18px",
        background: "#fff", cursor: "pointer", transition: ".16s", minWidth: 0,
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.borderColor = "#cfcfc8";
        e.currentTarget.style.boxShadow = "0 1px 2px rgba(27,27,25,.04),0 8px 24px rgba(27,27,25,.05)";
        e.currentTarget.style.transform = "translateY(-1px)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderColor = "var(--line)";
        e.currentTarget.style.boxShadow = "none";
        e.currentTarget.style.transform = "none";
      }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <b style={{ fontSize: 15, fontWeight: 600 }}>{c.name || c.category_cn}</b>
        <span className="hint" style={{ fontSize: 12 }}>{c.count} 个技能</span>
        <span style={{ flex: 1 }} />
        {c.universal && (
          <span style={{ fontSize: 10.5, color: "var(--blue)", background: "var(--blue-l)",
                         borderRadius: 5, padding: "1px 6px" }}>任何行业可用</span>
        )}
      </div>
      {c.note && (
        <div className="hint" style={{ fontSize: 12, marginTop: 4, lineHeight: 1.6 }}>{c.note}</div>
      )}
      <div style={{ display: "flex", gap: 5, flexWrap: "wrap", marginTop: 9 }}>
        {roles.map((r) => (
          <span key={r.id} style={{ fontSize: 11, color: "var(--ink-2)", background: "#f6f6f2",
                                    borderRadius: 6, padding: "2px 8px" }}>
            {r.name} {r.count}
          </span>
        ))}
        {roles.length === 0 && (
          <span style={{ fontSize: 11, color: "var(--muted)" }}>暂无岗位分组</span>
        )}
      </div>
    </div>
  );
}

export function SkillsPage({ onUse }: { onUse?: (a: Agent) => void }) {
  const [cats, setCats] = useState<Cat[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [pub, setPub] = useState<Agent[]>([]);
  const [total, setTotal] = useState(0);
  // ?mode=industry 直落行业视图（给培训/演示一条链接就能到指定视图）
  const [mode, setMode] = useState<"industry" | "role" | "public">(
    () => {
      const m = new URLSearchParams(window.location.search).get("mode");
      return m === "public" || m === "role" ? m : "industry";
    },
  );
  const [roleId, setRoleId] = useRole();
  const [picking, setPicking] = useState(false);
  // 行业视图两级：先选行业（catName），再选岗位（roleKey）。都空 = 还没开始选。
  const [catName, setCatName] = useState("");
  const [roleKey, setRoleKey] = useState("");
  const [q, setQ] = useState("");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.getAgentCatalog()
      .then((r) => {
        setCats(r.data.categories || []);
        setRoles(r.data.roles || []);
        setPub(r.data.public || []);
        setTotal(r.data.total || 0);
        setErr("");
      })
      .catch((e: any) => setErr("技能库加载失败：" + e.message))
      .finally(() => setLoading(false));
  }, []);

  // 没选岗位时不默认落到某个岗（那等于替用户做了决定，还会让他看到别人的包）
  const cur = useMemo(() => roles.find((r) => r.id === roleId), [roles, roleId]);
  const needPick = !cur;

  // 搜索要能命中「什么时候用」：用户搜的是自己的话（"请个假""签合同前"），
  // 不是技能名。M24 之前只搜 name/description，用户得先猜我们怎么命名。
  const match = (a: Agent) =>
    !q || `${a.name}${a.description || ""}${a.sub_function || ""}${a.role_name || ""}${a.when_to_use || ""}`
      .toLowerCase().includes(q.trim().toLowerCase());

  const roleCore = useMemo(() => (cur?.agents || []).filter((a) => a.core && match(a)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [cur, q]);
  const roleRest = useMemo(() => (cur?.agents || []).filter((a) => !a.core && match(a)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [cur, q]);

  const pubList = useMemo(() => pub.filter(match),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [pub, q]);

  // 公共库分组：按 写作/演示/表格/沟通与会议/阅读/方法与沉淀 归堆
  const pubGroups = useMemo(() => {
    const g: Record<string, Agent[]> = {};
    for (const a of pub) (g[a.sub_function || "其他"] ||= []).push(a);
    return g;
  }, [pub]);

  // 岗位页底部露出的 6 条：四件套优先，缺哪条就用后面的补上
  const pubQuick = useMemo(() => {
    const byId = new Map(pub.map((a) => [a.agent_id, a]));
    const head = PUB_QUICK.map((k) => byId.get("ag_" + k)).filter(Boolean) as Agent[];
    const used = new Set(head.map((a) => a.agent_id));
    const tail = pub.filter((a) => !used.has(a.agent_id));
    return [...head, ...tail].slice(0, 6);
  }, [pub]);

  // ── 行业视图 ────────────────────────────────────────────────
  const curCat = useMemo(() => cats.find((c) => c.category_cn === catName), [cats, catName]);
  const curRoles = useMemo(
    () => (curCat?.roles || []).filter((r) => r.count > 0), [curCat]);

  // 行业视图下的卡片：选了岗位就只看该岗，没选就按岗位分组全摊（带「岗位」小标题）
  const industryGroups = useMemo(() => {
    if (!curCat) return [];
    const list = roleKey
      ? (curCat.roles || []).filter((r) => r.id === roleKey)
      : (curCat.roles || []);
    return list
      .map((r) => ({ role: r, agents: r.agents.filter(match) }))
      .filter((g) => g.agents.length > 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [curCat, roleKey, q]);

  const industryShown = industryGroups.reduce((n, g) => n + g.agents.length, 0);

  const tabs: [typeof mode, string][] = [
    ["industry", "按行业"],
    ["role", "我的岗位"],
    ["public", "公共库"],
  ];

  const backToIndustries = () => { setCatName(""); setRoleKey(""); };

  return (
    <>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, fontWeight: 600, margin: "0 0 6px", letterSpacing: "-.4px" }}>
          技能库
          <span style={{ fontSize: 13, fontWeight: 400, color: "var(--muted)", marginLeft: 8 }}>
            {cats.length} 个行业 · {total} 个业务技能包 · 公共库 {pub.length} 个
          </span>
        </h1>
        <div className="hint">
          先选行业，再看这个行业下面有哪些岗位的技能。公共库不分行业，任何行业都能用。
        </div>
      </div>

      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginBottom: 14 }}>
        <div style={{ display: "inline-flex", border: "1px solid var(--line)", borderRadius: 9,
                      overflow: "hidden", background: "#fff" }}>
          {tabs.map(([k, label]) => (
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
        <div style={{ border: "1px solid var(--line)", borderRadius: 14, padding: "30px 24px",
                      background: "#fcfcfa", textAlign: "center", marginBottom: 16 }}>
          <div style={{ fontSize: 14, fontWeight: 550, color: "var(--ink)", marginBottom: 6 }}>
            当前身份看不到业务技能包
          </div>
          <div className="hint" style={{ maxWidth: 480, margin: "0 auto", lineHeight: 1.75 }}>
            常见三种原因：技能包还没导入；导入了但未在「审批台」通过；或者你的账号不在包的可见范围内。
            公共库不受影响，下面是平台自带的通用能力。
          </div>
        </div>
      )}

      {/* ── 按行业（默认视图）：行业 → 岗位 → 技能包 ─────────────────── */}
      {mode === "industry" && (
        <>
          {!curCat ? (
            <>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(320px,1fr))", gap: 10 }}>
                {cats.map((c) => (
                  <IndustryCard key={c.category_cn} c={c}
                                onPick={() => { setCatName(c.category_cn); setRoleKey(""); }} />
                ))}
                {/* 公共库挂成最后一张行业卡：它不属于任何行业，但每张行业卡下面都能进 */}
                <div
                  onClick={() => setMode("public")}
                  style={{ border: "1px dashed #d8d8d0", borderRadius: 14, padding: "16px 18px",
                           background: "#fcfcfa", cursor: "pointer", transition: ".16s" }}
                  onMouseEnter={(e) => { e.currentTarget.style.borderColor = "#bfbfb6"; }}
                  onMouseLeave={(e) => { e.currentTarget.style.borderColor = "#d8d8d0"; }}>
                  <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
                    <b style={{ fontSize: 15, fontWeight: 600 }}>公共库</b>
                    <span className="hint" style={{ fontSize: 12 }}>{pub.length} 个办公基础技能</span>
                  </div>
                  <div className="hint" style={{ fontSize: 12, marginTop: 4, lineHeight: 1.6 }}>
                    所有行业都能用：写材料、做汇报、弄表格、沟通开会、看文件、沉淀方法。
                  </div>
                </div>
              </div>
              {!loading && cats.length === 0 && (
                <div className="hint" style={{ marginTop: 14 }}>
                  还没有行业目录——通常是技能包还没导入，公共库不受影响。
                </div>
              )}
            </>
          ) : (
            <>
              {/* 面包屑：行业不是死路，一行就能退回去换行业 */}
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 10 }}>
                <span onClick={backToIndustries}
                      style={{ fontSize: 12.5, color: "var(--muted)", cursor: "pointer" }}>
                  ← 全部行业
                </span>
                <span style={{ color: "var(--line)" }}>/</span>
                <b style={{ fontSize: 14, fontWeight: 550 }}>{curCat.name || curCat.category_cn}</b>
                <span className="hint" style={{ fontSize: 12 }}>{curCat.count} 个技能</span>
                {curCat.universal && (
                  <span style={{ fontSize: 10.5, color: "var(--blue)", background: "var(--blue-l)",
                                 borderRadius: 5, padding: "1px 6px" }}>任何行业可用</span>
                )}
              </div>

              {/* 第二层：这个行业下的岗位。数量直接摊在标签上，用户不用一个个点开试。 */}
              {curRoles.length > 0 && (
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 12 }}>
                  <div className={"seg" + (roleKey === "" ? " on" : "")}
                       style={{ flex: "0 0 auto", padding: "6px 12px", fontSize: 12.5 }}
                       onClick={() => setRoleKey("")}>
                    全部岗位<span style={{ opacity: .6, marginLeft: 4 }}>{curCat.count}</span>
                  </div>
                  {curRoles.map((r) => (
                    <div key={r.id} className={"seg" + (roleKey === r.id ? " on" : "")}
                         data-testid={`role-${r.id}`}
                         title={r.note}
                         style={{ flex: "0 0 auto", padding: "6px 12px", fontSize: 12.5,
                                  display: "inline-flex", alignItems: "center", gap: 5 }}
                         onClick={() => setRoleKey(r.id)}>
                      <span style={{ width: 16, height: 16, borderRadius: 5, fontSize: 10,
                                     display: "inline-flex", alignItems: "center", justifyContent: "center",
                                     background: roleKey === r.id ? "rgba(255,255,255,.25)" : (r.color || "var(--ink)"),
                                     color: "#fff" }}>{r.initial}</span>
                      {r.name}
                      <span style={{ opacity: .6 }}>{r.count}</span>
                      {roleId === r.id && (
                        <span style={{ fontSize: 9.5, opacity: .8 }}>我的岗位</span>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {industryGroups.map(({ role, agents }) => (
                <div key={role.id}>
                  {!roleKey && (
                    <Section title={`${role.name} · ${agents.length} 个`} note={role.note} />
                  )}
                  <div style={GRID}>
                    {agents.map((a) => (
                      // 已经按岗位筛过了，卡片上再挂"品质工程"是重复信息：
                      // 只有"全部岗位"平铺时才需要靠岗位标签区分。
                      <SkillCard key={a.agent_id} a={a} onUse={onUse} kind={roleKey ? "func" : "industry"} />
                    ))}
                  </div>
                </div>
              ))}

              {/* 行业没有包 / 搜索没命中：给出下一步，而不是一片空白 */}
              {!loading && industryShown === 0 && (
                <div style={{ border: "1px solid var(--line)", borderRadius: 14, padding: "28px 24px",
                              background: "#fcfcfa", textAlign: "center" }}>
                  <div style={{ fontSize: 13.5, fontWeight: 550, marginBottom: 6 }}>
                    {q ? "这个行业下没有搜到匹配的技能包" : "这个行业还没有专属技能包"}
                  </div>
                  <div className="hint" style={{ maxWidth: 460, margin: "0 auto 12px", lineHeight: 1.75 }}>
                    {q ? "换个说法试试，或者清空搜索看全部。"
                       : "先用公共库顶上：写材料、做汇报、弄表格这些活不分行业，@公共库 里都有。"}
                  </div>
                  <span className="w-all" style={{ display: "inline-block" }} onClick={() => setMode("public")}>
                    去公共库看看 →
                  </span>
                </div>
              )}

              {/* 行业页底部也给公共库入口：挑行业的过程中随时能切到通用能力 */}
              <Section title={`公共库 · ${pub.length} 个`} note="不分行业，任何行业都能用"
                       right="在「公共库」页看全部" />
              <div style={GRID}>
                {pubQuick.map((a) => <SkillCard key={a.agent_id} a={a} onUse={onUse} />)}
              </div>
            </>
          )}
        </>
      )}

      {/* ── 我的岗位 ───────────────────────────────────────────────── */}
      {mode === "role" && (
        <>
          {needPick ? (
            <div className="role-pick" style={{ marginTop: 4 }}>
              <h4>先认一下你的岗位</h4>
              <div className="hint">
                选完这里只显示该岗位的技能包，加上公共库的办公基础技能。不确定就随便选，随时能换。
              </div>
              <RoleGrid roles={roles} value={roleId} onChange={(id) => setRoleId(id)} />
              {roles.length === 0 && !loading && (
                <div className="hint">还没有可选岗位——通常是技能包还没导入。</div>
              )}
              {/* 没选岗位不该是死路：公共库不依赖岗位，直接给入口 */}
              {pub.length > 0 && (
                <div className="w-all" style={{ marginTop: 14 }} onClick={() => setMode("public")}>
                  不急着选，先用公共库的文案、PPT、表格 →
                </div>
              )}
            </div>
          ) : (
            <>
              <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginBottom: 4 }}>
                <RoleChip role={cur!} onClick={() => setPicking((v) => !v)} title="点击换岗位" />
                <span className="hint" style={{ fontSize: 12 }}>{cur!.desc}</span>
              </div>

              {picking && (
                <div style={{ margin: "10px 0 6px" }}>
                  <RoleGrid roles={roles} value={roleId}
                            onChange={(id) => { setRoleId(id); setPicking(false); }} />
                </div>
              )}

              <Section title={`${cur!.name}岗 · 开箱即用`} note="不用挑，直接开工"
                       right={`${cur!.core_count ?? roleCore.length} 个默认配置`} />
              <div style={GRID_CORE}>
                {roleCore.map((a) => <SkillCard key={a.agent_id} a={a} onUse={onUse} highlight kind="func" />)}
              </div>

              {roleRest.length > 0 && (
                <>
                  <Section title={`${cur!.name}岗 · 其他 ${roleRest.length} 个`} note="用到再点，不必先配" />
                  <div style={GRID}>
                    {roleRest.map((a) => <SkillCard key={a.agent_id} a={a} onUse={onUse} kind="func" />)}
                  </div>
                </>
              )}

              {roleCore.length + roleRest.length === 0 && (
                <div style={{ textAlign: "center", padding: "30px 0", color: "var(--muted)", fontSize: 13 }}>
                  该岗位没有匹配的技能包
                </div>
              )}

              {/* 岗位页也要能看见公共库：文案/PPT/表格是每天都要用的，
                  让人为了写个通知先切 tab，是没必要的摩擦。 */}
              <Section title={`公共库 · ${pub.length} 个办公基础技能`}
                       note="跟岗位无关，谁都能用"
                       right="在「公共库」页看全部" />
              <div style={GRID}>
                {pubQuick.map((a) => <SkillCard key={a.agent_id} a={a} onUse={onUse} />)}
              </div>
            </>
          )}
        </>
      )}

      {/* ── 公共库 ─────────────────────────────────────────────────── */}
      {mode === "public" && (
        <>
          <div className="hint" style={{ marginBottom: 4 }}>
            平台自带的办公基础能力：写材料、做汇报、弄表格、沟通开会、看文件、沉淀方法。
            不分行业、不用选岗位，也不会占用业务技能包名额。搜自己的话就行（如"签合同前""请个假"）；
            鼠标停在卡片上可看「什么时候用 / 要给什么 / 会拿到什么」。
          </div>
          {Object.entries(pubGroups).map(([g, list]) => {
            const shown = list.filter(match);
            if (!shown.length) return null;
            return (
              <div key={g}>
                <Section title={g}
                         note={[`${shown.length} 个`, PUB_GROUP_NOTE[g]].filter(Boolean).join(" · ")} />
                <div style={GRID}>
                  {shown.map((a) => <SkillCard key={a.agent_id} a={a} onUse={onUse} />)}
                </div>
              </div>
            );
          })}
          {!loading && pubList.length === 0 && (
            <div style={{ textAlign: "center", padding: "40px 0", color: "var(--muted)", fontSize: 13 }}>
              {q ? "公共库里没有搜到匹配的技能，换个说法试试"
                 : "公共库还没预置——重启服务会自动补齐"}
            </div>
          )}
        </>
      )}
    </>
  );
}
