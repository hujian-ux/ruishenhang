const KEY = 'rsp_session';

function saveSession(s) {
  wx.setStorageSync(KEY, s);
}

function loadSession() {
  try { return wx.getStorageSync(KEY) || null; } catch (e) { return null; }
}

function clearSession() {
  try { wx.removeStorageSync(KEY); } catch (e) {}
}

module.exports = { saveSession, loadSession, clearSession };
