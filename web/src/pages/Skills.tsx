import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { getSession } from "../auth/session";
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
  // M24：技能契约（什么时候用 / 要给什么 / 会拿到什么）。公共库有，业务包可能没有。
  when_to_use?: string;
  inputs_contract?: string;
  outputs_contract?: string;
};

type Cat = {
  category: string;
  category_cn: string;
  count: number;
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
  // func = 显示职能（财务/销售），岗位视图用；industry = 显示行业（制造业/办公角色），行业视图用
  kind?: "func" | "industry";
}) {
  const clickable = a.status === "published";
  const isPub = !!(a as any).public || (a as any).category_cn === "公共库";
  // 岗位视图下不要再显示「办公角色 / 财务」——岗位已经在页面顶部选中了，重复且会让人以为还有别的维度。
  // 公共库同理，改显示它属于哪一组（写作/演示/表格…），比再写一遍"公共库"有用。
  // 岗位视图里挂行业标签（制造业/跨境电商）是噪音：我在财务岗，为什么要知道这个包原属制造业？
  const tags = (isPub || kind === "func")
    ? [a.sub_function || ""].filter(Boolean)
    : (a as any).category_cn ? [(a as any).category_cn] : (a.tags || []);
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

export function SkillsPage({ onUse }: { onUse?: (a: Agent) => void }) {
  const [cats, setCats] = useState<Cat[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [pub, setPub] = useState<Agent[]>([]);
  const [total, setTotal] = useState(0);
  // ?mode=industry 直落行业视图（给培训/演示一条链接就能到指定视图）
  const [mode, setMode] = useState<"role" | "public" | "industry">(
    () => {
      const m = new URLSearchParams(window.location.search).get("mode");
      return m === "industry" || m === "public" ? m : "role";
    },
  );
  const [roleId, setRoleId] = useRole();
  const [picking, setPicking] = useState(false);
  const [activeCat, setActiveCat] = useState("全部");
  const [activeSub, setActiveSub] = useState("全部");
  const [q, setQ] = useState("");
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);

  // 行业视角是给老板/售前看全公司资产的，普通员工默认没有这个视图：
  // 岗位是边界，不是筛选器——员工不该顺手点开别人的 115 个包。
  const isAdmin = (getSession()?.user?.role || "") === "platform_admin";

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
    !q || `${a.name}${a.description || ""}${a.sub_function || ""}${a.when_to_use || ""}`
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

  // 岗位页底部的公共库分组：按 写作/演示/表格/沟通与会议/阅读/方法与沉淀 归堆
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

  const tabs: [typeof mode, string][] = [
    ["role", "我的岗位"],
    ["public", "公共库"],
    ...(isAdmin ? [["industry", "按行业"] as [typeof mode, string]] : []),
  ];

  return (
    <>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, fontWeight: 600, margin: "0 0 6px", letterSpacing: "-.4px" }}>
          技能库
          <span style={{ fontSize: 13, fontWeight: 400, color: "var(--muted)", marginLeft: 8 }}>
            {cur ? `${cur.name}岗 ${cur.count} 个` : "还没选岗位"} · 公共库 {pub.length} 个
          </span>
        </h1>
        <div className="hint">
          每张卡片都是一套完整的执行手册。你只看得到本岗位和公共库——别的岗位不在这里，避免挑花眼。
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
            不需要选岗位，也不会占用业务技能包名额。搜自己的话就行（如"签合同前""请个假"）；
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
              公共库还没预置——重启服务会自动补齐
            </div>
          )}
        </>
      )}

      {/* ── 按行业（仅管理员） ──────────────────────────────────────── */}
      {mode === "industry" && isAdmin && (
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
