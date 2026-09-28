const setTab = require('../../utils/tab');
const fmt = require('../../utils/format');
const { listStores } = require('../../services/store');
const { listVisits } = require('../../services/visit');
const { ask } = require('../../services/chat');

const app = getApp();
const HISTORY_KEY = 'chatHistory';
const WELCOME_PROMPTS = ['介绍一下这家门店', '这家店最关心什么？', '下一步该做什么？', '上次拜访评分多少？'];

function readHistory() {
  try { return wx.getStorageSync(HISTORY_KEY) || []; } catch (e) { return []; }
}
function writeHistory(list) {
  try { wx.setStorageSync(HISTORY_KEY, list.slice(0, 30)); } catch (e) { /* 存储不可用时忽略 */ }
}

Page({
  data: {
    statusBarHeight: 20,
    navHeight: 44,
    userName: '',
    month: 0,
    day: 0,
    stores: [],
    currentStore: null,
    pendingCount: 0,
    prompts: WELCOME_PROMPTS,
    chatId: '',
    messages: [], // { id, role: 'user'|'ai', text, suggestions }
    input: '',
    thinking: false,
    scrollTo: '',
    drawerOpen: false,
    histories: [],
  },

  onLoad() {
    const win = wx.getWindowInfo();
    const menu = wx.getMenuButtonBoundingClientRect();
    const now = new Date();
    this.setData({
      statusBarHeight: win.statusBarHeight,
      navHeight: (menu.top - win.statusBarHeight) * 2 + menu.height,
      userName: app.globalData.userName,
      month: now.getMonth() + 1,
      day: now.getDate(),
      chatId: `c${Date.now()}`,
    });
  },

  onShow() {
    setTab(this, 0);
    this.load();
  },

  async load() {
    const [stores, visits] = await Promise.all([listStores(), listVisits()]);
    const currentStore = stores.find((s) => s.id === app.globalData.currentStoreId) || stores[0] || null;
    this.setData({
      stores,
      currentStore,
      pendingCount: visits.filter((v) => v.status === 'cost_pending').length,
      histories: readHistory().map((h) => ({ ...h, time: fmt.date(h.updatedAt) })),
    });
  },

  // ---- 门店上下文 ----
  switchStore() {
    const { stores } = this.data;
    if (!stores.length) return this.addStore();
    const shown = stores.slice(0, 6);
    wx.showActionSheet({
      itemList: shown.map((s) => s.name),
      success: ({ tapIndex }) => this.useStore(shown[tapIndex]),
    });
  },
  useStore(store) {
    app.setCurrentStore(store.id);
    this.setData({ currentStore: store });
  },
  addStore() {
    wx.navigateTo({ url: '/pages/store/edit/index' });
  },

  // ---- 录音入口 ----
  startRecord() {
    const s = this.data.currentStore;
    wx.navigateTo({ url: `/pages/record/index${s ? `?storeId=${s.id}` : ''}` });
  },
  moreActions() {
    wx.showActionSheet({
      itemList: ['开始拜访录音', '从聊天记录导入录音', '查看门店档案'],
      success: ({ tapIndex }) => {
        const s = this.data.currentStore;
        if (tapIndex === 0) this.startRecord();
        if (tapIndex === 1) wx.navigateTo({ url: `/pages/record/index?mode=import${s ? `&storeId=${s.id}` : ''}` });
        if (tapIndex === 2 && s) wx.navigateTo({ url: `/pages/store/detail/index?id=${s.id}` });
      },
    });
  },
  openPending() {
    wx.switchTab({ url: '/pages/visit/list/index' });
  },

  // ---- 问答 ----
  onInput(e) {
    this.setData({ input: e.detail.value });
  },
  tapPrompt(e) {
    this.send(e.currentTarget.dataset.q);
  },
  submit() {
    this.send(this.data.input);
  },
  async send(text) {
    const question = (text || '').trim();
    if (!question || this.data.thinking) return;
    const userMsg = { id: `m${Date.now()}`, role: 'user', text: question };
    const messages = [...this.data.messages, userMsg];
    this.setData({ messages, input: '', thinking: true, scrollTo: 'bottom' });
    let reply;
    try {
      const store = this.data.currentStore;
      reply = await ask({
        question,
        storeId: store && store.id,
        history: messages.slice(-10).map((m) => ({ role: m.role, text: m.text })),
      });
    } catch (err) {
      reply = { text: `出错了：${err.message}，请稍后再试。`, suggestions: [] };
    }
    const aiMsg = { id: `m${Date.now()}a`, role: 'ai', text: reply.text, suggestions: reply.suggestions || [] };
    this.setData({ messages: [...messages, aiMsg], thinking: false, scrollTo: 'bottom' });
    this.saveChat();
  },
  saveChat() {
    const { chatId, messages, currentStore } = this.data;
    const list = readHistory().filter((h) => h.id !== chatId);
    list.unshift({
      id: chatId,
      title: messages[0].text.slice(0, 16),
      storeName: currentStore ? currentStore.name : '',
      updatedAt: Date.now(),
      messages,
    });
    writeHistory(list);
  },
  newChat() {
    this.setData({ chatId: `c${Date.now()}`, messages: [], input: '' });
  },

  // ---- 抽屉 ----
  toggleDrawer() {
    this.setData({ drawerOpen: !this.data.drawerOpen, histories: readHistory().map((h) => ({ ...h, time: fmt.date(h.updatedAt) })) });
  },
  pickStore(e) {
    this.useStore(this.data.stores.find((x) => x.id === e.currentTarget.dataset.id));
    this.setData({ drawerOpen: false });
  },
  openHistory(e) {
    const h = readHistory().find((x) => x.id === e.currentTarget.dataset.id);
    if (h) this.setData({ chatId: h.id, messages: h.messages, drawerOpen: false, scrollTo: 'bottom' });
  },
  noop() {},
});
