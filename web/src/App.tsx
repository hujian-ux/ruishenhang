import { useState } from "react";
import { api } from "./api/client";
import { STYLE } from "./theme";
import { setSession, type Session } from "./auth/session";
import { AgentsPage } from "./pages/Agents";
import { ChatPage } from "./pages/Chat";
import { ApprovalPage } from "./pages/Approval";
import { UsagePage } from "./pages/Usage";
import { KnowledgePage } from "./pages/Knowledge";
import { SessionsPage } from "./pages/Sessions";
import { ModelsPage } from "./pages/Models";
import { PilotPage } from "./pages/Pilot";

function RetrievePage() {
  const [q, setQ] = useState("五一活动方案");
  const [res, setRes] = useState<any>(null);
  const go = async () => {
    try { setRes(await api.retrieve(q)); } catch (e: any) { setRes({ error: e.message }); }
  };
  return (
    <div className="card">
      <h3>带权召回演示（第18/36章 · 单一过滤点）</h3>
      <div className="field"><label>查询</label><input value={q} onChange={(e) => setQ(e.target.value)} /></div>
      <button className="btn" onClick={go}>召回</button>
      {res && !res.error && (
        <div style={{ marginTop: 10 }}>
          <div className="hint">可见 {res.data.hits.length} 条 · 被过滤 {res.data.filtered_out} 条（越权不进上下文）</div>
          {res.data.hits.map((h: any) => (
            <div key={h.doc_id} style={{ padding: "6px 0" }}><b>[{h.scope}]</b> {h.title} · 分数 {h.score}</div>
          ))}
        </div>
      )}
      {res && res.error && <div className="hint" style={{ color: "#c62828" }}>{res.error}</div>}
    </div>
  );
}

type Tab = "agents" | "chat" | "sessions" | "approval" | "kb" | "usage" | "pilot" | "models" | "retrieve";

export function App({ session, onLogout }: { session: Session; onLogout: () => void }) {
  const [tab, setTab] = useState<Tab>("agents");
  const isAdmin = session.user.role === "platform_admin" || session.user.role === "dept_admin";

  // 仅开发模式（dev token）下提供身份切换，方便演示两种角色；真实 OIDC 登录请退出重登。
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

  const NAV: { k: Tab; label: string }[] = [
    { k: "agents", label: "选型 / 建智能体" },
    { k: "chat", label: "智能体对话（会话记忆）" },
    { k: "sessions", label: "会话管理" },
    { k: "approval", label: "审批台（发布评审）" },
    { k: "kb", label: "知识库上传" },
    { k: "usage", label: "用量与成本" },
    { k: "pilot", label: "试点看板（M8）" },
    { k: "models", label: "模型管理" },
    { k: "retrieve", label: "召回演示" },
  ];

  return (
    <div className="app">
      <style>{STYLE}</style>
      <div className="side">
        <h1>企业 AI 智能体中台</h1>
        {NAV.map((n) => (
          <div key={n.k} className={"nav " + (tab === n.k ? "on" : "")} onClick={() => setTab(n.k)}>{n.label}</div>
        ))}
        <div style={{ marginTop: 18, padding: "0 6px", fontSize: 11.5, color: "#9db8d9" }}>
          当前身份：{session.user.name}（{session.user.role}）
          {session.user.deptId ? <div style={{ marginTop: 2 }}>部门：{session.user.deptId}</div> : null}
          <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 6 }}>
            {session.dev && (
              isAdmin
                ? <button className="btn" style={{ fontSize: 11.5, padding: "5px 9px" }} onClick={() => devSwitch(false)}>切回员工（张三）</button>
                : <button className="btn" style={{ fontSize: 11.5, padding: "5px 9px", background: "#37474f" }} onClick={() => devSwitch(true)}>切到管理员（王总）</button>
            )}
            <button className="btn ghost" style={{ fontSize: 11.5, padding: "5px 9px" }} onClick={onLogout}>退出登录</button>
          </div>
        </div>
      </div>
      <div className="main">
        {tab === "agents" && <AgentsPage />}
        {tab === "chat" && <ChatPage />}
        {tab === "sessions" && <SessionsPage />}
        {tab === "approval" && <ApprovalPage />}
        {tab === "kb" && <KnowledgePage />}
        {tab === "usage" && <UsagePage />}
        {tab === "pilot" && <PilotPage isAdmin={isAdmin} />}
        {tab === "models" && <ModelsPage isAdmin={isAdmin} />}
        {tab === "retrieve" && <RetrievePage />}
      </div>
    </div>
  );
}
