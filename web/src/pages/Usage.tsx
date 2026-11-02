import { useEffect, useState } from "react";
import { api } from "../api/client";

const DIMS = [
  { k: "dept", label: "按部门" },
  { k: "agent", label: "按智能体" },
  { k: "model", label: "按模型" },
  { k: "user", label: "按用户" },
  { k: "day", label: "按日" },
];

export function UsagePage() {
  const [dim, setDim] = useState("dept");
  const [data, setData] = useState<any>(null);
  const [recs, setRecs] = useState<any[]>([]);
  const [quota, setQuota] = useState<any>(null);
  const [err, setErr] = useState("");

  const load = async () => {
    try {
      const [u, r, q] = await Promise.all([
        api.getUsage(dim),
        api.getUsageRecords(30),
        api.getQuota(),
      ]);
      setData(u.data);
      setRecs(r.data.records);
      setQuota(q.data);
      setErr("");
    } catch (e: any) {
      setErr(e.message);
    }
  };
  useEffect(() => { load(); }, [dim]);

  const t = data?.total || {};
  const realPct = t.calls ? Math.round((t.real_calls / t.calls) * 100) : 0;
  const qPct = quota?.used_pct ?? 0;
  const qColor = qPct >= 100 ? "#c62828" : qPct >= 80 ? "#ef6c00" : "#2e7d32";

  return (
    <>
      <div className="card">
        <h3>用量与成本（第25章计量 · 真实落账，非演示数据）</h3>
        <div className="row" style={{ gap: 10, marginBottom: 12 }}>
          <div className="kpi"><span>调用次数</span><b>{t.calls ?? 0}</b></div>
          <div className="kpi"><span>真实 LLM 生成</span><b>{realPct}%</b><i>{t.real_calls ?? 0} 真 / {t.stub_calls ?? 0} 回退</i></div>
          <div className="kpi"><span>总 Token</span><b>{(t.total_tokens ?? 0).toLocaleString()}</b><i>入 {t.prompt_tokens ?? 0} · 出 {t.completion_tokens ?? 0}</i></div>
          <div className="kpi"><span>成本（CNY）</span><b>¥{(t.cost ?? 0).toFixed(4)}</b><i>单价落账即固化</i></div>
        </div>

        {quota && quota.quota_tokens > 0 && (
          <div style={{ marginBottom: 14 }}>
            <div className="hint" style={{ marginBottom: 4 }}>
              部门配额水位 · {quota.dept_id} · {quota.used_tokens.toLocaleString()} / {quota.quota_tokens.toLocaleString()} tokens（滚动 {quota.window_days} 天）
              <span style={{ color: qColor, fontWeight: 700 }}> {qPct}%</span>
              {quota.exceeded && <span style={{ color: "#c62828", fontWeight: 700 }}> · 已超限，调用将被 429 拦截</span>}
            </div>
            <div className="bar"><div className="bar-in" style={{ width: `${Math.min(100, qPct)}%`, background: qColor }} /></div>
          </div>
        )}

        <div className="row" style={{ gap: 6, marginBottom: 10 }}>
          {DIMS.map((d) => (
            <div key={d.k} className={"seg " + (dim === d.k ? "on" : "")} style={{ flex: "0 0 96px" }} onClick={() => setDim(d.k)}>
              {d.label}
            </div>
          ))}
          <button className="btn" style={{ marginLeft: "auto", padding: "6px 12px" }} onClick={load}>刷新</button>
        </div>

        {err && <div className="hint" style={{ color: "#c62828" }}>{err}</div>}
        {data && data.rows.length === 0 && (
          <div className="hint">该维度暂无数据 —— 先去「智能体对话」跑几轮，账单会实时出现在这里。</div>
        )}
        {data && data.rows.length > 0 && (
          <table className="tbl">
            <thead>
              <tr>
                <th>{DIMS.find((d) => d.k === dim)?.label}</th>
                <th>调用</th>
                <th>入 Token</th>
                <th>出 Token</th>
                <th>总 Token</th>
                <th>成本</th>
                <th>真生成</th>
                <th>均延迟</th>
                {dim === "dept" && <th>配额占用</th>}
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r: any) => (
                <tr key={r.key}>
                  <td><b>{r.key}</b></td>
                  <td>{r.calls}</td>
                  <td>{r.prompt_tokens.toLocaleString()}</td>
                  <td>{r.completion_tokens.toLocaleString()}</td>
                  <td>{r.total_tokens.toLocaleString()}</td>
                  <td>¥{r.cost.toFixed(4)}</td>
                  <td>{r.calls ? Math.round((r.real_calls / r.calls) * 100) : 0}%</td>
                  <td>{r.avg_latency_ms} ms</td>
                  {dim === "dept" && <td>{r.quota_used_pct}%</td>}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card">
        <h3>最近调用明细（每行一单，可对账）</h3>
        {recs.length === 0 && <div className="hint">暂无调用记录。</div>}
        {recs.length > 0 && (
          <table className="tbl">
            <thead>
              <tr><th>时间</th><th>智能体</th><th>模型</th><th>Token（入/出）</th><th>成本</th><th>生成方式</th><th>延迟</th></tr>
            </thead>
            <tbody>
              {recs.map((r: any, i: number) => (
                <tr key={i}>
                  <td>{r.time}</td>
                  <td>{r.agent_id}</td>
                  <td>{r.model_id}</td>
                  <td>{r.prompt_tokens} / {r.completion_tokens}</td>
                  <td>¥{r.cost.toFixed(6)}</td>
                  <td>{r.real ? <span className="tag" style={{ background: "#2e7d32" }}>真实 LLM</span> : <span className="tag" style={{ background: "#8d99ae" }}>stub 回退</span>}</td>
                  <td>{r.latency_ms} ms</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
