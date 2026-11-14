import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";

/** P1-11 · 审计完整性台（#306）。
 *  链是 tamper-evident（可检测篡改），不是 tamper-proof（不能阻止改写）。
 *  页面只呈现结论与序号/哈希，不把「通过」写成「绝对不可篡改」；
 *  合法留存清理 / 数据主体删除会显示为「已裁剪」，不伪装成链完整无缺。 */
type Any = Record<string, any>;

const small: React.CSSProperties = { fontSize: 12, color: "var(--muted)" };
const box: React.CSSProperties = {
  border: "1px solid var(--line)", borderRadius: 8, padding: 12, background: "var(--card)",
};

function short(h: string | null | undefined, n = 10) {
  if (!h) return "—";
  return h.length > n * 2 + 1 ? `${h.slice(0, n)}…${h.slice(-n)}` : h;
}

const STATUS: Record<string, { label: string; color: string; note: string }> = {
  ok: { label: "链完整", color: "var(--ok)", note: "所有入链记录重算一致，prev_hash 前后衔接。" },
  pruned: { label: "有合法裁剪", color: "var(--warn)", note: "存在留存清理 / 数据主体删除的合法缺口，缺口均已由裁剪墓碑覆盖。" },
  partial: { label: "部分校验", color: "var(--warn)", note: "达到单次校验条数上限，链尾未完整校验，未给出完整性结论。" },
  tampered: { label: "检测到异常", color: "#c0392b", note: "存在无法解释的哈希不衔接或字段改写，需按断点序号核查。" },
  empty: { label: "暂无入链记录", color: "var(--muted)", note: "该租户尚无链式审计记录。" },
  error: { label: "校验失败", color: "#c0392b", note: "读取链数据出错，未完成校验。" },
};

