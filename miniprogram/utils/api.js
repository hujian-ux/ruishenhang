// 后端 API 基地址。生产改为你的 HTTPS 域名（须在小程序后台加入 request 合法域名）。
// 本地联调可临时填 http://<电脑局域网IP>:8080，但真机预览必须 HTTPS。
const API_BASE = 'https://ai.your-domain.com';

function authHeader() {
  const app = getApp();
  return app && app.globalData.token
    ? { Authorization: 'Bearer ' + app.globalData.token }
    : {};
}

function req(method, path, data) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: API_BASE + '/api/v1' + path,
      method,
      data,
      header: Object.assign({ 'content-type': 'application/json' }, authHeader()),
      success: (res) => {
        if (res.statusCode >= 200 && res.statusCode < 300) resolve(res.data);
        else reject(new Error((res.data && (res.data.detail || res.data.msg)) || ('HTTP ' + res.statusCode)));
      },
      fail: (e) => reject(new Error(e.errMsg || 'network error'))
    });
  });
}

// 微信小程序登录：把 wx.login() 拿到的 code 交给后端换平台 sso 令牌。
// 后端启用 WX_MINI_LOGIN_ENABLED 后才可用；成功后把 token 存进全局供后续请求带鉴权头。
function wxLogin() {
  return new Promise((resolve, reject) => {
    wx.login({
      success: (res) => {
        if (!res.code) {
          reject(new Error(res.errMsg || 'wx.login 未返回 code'));
          return;
        }
        post('/auth/wx/login', { code: res.code })
          .then((r) => {
            const token = r && r.data && r.data.token;
            if (token && getApp()) getApp().globalData.token = token;
            resolve(r);
          })
          .catch(reject);
      },
      fail: (e) => reject(new Error(e.errMsg || 'wx.login 失败'))
    });
  });
}

module.exports = {
  API_BASE,
  get: (p) => req('GET', p),
  post: (p, d) => req('POST', p, d),
  wxLogin
};
