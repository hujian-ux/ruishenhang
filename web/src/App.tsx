import { useEffect, useState } from "react";
import { api, setToken } from "./api/client";
import { AgentsPage } from "./pages/Agents";
import { ChatPage } from "./pages/Chat";
import { ApprovalPage } from "./pages/Approval";

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
`;

function ModelsPage() {
  const [models, setModels] = useState<any[]>([]);
  useEffect(() => {
    api.listModels().then((r) => setModels(r.data.models)).catch(() => setModels([]));
  }, []);
  return (
    <div className="card">
      <h3>模型注册表（第25章 · 可插拔）</h3>
      {models.length === 0 && <div className="hint">加载中或后端未启动…</div>}
      {models.map((m) => (
        <div key={m.model_id} style={{ padding: "8px 0", borderBottom: "1px solid var(--line)" }}>
          <span className="tag">{m.vendor}</span> <b>{m.model_id}</b> · {m.adapter} · 能力 [{m.capabilities.join(",")}]
        </div>
      ))}
    </div>
  );
}

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
  const [tab, setTab] = useState<"agents" | "chat" | "approval" | "models" | "retrieve">("agents");
  const [user, setUser] = useState<any>(null);

  const doLogin = (code: string) => {
    setReady(false);
    api.login(code).then((r) => { setToken(r.data.token); setUser(r.data.user); setReady(true); })
      .catch(() => setReady(true));
  };

  useEffect(() => { doLogin("dev"); }, []);
  const isAdmin = user?.role === "platform_admin" || user?.role === "dept_admin";

  return (
    <div className="app">
      <style>{STYLE}</style>
      <div className="side">
        <h1>企业 AI 智能体中台</h1>
        <div className={"nav " + (tab === "agents" ? "on" : "")} onClick={() => setTab("agents")}>选型 / 建智能体</div>
        <div className={"nav " + (tab === "chat" ? "on" : "")} onClick={() => setTab("chat")}>智能体对话（会话记忆）</div>
        <div className={"nav " + (tab === "approval" ? "on" : "")} onClick={() => setTab("approval")}>审批台（发布评审）</div>
        <div className={"nav " + (tab === "models" ? "on" : "")} onClick={() => setTab("models")}>模型注册表</div>
        <div className={"nav " + (tab === "retrieve" ? "on" : "")} onClick={() => setTab("retrieve")}>召回演示</div>
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
        {ready && tab === "approval" && <ApprovalPage />}
        {ready && tab === "models" && <ModelsPage />}
        {ready && tab === "retrieve" && <RetrievePage />}
      </div>
    </div>
  );
}
