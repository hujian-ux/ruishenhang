import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";

/** P1-8 · 技能索引台账（#303）。
 *  让运营/管理员看清每个技能包在「提示词 / 硬规则 / 岗位映射 / 输入输出契约 /
 *  源码 / 样例输入 / few-shot / 材料」等维度的覆盖完备度，并能按缺口筛选。
 *  复用平台设计系统（card/btn/grid/kpi/bar），不做任何写操作。 */
type Any = Record<string, any>;

const small: React.CSSProperties = { fontSize: 12, color: "var(--muted)" };
const box: React.CSSProperties = { border: "1px solid var(--line)", borderRadius: 8, padding: 14, background: "var(--card)" };

// 维度顺序与后端 DIMENSIONS 对齐；opt_in 维度缺失不标红（是加分项，非盲区）
const DIM_META: { key: string; label: string; opt_in: boolean }[] = [
  { key: "prompt", label: "提示词", opt_in: false },
  { key: "hard_rules", label: "硬规则", opt_in: false },
  { key: "role", label: "岗位", opt_in: false },
  { key: "inputs", label: "输入契约", opt_in: false },
  { key: "outputs", label: "产出契约", opt_in: false },
  { key: "source", label: "源码目录", opt_in: false },
  { key: "samples", label: "样例输入", opt_in: false },
  { key: "fewshot", label: "few-shot", opt_in: true },
  { key: "materials", label: "材料规范", opt_in: true },
];
function scoreColor(p: number) {
  if (p >= 90) return "var(--ok)";
  if (p >= 70) return "#1a7f45";
  if (p >= 50) return "var(--warn)";
  return "var(--err)";
}

function scoreBadge(p: number) {
  return (
    <span style={{ fontFamily: "var(--mono)", fontWeight: 700, color: scoreColor(p) }}>{p}%</span>
  );
}

function FlagDots({ flags }: { flags: Any }) {
  return (
    <span style={{ display: "inline-flex", gap: 3, flexWrap: "wrap" }}>
      {DIM_META.map((d) => {
        const ok = flags[d.key];
        // opt_in 维度缺失用中性灰，必查维度缺失用红
        const color = ok ? "var(--ok)"
          : d.opt_in ? "#b9b9b2" : "var(--err)";
        return (
          <span key={d.key} title={`${d.label}：${ok ? "已覆盖" : (d.opt_in ? "未配置（可选）" : "缺失")}`}
            style={{ fontSize: 10, color, border: `1px solid ${color}`, borderRadius: 5,
                     padding: "0 4px", lineHeight: "15px", whiteSpace: "nowrap" }}>
            {d.label}
          </span>
        );
      })}
    </span>
  );
}

