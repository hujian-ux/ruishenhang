import { useEffect, useState } from "react";
import { api } from "../api/client";
import { TrendChart } from "../components/TrendChart";

// M8 试点看板：一屏回答「语料够不够、召回行不行、成本烧不烧得起」
export function PilotPage({ isAdmin }: { isAdmin: boolean }) {
  const [stats, setStats] = useState<any>(null);
  const [rep, setRep] = useState<any>(null);
  const [repSource, setRepSource] = useState("");
  const [trend, setTrend] = useState<any>(null);
  const [hist, setHist] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");

  const load = async () => {
    try {
      const [s, e, t, h] = await Promise.all([
        api.getKbStats(),
        api.getEvalLatest(),
        api.getUsageTrend(14),
        api.getEvalHistory(14),
      ]);
      setStats(s.data);
      setRep(e.data);
      setRepSource(e.source || "report");
      setTrend(t.data);
      setHist(h.data.rows);
      setErr("");
    } catch (e: any) {
      setErr(e.message);
    }
  };
  useEffect(() => { load(); }, []);

  const runEval = async () => {
    setBusy(true); setMsg(""); setErr("");
    try {
      const r = await api.runEval(100);
      setRep(r.data);
      setRepSource("report");
      setMsg(`已跑基线：golden ${r.data.golden_size} 条 · 召回@100 ${r.data.metrics["recall@100"]} · ${r.data.gate.passed ? "门禁通过" : "门禁未通过"}（报告已落盘）`);
      await load();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  const m = rep?.metrics || {};
  const gate = rep?.gate;
  const corpus = rep?.corpus || stats || {};
  const r100 = m["recall@100"];
  const gateColor = gate ? (gate.passed ? "#2e7d32" : "#c62828") : "#5b7185";

  return (
    <>
      <div className="card">
        <h3>试点就绪度（M8 · 一屏判断能不能开跑两周）</h3>
        <div className="row" style={{ gap: 10 }}>
          <div className="kpi">
            <span>语料规模</span>
            <b>{corpus.documents ?? 0} 篇</b>
            <i>{(corpus.chars ?? stats?.total_chars ?? 0).toLocaleString()} 字 · {corpus.children ? `${corpus.children} 子块` : "索引就绪"}</i>
          </div>
          <div className="kpi">
            <span>基线 Recall@100</span>
            <b style={{ color: gateColor }}>{r100 ?? "—"}</b>
            <i>门禁 ≥ 0.95 · {gate ? (gate.passed ? "通过" : "未通过") : "未评测"}</i>
          </div>
          <div className="kpi">
            <span>本月已发生成本</span>
            <b>¥{(trend?.month_to_date_cost ?? 0).toFixed(4)}</b>
            <i>{(trend?.calls ?? 0)} 次调用 · {(trend?.tokens ?? 0).toLocaleString()} tokens</i>
          </div>
          <div className="kpi">
            <span>月末外推</span>
            <b style={{ color: trend?.budget_status === "over" ? "#c62828" : trend?.budget_status === "warn" ? "#ef6c00" : "#0a2540" }}>
              ¥{(trend?.projected_month_cost ?? 0).toFixed(2)}
            </b>
            <i>预算 ¥{trend?.budget_cny ?? 0} · 外推占用 {trend?.projected_budget_pct ?? 0}%</i>
          </div>
        </div>
        <div className="hint" style={{ marginTop: 10 }}>
          判读顺序：① 语料先到 200 篇以上（少了召回没有意义）→ ② Recall@100 到 0.95（第36章门禁）
          → ③ 外推成本在预算内（不然试点结论是"用不起"，也是有效结论）。
        </div>
      </div>

      <div className="card">
        <h3>RAG 基线（第36/37章 · 客观指标，不依赖 LLM 打分）</h3>
        {rep ? (
          <>
            <div className="row" style={{ gap: 10, marginBottom: 10 }}>
              <div className="kpi"><span>golden 条数</span><b>{rep.golden_size}</b><i>{repSource === "computed" ? "现场现算（未落盘）" : "最近一次落盘报告"}</i></div>
              <div className="kpi"><span>Recall@10</span><b>{m["recall@10"] ?? "—"}</b><i>Recall@5 {m["recall@5"] ?? "—"}</i></div>
              <div className="kpi"><span>MRR@10</span><b>{m["mrr@10"] ?? "—"}</b><i>nDCG@10 {m["ndcg@10"] ?? "—"}</i></div>
              <div className="kpi"><span>过滤正确率</span><b>{rep.filter_accuracy ?? "—"}</b><i>第18章越权不进上下文</i></div>
            </div>
            <div className="hint" style={{ marginBottom: 8 }}>
              门禁结论：<b style={{ color: gateColor }}>{gate?.passed ? "通过" : "未通过"}</b> — {gate?.reason}
              {rep.generated_at ? ` · 生成于 ${rep.generated_at}` : ""}
              {rep.dropped?.length ? ` · 丢弃 ${rep.dropped.length} 条无效 golden` : ""}
            </div>
            {Object.keys(rep.by_scope || {}).length > 0 && (
              <table className="tbl" style={{ marginBottom: 8 }}>
                <thead><tr><th>知识范围</th><th>queries</th><th>Recall@10</th><th>Recall@100</th><th>MRR</th></tr></thead>
                <tbody>
                  {Object.entries(rep.by_scope).map(([sc, v]: any) => (
                    <tr key={sc}><td><b>{sc}</b></td><td>{v.queries}</td><td>{v["recall@10"]}</td><td>{v["recall@100"]}</td><td>{v.mrr}</td></tr>
                  ))}
                </tbody>
              </table>
            )}
            <div className="row" style={{ gap: 8, alignItems: "center" }}>
              <button className="btn" onClick={runEval} disabled={busy || !isAdmin}>
                {busy ? "跑批中…" : isAdmin ? "跑一次基线（前 100 条）" : "仅管理员可跑批"}
              </button>
              <button className="btn" style={{ background: "#37474f" }} onClick={load}>刷新</button>
              <span className="hint">命令行跑全量并出 Markdown 报告：<code>python scripts/eval_baseline.py --golden D:/corpus/golden.jsonl --gate</code></span>
            </div>
          </>
        ) : <div className="hint">加载中…</div>}
        {msg && <div className="ok">{msg}</div>}
        {err && <div className="hint" style={{ color: "#c62828", marginTop: 8 }}>{err}</div>}
      </div>

      <div className="card">
        <h3>成本曲线 · 近 14 天</h3>
        {trend ? (
          <>
            <TrendChart series={trend.series} budgetAvg={trend.budget_cny ? trend.budget_cny / trend.days_in_month : 0} />
            <div className="hint" style={{ marginTop: 6 }}>
              本月已发生 ¥{trend.month_to_date_cost.toFixed(4)}（第 {trend.elapsed_days}/{trend.days_in_month} 天）
              · 近 7 日均 ¥{trend.avg_7d_cost.toFixed(4)}
              · 外推 ¥{trend.projected_month_cost.toFixed(2)}（预算 ¥{trend.budget_cny}）
              · 活跃 {trend.active_days} 天 / 空白 {trend.zero_days} 天
              · 峰值 {trend.peak_day.date.slice(5)} ¥{trend.peak_day.cost.toFixed(4)}
            </div>
          </>
        ) : <div className="hint">加载中…</div>}
      </div>

      <div className="card">
        <h3>基线漂移（每天跑一次，看语料增长后召回有没有掉下来）</h3>
        {hist.length === 0 ? (
          <div className="hint">还没有历史记录。跑一次基线后，每次结果都会追加到这里。</div>
        ) : (
          <table className="tbl">
            <thead><tr><th>时间</th><th>golden</th><th>语料篇数</th><th>Recall@10</th><th>Recall@100</th><th>MRR@10</th><th>过滤正确率</th><th>门禁</th></tr></thead>
            <tbody>
              {[...hist].reverse().map((h, i) => (
                <tr key={i}>
                  <td>{h.at}</td>
                  <td>{h.golden_size}</td>
                  <td>{h.documents ?? "—"}</td>
                  <td>{h["recall@10"]}</td>
                  <td>{h["recall@100"]}</td>
                  <td>{h["mrr@10"]}</td>
                  <td>{h.filter_accuracy ?? "—"}</td>
                  <td>{h.passed ? <span className="tag" style={{ background: "#2e7d32" }}>通过</span> : <span className="tag" style={{ background: "#c62828" }}>未通过</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card">
        <h3>两周试点 SOP（照着做，D10 出结论）</h3>
        <table className="tbl">
          <thead><tr><th>阶段</th><th>动作</th><th>交付/判据</th></tr></thead>
          <tbody>
            <tr><td><b>D1</b></td><td>选一个部门，圈定语料范围与权限边界（哪些进 department、哪些只进 personal）</td><td>语料清单（≥200 篇）+ 权限口径</td></tr>
            <tr><td><b>D1</b></td><td><code>ingest_corpus.py</code> 批量导入 → 看导入报告（失败清单必须清零或逐条说明）</td><td>ingest_report.md：added ≥ 200、failed 已解释</td></tr>
            <tr><td><b>D2</b></td><td>写 50–100 条 golden（query + 标题），跑基线，修语料而不是改 golden 去凑指标</td><td>baseline.md：Recall@100 ≥ 0.95</td></tr>
            <tr><td><b>D3–D9</b></td><td>部门真实使用；每天 10 分钟看试点看板：篇数/召回/成本三件事</td><td>每日一行：调用数、失败率、成本</td></tr>
            <tr><td><b>D5</b></td><td>中期加 50 篇新语料后重跑基线（验证"语料增长召回不塌"）</td><td>history 两条记录对比</td></tr>
            <tr><td><b>D10</b></td><td>出结论：召回是否达标、人均成本、最常被问的 20 个问题、需要补的语料</td><td>一页试点结论 + 是否扩到第二个部门</td></tr>
          </tbody>
        </table>
        <div className="hint" style={{ marginTop: 8 }}>
          完整手册见 <code>docs/M8_试点运行手册.md</code>（指标口径、异常处置、扩部门前置条件）。
        </div>
      </div>
    </>
  );
}
