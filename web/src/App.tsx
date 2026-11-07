import { useCallback, useEffect, useState } from "react";
import { api } from "./api/client";
import { STYLE } from "./theme";
import { setSession, type Session } from "./auth/session";
import { AgentsPage } from "./pages/Agents";
import { ChatPage, type ChatSeed } from "./pages/Chat";
import { ApprovalPage } from "./pages/Approval";
import { UsagePage } from "./pages/Usage";
import { KnowledgePage } from "./pages/Knowledge";
import { SessionsPage } from "./pages/Sessions";
import { ModelsPage } from "./pages/Models";
import { PilotPage } from "./pages/Pilot";
import { SkillsPage } from "./pages/Skills";
import { PlatformOpsPage } from "./pages/PlatformOps";
import { TenantConfigPage } from "./pages/TenantConfig";
import { IdentityPage } from "./pages/Identity";
import { BuilderPage } from "./pages/Builder";
import { useRole } from "./role";

type View = "chat" | "skills" | "admin" | "builder";

const ADMIN_TABS: { k: string; label: string }[] = [
  { k: "approval", label: "审批台" },
  { k: "agents", label: "智能体注册" },
  { k: "sessions", label: "会话管理" },
  { k: "kb", label: "知识库" },
  { k: "usage", label: "用量成本" },
  { k: "pilot", label: "试点看板" },
  { k: "models", label: "模型管理" },
  { k: "retrieve", label: "召回演示" },
  { k: "platform", label: "平台运营台" },
  { k: "identity", label: "身份与集成" },
  { k: "tenant", label: "租户配置" },
];

function RetrievePage() {
  const [q, setQ] = useState("员工出差住宿标准");
  const [res, setRes] = useState<any>(null);
  const go = async () => {
    try { setRes(await api.retrieve(q)); } catch (e: any) { setRes({ error: e.message }); }
  };
  return (
    <div className="card">
      <h3>带权召回（越权内容不进上下文）</h3>
      <div className="field"><label>查询</label><input value={q} onChange={(e) => setQ(e.target.value)} /></div>
      <button className="btn" onClick={go}>召回</button>
      {res && !res.error && (
        <div style={{ marginTop: 12 }}>
          <div className="hint">可见 {res.data.hits.length} 条 · 被过滤 {res.data.filtered_out} 条</div>
          {res.data.hits.map((h: any) => (
            <div key={h.doc_id} style={{ padding: "6px 0", fontSize: 13 }}>
              <b>[{h.scope}]</b> {h.title} · 分数 {h.score}
            </div>
          ))}
        </div>
      )}
      {res && res.error && <div className="err">{res.error}</div>}
    </div>
  );
}

function dayKey(ts: number) {
  const d = new Date(ts * 1000);
  const now = new Date();
  const same = d.toDateString() === now.toDateString();
  if (same) return "今天";
  const y = new Date(now.getTime() - 86400000);
  if (d.toDateString() === y.toDateString()) return "昨天";
  return "更早";
}

