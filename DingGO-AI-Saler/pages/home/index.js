const setTab = require('../../utils/tab');
const config = require('../../config/index');
const { refreshUser } = require('../../services/request');
const fmt = require('../../utils/format');
const storage = require('../../utils/storage');
const { listStores } = require('../../services/store');
const { listTodos, setTodoDone } = require('../../services/todo');
const { getTodayPanel, markReviewSeen } = require('../../services/assistant');
const { ask } = require('../../services/chat');
const practice = require('../../services/practice');
const { wechatMessage } = require('../../utils/playbook');

const app = getApp();
const HISTORY_KEY = 'chatHistory';
const PLACEHOLDERS = [
  '试试问：这家店最关心什么？',
  '试试问：客户说网上更便宜怎么回？',
  '试试问：下一步该做什么？',
  '试试问：上次拜访评分多少？',
  '试试问：介绍一下这家门店',
];

let seq = 0;
const mid = () => `m${Date.now()}${(seq += 1)}`;

Page({
  data: {
    statusBarHeight: 20,
    navHeight: 44,
    navRight: 100,
    month: 0,
    day: 0,
    stores: [],
    currentStore: null,
    todayHint: '',
    todayCount: 0,
    chatId: '',
    // 消息类型：user | ai | todo | practice-intro | customer | result | divider
    messages: [],
    input: '',
    thinking: false,
    scrollTo: '',
    placeholder: PLACEHOLDERS[0],
    defaultPlaceholder: PLACEHOLDERS[0],
    practice: null, // 陪练进行中：{ sessionId, scenarioId, title, round, total }
    drawerOpen: false,
    histories: [],
  },

  onLoad() {
    const win = wx.getWindowInfo();
    const menu = wx.getMenuButtonBoundingClientRect();
    const now = new Date();
    const ph = PLACEHOLDERS[now.getDate() % PLACEHOLDERS.length];
    this.setData({
      statusBarHeight: win.statusBarHeight,
      navHeight: (menu.top - win.statusBarHeight) * 2 + menu.height,
      navRight: win.windowWidth - menu.left + 8,
      month: now.getMonth() + 1,
      day: now.getDate(),
      placeholder: ph,
      defaultPlaceholder: ph,
      chatId: `c${Date.now()}`,
    });
  },

  onShow() {
    setTab(this, 0);
    this.loadHeader();
    // 从简报 / 异议页点「陪练」跳回首页时，直接开始对应场景
    const pending = app.globalData.pendingPractice;
    if (pending) {
      app.globalData.pendingPractice = null;
      this.push({ type: 'user', text: 'AI 销售陪练' });
      this.startScenario(pending);
    }
  },

  onHide() {
    if (this.data.drawerOpen) this.toggleDrawer();
  },

  async loadHeader() {
    if (!config.useMock) await refreshUser();
    const stores = await listStores();
    const currentStore = stores.find((s) => s.id === app.globalData.currentStoreId) || stores[0] || null;
    const panel = await getTodayPanel(currentStore && currentStore.id);
    this.setData({ stores, currentStore, todayHint: panel.hint, todayCount: panel.count });
  },

  // ---- 消息工具 ----
  push(...items) {
    const messages = [...this.data.messages, ...items.map((m) => ({ id: mid(), ...m }))];
    this.setData({ messages, scrollTo: '' });
    this.setData({ scrollTo: 'bottom' });
    this.saveChat(messages);
  },
  // 替换某条消息（用于刷新今日待办卡片）
  replace(id, patch) {
    this.setData({ messages: this.data.messages.map((m) => (m.id === id ? { ...m, ...patch } : m)) });
  },
  saveChat(messages) {
    const first = messages.find((m) => m.type === 'user');
    if (!first) return;
    const { chatId, currentStore } = this.data;
    const list = storage.get(HISTORY_KEY, []).filter((h) => h.id !== chatId);
    list.unshift({ id: chatId, title: first.text.slice(0, 16), storeName: currentStore ? currentStore.name : '', updatedAt: Date.now(), messages });
    storage.set(HISTORY_KEY, list.slice(0, 30));
  },

  // ---- 门店 ----
  switchStore() {
    const { stores } = this.data;
    if (!stores.length) return this.go('/pages/store/edit/index');
    // 门店很多（上千家）：用可搜索的选择页，不能用最多 6 项的操作菜单
    wx.navigateTo({
      url: '/pages/store/pick/index',
      events: { picked: (store) => this.useStore(store) },
    });
  },
  useStore(store) {
    app.setCurrentStore(store.id);
    this.setData({ currentStore: store });
    this.loadHeader();
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

  // ---- 选项一：今日待办 ----
  async openToday() {
    const panel = await getTodayPanel(this.storeId());
    this.push({ type: 'user', text: '今日待办' }, { type: 'todo', panel });
  },
  async refreshToday(msgId) {
    const panel = await getTodayPanel(this.storeId());
    this.replace(msgId, { panel });
    this.loadHeader();
  },
  async onRowAction(e) {
    const { action, id, msg } = e.currentTarget.dataset;
    if (action === 'todoDone') {
      await setTodoDone(id, true);
      wx.showToast({ title: '已完成', icon: 'success' });
      return this.refreshToday(msg);
    }
    if (action === 'copyMsg') {
      const todo = (await listTodos()).find((t) => t.id === id);
      return wx.setClipboardData({
        data: wechatMessage(todo, app.globalData.userName),
        success: () => wx.showToast({ title: '话术已复制，去微信粘贴', icon: 'none' }),
      });
    }
    if (action === 'openVisit') {
      markReviewSeen(id);
      return this.go(`/pages/visit/detail/index?id=${id}`);
    }
    if (action === 'openBrief') return this.go(`/pages/brief/index?storeId=${id}`);
    if (action === 'allTodos') return this.go('/pages/todo/index');
    if (action === 'record') return this.startRecord();
    return undefined;
  },

  // ---- 选项二：AI 销售陪练（对话内模式，参考 AI 诊室） ----
  async openPractice() {
    if (this.data.drawerOpen) this.toggleDrawer();
    if (this.data.practice) this.exitPractice();
    const scenarios = await practice.listScenarios();
    this.push({ type: 'user', text: 'AI 销售陪练' }, { type: 'practice-intro', scenarios });
  },
  pickScenario(e) {
    const { id, title } = e.currentTarget.dataset;
    this.push({ type: 'user', text: `练「${title}」` });
    this.startScenario(id);
  },
  async startScenario(scenarioId) {
    if (this.data.practice) this.exitPractice();
    const res = await practice.start(scenarioId);
    this.setData({
      practice: { sessionId: res.sessionId, scenarioId, title: res.title, round: 1, total: res.totalRounds },
      placeholder: '你会怎么回答老板？',
    });
    this.push({ type: 'customer', text: res.reply });
  },
  async practiceTurn(text) {
    const p = this.data.practice;
    this.push({ type: 'user', text });
    this.setData({ thinking: true });
    const res = await practice.turn(p.sessionId, text);
    if (!res.finished) {
      this.setData({ thinking: false, 'practice.round': p.round + 1 });
      this.push({ type: 'customer', text: res.reply });
      return;
    }
    const result = await practice.finish(p.sessionId);
    this.setData({ thinking: false });
    this.push({ type: 'result', result: { ...result, scenarioId: p.scenarioId, title: p.title } });
    this.exitPractice();
  },
  exitPractice() {
    if (!this.data.practice) return;
    this.setData({ practice: null, placeholder: this.data.defaultPlaceholder });
    this.push({ type: 'divider', text: '你已退出 AI 陪练' });
  },
  againPractice(e) {
    this.startScenario(e.currentTarget.dataset.id);
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
    this.setData({ input: '' });
    if (this.data.practice) {
      this.practiceTurn(question);
      return;
    }
    this.push({ type: 'user', text: question });
    this.setData({ thinking: true });
    let reply;
    try {
      reply = await ask({
        question,
        storeId: this.storeId(),
        history: this.data.messages.filter((m) => m.type === 'user' || m.type === 'ai').slice(-10).map((m) => ({ role: m.type, text: m.text })),
      });
    } catch (err) {
      reply = { text: `出错了：${err.message}，请稍后再试。`, suggestions: [] };
    }
    this.setData({ thinking: false });
    this.push({ type: 'ai', text: reply.text, suggestions: reply.suggestions || [] });
  },
  newChat() {
    if (this.data.practice) this.setData({ practice: null, placeholder: this.data.defaultPlaceholder });
    this.setData({ chatId: `c${Date.now()}`, messages: [], input: '', scrollTo: '' });
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
    if (h) this.setData({ chatId: h.id, messages: h.messages, practice: null, placeholder: this.data.defaultPlaceholder, scrollTo: 'bottom' });
  },
  noop() {},
});
