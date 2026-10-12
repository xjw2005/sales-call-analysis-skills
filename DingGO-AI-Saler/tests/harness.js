// 小程序页面逻辑的测试环境：用 Node 模拟 wx / Page / getApp，不需要微信开发者工具。
// 运行：cd DingGO-AI-Saler && node --test tests/
const path = require('path');

const ROOT = path.resolve(__dirname, '..');

// 页面里的计时器（如录音计时、轮询）在测试里不应该阻止进程退出
const realSetInterval = global.setInterval;
global.setInterval = (...args) => { const t = realSetInterval(...args); if (t && t.unref) t.unref(); return t; };
const clone = (v) => JSON.parse(JSON.stringify(v));
const tick = async (n = 6) => { for (let i = 0; i < n; i += 1) await new Promise((r) => setImmediate(r)); };

// 可以手动决定什么时候返回的 Promise，用来制造「回答还没回来就换了对话」这类时序
function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function setPath(obj, key, value) {
  const parts = key.split('.');
  let cur = obj;
  parts.slice(0, -1).forEach((p) => { cur = cur[p]; });
  cur[parts[parts.length - 1]] = value;
}

// 加载一个页面：stubs 是 { '相对项目根的模块路径': 导出 }，会替换真实模块；其余模块用真实代码
function mountPage(pageFile, { stubs = {}, config = {}, storage = {}, modals = [], onWx = () => {} } = {}) {
  Object.keys(require.cache).forEach((k) => { if (k.startsWith(ROOT) && !k.includes('/tests/')) delete require.cache[k]; });
  Object.entries(stubs).forEach(([rel, exports]) => {
    const file = require.resolve(path.join(ROOT, rel));
    require.cache[file] = { id: file, filename: file, loaded: true, exports, children: [], paths: [] };
  });
  Object.assign(require(path.join(ROOT, 'config/index.js')), config);

  const log = { toasts: [], navigations: [], clipboard: [], modals: [], storage };
  const app = { globalData: { userName: '测试', currentStoreId: '' }, setCurrentStore(id) { this.globalData.currentStoreId = id; } };
  const tabBar = { data: {}, setData(p) { Object.assign(this.data, p); } };
  global.getApp = () => app;
  global.wx = {
    getStorageSync: (k) => (k in storage ? storage[k] : ''),
    setStorageSync: (k, v) => { storage[k] = clone(v); },
    removeStorageSync: (k) => { delete storage[k]; },
    getWindowInfo: () => ({ statusBarHeight: 20, windowWidth: 375 }),
    getMenuButtonBoundingClientRect: () => ({ top: 26, height: 32, left: 280 }),
    showToast: (o) => log.toasts.push(o.title),
    showLoading: () => {},
    hideLoading: () => {},
    // 弹窗：按 modals 队列里预先写好的答案响应（默认点「确定」）
    showModal: (o) => { log.modals.push(o); const r = modals.length ? modals.shift() : { confirm: true }; if (o.success) o.success(r); },
    navigateTo: (o) => log.navigations.push(`navigateTo:${o.url}`),
    redirectTo: (o) => log.navigations.push(`redirectTo:${o.url}`),
    switchTab: (o) => log.navigations.push(`switchTab:${o.url}`),
    reLaunch: (o) => log.navigations.push(`reLaunch:${o.url}`),
    navigateBack: () => log.navigations.push('navigateBack'),
    setNavigationBarTitle: () => {},
    stopPullDownRefresh: () => { log.pullStopped = (log.pullStopped || 0) + 1; },
    enableAlertBeforeUnload: () => { log.alertOn = true; },
    disableAlertBeforeUnload: () => { log.alertOn = false; },
    setClipboardData: (o) => { log.clipboard.push(o.data); if (o.success) o.success(); },
    getRecorderManager: () => ({ onStart() {}, onResume() {}, onPause() {}, onInterruptionBegin() {}, onInterruptionEnd() {}, onStop() {}, onError() {}, start() {}, stop() {}, pause() {}, resume() {} }),
    getFileSystemManager: () => ({ saveFile: ({ tempFilePath, success }) => success({ savedFilePath: tempFilePath }) }),
    createInnerAudioContext: () => ({ destroy() {} }),
  };
  onWx(global.wx);

  let def;
  global.Page = (d) => { def = d; };
  global.Component = () => {};
  require(path.join(ROOT, pageFile));
  const page = { ...def, data: clone(def.data) };
  page.setData = function setData(patch, cb) {
    Object.entries(patch).forEach(([k, v]) => setPath(this.data, k, v));
    if (cb) cb();
  };
  page.getTabBar = () => tabBar;
  return { page, log, app, tabBar };
}

module.exports = { mountPage, deferred, tick, clone };
