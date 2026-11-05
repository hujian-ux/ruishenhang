// 岗位选择器：在欢迎页和技能库共用。
// 刻意不做成"下拉框"——11 个岗位要一眼看全，且每个都带数量，用户能判断哪个装得多。
export type Role = {
  id: string;
  name: string;
  initial: string;
  desc?: string;
  color?: string;
  count?: number;
  core_count?: number;
};

export function RoleChip({
  role, onClick, title,
}: { role: Role; onClick?: () => void; title?: string }) {
  return (
    <span className="role-chip" onClick={onClick} title={title || `当前岗位：${role.name} · 点击切换`}>
      <span className="role-ini" style={{ background: role.color || "var(--ink)" }}>{role.initial}</span>
      {role.name}
      <span style={{ color: "var(--muted)", fontSize: 11 }}>⌄</span>
    </span>
  );
}

export function RoleGrid({
  roles, value, onChange, showCount = true,
}: {
  roles: Role[];
  value?: string;
  onChange: (id: string) => void;
  showCount?: boolean;
}) {
  return (
    <div className="role-grid">
      {roles.map((r) => (
        <div key={r.id} className={"role-card" + (value === r.id ? " on" : "")}
             onClick={() => onChange(r.id)} title={r.desc}>
          <span className="role-ini" style={{ background: r.color || "var(--ink)", width: 24, height: 24, fontSize: 12 }}>
            {r.initial}
          </span>
          <div style={{ minWidth: 0 }}>
            <b>{r.name}</b>
            <i>{showCount ? `${r.count ?? 0} 个技能` : r.desc}</i>
          </div>
        </div>
      ))}
    </div>
  );
}
