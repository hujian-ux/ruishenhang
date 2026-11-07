import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";

/** M48 · 租户配置（platform_admin 专属）：可视化配置审批多级模板。
 *  原则：只展示真实返回值；保存才落地到 config.approval.stages。 */
type Any = Record<string, any>;
type Stage = { name: string; approver_role: string };

const ROLE_LABEL: Record<string, string> = {
  dept_admin: "部门管理员",
  platform_admin: "平台管理员",
};

function move<T>(arr: T[], from: number, to: number): T[] {
  if (from < 0 || to < 0 || from >= arr.length || to >= arr.length || from === to) return arr;
  const c = arr.slice();
  const [it] = c.splice(from, 1);
  c.splice(to, 0, it);
  return c;
}

export function TenantConfigPage() {
  const [tenants, setTenants] = useState<any[]>([]);
  const [selId, setSelId] = useState("");
  const [stages, setStages] = useState<Stage[]>([]);
  const [allowGlobal, setAllowGlobal] = useState(false);
  const [dragIdx, setDragIdx] = useState<number | null>(null);
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(false);

  const loadTenants = useCallback(async () => {
    try {
      const r = await api.listTenants();
      const list = r.data?.tenants || [];
      setTenants(list);
      if (!selId && list.length) setSelId(list[0].tenant_id);
    } catch (e: any) { setErr(e.message); }
  }, [selId]);

  useEffect(() => { loadTenants(); }, [loadTenants]);

  const loadOne = useCallback(async (id: string) => {
    if (!id) return;
    setLoading(true); setErr("");
    try {
      const r = await api.getTenant(id);
      const ap = (r.data?.config || {}).approval || {};
      setStages((ap.stages || []).map((s: Any) => ({
        name: s.name || "", approver_role: s.approver_role || "dept_admin",
      })));
      setAllowGlobal(!!ap.allow_global_scope);
      setMsg("");
    } catch (e: any) { setErr(e.message); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { loadOne(selId); }, [selId, loadOne]);

  const save = async () => {
    if (!selId) return;
    // 前端基础校验（后端还会再校验角色合法性）
    if (stages.some((s) => !s.name.trim())) { setErr("存在未命名的审批节点"); return; }
    setSaving(true); setErr(""); setMsg("");
    try {
      await api.putTenantApprovalConfig(selId, {
        stages: stages.map((s) => ({ name: s.name.trim(), approver_role: s.approver_role })),
        allow_global_scope: allowGlobal,
      });
      setMsg("已保存：审批多级模板已更新（空模板将退化为单级默认）");
    } catch (e: any) { setErr(e.message); }
    finally { setSaving(false); }
  };

  const updateStage = (i: number, patch: Partial<Stage>) =>
    setStages((arr) => arr.map((s, k) => (k === i ? { ...s, ...patch } : s)));

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div>
          <h2 style={{ margin: 0 }}>租户配置 · 审批多级模板</h2>
          <div className="hint">为每家企业可视化配置审批链路：拖动卡片排序、调整每级审批人角色。空模板 = 单级默认（兼容现有行为）。</div>
        </div>
        <button className="btn" onClick={() => { loadTenants(); loadOne(selId); }}>刷新</button>
      </div>

      {err && <div className="card" style={{ borderColor: "#e57373", color: "#b03a3a", marginBottom: 12 }}>失败：{err}</div>}
      {msg && <div className="card" style={{ borderColor: "#2fa36b", color: "#1b7f4b", marginBottom: 12 }}>{msg}</div>}

      <div className="grid2">
        {/* 左：租户选择 */}
        <div className="card">
          <h3>① 选择租户</h3>
          <div style={{ maxHeight: 360, overflow: "auto" }}>
            {tenants.length === 0 && <div className="hint">暂无租户（先用「平台运营台」开通）</div>}
            {tenants.map((t: Any) => (
              <div key={t.tenant_id}
                   onClick={() => { setSelId(t.tenant_id); setMsg(""); }}
                   style={{ display: "flex", gap: 8, alignItems: "center", cursor: "pointer",
                            padding: "8px 10px", borderRadius: 8,
                            border: selId === t.tenant_id ? "1px solid var(--blue)" : "1px solid var(--line)",
                            background: selId === t.tenant_id ? "var(--soft)" : "#fff", marginBottom: 6 }}>
                <b style={{ fontSize: 13 }}>{t.name}</b>
                <span className="tag" style={{ marginLeft: "auto" }}>{t.tenant_id}</span>
                <span className="tag" style={{ color: t.status === "active" ? "#1b7f4b" : "#b06a1b" }}>{t.status}</span>
              </div>
            ))}
          </div>
        </div>

        {/* 右：多级模板编辑器 */}
        <div className="card">
          <h3>② 审批链路（拖拽排序）</h3>
          {loading && <div className="hint">加载中…</div>}
          {!loading && (
            <>
              <div className="hint" style={{ marginBottom: 8 }}>
                共 {stages.length} 级；末级 approve 才正式发布，任意级 reject 即时终止。
              </div>
              {stages.length === 0 && (
                <div className="card" style={{ borderStyle: "dashed", color: "var(--muted)" }}>
                  当前为空 → 退化为单级默认（部门管理员一审即发布）。
                </div>
              )}
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {stages.map((s, i) => (
                  <div key={i}
                       draggable
                       onDragStart={() => setDragIdx(i)}
                       onDragOver={(e) => e.preventDefault()}
                       onDrop={() => { if (dragIdx !== null) setStages((arr) => move(arr, dragIdx, i)); setDragIdx(null); }}
                       onDragEnd={() => setDragIdx(null)}
                       style={{ display: "flex", gap: 8, alignItems: "center", padding: 10,
                                borderRadius: 8, border: "1px solid var(--line)", background: "#fff",
                                opacity: dragIdx === i ? 0.5 : 1, cursor: "grab" }}>
                    <span style={{ color: "var(--muted)", fontSize: 12, width: 18, textAlign: "center" }}>{i + 1}</span>
                    <span title="拖拽排序" style={{ color: "var(--muted)" }}>⠿</span>
                    <input className="field" style={{ flex: 1 }}
                           value={s.name}
                           placeholder="节点名称，如「部门经理」"
                           onChange={(e) => updateStage(i, { name: e.target.value })} />
                    <select className="field" style={{ width: 130 }}
                            value={s.approver_role}
                            onChange={(e) => updateStage(i, { approver_role: e.target.value })}>
                      {Object.entries(ROLE_LABEL).map(([k, v]) => (
                        <option key={k} value={k}>{v}</option>
                      ))}
                    </select>
                    <button className="btn" title="上移" style={{ padding: "4px 8px" }}
                            disabled={i === 0} onClick={() => setStages((arr) => move(arr, i, i - 1))}>↑</button>
                    <button className="btn" title="下移" style={{ padding: "4px 8px" }}
                            disabled={i === stages.length - 1} onClick={() => setStages((arr) => move(arr, i, i + 1))}>↓</button>
                    <button className="btn" title="删除该级" style={{ padding: "4px 8px", color: "#b03a3a" }}
                            onClick={() => setStages((arr) => arr.filter((_, k) => k !== i))}>×</button>
                  </div>
                ))}
              </div>

              <div style={{ display: "flex", gap: 8, marginTop: 10, alignItems: "center", flexWrap: "wrap" }}>
                <button className="btn" onClick={() => setStages((arr) => [...arr, { name: "", approver_role: "dept_admin" }])}>
                  ＋ 添加节点
                </button>
                <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 13 }}>
                  <input type="checkbox" checked={allowGlobal} onChange={(e) => setAllowGlobal(e.target.checked)} />
                  允许提交「全局知识域」智能体（需平台管理员特别批准）
                </label>
                <span style={{ flex: 1 }} />
                <button className="btn blue" disabled={saving} onClick={save}>
                  {saving ? "保存中…" : "保存模板"}
                </button>
              </div>
            </>
          )}
        </div>
      </div>

      <div className="card" style={{ marginTop: 14 }}>
        <h3>③ 当前链路预览</h3>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          {stages.length === 0 && <span className="tag">单级默认：部门管理员</span>}
          {stages.map((s, i) => (
            <span key={i} style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span className="tag">{i + 1}. {s.name || "（未命名）"} · {ROLE_LABEL[s.approver_role] || s.approver_role}</span>
              {i < stages.length - 1 && <span style={{ color: "var(--muted)" }}>→</span>}
            </span>
          ))}
          {allowGlobal && <span className="tag" style={{ marginLeft: 8, borderColor: "#b06a1b", color: "#b06a1b" }}>允许全局知识域</span>}
        </div>
        <div className="hint" style={{ marginTop: 8 }}>
          预览仅展示，未保存的改动不会生效；点「保存模板」后新提交的审批单按此链路逐级流转。
        </div>
      </div>
    </div>
  );
}
