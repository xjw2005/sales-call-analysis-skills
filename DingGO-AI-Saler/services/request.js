const config = require('../config/index');

const TOKEN_KEY = 'token';
const USER_KEY = 'user';
let loginPromise = null;

// 去掉值为空的参数，避免把 undefined 当成字符串传给后端
function clean(data) {
  if (!data || typeof data !== 'object') return data;
  const out = {};
  Object.keys(data).forEach((k) => {
    if (data[k] !== undefined && data[k] !== null) out[k] = data[k];
  });
  return out;
}

function getToken() {
  try { return wx.getStorageSync(TOKEN_KEY) || ''; } catch (e) { return ''; }
}

// 发送一次请求（不处理登录），返回 { statusCode, data }
function send({ url, method, data, token }) {
  const header = token ? { Authorization: `Bearer ${token}` } : {};
  if (config.transport === 'cloud') {
    return wx.cloud.callContainer({
      config: { env: config.cloudEnv },
      path: url,
      method,
      data,
      header: { ...header, 'X-WX-SERVICE': config.cloudService },
    }).then((res) => ({ statusCode: res.statusCode, data: res.data }));
  }
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${config.baseUrl}${url}`,
      method,
      data,
      header,
      success: (res) => resolve({ statusCode: res.statusCode, data: res.data }),
      fail: (err) => reject(new Error(err.errMsg || '网络异常')),
    });
  });
}

// 记住当前登录的人，界面上显示的名字都从这里来（合并账号后会变成旧人员的名字）
function saveUser(user) {
  if (!user) return;
  try { wx.setStorageSync(USER_KEY, user); } catch (e) { /* 存储不可用时忽略 */ }
  const app = getApp();
  if (app && user.name) app.globalData.userName = user.name;
}

// 微信登录：wx.login 拿 code → 后端换登录凭证；多个请求同时触发时只登录一次
function login() {
  if (!loginPromise) {
    loginPromise = new Promise((resolve, reject) => {
      wx.login({ success: resolve, fail: () => reject(new Error('微信登录失败')) });
    })
      .then(({ code }) => send({ url: '/auth/wx-login', method: 'POST', data: { code, name: '新用户' } }))
      .then((res) => {
        if (res.statusCode !== 200) throw new Error((res.data && res.data.message) || '登录失败');
        wx.setStorageSync(TOKEN_KEY, res.data.token);
        saveUser(res.data.user);
        return res.data.token;
      })
      .finally(() => { loginPromise = null; });
  }
  return loginPromise;
}

async function ensureToken() {
  return getToken() || login();
}

// 真实接口请求：自动登录并带凭证；凭证失效（401）时重新登录再试一次；非 2xx 统一抛错
async function request({ url, method = 'GET', data }) {
  let res = await send({ url, method, data: clean(data), token: await ensureToken() });
  if (res.statusCode === 401) {
    wx.removeStorageSync(TOKEN_KEY);
    res = await send({ url, method, data: clean(data), token: await login() });
  }
  if (res.statusCode >= 200 && res.statusCode < 300) return res.data;
  throw new Error((res.data && res.data.message) || `请求失败 ${res.statusCode}`);
}

// 向后端取当前登录的人并更新本地名字；演示模式或网络失败时保持原样
async function refreshUser() {
  if (config.useMock) return null;
  try {
    const user = await request({ url: '/auth/me' });
    saveUser(user);
    return user;
  } catch (e) {
    return null;
  }
}

const delay = (value, ms = 200) => new Promise((resolve) => setTimeout(() => resolve(value), ms));

module.exports = { request, delay, ensureToken, refreshUser };
