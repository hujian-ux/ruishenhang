import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";

export function ApprovalPage() {
  const [tickets, setTickets] = useState<any[]>([]);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    setErr(""); setMsg("");
    try {
      const r = await api.pendingApprovals();
      setTickets(r.data.tickets || []);
    } catch (e: any) {
      setErr(e.message || "加载失败（需要管理员身份）");
      setTickets([]);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const decide = async (id: string, d: "approve" | "reject") => {
    setErr(""); setMsg("");
    try {
      const r = await api.decideApproval(id, d);
      setMsg(`${id} → ${r.data.status}（智能体 ${r.data.agent_status}）`);
      load();
    } catch (e: any) {
      setErr(e.message || "决策失败");
    }
  };

  return (
    <div className="card">
      <h3>审批台（M5 · 第35章红线：评审通过才发布）</h3>
      <div className="hint" style={{ marginBottom: 8 }}>
        审批通过 → 智能体 <b>published</b>（可被调用）；驳回 → <b>rejected</b>。
        dept_admin 只能审本部门单据，platform_admin 全局可审。
      </div>
      <button className="btn" onClick={load} style={{ marginBottom: 10 }}>刷新待审列表</button>
      {err && <div className="hint" style={{ color: "#c62828", marginBottom: 8 }}>{err}</div>}
      {msg && <div className="ok" style={{ marginBottom: 8 }}>{msg}</div>}
      {tickets.length === 0 && !err && <div className="hint">暂无待审单据（或当前身份无权查看）。</div>}
      {tickets.map((t) => (
        <div key={t.approval_id} style={{ padding: "10px 0", borderBottom: "1px solid var(--line)" }}>
          <div><span className="tag">{t.approval_id}</span> 智能体 <b>{t.agent_id}</b> · 发起人 {t.requester_id} · 部门 {t.dept_id}</div>
          <div className="row" style={{ marginTop: 6 }}>
            <button className="btn" onClick={() => decide(t.approval_id, "approve")}>✓ 通过</button>
            <button className="btn" style={{ background: "#c62828" }} onClick={() => decide(t.approval_id, "reject")}>✗ 驳回</button>
          </div>
        </div>
      ))}
    </div>
  );
}
