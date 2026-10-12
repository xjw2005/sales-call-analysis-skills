const fmt = require('../../../utils/format');
const { COOP_TONE, COOPERATION_STATUSES } = require('../../../utils/constants');
const { getStore, submitCorrection, updateStore, editProfile } = require('../../../services/store');
const { setTodoDone, cancelTodo } = require('../../../services/todo');

const STATE_TONE = { 稳定档案: 'success', 当前状态: 'info', 未确认: 'muted' };
const app = getApp();

// 门店详情是「以门店为中心」的枢纽：简报、问助手、记待办、编辑资料、录音都从这里出发
Page({
  data: { hasMetrics: true, id: '', coopTone: 'muted', store: null, tab: 0, tabs: ['门店档案', '拜访记录', '行动闭环'], grade: '', sections: [], canManage: false },
  onLoad({ id, tab }) {
    this.setData({ id, tab: Number(tab) || 0 });
  },
  onShow() {
    this.load();
  },
  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh());
  },
  async load() {
    try {
      const store = await getStore(this.data.id);
      const sections = store.profile ? store.profile.sections.map((s) => ({ ...s, tone: STATE_TONE[s.state] || 'muted' })) : [];
      store.loops = store.loops.map((l) => ({ ...l, time: fmt.date(l.createdAt) }));
      store.todos = (store.todos || []).map((t) => ({ ...t, due: t.due || { text: '', tone: 'muted' } }));
      store.legacyActions = (store.legacyActions || []).map((l) => ({ ...l, time: fmt.date(l.createdAt) }));
      const m = store.metrics || {};
      const hasMetrics = m.score !== null && m.score !== undefined || m.concernHits !== null && m.concernHits !== undefined || m.loopRate !== null && m.loopRate !== undefined;
      this.setData({ hasMetrics, store, sections, canManage: store.canManage !== false, coopTone: COOP_TONE[store.cooperationStatus] || 'muted', grade: fmt.grade(m.score) });
      wx.setNavigationBarTitle({ title: store.name });
    } catch (err) {
      wx.showToast({ title: err.message, icon: 'none' });
    }
  },
  switchTab(e) {
    this.setData({ tab: Number(e.currentTarget.dataset.i) });
  },
  sheet(itemList) {
    return new Promise((resolve) => wx.showActionSheet({ itemList, success: (r) => resolve(itemList[r.tapIndex]), fail: () => resolve('') }));
  },

  // ---- 快捷入口 ----
  record() {
    app.setCurrentStore(this.data.id);
    wx.navigateTo({ url: `/pages/record/index?storeId=${this.data.id}` });
  },
  brief() {
    app.setCurrentStore(this.data.id);
    wx.navigateTo({ url: `/pages/brief/index?storeId=${this.data.id}` });
  },
  // 问助手：把这家店设为助手的「当前门店」，回到首页直接问
  ask() {
    app.setCurrentStore(this.data.id);
    app.globalData.askHint = this.data.store ? this.data.store.name : '';
    wx.switchTab({ url: '/pages/home/index' });
  },
  addTodo() {
    wx.navigateTo({ url: `/pages/todo/edit/index?storeId=${this.data.id}` });
  },
  edit() {
    wx.navigateTo({ url: `/pages/store/edit/index?id=${this.data.id}` });
  },
  call() {
    const phone = this.data.store && this.data.store.contactPhone;
    if (phone) wx.makePhoneCall({ phoneNumber: phone, fail: () => {} });
  },
  // 点合作状态标签：直接改状态（不用进编辑页）
  async changeCoop() {
    if (!this.data.canManage) return undefined;
    const pick = await this.sheet(COOPERATION_STATUSES);
    if (!pick || pick === this.data.store.cooperationStatus) return undefined;
    try {
      await updateStore(this.data.id, { cooperationStatus: pick });
    } catch (err) {
      return wx.showToast({ title: err.message, icon: 'none' });
    }
    wx.showToast({ title: `已改为「${pick}」`, icon: 'none' });
    return this.load();
  },

  // ---- 档案 ----
  // 补充门店事实：AI 下次分析档案时当作最可信的信息
  correct() {
    wx.showModal({
      title: '补充门店事实',
      editable: true,
      placeholderText: '例如：门店面积约 80 平，老板娘负责进货',
      content: this.data.store.correction || '',
      success: async (res) => {
        if (!res.confirm || !res.content.trim()) return;
        try {
          await submitCorrection(this.data.id, res.content.trim());
        } catch (err) {
          wx.showToast({ title: err.message, icon: 'none' });
          return;
        }
        wx.showToast({ title: '已提交，下次分析生效', icon: 'none' });
        this.load();
      },
    });
  },
  // 点某个档案维度：直接改这一项（AI 写错了的时候），留下修改记录
  editSection(e) {
    if (!this.data.canManage) return;
    const { key, label } = e.currentTarget.dataset;
    const sec = this.data.sections.find((s) => s.key === key);
    wx.showModal({
      title: `修改「${label}」`,
      editable: true,
      content: sec && sec.content !== '未确认' ? sec.content : '',
      placeholderText: '写下正确的内容',
      success: async (res) => {
        if (!res.confirm) return;
        const content = (res.content || '').trim();
        if (!content || (sec && content === sec.content)) return;
        try {
          await editProfile(this.data.id, key, content, '当前状态');
        } catch (err) {
          wx.showToast({ title: err.message, icon: 'none' });
          return;
        }
        wx.showToast({ title: '已修改', icon: 'none' });
        this.load();
      },
    });
  },

  // ---- 行动闭环里的待办：完成 / 修改 / 删除 ----
  async todoAction(e) {
    const { id, done, topic } = e.currentTarget.dataset;
    const pick = await this.sheet([done ? '撤销完成' : '标记完成', '修改', '删除']);
    try {
      if (pick === '标记完成' || pick === '撤销完成') {
        await setTodoDone(id, !done);
        if (!done) wx.showToast({ title: '已完成', icon: 'success' });
      } else if (pick === '修改') {
        return wx.navigateTo({ url: `/pages/todo/edit/index?id=${id}` });
      } else if (pick === '删除') {
        const ok = await new Promise((resolve) => wx.showModal({
          title: '删除这条待办', content: `「${topic}」删除后不再提醒。是做完了的话，请点「标记完成」。`, confirmText: '删除', confirmColor: '#e5484d',
          success: (r) => resolve(!!r.confirm), fail: () => resolve(false),
        }));
        if (!ok) return undefined;
        await cancelTodo(id);
        wx.showToast({ title: '已删除', icon: 'none' });
      } else {
        return undefined;
      }
    } catch (err) {
      return wx.showToast({ title: err.message, icon: 'none' });
    }
    return this.load();
  },
});
