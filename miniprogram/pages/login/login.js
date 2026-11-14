const api = require('../../utils/api.js');
const session = require('../../utils/session.js');

// 把后端登录响应落盘并跳转聊天页（dev / 微信两条路径共用）
function applySession(r) {
  const d = (r && r.data) || {};
  const sess = { token: d.token, tenantId: d.tenant_id || '', user: d.user };
  session.saveSession(sess);
  const app = getApp();
  if (app) {
    app.globalData.token = sess.token;
    app.globalData.tenantId = sess.tenantId;
    app.globalData.user = sess.user;
  }
  wx.reLaunch({ url: '/pages/chat/chat' });
}

Page({
  data: { busy: false, err: '', enforce: false, wxEnabled: false },

  onLoad() {
    // 探测收口态与可用身份源：收口后隐藏演示入口，引导走真实身份源。
    api.get('/auth/settings').then((r) => {
      const d = (r && r.data) || {};
      const enforce = !!d.enforce_oidc;
      const wxEnabled = !!d.wx_miniapp_enabled;
      this.setData({ enforce, wxEnabled });
      // 收口 + 启用微信身份源：直接进入微信一键登录（无演示入口，符合生产收口预期）。
      if (enforce && wxEnabled) {
        this.wxLogin();
      }
    }).catch(() => {});
  },

  // 演示入口（仅未收口时可见）
  devLogin(admin) {
    if (this.data.busy) return;
    this.setData({ busy: true, err: '' });
    api.post('/auth/sso/callback', { provider: 'wecom', code: admin ? 'dev_admin' : 'dev', device_id: 'mp' })
      .then(applySession)
      .catch((e) => this.setData({ err: e.message || '登录失败', busy: false }));
  },

  // 收口态主路径：wx.login() → 后端 /auth/wx/login 换平台 sso 令牌
  wxLogin() {
    if (this.data.busy) return;
    this.setData({ busy: true, err: '' });
    api.wxLogin()
      .then(applySession)
      .catch((e) => this.setData({ err: e.message || '微信登录失败', busy: false }));
  }
});
