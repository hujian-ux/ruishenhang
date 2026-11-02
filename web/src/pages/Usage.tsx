import { useEffect, useState } from "react";
import { api } from "../api/client";
import { TrendChart } from "../components/TrendChart";

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
  const [trend, setTrend] = useState<any>(null);
  const [err, setErr] = useState("");

  const load = async () => {
    try {
      const [u, r, q, tr] = await Promise.all([
        api.getUsage(dim),
        api.getUsageRecords(30),
        api.getQuota(),
        api.getUsageTrend(14),
      ]);
      setData(u.data);
      setRecs(r.data.records);
      setQuota(q.data);
      setTrend(tr.data);
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
        <h3>成本曲线 · 近 14 天（M8 试点：逐日真落账，空白日补 0）</h3>
        {trend ? (
          <>
            <div className="row" style={{ gap: 10, marginBottom: 10 }}>
              <div className="kpi"><span>本月已发生</span><b>¥{trend.month_to_date_cost.toFixed(4)}</b><i>{trend.month_to_date_tokens.toLocaleString()} tokens · 第 {trend.elapsed_days}/{trend.days_in_month} 天</i></div>
              <div className="kpi"><span>月末外推</span><b>¥{trend.projected_month_cost.toFixed(2)}</b><i>近 7 日均 ¥{trend.avg_7d_cost.toFixed(4)} × 剩余 {trend.days_in_month - trend.elapsed_days} 天</i></div>
              <div className="kpi"><span>月度预算</span><b>{trend.budget_cny ? `¥${trend.budget_cny}` : "未设"}</b><i>{trend.budget_cny ? `外推占用 ${trend.projected_budget_pct}%` : "设 USAGE_MONTHLY_BUDGET_CNY 启用"}</i></div>
              <div className="kpi"><span>活跃 / 空白日</span><b>{trend.active_days} / {trend.zero_days}</b><i>窗口 {trend.window_days} 天 · 峰值 {trend.peak_day.date.slice(5)}</i></div>
            </div>
            <TrendChart series={trend.series} budgetAvg={trend.budget_cny ? trend.budget_cny / trend.days_in_month : 0} />
            {trend.budget_status === "over" && (
              <div className="hint" style={{ color: "#c62828", marginTop: 6 }}>
                外推已超月度预算 —— 试点要么加预算，要么降模型档位（模型管理页可停用高价模型）。
              </div>
            )}
            {trend.budget_status === "warn" && (
              <div className="hint" style={{ color: "#ef6c00", marginTop: 6 }}>
                外推已过预算 80% —— 建议核对哪类调用在拉高成本（按智能体/模型维度下钻）。
              </div>
            )}
          </>
        ) : <div className="hint">加载中…</div>}
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
