import { useEffect, useState } from "react";
import { api, setToken } from "./api/client";
import { AgentsPage } from "./pages/Agents";
import { ChatPage } from "./pages/Chat";
import { ApprovalPage } from "./pages/Approval";
import { UsagePage } from "./pages/Usage";
import { KnowledgePage } from "./pages/Knowledge";
import { SessionsPage } from "./pages/Sessions";
import { ModelsPage } from "./pages/Models";

const STYLE = `
:root{--navy:#0a2540;--blue:#1565c0;--accent:#2d7ff9;--bg:#f6f8fb;--card:#fff;--line:#dbe6f2;--ink:#1f2d3d;--muted:#5b7185;}
*{box-sizing:border-box} body{margin:0;font-family:-apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;background:var(--bg);color:var(--ink)}
.app{display:flex;min-height:100vh}
.side{width:200px;background:var(--navy);color:#fff;padding:18px 12px}
.side h1{font-size:15px;margin:0 0 18px;padding-left:6px}
.side .nav{display:block;padding:9px 10px;border-radius:8px;color:#cfe0f5;cursor:pointer;font-size:13px;margin-bottom:4px}
.side .nav.on{background:var(--accent);color:#fff}
.main{flex:1;padding:22px 26px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-bottom:14px}
.card h3{margin:0 0 10px;font-size:14.5px;color:var(--blue)}
.btn{background:var(--accent);color:#fff;border:none;border-radius:8px;padding:8px 14px;cursor:pointer;font-size:13px}
.btn:disabled{opacity:.5}
.field{margin:10px 0} .field label{display:block;font-size:12px;color:var(--muted);margin-bottom:4px}
.field select,.field input,.field textarea{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:8px;font-size:13px}
.tag{font-size:10.5px;font-weight:800;padding:2px 9px;border-radius:20px;color:#fff;background:var(--accent)}
.row{display:flex;gap:8px;flex-wrap:wrap}
.seg{flex:1;padding:8px;border:1px solid var(--line);border-radius:8px;text-align:center;cursor:pointer;font-size:12.5px}
.seg.on{background:var(--blue);color:#fff;border-color:var(--blue)}
.ok{margin-top:10px;padding:10px 12px;border-radius:8px;background:#ecfdf5;border:1px solid #a7f3d0;color:#2e7d32;font-size:13px}
.hint{font-size:12px;color:var(--muted)}
.kpi{flex:1 1 150px;background:#f8fbfe;border:1px solid var(--line);border-radius:10px;padding:10px 12px;display:flex;flex-direction:column;gap:2px}
.kpi span{font-size:11.5px;color:var(--muted)}
.kpi b{font-size:19px;color:var(--navy);line-height:1.2}
.kpi i{font-size:11px;color:var(--muted);font-style:normal}
.bar{height:8px;background:#e8eff8;border-radius:20px;overflow:hidden}
.bar-in{height:100%;border-radius:20px;transition:width .3s}
.tbl{width:100%;border-collapse:collapse;font-size:12.5px}
.tbl th{text-align:left;font-weight:600;color:var(--muted);border-bottom:1px solid var(--line);padding:7px 6px;white-space:nowrap}
.tbl td{padding:7px 6px;border-bottom:1px solid #eef3f9;vertical-align:top}
.tbl tr:hover td{background:#fafcff}
`;

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

export function App() {
  const [ready, setReady] = useState(false);
  const [tab, setTab] = useState<"agents" | "chat" | "sessions" | "approval" | "kb" | "usage" | "models" | "retrieve">("agents");
  const [user, setUser] = useState<any>(null);

  const doLogin = (code: string) => {
    setReady(false);
    api.login(code).then((r) => { setToken(r.data.token); setUser(r.data.user); setReady(true); })
      .catch(() => setReady(true));
  };

  useEffect(() => { doLogin("dev"); }, []);
  const isAdmin = user?.role === "platform_admin" || user?.role === "dept_admin";

  const NAV: { k: typeof tab; label: string }[] = [
    { k: "agents", label: "选型 / 建智能体" },
    { k: "chat", label: "智能体对话（会话记忆）" },
    { k: "sessions", label: "会话管理" },
    { k: "approval", label: "审批台（发布评审）" },
    { k: "kb", label: "知识库上传" },
    { k: "usage", label: "用量与成本" },
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
          当前身份：{user ? `${user.name}（${user.role}）` : "…"}
          <div style={{ marginTop: 6 }}>
            {isAdmin
              ? <button className="btn" style={{ fontSize: 11.5, padding: "5px 9px" }} onClick={() => doLogin("dev")}>切回员工（张三）</button>
              : <button className="btn" style={{ fontSize: 11.5, padding: "5px 9px", background: "#37474f" }} onClick={() => doLogin("dev_admin")}>切到管理员（王总）</button>}
          </div>
        </div>
      </div>
      <div className="main">
        {!ready && <div className="hint">正在签发 dev token …</div>}
        {ready && tab === "agents" && <AgentsPage />}
        {ready && tab === "chat" && <ChatPage />}
        {ready && tab === "sessions" && <SessionsPage />}
        {ready && tab === "approval" && <ApprovalPage />}
        {ready && tab === "kb" && <KnowledgePage />}
        {ready && tab === "usage" && <UsagePage />}
        {ready && tab === "models" && <ModelsPage isAdmin={isAdmin} />}
        {ready && tab === "retrieve" && <RetrievePage />}
      </div>
    </div>
  );
}
