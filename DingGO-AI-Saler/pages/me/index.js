const setTab = require('../../utils/tab');
const config = require('../../config/index');
const fmt = require('../../utils/format');
const { listVisits } = require('../../services/visit');
const { refreshUser } = require('../../services/request');

const app = getApp();

Page({
  data: { userName: '', useMock: config.useMock, stats: { visits: 0, minutes: 0, cost: '0.00' } },
  async onShow() {
    setTab(this, 3);
    this.setData({ userName: app.globalData.userName });
    if (!config.useMock) {
      await refreshUser();
      this.setData({ userName: app.globalData.userName });
    }
    this.load();
  },
  async load() {
    const visits = await listVisits();
    const month = fmt.dayKey(Date.now()).slice(0, 7);
    const mine = visits.filter((v) => fmt.dayKey(v.createdAt).startsWith(month));
    this.setData({
      stats: {
        visits: mine.length,
        minutes: Math.round(mine.reduce((s, v) => s + (v.durationSec || 0), 0) / 60),
        cost: mine.reduce((s, v) => s + (v.estCost || 0), 0).toFixed(2),
      },
    });
  },
  openMemory() {
    wx.navigateTo({ url: '/pages/memory/index' });
  },
  showPrivacy() {
    wx.showModal({
      title: '隐私与录音告知',
      content: '拜访录音仅用于销售分析与门店档案生成。录音前请告知客户；录音与分析结果保存在公司服务器，不会对外提供。',
      showCancel: false,
    });
  },
  showAbout() {
    wx.showModal({ title: '关于', content: 'AI 赋能销售云 · 门店拜访助手（前端演示版）', showCancel: false });
  },
  clearChats() {
    wx.showModal({
      title: '清空对话记录',
      content: '将删除本机保存的助手对话，确定吗？',
      success: (r) => {
        if (!r.confirm) return;
        try { wx.removeStorageSync('chatHistory'); } catch (e) { /* 忽略 */ }
        wx.showToast({ title: '已清空', icon: 'none' });
      },
    });
  },
});
