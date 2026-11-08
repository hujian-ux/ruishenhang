const api = require('../../utils/api.js');
const app = getApp();

Page({
  data: { items: [], busy: false, err: '' },

  onShow() { this.load(); },

  load() {
    this.setData({ busy: true, err: '' });
    api.get('/sessions').then((r) => {
      const sessions = (r && (r.data && (r.data.items || r.data.sessions || r.data))) || [];
      const out = [];
      (Array.isArray(sessions) ? sessions : []).forEach((s) => {
        const outputs = (s && (s.outputs || (s.data && s.data.outputs))) || [];
        (Array.isArray(outputs) ? outputs : []).forEach((o) => {
          out.push({
            sessionId: s.id || s.session_id,
            name: o.name || o.filename || '产出物',
            url: o.url || (app.globalData.apiBase + '/api/v1/sessions/' + (s.id || s.session_id) + '/outputs/' + (o.id || o.output_id))
          });
        });
      });
      this.setData({ items: out, busy: false });
    }).catch((e) => this.setData({ err: e.message || '加载产出物失败', busy: false }));
  },

  open(e) {
    const url = e.currentTarget.dataset.url;
    wx.showModal({
      title: '打开产出物', content: '请在浏览器中打开下载链接？',
      success: (res) => { if (res.confirm) wx.setClipboardData({ data: url }); }
    });
  }
});
