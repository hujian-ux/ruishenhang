const api = require('../../utils/api.js');
const session = require('../../utils/session.js');

Page({
  data: { busy: false, err: '', enforce: false, ssoUrl: '' },

  onLoad() {
    // 探测收口态：收口后隐藏演示入口，引导走真实 SSO（生产应改用 wx.login→后端换码）。
    api.get('/auth/settings').then((r) => {
      const on = !!(r && r.data && r.data.enforce_oidc);
      this.setData({ enforce: on });
    }).catch(() => {});
  },

  devLogin(admin) {
    if (this.data.busy) return;
    this.setData({ busy: true, err: '' });
    api.post('/auth/sso/callback', { provider: 'wecom', code: admin ? 'dev_admin' : 'dev', device_id: 'mp' })
      .then((r) => {
        const sess = { token: r.data.token, tenantId: r.data.tenant_id || '', user: r.data.user };
        session.saveSession(sess);
        const app = getApp();
        app.globalData.token = sess.token;
        app.globalData.tenantId = sess.tenantId;
        app.globalData.user = sess.user;
        wx.reLaunch({ url: '/pages/chat/chat' });
      })
      .catch((e) => this.setData({ err: e.message || '登录失败', busy: false }));
  }
});
