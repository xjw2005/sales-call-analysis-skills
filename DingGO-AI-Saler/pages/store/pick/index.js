const { listStores } = require('../../../services/store');

const PAGE = 40;

// 门店选择：可搜索（名称 / 地址 / 平台门店 ID），按最近拜访排序；选中后通过事件通道把门店回传给上一页
Page({
  data: { keyword: '', rows: [], total: 0, loaded: false },
  onLoad() {
    this.channel = this.getOpenerEventChannel();
  },
  async onShow() {
    if (!this.all) this.all = await listStores();
    this.setData({ loaded: true });
    this.filter();
  },
  onSearch(e) {
    this.setData({ keyword: e.detail.value });
    this.filter();
  },
  filter() {
    const k = this.data.keyword.trim();
    this.shown = k ? this.all.filter((s) => s.name.includes(k) || (s.address || '').includes(k) || (s.externalId || '').includes(k)) : this.all;
    this.setData({ rows: this.shown.slice(0, PAGE), total: this.shown.length });
  },
  onReachBottom() {
    const n = this.data.rows.length;
    if (n < this.shown.length) this.setData({ rows: this.shown.slice(0, n + PAGE) });
  },
  pick(e) {
    const store = this.all.find((s) => s.id === e.currentTarget.dataset.id);
    if (this.channel && store) this.channel.emit('picked', store);
    wx.navigateBack();
  },
});
