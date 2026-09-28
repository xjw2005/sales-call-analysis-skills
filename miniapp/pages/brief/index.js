const { getBrief } = require('../../services/assistant');
const { scenarioByConcern } = require('../../services/practice');

Page({
  data: { brief: null, storeId: '' },
  onLoad({ storeId }) {
    this.setData({ storeId });
  },
  async onShow() {
    try {
      const brief = await getBrief(this.data.storeId);
      this.setData({ brief });
      wx.setNavigationBarTitle({ title: `${brief.store.name} · 简报` });
    } catch (err) {
      wx.showToast({ title: err.message, icon: 'none' });
    }
  },
  record() {
    wx.navigateTo({ url: `/pages/record/index?storeId=${this.data.storeId}` });
  },
  openStore() {
    wx.navigateTo({ url: `/pages/store/detail/index?id=${this.data.storeId}` });
  },
  practice(e) {
    wx.navigateTo({ url: `/pages/practice/chat/index?scenario=${scenarioByConcern(e.currentTarget.dataset.name)}` });
  },
  copyQuestions() {
    wx.setClipboardData({ data: this.data.brief.questions.map((q, i) => `${i + 1}. ${q}`).join('\n') });
  },
});