export function SkillLedgerPage() {
  const [err, setErr] = useState("");
  const [stats, setStats] = useState<Any>({});
  const [items, setItems] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);

  // 过滤器
  const [q, setQ] = useState("");
  const [kind, setKind] = useState("");       // "" | business | public
  const [flag, setFlag] = useState("");        // 维度 key 缺口筛选
  const [detail, setDetail] = useState<Any | null>(null);

  const load = async () => {
    setLoading(true); setErr("");
    try {
      const [s, l] = await Promise.all([
        api.getSkillLedgerStats(),
        api.getSkillLedger({ q, kind, flag, limit: 200 }),
      ]);
      setStats(s.data || {});
      setItems(l.data?.items || []);
      setTotal(l.data?.total || 0);
    } catch (e: any) { setErr(e.message); }
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  // 过滤条件变化（q 走输入框，debounce 一下）
  useEffect(() => {
    const t = setTimeout(load, 250);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, kind, flag]);

  const openDetail = async (agentId: string) => {
    setBusy(true); setErr("");
    try {
      const r = await api.getSkillLedgerDetail(agentId);
      setDetail(r.data || {});
    } catch (e: any) { setErr(e.message); }
    setBusy(false);
  };

  const dimCov = stats.dimension_coverage || {};
  const industries = useMemo(() => (stats.by_industry || []) as Any[], [stats]);
  const buckets = stats.score_buckets || {};

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div>
          <h2 style={{ margin: 0 }}>技能索引台账 · P1-8</h2>
          <div style={small}>203 业务包 + 28 公共库的覆盖完备度台账。按缺口筛选，避免覆盖盲区与口径不一致。</div>
        </div>
        <button className="btn" onClick={load}>刷新</button>
      </div>
      {err && <div className="card" style={{ borderColor: "#e57373", color: "#b03a3a", marginBottom: 12 }}>加载失败：{err}</div>}

      {/* ── 看板：总览 KPI + 维度覆盖 + 评分分布 + 行业/岗位 ── */}
      <div className="grid4">
        <div className="kpi"><span>技能总数</span><b>{stats.total ?? 0}</b><i>业务 {stats.business ?? 0} · 公共 {stats.public ?? 0}</i></div>
        <div className="kpi"><span>平均完备度</span><b style={{ color: scoreColor(stats.avg_score_pct ?? 0) }}>{stats.avg_score_pct ?? 0}%</b><i>满分 100</i></div>
        <div className="kpi"><span>无盲区（必查维度齐全）</span><b>{stats.complete_count ?? 0}</b><i>缺失 {((stats.total ?? 0) - (stats.complete_count ?? 0))} 个</i></div>
        <div className="kpi"><span>最大缺口维度</span><b style={{ color: "var(--err)" }}>{stats.gap_top?.[0]?.label ?? "—"}</b><i>缺 {(stats.gap_top?.[0]?.count ?? 0)} 个</i></div>
      </div>

      <div className="grid2">
        {/* 维度覆盖 */}
        <div className="card">
          <h3>① 维度覆盖情况</h3>
          <div style={small}>必查维度缺失=盲区（红）；few-shot / 材料规范为可选加分项（灰）。</div>
          <div style={{ marginTop: 10 }}>
            {DIM_META.map((d) => {
              const cov = dimCov[d.key] || { covered: 0, ratio: 0, opt_in: d.opt_in };
              const totalN = stats.total || 1;
              return (
                <div key={d.key} style={{ marginBottom: 9 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", fontSize: 13 }}>
                    <span>{d.label}{d.opt_in && <span style={small}> （可选）</span>}</span>
                    <span style={{ fontFamily: "var(--mono)" }}>{cov.covered}/{totalN} · {Math.round((cov.covered / totalN) * 100)}%</span>
                  </div>
                  <div className="bar" style={{ marginTop: 4 }}>
                    <div className="bar-in" style={{ width: `${Math.round((cov.covered / totalN) * 100)}%`,
                      background: cov.covered === totalN ? "var(--ok)" : (d.opt_in ? "var(--blue)" : "var(--warn)") }} />
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* 评分分布 + 行业 */}
        <div className="card">
          <h3>② 完备度分布 & 行业</h3>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
            {Object.entries(buckets).map(([k, v]) => (
              <div key={k} style={{ ...box, flex: "1 1 70px", textAlign: "center" }}>
                <div style={small}>{k} 分</div>
                <div style={{ fontSize: 18, fontWeight: 700 }}>{v as number}</div>
              </div>
            ))}
          </div>
          <div style={{ marginTop: 12, maxHeight: 220, overflow: "auto" }}>
            <table className="tbl">
              <thead><tr><th>行业</th><th>包数</th><th>平均</th><th>有缺口</th><th>无盲区</th></tr></thead>
              <tbody>
                {industries.map((g: Any) => (
                  <tr key={g.category_cn}>
                    <td>{g.category_cn}</td><td>{g.count}</td>
                    <td style={{ color: scoreColor(g.avg_score_pct), fontWeight: 600 }}>{g.avg_score_pct}%</td>
                    <td style={{ color: g.with_gap ? "var(--warn)" : "var(--ok)" }}>{g.with_gap}</td>
                    <td>{g.complete}</td>
                  </tr>
                ))}
                {!industries.length && <tr><td colSpan={5} style={small}>无数据</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* ── 过滤器 + 台账表 ── */}
      <div className="card" style={{ marginTop: 14 }}>
        <h3>③ 覆盖度台账（按缺口排序）</h3>
        <div className="row" style={{ marginBottom: 10, alignItems: "center" }}>
          <input className="field" placeholder="搜索名称/描述/岗位"
            value={q} onChange={(e) => setQ(e.target.value)}
            style={{ width: 240, margin: 0 }} />
          <button className={"seg" + (kind === "" ? " on" : "")} onClick={() => setKind("")}>全部</button>
          <button className={"seg" + (kind === "business" ? " on" : "")} onClick={() => setKind("business")}>仅业务包</button>
          <button className={"seg" + (kind === "public" ? " on" : "")} onClick={() => setKind("public")}>仅公共库</button>
          <span style={{ ...small, marginLeft: 8 }}>缺口筛选：</span>
          <select className="field" value={flag} onChange={(e) => setFlag(e.target.value)}
            style={{ width: 150, margin: 0 }}>
            <option value="">不限</option>
            {DIM_META.filter((d) => !d.opt_in).map((d) => (
              <option key={d.key} value={d.key}>缺 {d.label}</option>
            ))}
          </select>
          <span style={{ marginLeft: "auto", ...small }}>{loading ? "加载中…" : `共 ${total} 个`}</span>
        </div>

        <div style={{ overflowX: "auto" }}>
          <table className="tbl">
            <thead>
              <tr>
                <th>技能</th><th>类型</th><th>行业/岗位</th><th>完备度</th>
                <th>提示词</th><th>维度覆盖</th><th></th>
              </tr>
            </thead>
            <tbody>
              {items.map((it: Any) => (
                <tr key={it.agent_id}>
                  <td>
                    <b style={{ color: "var(--ink)" }}>{it.name}</b>
                    <div style={small}>{it.role_name && <>{it.role_name} · </>}{it.status}</div>
                  </td>
                  <td><span className="tag">{it.kind === "public" ? "公共库" : "业务包"}</span></td>
                  <td style={small}>{it.kind === "public" ? "—" : it.category_cn}</td>
                  <td>{scoreBadge(it.score_pct)}</td>
                  <td style={small}>{it.prompt_len} 字</td>
                  <td style={{ maxWidth: 280 }}><FlagDots flags={it.flags} /></td>
                  <td><button className="btn ghost" disabled={busy} onClick={() => openDetail(it.agent_id)}>明细</button></td>
                </tr>
              ))}
              {!items.length && <tr><td colSpan={7} style={small}>无匹配技能（或用例未 bootstrap）</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── 明细抽屉 ── */}
      {detail && (
        <div className="card" style={{ marginTop: 14, borderColor: "var(--blue)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h3 style={{ margin: 0 }}>明细 · {detail.name} <span style={small}>({detail.kind === "public" ? "公共库" : "业务包"})</span></h3>
            <button className="btn" onClick={() => setDetail(null)}>关闭</button>
          </div>
          <div className="grid4" style={{ marginTop: 12 }}>
            <div className="kpi"><span>完备度</span><b style={{ color: scoreColor(detail.score_pct) }}>{detail.score_pct}%</b><i>必查盲区 {detail.gaps?.length || 0}</i></div>
            <div className="kpi"><span>提示词长度</span><b>{detail.prompt_len}</b><i>含硬规则 {detail.flags?.hard_rules ? "是" : "否"}</i></div>
            <div className="kpi"><span>样例输入</span><b>{detail.input_sample_count}</b><i>包内文件 {detail.sample_count}</i></div>
            <div className="kpi"><span>输入清单条目</span><b>{detail.checklist_count}</b><i>材料规范 {detail.reference_spec_count}</i></div>
          </div>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginTop: 10 }}>
            {DIM_META.map((d) => {
              const ok = detail.flags?.[d.key];
              return (
                <span key={d.key} className="badge"
                  style={{ background: ok ? "#e6f4ea" : (d.opt_in ? "#f2f2ee" : "#fdecea"),
                           color: ok ? "#1b7f4b" : (d.opt_in ? "#8a8a83" : "#b03a3a") }}>
                  {d.label}：{ok ? "已覆盖" : (d.opt_in ? "未配置" : "缺失")}
                </span>
              );
            })}
          </div>

          <div className="grid2" style={{ marginTop: 12 }}>
            <div>
              <div style={small}>输入清单（要给什么）</div>
              <div style={{ marginTop: 4 }}>
                {(detail.checklist && detail.checklist.length) ? (
                  detail.checklist.map((c: string, i: number) => (
                    <span key={i} className="pi-i" style={{ marginRight: 6, display: "inline-block", marginBottom: 6 }}>{c}</span>
                  ))
                ) : <span style={small}>—</span>}
              </div>
              <div style={{ ...small, marginTop: 8 }}>材料/规范：验收标准 {detail.has_acceptance ? "已挂" : "无"} · 领域规范 {detail.reference_spec_count} 份 · few-shot {detail.fewshot_version ? "已注入" : "无"}</div>
            </div>
            <div>
              <div style={small}>包内样例文件（kind 分类）</div>
              <div style={{ marginTop: 4, maxHeight: 150, overflow: "auto" }}>
                {(detail.samples && detail.samples.length) ? detail.samples.map((s: Any, i: number) => (
                  <div key={i} style={{ display: "flex", gap: 6, fontSize: 12, padding: "2px 0" }}>
                    <span className="tag" style={{ padding: "0 5px" }}>{s.kind}</span>
                    <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.display_name}</span>
                    <span style={{ ...small, fontFamily: "var(--mono)" }}>{s.size}B</span>
                  </div>
                )) : <span style={small}>无（源码目录 {detail.pack_found ? "存在" : "未定位"}）</span>}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
