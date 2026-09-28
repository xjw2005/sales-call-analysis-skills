const setTab = require('../../utils/tab');
const fmt = require('../../utils/format');
const storage = require('../../utils/storage');
const { listStores } = require('../../services/store');
const { listVisits } = require('../../services/visit');
const { listTodos, setTodoDone } = require('../../services/todo');
const { getTodayFeed, markReviewSeen } = require('../../services/assistant');
const { ask } = require('../../services/chat');
const { wechatMessage } = require('../../utils/playbook');

const app = getApp();
const HISTORY_KEY = 'chatHistory';

// 固定选项：新手不用组织问题，点了就走
const OPTIONS = [
  { key: 'brief', text: '拜访前准备' },
  { key: 'review', text: '拜访复盘' },
  { key: 'objection', text: '异议应对' },
  { key: 'todo', text: '我的待办' },
  { key: 'profile', text: '介绍一下这家门店' },
];

// 输入框提示语每天换一句，教新手可以怎么问
const PLACEHOLDERS = [
  '试试问：这家店最关心什么？',
  '试试问：客户说网上更便宜怎么回？',
  '试试问：下一步该做什么？',
  '试试问：上次拜访评分多少？',
  '试试问：介绍一下这家门店',
];

function greeting() {
  const h = new Date().getHours();
  if (h < 11) return '早上好';
  if (h < 14) return '中午好';
  if (h < 18) return '下午好';
  return '晚上好';
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
    steps: null,
    feed: null, // 助手主动发的第一条消息 { text, cards }
    options: OPTIONS,
    placeholder: PLACEHOLDERS[0],
    chatId: '',
    messages: [], // 之后的问答 { id, role: 'user'|'ai', text, suggestions }
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
      placeholder: PLACEHOLDERS[now.getDate() % PLACEHOLDERS.length],
      chatId: `c${Date.now()}`,
    });
  },

  onShow() {
    setTab(this, 0);
    this.load();
  },

  onHide() {
    if (this.data.drawerOpen) this.toggleDrawer();
  },

  async load() {
    const [stores, visits, todos] = await Promise.all([listStores(), listVisits(), listTodos()]);
    const currentStore = stores.find((s) => s.id === app.globalData.currentStoreId) || stores[0] || null;
    const { cards, steps } = await getTodayFeed(currentStore && currentStore.id);
    this.todos = todos;
    this.visits = visits;
    this.setData({
      stores,
      currentStore,
      steps,
      feed: {
        text: cards.length
          ? `${this.data.userName}，${greeting()}！今天有 ${cards.length} 件事建议你做：`
          : `${this.data.userName}，${greeting()}！今天没有要提醒你的事，想了解哪家门店直接问我。`,
        cards,
      },
    });
  },

  // ---- 门店上下文 ----
  switchStore() {
    const { stores } = this.data;
    if (!stores.length) return this.go('/pages/store/edit/index');
    const shown = stores.slice(0, 6);
    wx.showActionSheet({
      itemList: shown.map((s) => s.name),
      success: ({ tapIndex }) => this.useStore(shown[tapIndex]),
    });
  },
  useStore(store) {
    app.setCurrentStore(store.id);
    this.setData({ currentStore: store });
    this.load();
  },
  addStore() {
    this.go('/pages/store/edit/index');
  },
  go(url) {
    wx.navigateTo({ url });
  },
  storeId() {
    return this.data.currentStore ? this.data.currentStore.id : '';
  },

  // ---- 主动卡片按钮 ----
  async onCardAction(e) {
    const { action, id } = e.currentTarget.dataset;
    if (action === 'todoDone') {
      await setTodoDone(id, true);
      wx.showToast({ title: '已完成', icon: 'success' });
      return this.load();
    }
    if (action === 'copyMsg') {
      const todo = this.todos.find((t) => t.id === id);
      return wx.setClipboardData({
        data: wechatMessage(todo, app.globalData.userName),
        success: () => wx.showToast({ title: '话术已复制，去微信粘贴', icon: 'none' }),
      });
    }
    if (action === 'dismissReview') {
      markReviewSeen(id);
      return this.load();
    }
    if (action === 'openVisit') {
      markReviewSeen(id);
      return this.go(`/pages/visit/detail/index?id=${id}`);
    }
    if (action === 'openTodos') return this.go('/pages/todo/index');
    if (action === 'openBrief') return this.go(`/pages/brief/index?storeId=${id}`);
    if (action === 'record') return this.go(`/pages/record/index?storeId=${id}`);
    if (action === 'openObjection') return this.go(`/pages/objection/index?name=${encodeURIComponent(id)}`);
    return undefined;
  },

  // ---- 固定选项 ----
  onOption(e) {
    const { key } = e.currentTarget.dataset;
    const sid = this.storeId();
    if (key === 'brief') return sid ? this.go(`/pages/brief/index?storeId=${sid}`) : this.addStore();
    if (key === 'review') {
      const v = this.visits.find((x) => x.status === 'done');
      return v ? this.go(`/pages/visit/detail/index?id=${v.id}`) : wx.showToast({ title: '还没有分析完成的拜访', icon: 'none' });
    }
    if (key === 'objection') return this.go('/pages/objection/index');
    if (key === 'todo') return this.go('/pages/todo/index');
    if (key === 'profile') return this.send('介绍一下这家门店');
    return undefined;
  },

  // ---- 问答 ----
  onInput(e) {
    this.setData({ input: e.detail.value });
  },
  tapSuggestion(e) {
    this.send(e.currentTarget.dataset.q);
  },
  submit() {
    this.send(this.data.input);
  },
  async send(text) {
    const question = (text || '').trim();
    if (!question || this.data.thinking) return;
    const messages = [...this.data.messages, { id: `m${Date.now()}`, role: 'user', text: question }];
    this.setData({ messages, input: '', thinking: true, scrollTo: 'bottom' });
    let reply;
    try {
      reply = await ask({
        question,
        storeId: this.storeId(),
        history: messages.slice(-10).map((m) => ({ role: m.role, text: m.text })),
      });
    } catch (err) {
      reply = { text: `出错了：${err.message}，请稍后再试。`, suggestions: [] };
    }
    const next = [...messages, { id: `m${Date.now()}a`, role: 'ai', text: reply.text, suggestions: reply.suggestions || [] }];
    this.setData({ messages: next, thinking: false, scrollTo: 'bottom' });
    this.saveChat(next);
  },
  saveChat(messages) {
    const { chatId, currentStore } = this.data;
    const list = storage.get(HISTORY_KEY, []).filter((h) => h.id !== chatId);
    list.unshift({ id: chatId, title: messages[0].text.slice(0, 16), storeName: currentStore ? currentStore.name : '', updatedAt: Date.now(), messages });
    storage.set(HISTORY_KEY, list.slice(0, 30));
  },
  newChat() {
    this.setData({ chatId: `c${Date.now()}`, messages: [], input: '', scrollTo: '' });
    this.load();
  },

  // ---- 录音 ----
  startRecord() {
    const sid = this.storeId();
    this.go(`/pages/record/index${sid ? `?storeId=${sid}` : ''}`);
  },
  moreActions() {
    wx.showActionSheet({
      itemList: ['从聊天记录导入录音', '查看当前门店档案', '新对话'],
      success: ({ tapIndex }) => {
        const sid = this.storeId();
        if (tapIndex === 0) this.go(`/pages/record/index?mode=import${sid ? `&storeId=${sid}` : ''}`);
        if (tapIndex === 1 && sid) this.go(`/pages/store/detail/index?id=${sid}`);
        if (tapIndex === 2) this.newChat();
      },
    });
  },

  // ---- 陪练 ----
  openPractice() {
    if (this.data.drawerOpen) this.toggleDrawer();
    this.go('/pages/practice/list/index');
  },

  // ---- 抽屉：打开时隐藏自定义标签栏，避免它浮在抽屉之上 ----
  toggleDrawer() {
    const drawerOpen = !this.data.drawerOpen;
    this.setData({
      drawerOpen,
      histories: drawerOpen ? storage.get(HISTORY_KEY, []).map((h) => ({ ...h, time: fmt.date(h.updatedAt) })) : this.data.histories,
    });
    if (typeof this.getTabBar === 'function' && this.getTabBar()) this.getTabBar().setData({ hidden: drawerOpen });
  },
  pickStore(e) {
    this.toggleDrawer();
    this.useStore(this.data.stores.find((x) => x.id === e.currentTarget.dataset.id));
  },
  openHistory(e) {
    const h = storage.get(HISTORY_KEY, []).find((x) => x.id === e.currentTarget.dataset.id);
    this.toggleDrawer();
    if (h) this.setData({ chatId: h.id, messages: h.messages, scrollTo: 'bottom' });
  },
  noop() {},
});
