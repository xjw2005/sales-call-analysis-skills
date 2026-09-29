const setTab = require('../../../utils/tab');
const fmt = require('../../../utils/format');
const { COOP_TONE } = require('../../../utils/constants');
const { listStores } = require('../../../services/store');

const DAY = 24 * 3600 * 1000;
const PAGE = 40;
const COOP = ['全部', '已合作', '已触达未合作', '意向中', '未触达'];
const VISIT = [
  { key: 'all', label: '全部' },
  { key: 'visited', label: '有拜访' },
  { key: 'never', label: '从未拜访' },
  { key: 'stale', label: '30天未访' },
  { key: 'recent', label: '近30天有访' },
];

Page({
  data: {
    stores: [], shown: [], rows: [], keyword: '', loaded: false,
    coopOpts: COOP, visitOpts: VISIT, owners: [],
    coop: '全部', visit: 'all', owner: '全部', filterOpen: false, activeCount: 0, counts: {},
  },
  onShow() {
    setTab(this, 1);
    this.load();
  },
  async load() {
    const stores = (await listStores()).map((s) => ({
      ...s,
      coopTone: COOP_TONE[s.cooperationStatus] || 'muted',
      lastText: s.lastVisitAt ? `最近拜访 ${fmt.date(s.lastVisitAt)}` : '尚未拜访',
    }));
    const names = [...new Set(stores.map((s) => s.primarySalesName).filter(Boolean))];
    const owners = names.length > 1 ? ['全部', ...names] : [];
    this.setData({ stores, owners, loaded: true });
    this.filter();
  },
  onSearch(e) {
    this.setData({ keyword: e.detail.value });
    this.filter();
  },
  toggleFilter() {
    this.setData({ filterOpen: !this.data.filterOpen });
  },
  pickCoop(e) {
    this.setData({ coop: e.currentTarget.dataset.v });
    this.filter();
  },
  pickVisit(e) {
    this.setData({ visit: e.currentTarget.dataset.v });
    this.filter();
  },
  pickOwner(e) {
    this.setData({ owner: e.currentTarget.dataset.v });
    this.filter();
  },
  resetFilter() {
    this.setData({ coop: '全部', visit: 'all', owner: '全部' });
    this.filter();
  },
  filter() {
    const { stores, keyword, coop, visit, owner } = this.data;
    const k = keyword.trim();
    const now = Date.now();
    const shown = stores.filter((s) => {
      if (k && !(s.name.includes(k) || (s.address || '').includes(k) || (s.externalId || '').includes(k))) return false;
      if (coop !== '全部' && s.cooperationStatus !== coop) return false;
      if (owner !== '全部' && s.primarySalesName !== owner) return false;
      if (visit === 'visited' && !s.visitCount) return false;
      if (visit === 'never' && s.visitCount) return false;
      if (visit === 'stale' && !(s.visitCount && now - s.lastVisitAt > 30 * DAY)) return false;
      if (visit === 'recent' && !(s.visitCount && now - s.lastVisitAt <= 30 * DAY)) return false;
      return true;
    });
    const activeCount = (coop !== '全部' ? 1 : 0) + (visit !== 'all' ? 1 : 0) + (owner !== '全部' ? 1 : 0);
    this.shown = shown;
    this.setData({ shown, rows: shown.slice(0, PAGE), activeCount });
  },
  onReachBottom() {
    const { rows } = this.data;
    const all = this.shown || [];
    if (rows.length < all.length) this.setData({ rows: all.slice(0, rows.length + PAGE) });
  },
  openStore(e) {
    wx.navigateTo({ url: `/pages/store/detail/index?id=${e.currentTarget.dataset.id}` });
  },
  addStore() {
    wx.navigateTo({ url: '/pages/store/edit/index' });
  },
});
