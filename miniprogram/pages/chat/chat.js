const api = require('../../utils/api.js');

function lastReply(session) {
  const turns = (session && (session.turns || (session.data && session.data.turns))) || [];
  for (let i = turns.length - 1; i >= 0; i--) {
    const t = turns[i];
    if (t && (t.role === 'assistant' || t.role === 'ai')) return t.content || t.text || '';
  }
  return '';
}

Page({
  data: { agents: [], agentId: '', sessionId: '', input: '', reply: '', busy: false, err: '' },

  onLoad() {
    api.get('/agents').then((r) => {
      const list = (r && (r.data && (r.data.items || r.data.agents || r.data))) || [];
      this.setData({ agents: Array.isArray(list) ? list.slice(0, 50) : [] });
    }).catch((e) => this.setData({ err: e.message || '加载技能失败' }));
  },

  selectAgent(e) {
    const id = e.currentTarget.dataset.id;
    this.setData({ agentId: id, sessionId: '', reply: '', err: '' });
    api.post('/sessions', { agent_id: id }).then((r) => {
      this.setData({ sessionId: (r.data && (r.data.id || r.data.session_id)) || '' });
    }).catch((e) => this.setData({ err: e.message || '创建会话失败' }));
  },

  onInput(e) { this.setData({ input: e.detail.value }); },

  send() {
    const { sessionId, input, busy } = this.data;
    if (!sessionId || !input || busy) return;
    this.setData({ busy: true, err: '' });
    api.post('/sessions/' + sessionId + '/messages', { content: input })
      .then(() => api.get('/sessions/' + sessionId))
      .then((r) => {
        this.setData({ reply: lastReply(r.data || r), input: '', busy: false });
      })
      .catch((e) => this.setData({ err: e.message || '发送失败', busy: false }));
  }
});