export function App({ session, onLogout }: { session: Session; onLogout: () => void }) {
  // 支持 ?view=skills|admin 直达（客户培训时给一个链接就能落在指定页）
  const [view, setView] = useState<View>(() => {
    const v = new URLSearchParams(window.location.search).get("view");
    return v === "skills" || v === "admin" ? v : "chat";
  });
  const [adminTab, setAdminTab] = useState(
    () => new URLSearchParams(window.location.search).get("tab") || "approval",
  );
  const [sessions, setSessions] = useState<any[]>([]);
  const [seed, setSeed] = useState<ChatSeed>({});
  const [chatKey, setChatKey] = useState(0);
  const [agentName, setAgentName] = useState("");
  const [menu, setMenu] = useState(false);
  const [gateway, setGateway] = useState<any>(null);
  const [railOpen, setRailOpen] = useState(false);
  const [roleId] = useRole();
  const [roleList, setRoleList] = useState<any[]>([]);
  // 公共库条数：侧栏"技能库"显示的是本岗 + 公共库，不是全公司 115
  const [pubCount, setPubCount] = useState(0);

  const isAdmin = session.user.role === "platform_admin" || session.user.role === "dept_admin";
  const initial = (session.user.name || "用户").trim().slice(0, 1);

  const loadSessions = useCallback(() => {
    api.listSessions(50).then((r) => setSessions(r.data.sessions || [])).catch(() => setSessions([]));
  }, []);

  useEffect(() => { loadSessions(); }, [loadSessions]);

  // 刷新页面 / 从外部链接进入时保持当前对话（?session=xxx）
  useEffect(() => {
    const sid = new URLSearchParams(window.location.search).get("session");
    if (sid) openSession({ session_id: sid });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    api.gatewayHealth().then((r) => setGateway(r.data)).catch(() => setGateway({ available: false }));
  }, [chatKey]);

  useEffect(() => {
    api.getRoles().then((r) => {
      setRoleList(r.data.roles || []);
      setPubCount((r.data.public || []).length);
    }).catch(() => setRoleList([]));
  }, []);

  const myRole = roleList.find((r) => r.id === roleId);

  const newChat = (agentId = "", name = "") => {
    setSeed({ agentId });
    setAgentName(name);
    setChatKey((k) => k + 1);
    setView("chat");
    setRailOpen(false);
  };

  const openSession = async (s: any) => {
    try {
      const r = await api.getSession(s.session_id);
      const d = r.data;
      setSeed({
        sessionId: d.session_id,
        agentId: d.agent_id,
        turns: (d.turns || []).map((t: any) => ({
          role: t.role,
          content: t.content,
          artifacts: t.artifacts || [],
          llmReal: t.llm_real,
          webSources: t.web_sources || [],
          webSearch: t.web_search,
          ts: t.ts,
        })),
      });
      setAgentName("");
      setChatKey((k) => k + 1);
      setView("chat");
      setRailOpen(false);
    } catch {
      loadSessions();
    }
  };

  const removeSession = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    try {
      await api.deleteSession(id);
      loadSessions();
      if (seed.sessionId === id) newChat();
    } catch { /* 忽略：列表刷新会反映真实状态 */ }
  };

  const devSwitch = async (admin: boolean) => {
    const r = await api.login(admin ? "dev_admin" : "dev");
    setSession({
      idToken: r.data.token,
      user: {
        userId: r.data.user?.userId ?? "u_dev",
        name: r.data.user?.name ?? "开发用户",
        deptId: r.data.user?.deptId ?? "d_demo",
        role: r.data.user?.role ?? "employee",
      },
      dev: true,
    });
    window.location.reload();
  };

  const grouped = ["今天", "昨天", "更早"].map((label) => ({
    label,
    items: sessions.filter((s) => dayKey(s.updated_at || s.created_at) === label),
  })).filter((g) => g.items.length);

  const roleLabel = session.user.role === "platform_admin" ? "平台管理员"
    : session.user.role === "dept_admin" ? "部门管理员" : "员工";

  const title = view === "chat" ? (agentName || "AI 对话")
    : view === "skills" ? "技能库"
    : view === "builder" ? "智能体搭建"
    : ADMIN_TABS.find((t) => t.k === adminTab)?.label || "管理后台";

  return (
    <div className="shell">
      <style>{STYLE}</style>

      {/* ── 左侧栏：品牌 / 新对话 / 会话历史 / 底部入口 ── */}
      <aside className={"rail" + (railOpen ? " open" : "")}>
        <div className="rail-brand">
          <div className="rail-logo">睿</div>
          <div>
            <b>睿深行 AI 工作台</b>
            <i>{isAdmin ? "企业智能体 · 115 个技能包" : "企业智能体 · 岗位工作台"}</i>
          </div>
        </div>

        <button className="rail-new" onClick={() => newChat()}>
          <span style={{ fontSize: 15, lineHeight: 1 }}>＋</span> 新对话
        </button>

        <div className="rail-scroll">
          {grouped.length === 0 && (
            <div className="hint" style={{ padding: "14px 10px" }}>
              还没有历史对话，直接说需求就会自动建档。
            </div>
          )}
          {grouped.map((g) => (
            <div key={g.label}>
              <div className="rail-sec">{g.label}</div>
              {g.items.map((s) => (
                <div key={s.session_id}
                     className={"rail-item" + (seed.sessionId === s.session_id && view === "chat" ? " on" : "")}
                     onClick={() => openSession(s)} title={s.last_message || s.title}>
                  <span>{s.title || "新对话"}</span>
                  <span className="x" onClick={(e) => removeSession(e, s.session_id)}>×</span>
                </div>
              ))}
            </div>
          ))}
        </div>

        <div className="rail-foot">
          {/* 岗位是这一版的入口：员工先认领岗位，再从这里进技能库 */}
          <div className="rail-link" onClick={() => { setView("skills"); setRailOpen(false); }}>
            {myRole ? (
              <>
                <span className="role-ini" style={{ background: myRole.color, width: 16, height: 16, fontSize: 10 }}>
                  {myRole.initial}
                </span>
                {myRole.name}
                <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--muted)" }}>{myRole.count}</span>
              </>
            ) : (
              <>
                <span style={{ fontSize: 12 }}>◇</span> 还没选岗位
                <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--blue)" }}>去选</span>
              </>
            )}
          </div>
          <div className="rail-link" onClick={() => { setView("skills"); setRailOpen(false); }}>
            <span style={{ fontSize: 13 }}>◈</span> 技能库
            <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--muted)" }}>
              {myRole ? myRole.count + pubCount : "—"}
            </span>
          </div>
          <div className="rail-link" onClick={() => { setView("builder"); setRailOpen(false); }}>
            <span style={{ fontSize: 13 }}>⬡</span> 智能体搭建
          </div>
          {isAdmin && (
            <div className="rail-link" onClick={() => { setView("admin"); setRailOpen(false); }}>
              <span style={{ fontSize: 12 }}>▦</span> 管理后台
            </div>
          )}
          <div style={{ position: "relative" }}>
            <div className="rail-user" onClick={() => setMenu((m) => !m)}>
              <div className="avatar">{initial}</div>
              <div className="u">
                <b>{session.user.name}</b>
                <i>{roleLabel}</i>
              </div>
              <span style={{ color: "var(--muted)", fontSize: 11 }}>⌃</span>
            </div>
            {menu && (
              <div style={{ position: "absolute", bottom: "calc(100% + 6px)", left: 6, right: 6,
                            background: "#fff", border: "1px solid var(--line)", borderRadius: 10,
                            boxShadow: "0 10px 30px rgba(27,27,25,.14)", padding: 5, zIndex: 50 }}>
                {session.dev && (
                  <div className="rail-link" onClick={() => devSwitch(!isAdmin)}>
                    {isAdmin ? "切回员工身份" : "切到管理员身份"}
                  </div>
                )}
                <div className="rail-link" onClick={onLogout}>退出登录</div>
              </div>
            )}
          </div>
        </div>
      </aside>

      {/* 移动端侧栏抽屉遮罩：点遮罩关抽屉（桌面端 display:none） */}
      {railOpen && <div className="rail-backdrop" onClick={() => setRailOpen(false)} />}

      {/* ── 主舞台 ── */}
      <main className="stage">
        <div className="topbar">
          <button className="tool only-narrow" onClick={() => setRailOpen((v) => !v)} title="菜单">☰</button>
          <h2>{title}</h2>
          {view === "chat" && <span className="sub">· 产出可直接下载</span>}
          <span className="grow" />
          <div className="hint" style={{ display: "flex", alignItems: "center", gap: 6 }}
               title={gateway?.available ? `模型通道就绪（${gateway?.default_model || ""}）`
                                          : "模型通道未就绪：对话会返回占位答案"}>
            <span style={{ width: 7, height: 7, borderRadius: "50%", display: "inline-block",
                           background: gateway?.available ? "#2fa36b" : "#e0a03a" }} />
            {gateway?.available ? "模型已连接" : "模型未连接"}
          </div>
        </div>

        {view === "chat" && (
          <ChatPage
            key={chatKey}
            seed={seed}
            userName={(session.user.name || "你").trim()}
            onOpenSkills={() => setView("skills")}
            onSession={(sid, first) => {
              setSeed((s) => ({ ...s, sessionId: sid }));
              loadSessions();
              if (first) setAgentName(first);
            }}
          />
        )}

        {view === "skills" && (
          <div className="content">
            <div className="page">
              <SkillsPage onUse={(a) => newChat(a.agent_id, a.name)} />
            </div>
          </div>
        )}

        {view === "builder" && (
          <div className="content">
            <div className="page">
              <BuilderPage session={session} />
            </div>
          </div>
        )}

        {view === "admin" && (
          <>
            <div className="admin-tabs">
              {ADMIN_TABS.filter((t) => t.k !== "tenant" || session.user.role === "platform_admin").map((t) => (
                <div key={t.k} className={"tool" + (adminTab === t.k ? " on" : "")}
                     style={{ borderRadius: 0, padding: "11px 12px",
                              borderBottom: adminTab === t.k ? "2px solid var(--blue)" : "2px solid transparent" }}
                     onClick={() => setAdminTab(t.k)}>
                  {t.label}
                </div>
              ))}
            </div>
            <div className="content">
              <div className="page">
                {adminTab === "approval" && <ApprovalPage />}
                {adminTab === "agents" && <AgentsPage />}
                {adminTab === "sessions" && <SessionsPage />}
                {adminTab === "kb" && <KnowledgePage />}
                {adminTab === "usage" && <UsagePage />}
                {adminTab === "pilot" && <PilotPage isAdmin={isAdmin} />}
                {adminTab === "models" && <ModelsPage isAdmin={isAdmin} />}
                {adminTab === "retrieve" && <RetrievePage />}
                {adminTab === "platform" && <PlatformOpsPage isAdmin={isAdmin} />}
                {adminTab === "identity" && <IdentityPage isAdmin={isAdmin} />}
                {adminTab === "tenant" && session.user.role === "platform_admin" && <TenantConfigPage />}
              </div>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
