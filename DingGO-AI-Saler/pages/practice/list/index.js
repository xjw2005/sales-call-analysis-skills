const { listScenarios } = require('../../../services/practice');

Page({
  data: { scenarios: [] },
  async onLoad() {
    this.setData({ scenarios: await listScenarios() });
  },
  open(e) {
    wx.navigateTo({ url: `/pages/practice/chat/index?scenario=${e.currentTarget.dataset.id}` });
  },
});
