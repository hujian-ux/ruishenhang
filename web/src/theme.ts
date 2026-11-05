// 设计系统（对外产品形态）：浅色极简 + 细边框 + 大留白，蓝色只做强调。
//
// 为什么整体重写：上一版是内部仪表盘审美——深色侧栏、`M4/M16` 里程碑代号、
// 10 个技术标签平铺。客户第一眼看到的应该是"能干活的工作台"，不是调试面板。
// 旧 class（card/btn/field/tbl…）全部保留并重新美化，其余管理页无需改动即可跟随。
export const STYLE = `
:root{
  --rail:#f8f8f6; --bg:#fff; --line:#e9e9e4; --line-2:#f1f1ec;
  --ink:#1b1b19; --ink-2:#4a4a45; --muted:#8a8a83;
  --blue:#1a5fd0; --blue-d:#12479f; --blue-l:#eef4ff;
  --ok:#1a7f45; --warn:#a85800; --err:#c0392b;
  --r-sm:8px; --r:12px; --r-lg:16px; --r-xl:22px;
  --shadow:0 1px 2px rgba(27,27,25,.04),0 8px 24px rgba(27,27,25,.05);
  --mono:ui-monospace,SFMono-Regular,Consolas,'Courier New',monospace;
}
*{box-sizing:border-box}
html,body,#root{height:100%}
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;
  background:var(--bg);color:var(--ink);font-size:14px;line-height:1.6;
  -webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale}
button{font-family:inherit}
::selection{background:#d8e6ff}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-thumb{background:#dcdcd6;border-radius:20px;border:3px solid transparent;background-clip:content-box}
::-webkit-scrollbar-thumb:hover{background:#c6c6bf;background-clip:content-box}
::-webkit-scrollbar-track{background:transparent}

/* ── 骨架：左侧栏 + 主舞台 ── */
.shell{display:flex;height:100vh;overflow:hidden}
.rail{width:262px;flex:none;background:var(--rail);display:flex;flex-direction:column;
  border-right:1px solid var(--line-2);padding:12px 10px}
.rail-brand{display:flex;align-items:center;gap:9px;padding:6px 8px 12px}
.rail-logo{width:28px;height:28px;border-radius:8px;background:linear-gradient(145deg,#2b7bf3,#12479f);
  color:#fff;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:13px;
  letter-spacing:-.5px;box-shadow:0 2px 6px rgba(26,95,208,.28)}
.rail-brand b{font-size:13.5px;font-weight:600;letter-spacing:.2px}
.rail-brand i{display:block;font-size:11px;color:var(--muted);font-style:normal;margin-top:-2px}
.rail-new{display:flex;align-items:center;gap:8px;width:100%;padding:8px 10px;margin-bottom:10px;
  background:#fff;border:1px solid var(--line);border-radius:10px;cursor:pointer;
  font-size:13px;color:var(--ink-2);text-align:left;transition:.15s}
.rail-new:hover{border-color:#d3d3cc;box-shadow:0 1px 3px rgba(27,27,25,.06);color:var(--ink)}
.rail-new kbd{margin-left:auto;font-family:var(--mono);font-size:10px;color:var(--muted);
  background:#f2f2ee;border:1px solid var(--line);border-radius:4px;padding:1px 4px}
.rail-scroll{flex:1;overflow-y:auto;min-height:0;padding-bottom:6px}
.rail-sec{font-size:10.5px;font-weight:600;color:var(--muted);letter-spacing:.8px;
  padding:12px 9px 5px;text-transform:uppercase}
.rail-item{display:flex;align-items:center;gap:8px;padding:7px 9px;border-radius:8px;cursor:pointer;
  font-size:13px;color:var(--ink-2);position:relative;transition:background .12s}
.rail-item:hover{background:#efefea}
.rail-item.on{background:#e8e8e2;color:var(--ink);font-weight:500}
.rail-item span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;min-width:0}
.rail-item .x{opacity:0;flex:none;font-size:14px;color:var(--muted);padding:0 2px;border-radius:4px}
.rail-item:hover .x{opacity:1}
.rail-item .x:hover{background:#dcdcd4;color:var(--err)}
.rail-foot{border-top:1px solid var(--line);padding-top:8px;margin-top:4px}
.rail-link{display:flex;align-items:center;gap:8px;padding:7px 9px;border-radius:8px;cursor:pointer;
  font-size:12.5px;color:var(--ink-2)}
.rail-link:hover{background:#efefea}
.rail-user{display:flex;align-items:center;gap:9px;padding:9px;margin-top:2px;border-radius:10px;cursor:pointer}
.rail-user:hover{background:#efefea}
.avatar{width:26px;height:26px;border-radius:50%;flex:none;display:flex;align-items:center;justify-content:center;
  font-size:11.5px;font-weight:600;color:#fff;background:linear-gradient(145deg,#5b8def,#2b5bb5)}
.rail-user .u{flex:1;min-width:0}
.rail-user .u b{display:block;font-size:12.5px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rail-user .u i{font-size:11px;color:var(--muted);font-style:normal}

.stage{flex:1;min-width:0;display:flex;flex-direction:column;background:var(--bg)}
.topbar{height:52px;flex:none;display:flex;align-items:center;gap:10px;padding:0 22px;
  border-bottom:1px solid var(--line-2);overflow:hidden}
.topbar h2{margin:0;font-size:14px;font-weight:600;letter-spacing:.1px;white-space:nowrap}
.topbar .sub{font-size:12px;color:var(--muted)}
.topbar .grow{flex:1}
.content{flex:1;min-height:0;overflow-y:auto;padding:26px 22px 0}
.content.wide{padding:0}
.page{max-width:1080px;margin:0 auto;padding-bottom:32px}

/* ── 通用组件（旧页面复用） ── */
.card{background:#fff;border:1px solid var(--line);border-radius:var(--r-lg);padding:18px 20px;margin-bottom:14px}
.card h3{margin:0 0 12px;font-size:14px;font-weight:600;color:var(--ink);letter-spacing:.1px}
.btn{background:var(--ink);color:#fff;border:none;border-radius:9px;padding:8px 14px;cursor:pointer;
  font-size:13px;font-weight:500;transition:.15s;white-space:nowrap}
.btn:hover{background:#33332f}
.btn:disabled{opacity:.45;cursor:not-allowed}
.btn.ghost{background:#fff;color:var(--ink-2);border:1px solid var(--line)}
.btn.ghost:hover{background:#fafaf8;border-color:#d6d6cf;color:var(--ink)}
.btn.blue{background:var(--blue)}
.btn.blue:hover{background:var(--blue-d)}
.field{margin:12px 0}
.field label{display:block;font-size:12px;color:var(--muted);margin-bottom:5px}
.field select,.field input,.field textarea{width:100%;padding:9px 11px;border:1px solid var(--line);
  border-radius:9px;font-size:13px;font-family:inherit;background:#fff;color:var(--ink);transition:.15s}
.field select:focus,.field input:focus,.field textarea:focus{outline:none;border-color:#a9c8f5;
  box-shadow:0 0 0 3px rgba(26,95,208,.09)}
.tag{display:inline-flex;align-items:center;gap:4px;font-size:11px;font-weight:500;padding:2px 8px;
  border-radius:6px;color:var(--ink-2);background:#f2f2ee;border:1px solid transparent;white-space:nowrap}
.row{display:flex;gap:8px;flex-wrap:wrap}
.seg{flex:1;padding:7px 10px;border:1px solid var(--line);border-radius:8px;text-align:center;
  cursor:pointer;font-size:12.5px;color:var(--ink-2);background:#fff;white-space:nowrap}
.seg:hover{border-color:#d6d6cf}
.seg.on{background:var(--ink);color:#fff;border-color:var(--ink)}
.ok{margin-top:10px;padding:10px 13px;border-radius:10px;background:#f2fbf5;border:1px solid #cdebd8;color:var(--ok);font-size:13px}
.warn{margin-top:10px;padding:10px 13px;border-radius:10px;background:#fffbf0;border:1px solid #f3e3bd;color:var(--warn);font-size:13px}
.err{margin-top:10px;padding:10px 13px;border-radius:10px;background:#fdf3f2;border:1px solid #f3cdc8;color:var(--err);font-size:13px}
.hint{font-size:12.5px;color:var(--muted);line-height:1.65}
.mono{font-family:var(--mono);font-size:12px}
.kpi{flex:1 1 150px;background:#fbfbf9;border:1px solid var(--line);border-radius:12px;padding:12px 14px;
  display:flex;flex-direction:column;gap:3px}
.kpi span{font-size:11.5px;color:var(--muted)}
.kpi b{font-size:20px;color:var(--ink);line-height:1.25;font-weight:600;letter-spacing:-.3px}
.kpi i{font-size:11px;color:var(--muted);font-style:normal}
.bar{height:7px;background:#f0f0ea;border-radius:20px;overflow:hidden}
.bar-in{height:100%;border-radius:20px;transition:width .3s;background:var(--blue)}
.tbl{width:100%;border-collapse:collapse;font-size:13px}
.tbl th{text-align:left;font-weight:500;color:var(--muted);border-bottom:1px solid var(--line);
  padding:9px 8px;white-space:nowrap;font-size:11.5px;letter-spacing:.3px}
.tbl td{padding:9px 8px;border-bottom:1px solid var(--line-2);vertical-align:top;color:var(--ink-2)}
.tbl tr:hover td{background:#fbfbf9}

/* ── 对话：欢迎页 ── */
.welcome{max-width:760px;margin:0 auto;padding:0 24px 12px;width:100%}
.welcome .hello{font-size:13px;color:var(--muted);margin-bottom:10px}
.welcome h1{margin:0 0 8px;font-size:30px;line-height:1.28;font-weight:600;letter-spacing:-.7px;color:var(--ink)}
.welcome h1 em{font-style:normal;color:var(--blue)}
.welcome p.lead{margin:0 0 26px;font-size:14px;color:var(--muted)}
.w-cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-bottom:18px}
.w-card{border:1px solid var(--line);border-radius:14px;padding:13px 15px;cursor:pointer;
  background:#fff;transition:.16s;display:flex;gap:11px;align-items:flex-start;min-width:0}
.w-card:hover{border-color:#cfcfc8;box-shadow:var(--shadow);transform:translateY(-1px)}
.w-ico{width:30px;height:30px;flex:none;border-radius:9px;display:flex;align-items:center;justify-content:center;
  font-size:15px;background:var(--blue-l);color:var(--blue)}
.w-card b{display:block;font-size:13.5px;font-weight:500;margin-bottom:2px;color:var(--ink)}
.w-card span{font-size:12px;color:var(--muted);display:-webkit-box;-webkit-line-clamp:2;
  -webkit-box-orient:vertical;overflow:hidden;line-height:1.55}
.w-all{font-size:12.5px;color:var(--blue);cursor:pointer;display:inline-flex;align-items:center;gap:4px}
.w-all:hover{text-decoration:underline}

/* ── 对话：消息流 ── */
.chat-shell{flex:1;min-height:0;display:flex;flex-direction:column}
.thread-scroll{flex:1;overflow-y:auto;min-height:0}
.thread{max-width:760px;margin:0 auto;padding:8px 24px 24px}
.msg{margin-bottom:22px;display:flex;flex-direction:column}
.msg.user{align-items:flex-end}
.msg .who{font-size:11.5px;color:var(--muted);margin-bottom:5px;display:flex;align-items:center;gap:6px}
.bubble{background:#f4f4f0;color:var(--ink);padding:10px 14px;border-radius:16px 16px 4px 16px;
  max-width:86%;font-size:13.5px;white-space:pre-wrap;word-break:break-word}
.msg.assistant .body{font-size:14px;line-height:1.78;color:var(--ink);white-space:pre-wrap;word-break:break-word}
.msg.assistant .body code{background:#f4f4f0;padding:1px 5px;border-radius:4px;font-family:var(--mono);font-size:12.5px}
.pending{display:inline-flex;align-items:center;gap:8px;color:var(--muted);font-size:13px}
.dot{width:6px;height:6px;border-radius:50%;background:var(--blue);animation:pulse 1.1s infinite ease-in-out}
.dot:nth-child(2){animation-delay:.15s}.dot:nth-child(3){animation-delay:.3s}
@keyframes pulse{0%,80%,100%{opacity:.25;transform:scale(.85)}40%{opacity:1;transform:scale(1)}}
.notice{margin-top:9px;font-size:12px;color:var(--err);background:#fdf3f2;border:1px solid #f3cdc8;
  border-radius:8px;padding:7px 10px;max-width:560px}

/* 产出物卡片 */
.artifacts{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
.artifact{display:flex;align-items:center;gap:10px;border:1px solid var(--line);border-radius:12px;
  padding:9px 13px 9px 10px;background:#fff;cursor:pointer;transition:.15s;width:212px}
.artifact:hover{border-color:#b9d3f7;background:#fcfdff;box-shadow:0 1px 4px rgba(26,95,208,.07)}
.artifact .ext{width:32px;height:32px;border-radius:8px;display:flex;align-items:center;justify-content:center;
  font-size:9.5px;font-weight:700;letter-spacing:.3px;color:#fff;background:#5b8def;flex:none}
.artifact .ext.xlsx{background:#1e7145}.artifact .ext.docx{background:#2b579a}
.artifact .ext.pptx{background:#b7472a}.artifact .ext.pdf{background:#c0392b}
.artifact .ext.html{background:#e08c00}
.artifact .meta{min-width:0;flex:1}
.artifact .meta b{display:block;font-size:12.5px;font-weight:500;color:var(--ink);
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.artifact .meta span{font-size:11px;color:var(--muted)}

/* ── 输入区（composer） ── */
.composer-wrap{flex:none;padding:10px 24px 18px;background:linear-gradient(to top,#fff 62%,rgba(255,255,255,0))}
.composer{max-width:760px;margin:0 auto;border:1px solid var(--line);border-radius:var(--r-xl);
  background:#fff;box-shadow:0 2px 10px rgba(27,27,25,.05);transition:.16s;padding:4px}
.composer:focus-within{border-color:#b6cff5;box-shadow:0 2px 14px rgba(26,95,208,.1)}
.chips{display:flex;flex-wrap:wrap;gap:6px;padding:8px 10px 2px}
.chip{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;background:#f4f4f0;
  border:1px solid transparent;border-radius:7px;padding:3px 8px;color:var(--ink-2)}
.chip .x{cursor:pointer;color:var(--muted);font-weight:700}
.chip .x:hover{color:var(--err)}
.chip.bad{background:#fdf3f2;color:var(--err)}
.composer textarea{width:100%;border:none;outline:none;resize:none;padding:8px 12px;font-size:14px;
  font-family:inherit;line-height:1.6;background:transparent;color:var(--ink);max-height:180px}
.composer textarea::placeholder{color:#a6a69f}
.ctools{display:flex;align-items:center;gap:4px;padding:4px 6px 6px}
.tool{display:inline-flex;align-items:center;gap:5px;border:none;background:transparent;cursor:pointer;
  font-size:12px;color:var(--ink-2);padding:5px 8px;border-radius:8px;transition:.12s}
.tool:hover{background:#f4f4f0;color:var(--ink)}
.tool.on{background:var(--blue-l);color:var(--blue)}
.tool:disabled{opacity:.4;cursor:not-allowed}
.send{margin-left:auto;width:30px;height:30px;border-radius:50%;border:none;cursor:pointer;
  background:var(--ink);color:#fff;display:flex;align-items:center;justify-content:center;font-size:14px;transition:.15s}
.send:hover:not(:disabled){background:#000;transform:scale(1.04)}
.send:disabled{background:#e2e2dc;color:#a8a8a1;cursor:not-allowed;transform:none}
.foot-note{max-width:760px;margin:7px auto 0;text-align:center;font-size:11px;color:#adada6}

/* 技能选择弹层 */
.pop{position:absolute;bottom:calc(100% + 8px);left:8px;right:8px;background:#fff;border:1px solid var(--line);
  border-radius:14px;box-shadow:0 12px 40px rgba(27,27,25,.14);z-index:40;overflow:hidden}
.pop-in{padding:9px}
.pop-in input{width:100%;padding:8px 11px;border:1px solid var(--line);border-radius:9px;font-size:13px;
  outline:none;font-family:inherit}
.pop-in input:focus{border-color:#b6cff5}
.pop-list{max-height:260px;overflow-y:auto;margin-top:6px}
.pop-item{padding:7px 9px;border-radius:8px;cursor:pointer;font-size:13px;display:flex;align-items:center;gap:8px}
.pop-item:hover{background:#f4f4f0}
.pop-item b{font-weight:500;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pop-item span{font-size:11px;color:var(--muted)}
.pop-empty{padding:14px;text-align:center;color:var(--muted);font-size:12.5px}

/* 格式选择 */
.fmt-item{display:flex;align-items:center;gap:7px;padding:6px 9px;border-radius:8px;cursor:pointer;font-size:12.5px}
.fmt-item:hover{background:#f4f4f0}

.only-narrow{display:none}
@media (max-width:860px){
  .rail{position:fixed;left:-270px;z-index:60;height:100vh;transition:left .2s}
  .rail.open{left:0;box-shadow:0 0 0 100vmax rgba(0,0,0,.28)}
  .w-cards{grid-template-columns:minmax(0,1fr)}
  .welcome{padding:0 16px 12px}
  .welcome h1{font-size:24px}
  .only-narrow{display:inline-flex}
  .topbar{padding:0 12px;gap:6px}
  .topbar .sub{display:none}
  .thread{padding:8px 16px 20px}
  .composer-wrap{padding:8px 12px 14px}
  .bubble{max-width:92%}
  .artifact{width:100%}
  .foot-note{padding:0 6px}
  .content{padding:18px 14px 0}
}
`;

