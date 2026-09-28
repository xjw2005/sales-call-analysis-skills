const { createStore } = require('../../../services/store');

const app = getApp();

Page({
  data: { name: '', province: '', city: '', address: '', saving: false },
  onField(e) {
    this.setData({ [e.currentTarget.dataset.key]: e.detail.value });
  },
  onRegion(e) {
    const [province, city] = e.detail.value;
    this.setData({ province, city });
  },
  chooseLocation() {
    wx.chooseLocation({
      success: (res) => this.setData({ address: res.address + (res.name ? ` ${res.name}` : '') }),
    });
  },
  async save() {
    const { name, province, city, address } = this.data;
    if (!name.trim()) return wx.showToast({ title: '请填写门店名称', icon: 'none' });
    this.setData({ saving: true });
    try {
      const store = await createStore({ name: name.trim(), province, city, address });
      app.setCurrentStore(store.id);
      wx.showToast({ title: '已添加', icon: 'success' });
      setTimeout(() => wx.navigateBack(), 600);
    } catch (err) {
      wx.showToast({ title: err.message, icon: 'none' });
      this.setData({ saving: false });
    }
  },
});
