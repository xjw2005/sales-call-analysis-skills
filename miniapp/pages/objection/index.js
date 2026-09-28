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
    wx.navigateTo({ url: `/pages/practice/chat/index?scenario=${scenarioByConcern(e.currentTarget.dataset.name)}` });
  },
});
