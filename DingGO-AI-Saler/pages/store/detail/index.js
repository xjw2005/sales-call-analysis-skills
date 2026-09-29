const fmt = require('../../../utils/format');
const { COOP_TONE } = require('../../../utils/constants');
const { getStore, submitCorrection } = require('../../../services/store');

const STATE_TONE = { 稳定档案: 'success', 当前状态: 'info', 未确认: 'muted' };
const app = getApp();

Page({
  data: { hasMetrics: true, id: '', coopTone: 'muted', store: null, tab: 0, tabs: ['门店档案', '拜访记录', '行动闭环'], grade: '', sections: [] },
  onLoad({ id }) {
    this.setData({ id });
  },
  onShow() {
    this.load();
  },
  async load() {
    try {
      const store = await getStore(this.data.id);
      const sections = store.profile ? store.profile.sections.map((s) => ({ ...s, tone: STATE_TONE[s.state] || 'muted' })) : [];
      store.loops = store.loops.map((l) => ({ ...l, time: fmt.date(l.createdAt) }));
      store.todos = store.todos || [];
      store.legacyActions = (store.legacyActions || []).map((l) => ({ ...l, time: fmt.date(l.createdAt) }));
      const m = store.metrics || {};
      const hasMetrics = m.score !== null && m.score !== undefined || m.concernHits !== null && m.concernHits !== undefined || m.loopRate !== null && m.loopRate !== undefined;
      this.setData({ hasMetrics, store, sections, coopTone: COOP_TONE[store.cooperationStatus] || 'muted', grade: fmt.grade(m.score) });
      wx.setNavigationBarTitle({ title: store.name });
    } catch (err) {
      wx.showToast({ title: err.message, icon: 'none' });
    }
  },
  switchTab(e) {
    this.setData({ tab: Number(e.currentTarget.dataset.i) });
  },
  record() {
    app.setCurrentStore(this.data.id);
    wx.navigateTo({ url: `/pages/record/index?storeId=${this.data.id}` });
  },
  correct() {
    wx.showModal({
      title: '纠正门店档案',
      editable: true,
      placeholderText: '例如：门店面积约 80 平，老板娘负责进货',
      content: this.data.store.correction || '',
      success: async (res) => {
        if (!res.confirm || !res.content.trim()) return;
        await submitCorrection(this.data.id, res.content.trim());
        wx.showToast({ title: '已提交，下次分析生效', icon: 'none' });
        this.load();
      },
    });
  },
});
