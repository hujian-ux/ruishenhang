import { useEffect, useState } from "react";
import { api } from "../api/client";

function fmt(ts: number) {
  if (!ts) return "-";
  const d = new Date(ts * 1000);
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

export function SessionsPage() {
  const [rows, setRows] = useState<any[]>([]);
  const [mem, setMem] = useState<any>(null);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");

  const load = async () => {
    try {
      const r = await api.listSessions(50);
      setRows(r.data.sessions);
      setErr("");
    } catch (e: any) {
      setErr(e.message);
    }
  };
  useEffect(() => { load(); }, []);

  const view = async (id: string) => {
    setErr("");
    try { setMem((await api.getSession(id)).data); } catch (e: any) { setErr(e.message); }
  };

  const remove = async (id: string) => {
    setErr(""); setMsg("");
    try {
      await api.deleteSession(id);
      if (mem?.session_id === id) setMem(null);
      setMsg(`已删除会话 ${id}（PG 副本一并删除，重启不会复活）`);
      await load();
    } catch (e: any) {
      setErr(e.message);
    }
  };

  return (
    <>
      <div className="card">
        <h3>会话管理（第9/22章 · 记忆窗口 {rows.length} 个会话）</h3>
        <div className="hint" style={{ marginBottom: 10 }}>
          会话仅创建者本人可见可删（他人访问 403）。记忆注入上限 6 轮、单会话保留 40 轮滑动窗口。
          <button className="btn" style={{ marginLeft: 10, padding: "4px 10px", fontSize: 11.5 }} onClick={load}>刷新</button>
        </div>
        {msg && <div className="ok">{msg}</div>}
        {err && <div className="hint" style={{ color: "#c62828" }}>{err}</div>}
        {rows.length === 0 && <div className="hint">还没有会话 —— 去「智能体对话」问一轮就会生成。</div>}
        {rows.length > 0 && (
          <table className="tbl">
            <thead>
              <tr><th>会话</th><th>智能体</th><th>轮次</th><th>最后一条</th><th>更新时间</th><th>操作</th></tr>
            </thead>
            <tbody>
              {rows.map((s: any) => (
                <tr key={s.session_id}>
                  <td><b>{s.session_id}</b></td>
                  <td>{s.agent_id}</td>
                  <td>{s.turn_count}</td>
                  <td className="hint" style={{ maxWidth: 320 }}>{s.last_role === "user" ? "用户：" : "助手："}{s.last_message}</td>
                  <td>{fmt(s.updated_at)}</td>
                  <td>
                    <button className="btn" style={{ padding: "4px 9px", fontSize: 11.5 }} onClick={() => view(s.session_id)}>看记忆</button>{" "}
                    <button className="btn" style={{ padding: "4px 9px", fontSize: 11.5, background: "#c62828" }} onClick={() => remove(s.session_id)}>删除</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {mem && (
        <div className="card">
          <h3>会话记忆 · {mem.session_id}（{mem.turn_count} 轮 · 智能体 {mem.agent_id}）</h3>
          <div className="hint" style={{ marginBottom: 8 }}>下面是「会真正注入生成器」的那段上下文（最近 6 轮，越界已截断）</div>
          <pre style={{ whiteSpace: "pre-wrap", fontSize: 12.5, lineHeight: 1.7, margin: 0, maxHeight: 360, overflow: "auto", background: "#f8fbfe", border: "1px solid var(--line)", borderRadius: 8, padding: 12 }}>
            {mem.context_preview || "（空）"}
          </pre>
        </div>
      )}
    </>
  );
}
