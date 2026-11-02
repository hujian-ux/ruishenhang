import { useEffect, useState } from "react";
import { api } from "../api/client";

export function ModelsPage({ isAdmin }: { isAdmin: boolean }) {
  const [models, setModels] = useState<any[]>([]);
  const [gw, setGw] = useState<any>(null);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");

  const load = async () => {
    try {
      const [all, h] = await Promise.all([api.listModelsAll("all"), api.gatewayHealth()]);
      setModels(all.data.models);
      setGw(h.data);
      setErr("");
    } catch (e: any) {
      setErr(e.message);
    }
  };
  useEffect(() => { load(); }, []);

  const toggle = async (m: any) => {
    setErr(""); setMsg("");
    const next = m.status === "active" ? "inactive" : "active";
    try {
      await api.toggleModel(m.model_id, next);
      setMsg(`${m.model_id} 已${next === "active" ? "启用" : "停用"}（网关侧即时生效，业务代码零改动）`);
      await load();
    } catch (e: any) {
      setErr(e.message);
    }
  };

  return (
    <>
      <div className="card">
        <h3>模型网关状态（第25章 · 可插拔，业务侧只认 model_id）</h3>
        <div className="row" style={{ gap: 10, marginBottom: 8 }}>
          <div className="kpi">
            <span>LiteLLM 网关</span>
            <b style={{ color: gw?.available ? "#2e7d32" : "#c62828" }}>{gw?.available ? "在线" : "离线（走 stub 回退）"}</b>
            <i>{gw?.base_url}</i>
          </div>
          <div className="kpi"><span>默认路由模型</span><b style={{ fontSize: 15 }}>{gw?.default_model || "-"}</b><i>环境变量 LLM_DEFAULT_MODEL</i></div>
          <div className="kpi"><span>注册模型</span><b>{models.length}</b><i>{models.filter((m) => m.status === "active").length} 个启用中</i></div>
        </div>
        <div className="hint">网关离线不影响平台可用：调用自动回退确定性 stub，且照常计量落账（标记为 stub）。</div>
      </div>

      <div className="card">
        <h3>模型注册表</h3>
        {msg && <div className="ok">{msg}</div>}
        {err && <div className="hint" style={{ color: "#c62828" }}>{err}</div>}
        <table className="tbl">
          <thead>
            <tr><th>model_id</th><th>厂商</th><th>适配器</th><th>能力</th><th>上下文</th><th>单价（元/1k）</th><th>状态</th><th>操作</th></tr>
          </thead>
          <tbody>
            {models.map((m: any) => (
              <tr key={m.model_id}>
                <td><b>{m.model_id}</b>{m.model_id === gw?.default_model && <span className="tag" style={{ marginLeft: 6 }}>默认</span>}</td>
                <td>{m.vendor}</td>
                <td className="hint">{m.adapter}</td>
                <td>{(m.capabilities || []).join(" / ")}</td>
                <td>{m.context_window ? `${(m.context_window / 1000).toFixed(0)}k` : "-"}</td>
                <td>{m.price_per_1k_prompt} / {m.price_per_1k_completion}</td>
                <td>{m.status === "active" ? <span className="tag" style={{ background: "#2e7d32" }}>active</span> : <span className="tag" style={{ background: "#8d99ae" }}>inactive</span>}</td>
                <td>
                  <button
                    className="btn"
                    style={{ padding: "4px 9px", fontSize: 11.5, background: m.status === "active" ? "#c62828" : "#2e7d32", opacity: isAdmin ? 1 : 0.45 }}
                    disabled={!isAdmin}
                    onClick={() => toggle(m)}
                  >
                    {m.status === "active" ? "停用" : "启用"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!isAdmin && <div className="hint" style={{ marginTop: 8 }}>启停模型需要平台管理员身份（左上角身份可切换）。</div>}
      </div>
    </>
  );
}
