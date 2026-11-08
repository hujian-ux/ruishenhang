// 小程序入口。API 基地址在 utils/api.js 的 API_BASE 配置；生产需在小程序后台
// 「开发管理 → 开发设置 → 服务器域名」把该 HTTPS 域名加入 request 合法域名白名单。
App({
  globalData: {
    apiBase: require('./utils/api.js').API_BASE,
    token: '',
    tenantId: '',
    user: null
  },
  onLaunch() {
    const s = require('./utils/session.js');
    const sess = s.loadSession();
    if (sess && sess.token) {
      this.globalData.token = sess.token;
      this.globalData.tenantId = sess.tenantId || '';
      this.globalData.user = sess.user || null;
    }
  }
});
