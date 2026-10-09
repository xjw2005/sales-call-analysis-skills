const setTab = require('../../utils/tab');
const config = require('../../config/index');
const { refreshUser } = require('../../services/request');
const fmt = require('../../utils/format');
const storage = require('../../utils/storage');
const { listStores } = require('../../services/store');
const { listTodos, setTodoDone } = require('../../services/todo');
const { getTodayPanel, markReviewSeen } = require('../../services/assistant');
const { askStream, sendFeedback, listSessions, getSession } = require('../../services/chat');
const plans = require('../../services/plan');
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
    sessionId: 0, // 服务端的对话号（真实模式）：上下文由服务端按它取；第一次提问后由服务端分配
    // 消息类型：user | ai | greet | cands | plan | todo | practice-intro | customer | result | divider
    messages: [],
    input: '',
    thinking: false,
    asking: false, // 正在流式问答（回答气泡自己显示进度，不再显示底部的三个点）
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
    this.greet();
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
    if (!config.useMock && !config.mockAI) return; // 真实模式：对话在服务端保存
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

  // ---- 主动问候：每次新对话先看今天有没有真正的待办（计划 / 到期的约定），没有就问今天去哪 ----
  async greet() {
    const { chatId, messages, practice } = this.data;
    if (practice || messages.length || this.greetedChat === chatId) return;
    this.greetedChat = chatId;
    try {
      const g = await plans.greeting();
      if (this.data.chatId !== chatId || this.data.messages.length) return;
      this.push({ type: 'greet', greeting: g });
    } catch (err) {
      this.greetedChat = '';
    }
  },
  // 点选项：按区 / 最近拜访 / 到期约定 → 给出候选门店卡片
  async tapOption(e) {
    const { kind, district, label } = e.currentTarget.dataset;
    if (this.data.thinking) return;
    this.push({ type: 'user', text: label });
    this.setData({ thinking: true });
    try {
      const res = await plans.suggest({ kind, district });
      this.setData({ thinking: false });
      if (!res.items.length) {
        this.push({ type: 'ai', text: kind === 'commitments' ? '最近没有到期的约定。' : '没有找到合适的门店，可以换一个区，或直接告诉我想去哪家。', suggestions: [] });
        return;
      }
      this.push({ type: 'cands', cands: { title: res.title, kind, items: res.items.map((it) => ({ ...it, checked: !!it.checked })), done: false } });
    } catch (err) {
      this.setData({ thinking: false });
      this.push({ type: 'ai', text: `出错了：${err.message}`, suggestions: [] });
    }
  },
  toggleCand(e) {
    const { msg, i } = e.currentTarget.dataset;
    const m = this.data.messages.find((x) => x.id === msg);
    if (!m || m.cands.done) return;
    const items = m.cands.items.map((it, idx) => (idx === Number(i) ? { ...it, checked: !it.checked } : it));
    this.replace(msg, { cands: { ...m.cands, items } });
  },
  async confirmCands(e) {
    const m = this.data.messages.find((x) => x.id === e.currentTarget.dataset.msg);
    if (!m || m.cands.done) return;
    const picked = m.cands.items.filter((it) => it.checked);
    if (!picked.length) return wx.showToast({ title: '先勾选要去的门店', icon: 'none' });
    try {
      const reasons = {};
      picked.forEach((it) => { reasons[it.storeId] = it.reason; });
      const plan = await plans.addPlan(picked.map((it) => it.storeId), m.cands.kind, reasons);
      this.replace(m.id, { cands: { ...m.cands, done: true } });
      this.push({ type: 'ai', text: `好的，已加入今日计划，共 ${plan.length} 家。到店后点「到店录音」就行。`, suggestions: [] }, { type: 'plan', plan });
      this.loadHeader();
    } catch (err) {
      wx.showToast({ title: err.message, icon: 'none' });
    }
    return undefined;
  },
  async onPlanAction(e) {
    const { action, id, store, plan } = e.currentTarget.dataset;
    if (action === 'brief') {
      app.setCurrentStore(store);
      return this.go(`/pages/brief/index?storeId=${store}`);
    }
    if (action === 'record') {
      app.setCurrentStore(store);
      return this.go(`/pages/record/index?storeId=${store}`);
    }
    if (action === 'visit') return this.go(`/pages/visit/detail/index?id=${id}`);
    if (action === 'remove') {
      const { confirm } = await new Promise((resolve) => wx.showModal({ title: '移出今日计划', content: '不去这家店了吗？', success: resolve }));
      if (!confirm) return undefined;
      await plans.removePlan(id);
      const fresh = await plans.todayPlan();
      this.replace(plan, { plan: fresh });
      this.loadHeader();
    }
    return undefined;
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
    if (action === 'recordStore') {
      app.setCurrentStore(id);
      return this.go(`/pages/record/index?storeId=${id}`);
    }
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
    await this.askAI(question);
  },
  // 流式问答：先放一个空的回答气泡，收到一点文字就更新一点；结束后补候选门店、计划卡片
  async askAI(question) {
    this.push({ type: 'ai', text: '', status: '思考中…', streaming: true, suggestions: [] });
    const id = this.data.messages[this.data.messages.length - 1].id;
    this.setData({ thinking: true, asking: true });
    let text = '';
    let timer = null;
    const flush = () => { timer = null; this.replace(id, { text, status: '' }); this.setData({ scrollTo: 'bottom' }); };
    try {
      const reply = await askStream(
        { question, storeId: this.storeId(), sessionId: this.data.sessionId || undefined },
        {
          onStatus: (st) => { if (!text) this.replace(id, { status: st }); },
          onDelta: (d) => { text += d; if (!timer) timer = setTimeout(flush, 80); },
        },
      );
      if (timer) clearTimeout(timer);
      if (reply.sessionId) this.setData({ sessionId: reply.sessionId });
      this.replace(id, { text: reply.text || text, status: '', streaming: false, suggestions: reply.suggestions || [], demo: !!reply.demo, logId: reply.logId || 0 });
      this.setData({ thinking: false, asking: false });
      // 对话里说“今天想去销量低的店”：助手给出真实候选门店，勾选后确定今日计划
      if (reply.cands && reply.cands.items && reply.cands.items.length) {
        const { title, kind, items } = reply.cands;
        this.push({ type: 'cands', cands: { title, kind, items: items.map((it) => ({ ...it, checked: !!it.checked })), done: false } });
      }
      // 对话里说“把前三家加进今天”：计划已经改了，展示最新的今日计划
      if (reply.plan) {
        this.push({ type: 'plan', plan: reply.plan });
        this.loadHeader();
      }
    } catch (err) {
      if (timer) clearTimeout(timer);
      this.replace(id, { text: text || err.message || '出错了，请稍后再试', status: '', streaming: false });
      this.setData({ thinking: false, asking: false });
    }
    this.saveChat(this.data.messages);
  },
  // 对回答点「有用 / 没用」；没用时可以补一句原因
  async rate(e) {
    const { id, rating } = e.currentTarget.dataset;
    const m = this.data.messages.find((x) => x.id === id);
    if (!m || !m.logId || m.rating) return;
    const value = Number(rating);
    this.replace(id, { rating: value });
    try {
      await sendFeedback(m.logId, value);
    } catch (err) {
      this.replace(id, { rating: 0 });
      return wx.showToast({ title: err.message, icon: 'none' });
    }
    if (value < 0) {
      wx.showModal({
        title: '哪里不对？', editable: true, placeholderText: '例如：没找到我想要的店 / 回答和这家店对不上（可不填）', confirmText: '提交', cancelText: '跳过',
        success: (r) => { if (r.confirm && r.content && r.content.trim()) sendFeedback(m.logId, -1, r.content.trim()).catch(() => {}); },
      });
    } else {
      wx.showToast({ title: '谢谢反馈', icon: 'none' });
    }
    return undefined;
  },
  newChat() {
    if (this.data.practice) this.setData({ practice: null, placeholder: this.data.defaultPlaceholder });
    this.setData({ chatId: `c${Date.now()}`, sessionId: 0, messages: [], input: '', scrollTo: '' });
    this.greet();
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
    this.setData({ drawerOpen });
    if (drawerOpen) this.loadHistories();
    if (typeof this.getTabBar === 'function' && this.getTabBar()) this.getTabBar().setData({ hidden: drawerOpen });
  },
  // 对话记录：真实模式从服务端取（只有自己的），演示模式用本机存储
  async loadHistories() {
    if (config.useMock || config.mockAI) {
      return this.setData({ histories: storage.get(HISTORY_KEY, []).map((h) => ({ ...h, time: fmt.date(h.updatedAt) })) });
    }
    try {
      const list = await listSessions();
      return this.setData({ histories: list.map((h) => ({ id: h.id, title: h.title, time: fmt.date(h.updatedAt) })) });
    } catch (err) {
      return wx.showToast({ title: err.message, icon: 'none' });
    }
  },
  pickStore(e) {
    this.toggleDrawer();
    this.useStore(this.data.stores.find((x) => x.id === e.currentTarget.dataset.id));
  },
  async openHistory(e) {
    const id = e.currentTarget.dataset.id;
    this.toggleDrawer();
    if (config.useMock || config.mockAI) {
      const h = storage.get(HISTORY_KEY, []).find((x) => x.id === id);
      if (h) this.setData({ chatId: h.id, messages: h.messages.map((m) => (m.streaming ? { ...m, streaming: false, text: m.text || '（这条回答没有完成）' } : m)), practice: null, placeholder: this.data.defaultPlaceholder, scrollTo: 'bottom' });
      return;
    }
    try {
      const rows = await getSession(id);
      // 服务端保存的消息还原成页面上的消息：提问 / 回答 / 候选门店卡片 / 今日计划卡片
      const messages = rows.map((m) => {
        if (m.role === 'user') return { id: mid(), type: 'user', text: m.text };
        if (m.role === 'ai') return { id: mid(), type: 'ai', text: m.text, suggestions: [], logId: m.logId || 0 };
        if (m.role === 'cands') return { id: mid(), type: 'cands', cands: { ...m.payload, items: (m.payload.items || []).map((it) => ({ ...it, checked: false })), done: true } };
        return { id: mid(), type: 'plan', plan: (m.payload && m.payload.plan) || [] };
      });
      this.setData({ chatId: `s${id}`, sessionId: Number(id), messages, practice: null, placeholder: this.data.defaultPlaceholder, scrollTo: 'bottom' });
    } catch (err) {
      wx.showToast({ title: err.message, icon: 'none' });
    }
  },
  noop() {},
});
