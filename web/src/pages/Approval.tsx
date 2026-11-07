import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";

// 把多级审批的单据渲染成「进度条 + 审批助手建议 + 决策」三合一卡片。
// 多级进度直接来自待审列表里的 stages/decisions/current_stage（无需逐个拉详情）；
// 助手建议按需拉 /assist（避免 N+1 与真模型滥用）；决策后回显多级推进信息。

function StageProgress({ ticket }: { ticket: any }) {
  const stages: any[] = ticket.stages && ticket.stages.length ? ticket.stages : [{ name: "默认审批" }];
  const decided: Record<number, string> = {};
  (ticket.decisions || []).forEach((d: any) => { decided[d.stage] = d.decision; });
  const cur = ticket.current_stage ?? 0;
  const finished = ticket.status !== "pending";
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
      {stages.map((s: any, i: number) => {
        const dec = decided[i];
        const isCur = i === cur && !finished;
        const bg = dec === "approve" ? "#2e7d32" : dec === "reject" ? "#c62828" : isCur ? "var(--blue)" : "#eee";
        const color = dec || isCur ? "#fff" : "#333";
        return (
          <span key={i} style={{ fontSize: 12, padding: "3px 9px", borderRadius: 12, background: bg, color }}>
            {dec === "approve" ? "✓ " : dec === "reject" ? "✗ " : ""}{s.name}
            {stages.length > 1 && <span style={{ opacity: 0.7 }}> {i + 1}/{stages.length}</span>}
          </span>
        );
      })}
      {ticket.status === "approved" && <span style={{ fontSize: 12, padding: "3px 9px", borderRadius: 12, background: "#2e7d32", color: "#fff" }}>已发布</span>}
      {ticket.status === "rejected" && <span style={{ fontSize: 12, padding: "3px 9px", borderRadius: 12, background: "#c62828", color: "#fff" }}>已驳回</span>}
    </div>
  );
}