// 登录页：左右分栏——左侧品牌主张、右侧登录动作。对外产品第一屏不该是配置面板。
export const AUTH_STYLE = `
.auth-screen{display:flex;height:100vh;background:#fff}
.auth-left{flex:1;min-width:0;position:relative;overflow:hidden;
  background:linear-gradient(160deg,#0b1f3a 0%,#123b6d 48%,#1a5fd0 100%);color:#fff;
  display:flex;flex-direction:column;justify-content:space-between;padding:44px 48px}
.auth-left:after{content:'';position:absolute;right:-140px;bottom:-160px;width:520px;height:520px;
  border-radius:50%;background:radial-gradient(circle,rgba(120,180,255,.34),transparent 62%)}
.auth-left .brand{display:flex;align-items:center;gap:11px;position:relative;z-index:1}
.auth-left .brand .lg{width:34px;height:34px;border-radius:9px;background:rgba(255,255,255,.14);
  border:1px solid rgba(255,255,255,.24);display:flex;align-items:center;justify-content:center;
  font-weight:700;font-size:13px;letter-spacing:-.4px}
.auth-left .brand b{font-size:14px;font-weight:600}
.auth-left .brand i{display:block;font-size:11.5px;opacity:.62;font-style:normal;margin-top:-2px}
.auth-hero{position:relative;z-index:1;max-width:430px;margin:auto 0}
.auth-hero h1{font-size:33px;line-height:1.32;margin:0 0 14px;font-weight:600;letter-spacing:-.9px}
.auth-hero p{margin:0;font-size:13.5px;line-height:1.85;color:rgba(255,255,255,.72)}
.auth-points{margin-top:26px;display:flex;flex-direction:column;gap:10px}
.auth-points div{display:flex;gap:9px;font-size:12.5px;color:rgba(255,255,255,.82)}
.auth-points b{width:16px;height:16px;flex:none;border-radius:50%;background:rgba(255,255,255,.16);
  display:flex;align-items:center;justify-content:center;font-size:9px;margin-top:2px}
.auth-left .foot{position:relative;z-index:1;font-size:11.5px;color:rgba(255,255,255,.42)}
.auth-right{width:460px;flex:none;display:flex;align-items:center;justify-content:center;padding:32px}
.auth-card{width:100%;max-width:352px}
.auth-card h2{margin:0 0 6px;font-size:23px;font-weight:600;letter-spacing:-.5px}
.auth-sub{font-size:13px;color:var(--muted);margin:0 0 24px;line-height:1.7}
.auth-actions{display:flex;flex-direction:column;gap:10px;margin-top:16px}
.auth-actions .btn{width:100%;padding:11px 14px;font-size:13.5px;border-radius:10px}
.auth-foot{margin-top:22px;display:flex;justify-content:center;gap:6px;align-items:center;font-size:12px;color:var(--muted)}
.link{color:var(--blue);cursor:pointer;text-decoration:none;font-size:12px}
.link:hover{text-decoration:underline}
.auth-rule{display:flex;align-items:center;gap:10px;margin:22px 0 0;font-size:11.5px;color:var(--muted)}
.auth-rule:before,.auth-rule:after{content:'';height:1px;background:var(--line);flex:1}
.grid2{display:flex;gap:12px;flex-wrap:wrap}
.grid2>.field{flex:1 1 200px;margin:0}
.section-title{font-size:12.5px;font-weight:600;color:var(--ink);margin:20px 0 2px;
  padding-top:14px;border-top:1px solid var(--line)}
.copybox{background:#fbfbf9;border:1px solid var(--line);border-radius:10px;padding:12px 14px;font-size:12px;
  color:var(--ink-2);white-space:pre-wrap;line-height:1.75;overflow:auto;max-height:220px}
@media (max-width:900px){
  .auth-left{display:none}
  .auth-right{width:100%}
}
`;
