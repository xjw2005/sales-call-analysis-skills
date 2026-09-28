const { OBJECTIONS } = require('../../utils/playbook');
const { scenarioByConcern } = require('../../services/practice');

Page({
  data: { list: [], openName: '' },
  onLoad({ name }) {
    const list = Object.keys(OBJECTIONS).map((k) => ({ name: k, ...OBJECTIONS[k] }));
    this.setData({ list, openName: name ? decodeURIComponent(name) : list[0].name });
  },
  toggle(e) {
    const { name } = e.currentTarget.dataset;
    this.setData({ openName: this.data.openName === name ? '' : name });
  },
  copy(e) {
    wx.setClipboardData({ data: e.currentTarget.dataset.text });
  },
  practice(e) {
    // 陪练在首页对话里进行：记下场景，切回首页后自动开始
    getApp().globalData.pendingPractice = scenarioByConcern(e.currentTarget.dataset.name);
    wx.switchTab({ url: '/pages/home/index' });
  },
});