function AssistPanel({ approvalId, enrich }: { approvalId: string; enrich: boolean }) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const go = useCallback(async () => {
    setLoading(true); setErr("");
    try { setData((await api.assistApproval(approvalId, enrich)).data); }
    catch (e: any) { setErr(e.message || "助手加载失败"); }
    finally { setLoading(false); }
  }, [approvalId, enrich]);
  return (
    <div style={{ marginTop: 10, padding: 10, background: "#fbfbf9", borderRadius: 8, border: "1px solid var(--line)" }}>
      {!data && !loading && (
        <button className="btn" style={{ fontSize: 13 }} onClick={go}>查看审批助手建议</button>
      )}
      {loading && <div className="hint">助手分析中…</div>}
      {err && <div className="hint" style={{ color: "#c62828" }}>{err}</div>}
      {data && (
        <div style={{ fontSize: 13 }}>
          <div className="hint" style={{ marginBottom: 6 }}>🤖 审批助手（仅建议，不落账）</div>
          <div style={{ marginBottom: 6 }}>
            <b>摘要：</b>{data.summary?.summary}
            {data.summary?.stage_progress && (
              <span className="tag" style={{ marginLeft: 6 }}>进度 {data.summary.stage_progress}</span>
            )}
          </div>
          {data.summary?.risk_flags?.length > 0 && (
            <div style={{ marginBottom: 6 }}>
              <b>风险标记：</b>
              <ul style={{ margin: "4px 0", paddingLeft: 18 }}>
                {data.summary.risk_flags.map((f: string, i: number) => <li key={i}>{f}</li>)}
              </ul>
            </div>
          )}
          {data.compliance_flags?.length > 0 && (
            <div style={{ marginBottom: 6 }}>
              <b style={{ color: "#c62828" }}>合规提醒：</b>
              <ul style={{ margin: "4px 0", paddingLeft: 18 }}>
                {data.compliance_flags.map((f: string, i: number) => <li key={i}>{f}</li>)}
              </ul>
            </div>
          )}
          {data.llm_hint && (
            <div style={{ marginBottom: 6, padding: 8, background: "rgba(33,150,243,0.08)", borderRadius: 6 }}>
              <b>🧠 真模型提示：</b>{data.llm_hint}
            </div>
          )}
          {data.draft?.reason_template && (
            <div style={{ marginBottom: 6 }}>
              <b>申请要点：</b>{data.draft.reason_template}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function ApprovalPage() {
  const [tickets, setTickets] = useState<any[]>([]);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [onlyMyStage, setOnlyMyStage] = useState(false);
  const [enrich, setEnrich] = useState(false);
  const [imProvider, setImProvider] = useState<"wecom" | "dingtalk">("wecom");
  const [imToUser, setImToUser] = useState("");
  const [imBusyId, setImBusyId] = useState("");

  const load = useCallback(async () => {
    setErr(""); setMsg("");
    try {
      const r = await api.pendingApprovals(onlyMyStage);
      setTickets(r.data.tickets || []);
    } catch (e: any) {
      setErr(e.message || "加载失败（需要管理员身份）");
      setTickets([]);
    }
  }, [onlyMyStage]);

  useEffect(() => { load(); }, [load]);

  const decide = async (id: string, d: "approve" | "reject") => {
    setErr(""); setMsg("");
    try {
      const r = await api.decideApproval(id, d);
      const dt = r.data;
      const prog = dt.finished
        ? (dt.status === "approved" ? "已最终通过" : "已驳回")
        : `已通过第 ${dt.current_stage + 1}/${dt.total_stages} 级（${dt.stage_name}），待 ${dt.next_stage_name}`;
      setMsg(`${id} → ${dt.status}（智能体 ${dt.agent_status}）· ${prog}`);
      load();
    } catch (e: any) {
      setErr(e.message || "决策失败");
    }
  };

  // M48：把审批助手摘要通过企微/钉钉 OA 卡片回推给指定审批人（只展示，不落账）
  const pushIm = async (id: string) => {
    if (!imToUser.trim()) { setErr("请先填写回推对象的 IM 账号（to_user）"); return; }
    setImBusyId(id); setErr(""); setMsg("");
    try {
      await api.imApprove({ provider: imProvider, to_user: imToUser.trim(), approval_id: id });
      setMsg(`${id} 已回推 ${imProvider} OA 卡片（含审批助手摘要）给 ${imToUser.trim()}`);
    } catch (e: any) {
      setErr(e.message || "回推失败（请确认已配置该租户的 IM）");
    } finally { setImBusyId(""); }
  };

  return (
    <div className="card">
      <h3>审批台（M5/M46/M48 · 多级状态机 + 审批助手 + IM 回推）</h3>
      <div className="hint" style={{ marginBottom: 8 }}>
        多级审批：当前级通过才流转下一级，末级通过才 <b>published</b>；驳回或任意级拒绝即终态。
        dept_admin 只能审本部门单据，platform_admin 全局可审。
      </div>
      <div style={{ display: "flex", gap: 16, alignItems: "center", marginBottom: 10, flexWrap: "wrap" }}>
        <label style={{ fontSize: 13 }}><input type="checkbox" checked={onlyMyStage} onChange={(e) => setOnlyMyStage(e.target.checked)} /> 仅显示当前级我能审的单</label>
        <label style={{ fontSize: 13 }}><input type="checkbox" checked={enrich} onChange={(e) => setEnrich(e.target.checked)} /> 用真模型增强助手提示</label>
        <button className="btn" onClick={load}>刷新待审列表</button>
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 10, flexWrap: "wrap" }}>
        <span className="hint">手动重推 IM 卡片：</span>
        <select className="field" style={{ width: 110, margin: 0 }} value={imProvider} onChange={(e) => setImProvider(e.target.value as any)}>
          <option value="wecom">企业微信</option>
          <option value="dingtalk">钉钉</option>
        </select>
        <input className="field" style={{ width: 180, margin: 0 }} placeholder="to_user（审批人 IM 账号）"
               value={imToUser} onChange={(e) => setImToUser(e.target.value)} />
        <span className="hint">新单/流转已自动回推；此按钮用于手动补推</span>
      </div>
      {err && <div className="hint" style={{ color: "#c62828", marginBottom: 8 }}>{err}</div>}
      {msg && <div className="ok" style={{ marginBottom: 8 }}>{msg}</div>}
      {tickets.length === 0 && !err && <div className="hint">暂无待审单据（或当前身份无权查看）。</div>}
      {tickets.map((t) => (
        <div key={t.approval_id} style={{ padding: "12px 0", borderBottom: "1px solid var(--line)" }}>
          <div>
            <span className="tag">{t.approval_id}</span> 智能体 <b>{t.agent_id}</b>
            {t.batch && <span className="tag" style={{ marginLeft: 6 }}>批量 {t.agent_ids?.length || 0}</span>}
            · 发起人 {t.requester_id} · 部门 {t.dept_id}
          </div>
          <StageProgress ticket={t} />
          <AssistPanel approvalId={t.approval_id} enrich={enrich} />
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn" onClick={() => decide(t.approval_id, "approve")}>✓ 通过</button>
            <button className="btn" style={{ background: "#c62828" }} onClick={() => decide(t.approval_id, "reject")}>✗ 驳回</button>
            <button className="btn blue" disabled={imBusyId === t.approval_id} onClick={() => pushIm(t.approval_id)}>
              {imBusyId === t.approval_id ? "重推中…" : "↩ 手动重推 IM"}
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
