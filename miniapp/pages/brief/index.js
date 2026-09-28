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
    // 陪练在首页对话里进行：记下场景，切回首页后自动开始
    getApp().globalData.pendingPractice = scenarioByConcern(e.currentTarget.dataset.name);
    wx.switchTab({ url: '/pages/home/index' });
  },
  copyQuestions() {
    wx.setClipboardData({ data: this.data.brief.questions.map((q, i) => `${i + 1}. ${q}`).join('\n') });
  },
});
