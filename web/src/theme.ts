// 共享主题样式（工业蓝 · 深色侧栏）——仪表盘与登录/配置页共用，避免各页重复。
export const STYLE = `
:root{--navy:#0a2540;--blue:#1565c0;--accent:#2d7ff9;--bg:#f6f8fb;--card:#fff;--line:#dbe6f2;--ink:#1f2d3d;--muted:#5b7185;}
*{box-sizing:border-box} body{margin:0;font-family:-apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;background:var(--bg);color:var(--ink)}
.app{display:flex;min-height:100vh}
.side{width:200px;background:var(--navy);color:#fff;padding:18px 12px}
.side h1{font-size:15px;margin:0 0 18px;padding-left:6px}
.side .nav{display:block;padding:9px 10px;border-radius:8px;color:#cfe0f5;cursor:pointer;font-size:13px;margin-bottom:4px}
.side .nav.on{background:var(--accent);color:#fff}
.main{flex:1;padding:22px 26px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-bottom:14px}
.card h3{margin:0 0 10px;font-size:14.5px;color:var(--blue)}
.btn{background:var(--accent);color:#fff;border:none;border-radius:8px;padding:8px 14px;cursor:pointer;font-size:13px}
.btn:disabled{opacity:.5}
.btn.ghost{background:#fff;color:var(--blue);border:1px solid var(--line)}
.field{margin:10px 0} .field label{display:block;font-size:12px;color:var(--muted);margin-bottom:4px}
.field select,.field input,.field textarea{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:8px;font-size:13px}
.tag{font-size:10.5px;font-weight:800;padding:2px 9px;border-radius:20px;color:#fff;background:var(--accent)}
.row{display:flex;gap:8px;flex-wrap:wrap}
.seg{flex:1;padding:8px;border:1px solid var(--line);border-radius:8px;text-align:center;cursor:pointer;font-size:12.5px}
.seg.on{background:var(--blue);color:#fff;border-color:var(--blue)}
.ok{margin-top:10px;padding:10px 12px;border-radius:8px;background:#ecfdf5;border:1px solid #a7f3d0;color:#2e7d32;font-size:13px}
.warn{margin-top:10px;padding:10px 12px;border-radius:8px;background:#fffbeb;border:1px solid #fde68a;color:#92400e;font-size:13px}
.err{margin-top:10px;padding:10px 12px;border-radius:8px;background:#fef2f2;border:1px solid #fecaca;color:#b91c1c;font-size:13px}
.hint{font-size:12px;color:var(--muted)}
.mono{font-family:ui-monospace,Consolas,'Courier New',monospace}
.kpi{flex:1 1 150px;background:#f8fbfe;border:1px solid var(--line);border-radius:10px;padding:10px 12px;display:flex;flex-direction:column;gap:2px}
.kpi span{font-size:11.5px;color:var(--muted)}
.kpi b{font-size:19px;color:var(--navy);line-height:1.2}
.kpi i{font-size:11px;color:var(--muted);font-style:normal}
.bar{height:8px;background:#e8eff8;border-radius:20px;overflow:hidden}
.bar-in{height:100%;border-radius:20px;transition:width .3s}
.tbl{width:100%;border-collapse:collapse;font-size:12.5px}
.tbl th{text-align:left;font-weight:600;color:var(--muted);border-bottom:1px solid var(--line);padding:7px 6px;white-space:nowrap}
.tbl td{padding:7px 6px;border-bottom:1px solid #eef3f9;vertical-align:top}
.tbl tr:hover td{background:#fafcff}
`;

// 登录 / 配置页专用：居中卡片布局
export const AUTH_STYLE = `
.auth-wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:28px 16px;
  background:radial-gradient(1200px 500px at 50% -10%,#e8f1fd 0%,var(--bg) 60%)}
.auth-card{width:100%;max-width:460px;background:#fff;border:1px solid var(--line);border-radius:16px;
  padding:26px 26px 22px;box-shadow:0 18px 50px rgba(10,37,64,.08)}
.auth-card.wide{max-width:760px}
.auth-brand{display:flex;align-items:center;gap:10px;margin-bottom:6px}
.auth-logo{width:34px;height:34px;border-radius:9px;background:linear-gradient(135deg,var(--accent),var(--navy));
  display:flex;align-items:center;justify-content:center;color:#fff;font-weight:800;font-size:15px}
.auth-card h2{margin:2px 0 4px;font-size:20px;color:var(--navy)}
.auth-sub{font-size:12.5px;color:var(--muted);margin:0 0 18px}
.auth-actions{display:flex;flex-direction:column;gap:10px;margin-top:8px}
.auth-actions .btn{width:100%;padding:11px 14px;font-size:14px}
.auth-foot{margin-top:16px;display:flex;justify-content:space-between;align-items:center;font-size:12px;color:var(--muted)}
.link{color:var(--blue);cursor:pointer;text-decoration:none}
.link:hover{text-decoration:underline}
.grid2{display:flex;gap:10px;flex-wrap:wrap}
.grid2>.field{flex:1 1 200px;margin:0}
.section-title{font-size:12.5px;font-weight:700;color:var(--navy);margin:18px 0 2px;
  padding-top:12px;border-top:1px dashed var(--line)}
.copybox{background:#f8fbfe;border:1px solid var(--line);border-radius:8px;padding:10px 12px;font-size:12px;
  color:#334155;white-space:pre-wrap;line-height:1.7;overflow:auto;max-height:220px}
`;
