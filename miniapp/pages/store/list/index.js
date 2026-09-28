const setTab = require('../../../utils/tab');
const fmt = require('../../../utils/format');
const { listStores } = require('../../../services/store');

Page({
  data: { stores: [], shown: [], keyword: '', loaded: false },
  onShow() {
    setTab(this, 1);
    this.load();
  },
  async load() {
    const stores = (await listStores()).map((s) => ({
      ...s,
      lastText: s.lastVisitAt ? `最近拜访 ${fmt.date(s.lastVisitAt)}` : '尚未拜访',
    }));
    this.setData({ stores, loaded: true });
    this.filter();
  },
  onSearch(e) {
    this.setData({ keyword: e.detail.value });
    this.filter();
  },
  filter() {
    const k = this.data.keyword.trim();
    this.setData({ shown: k ? this.data.stores.filter((s) => s.name.includes(k) || (s.address || '').includes(k)) : this.data.stores });
  },
  openStore(e) {
    wx.navigateTo({ url: `/pages/store/detail/index?id=${e.currentTarget.dataset.id}` });
  },
  addStore() {
    wx.navigateTo({ url: '/pages/store/edit/index' });
  },
});
