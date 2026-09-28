// 本机存储的安全读写（存储不可用时不影响页面）
function get(key, fallback) {
  try {
    const v = wx.getStorageSync(key);
    return v === '' || v == null ? fallback : v;
  } catch (e) {
    return fallback;
  }
}
function set(key, value) {
  try { wx.setStorageSync(key, value); } catch (e) { /* 忽略 */ }
}
module.exports = { get, set };
