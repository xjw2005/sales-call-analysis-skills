const setTab = require('../../../utils/tab');
const { listVisits } = require('../../../services/visit');

const FILTERS = [
  { text: '全部', match: () => true },
  { text: '待确认', match: (v) => v.status === 'cost_pending' },
  { text: '处理中', match: (v) => ['uploading', 'asr_running', 'role_classifying', 'analyzing', 'reviewing'].includes(v.status) },
  { text: '已完成', match: (v) => ['done', 'partial_manual'].includes(v.status) },
  { text: '无效', match: (v) => ['invalid_short', 'invalid_content', 'failed'].includes(v.status) },
];

Page({
  data: { filters: FILTERS.map((f) => f.text), active: 0, all: [], shown: [] },
  onShow() {
    setTab(this, 2);
    this.load();
  },
  onPullDownRefresh() {
    this.load().then(() => wx.stopPullDownRefresh());
  },
  async load() {
    const all = await listVisits();
    this.setData({ all });
    this.apply();
  },
  pick(e) {
    this.setData({ active: Number(e.currentTarget.dataset.i) });
    this.apply();
  },
  apply() {
    this.setData({ shown: this.data.all.filter(FILTERS[this.data.active].match) });
  },
  record() {
    wx.navigateTo({ url: '/pages/record/index' });
  },
});