export function AuditChainPage() {
  const [status, setStatus] = useState<Any>({});
  const [verify, setVerify] = useState<Any | null>(null);
  const [logs, setLogs] = useState<any[]>([]);
  const [allTenants, setAllTenants] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    try {
      const [s, l] = await Promise.all([
        api.getAuditChainStatus(),
        api.getAuditLogs({ limit: 20 }),
      ]);
      setStatus(s.data || {});
      setLogs(l.data?.logs || []);
      setErr("");
    } catch (ex: any) { setErr(ex.message); }
  }, []);

  const runVerify = useCallback(async () => {
    setBusy(true); setErr("");
    try {
      const r = await api.verifyAuditChain(allTenants ? { tenant_id: "*" } : {});
      setVerify(r.data || {});
      await load();  // 验链本身也写一条审计，刷新链高与日志
    } catch (ex: any) { setErr(ex.message); }
    setBusy(false);
  }, [allTenants, load]);

  useEffect(() => { load(); }, [load]);

  const heads: Any[] = status.heads || [];
  const maxSeq = heads.reduce((m, h) => Math.max(m, h.last_seq || 0), 0);
  const st = STATUS[verify?.status || ""] || null;
  const pruned: Any[] = verify?.pruned || [];
  const tenants: Any[] = verify?.tenants || [];

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div>
          <h2 style={{ margin: 0 }}>审计完整性台 · P1-11</h2>
          <div style={small}>
            按租户分链的审计哈希链：逐条重算摘要、校验 prev_hash 衔接；留存清理与数据主体删除留裁剪墓碑。
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <label style={{ ...small, display: "flex", gap: 5, alignItems: "center" }}>
            <input type="checkbox" checked={allTenants} onChange={(e) => setAllTenants(e.target.checked)} />
            全部租户
          </label>
          <button className="btn ghost" onClick={load}>刷新</button>
          <button className="btn" disabled={busy} onClick={runVerify}>{busy ? "校验中…" : "立即验链"}</button>
        </div>
      </div>

      {err && <div className="card" style={{ borderColor: "#e57373", color: "#b03a3a", marginBottom: 12 }}>{err}</div>}

      <div className="grid4">
        <div className="kpi"><span>链写入</span>
          <b style={{ color: status.enabled ? "var(--ok)" : "var(--warn)" }}>{status.enabled ? "已启用" : "已关闭"}</b>
          <i>AUDIT_CHAIN_ENABLED</i></div>
        <div className="kpi"><span>摘要算法</span><b style={{ fontSize: 18 }}>{status.algo || "—"}</b>
          <i>{status.key_configured ? "密钥已配置（HMAC）" : "无密钥（仅 SHA-256）"}</i></div>
        <div className="kpi"><span>最高链序号</span><b>{maxSeq}</b>
          <i>{heads.length} 个租户链 · 后端 {status.backend || "—"}</i></div>
        <div className="kpi"><span>历史未入链</span><b>{status.legacy ?? 0}</b>
          <i>裁剪墓碑 {status.gaps ?? 0} 段 · 单次校验上限 {status.verify_max ?? 0}</i></div>
      </div>

      {verify && st && (
        <div className="card" style={{ marginTop: 14, borderColor: verify.ok ? "var(--line)" : st.color }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
            <h3 style={{ margin: 0, color: st.color }}>
              {st.label} · 已验证 {verify.verified ?? 0} 条
            </h3>
            <span style={small}>{verify.backend || status.backend} · {verify.algo || status.algo}</span>
          </div>
          <div style={{ ...small, marginTop: 6 }}>{st.note}</div>
          {verify.error && <div style={{ marginTop: 8, color: "#c0392b" }}>{verify.error}</div>}
          {verify.broken_at != null && (
            <div style={{ marginTop: 8 }}>首个异常链序号：<b>{verify.broken_at}</b></div>
          )}
          {!!pruned.length && (
            <div style={{ marginTop: 10 }}>
              <div style={{ ...small, marginBottom: 4 }}>合法裁剪区间</div>
              <table className="tbl">
                <thead><tr><th>起始 seq</th><th>结束 seq</th><th>原因</th></tr></thead>
                <tbody>
                  {pruned.map((p, i) => (
                    <tr key={i}>
                      <td>{p.from_seq}</td><td>{p.to_seq}</td>
                      <td style={small}>{p.reason === "retention_purge" ? "留存到期清理" :
                        p.reason === "data_subject_erasure" ? "数据主体删除" : (p.reason || "—")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {!!tenants.length && (
            <div style={{ marginTop: 10 }}>
              <div style={{ ...small, marginBottom: 4 }}>分租户结果</div>
              {tenants.map((t) => (
                <span key={t.tenant_id} className="badge" style={{ marginRight: 6 }}>
                  {t.tenant_id} · {STATUS[t.status]?.label || t.status} · {t.verified} 条
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="card" style={{ marginTop: 14 }}>
        <h3 style={{ marginTop: 0 }}>链头（每租户一条）</h3>
        <table className="tbl">
          <thead><tr><th>租户</th><th>last_seq</th><th>last_hash</th><th>更新时间</th></tr></thead>
          <tbody>
            {heads.map((h) => (
              <tr key={h.tenant_id}>
                <td><b style={{ color: "var(--ink)" }}>{h.tenant_id}</b></td>
                <td>{h.last_seq}</td>
                <td title={h.last_hash} style={{ fontFamily: "monospace" }}>{short(h.last_hash, 12)}</td>
                <td style={small}>{h.updated_at || "—"}</td>
              </tr>
            ))}
            {!heads.length && <tr><td colSpan={4} style={small}>暂无链头；写入第一条审计后自动建立。</td></tr>}
          </tbody>
        </table>
      </div>

      <div className="card" style={{ marginTop: 14 }}>
        <h3 style={{ marginTop: 0 }}>最近审计记录（链字段）</h3>
        <table className="tbl">
          <thead><tr><th>时间</th><th>动作</th><th>目标</th><th>结果</th><th>chain_seq</th><th>record_hash</th></tr></thead>
          <tbody>
            {logs.map((r, i) => (
              <tr key={`${r.trace_id}-${i}`}>
                <td style={small}>{String(r.created_at || "").slice(0, 19).replace("T", " ")}</td>
                <td>{r.action}</td>
                <td style={small}>{r.target || "—"}</td>
                <td style={small}>{r.result}</td>
                <td>{r.chain_seq ?? <span style={small}>legacy</span>}</td>
                <td title={r.record_hash} style={{ fontFamily: "monospace" }}>{short(r.record_hash, 8)}</td>
              </tr>
            ))}
            {!logs.length && <tr><td colSpan={6} style={small}>暂无审计记录。</td></tr>}
          </tbody>
        </table>
      </div>

      <div className="card" style={{ marginTop: 14, ...box }}>
        <h3 style={{ marginTop: 0 }}>能力与边界</h3>
        <div style={{ ...small, lineHeight: 1.8 }}>
          1. 每条审计在 <code>audit.record</code> 单点写入 chain_seq / prev_hash / record_hash；载荷含全部业务字段与时间戳，改任一字段都会被验链重算发现。<br />
          2. 这是 <b>tamper-evident（可检测篡改）</b>，不是 tamper-proof（不能阻止改写）。未配密钥时仅 SHA-256，持有数据库写权限者可重算整条链；配置 <code>AUDIT_CHAIN_KEY</code> 后走 HMAC-SHA256，密钥不入库。<br />
          3. 留存到期清理与数据主体删除前会写 <code>audit_chain_gaps</code> 裁剪墓碑（区间 + 被删末条哈希 + 摘要），验链判「已裁剪」而非「异常」；绕过服务层直接删行会被判异常并给出断点 seq。<br />
          4. 导出的 CSV 已附带链字段，可用 <code>audit.compute_hash</code> 在离线环境独立复验；更强的保证需外部只追加存储或定期哈希锚定。<br />
          5. 验链本身也写一条 <code>audit_chain_verify</code> 审计，链高会随每次校验递增，属预期行为。
        </div>
        <pre style={{ margin: "10px 0 0", padding: 10, background: "var(--bg)", borderRadius: 6, fontSize: 12, overflowX: "auto" }}>
{`AUDIT_CHAIN_ENABLED=1          # 设 0 回到 M13 写入形态（零链字段）
AUDIT_CHAIN_KEY=<随机密钥>      # 留空用 SHA-256；配置后启用 HMAC-SHA256
AUDIT_CHAIN_VERIFY_MAX=20000    # 单租户单次验链条数上限`}
        </pre>
      </div>
    </div>
  );
}
