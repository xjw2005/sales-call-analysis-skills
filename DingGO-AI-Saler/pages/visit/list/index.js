const setTab = require('../../../utils/tab');
const { listVisits } = require('../../../services/visit');
const trackScroll = require('../../../utils/backtop');

const FILTERS = [
  { text: '全部', match: () => true },
  { text: '待确认', match: (v) => v.status === 'cost_pending' && !v.legacy },
  { text: '处理中', match: (v) => ['uploading', 'asr_running', 'role_classifying', 'analyzing', 'reviewing'].includes(v.status) },
  { text: '已完成', match: (v) => ['done', 'partial_manual'].includes(v.status) },
  { text: '无效', match: (v) => ['invalid_short', 'invalid_content', 'failed'].includes(v.status) },
];

// 搜索：门店名、现场速记、拜访阶段、无录音原因里包含关键词就算
const hit = (v, k) => [v.storeName, v.note, v.stage, v.noRecordingReason].some((s) => (s || '').includes(k));

Page({
  data: { filters: FILTERS.map((f) => f.text), active: 0, all: [], shown: [], counts: [], keyword: '', loaded: false, showTop: false },
  onPageScroll(e) {
    trackScroll(this, e.scrollTop);
  },
  onShow() {
    setTab(this, 2);
    this.load();
  },
  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh()); // 加载失败也要把下拉的转圈收起来
  },
  async load() {
    try {
      const all = await listVisits();
      this.setData({ all, loaded: true });
      this.apply();
    } catch (err) {
      this.setData({ loaded: true });
      wx.showToast({ title: err.message, icon: 'none' });
    }
  },
  pick(e) {
    this.setData({ active: Number(e.currentTarget.dataset.i) });
    this.apply();
  },
  onSearch(e) {
    this.setData({ keyword: e.detail.value });
    this.apply();
  },
  apply() {
    const k = this.data.keyword.trim();
    const found = k ? this.data.all.filter((v) => hit(v, k)) : this.data.all;
    // 「待确认」这类需要处理的，在筛选标签上直接显示条数（全部不显示，避免数字太多）
    const counts = FILTERS.map((f, i) => (i === 1 || i === 2 ? found.filter(f.match).length : 0));
    this.setData({ shown: found.filter(FILTERS[this.data.active].match), counts });
  },
  record() {
    wx.navigateTo({ url: '/pages/record/index' });
  },
});
