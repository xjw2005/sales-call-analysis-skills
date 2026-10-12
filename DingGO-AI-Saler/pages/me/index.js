const setTab = require('../../utils/tab');
const config = require('../../config/index');
const fmt = require('../../utils/format');
const { listVisits } = require('../../services/visit');
const { refreshUser } = require('../../services/request');
const { clearSessions } = require('../../services/chat');

const app = getApp();

Page({
  data: { userName: '', initial: '', roleText: '销售', useMock: config.useMock, stats: { visits: 0, minutes: 0, cost: '0.00' } },
  async onShow() {
    setTab(this, 3);
    this.showUser();
    if (!config.useMock) {
      await refreshUser();
      this.showUser();
    }
    this.load();
  },
  // 名字、头像字、角色都跟着当前登录的人走（不再固定写「熊」和「销售」）
  showUser() {
    const name = app.globalData.userName || '';
    const user = config.useMock ? {} : (wx.getStorageSync('user') || {});
    this.setData({ userName: name, initial: name.slice(0, 1), roleText: user.role === 'manager' ? '区域经理' : '销售' });
  },
  async load() {
    let visits;
    try {
      visits = await listVisits();
    } catch (err) {
      return wx.showToast({ title: err.message, icon: 'none' });
    }
    const month = fmt.dayKey(Date.now()).slice(0, 7);
    const mine = visits.filter((v) => fmt.dayKey(v.createdAt).startsWith(month));
    return this.setData({
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
    wx.showModal({ title: '关于', content: config.useMock ? 'AI 赋能销售云 · 门店拜访助手（前端演示版）' : 'AI 赋能销售云 · 门店拜访助手', showCancel: false });
  },
  clearChats() {
    wx.showModal({
      title: '清空对话记录',
      content: '将删除助手里的所有对话，无法恢复，确定吗？',
      confirmColor: '#e5484d',
      success: async (r) => {
        if (!r.confirm) return;
        try {
          // 真实模式对话在服务器上，要连服务器上的一起清；演示模式只有本机缓存
          if (!config.useMock && !config.mockAI) await clearSessions();
          wx.removeStorageSync('chatHistory');
        } catch (e) {
          wx.showToast({ title: e.message || '清空失败', icon: 'none' });
          return;
        }
        app.globalData.chatsCleared = true; // 助手页回来时换成一段新对话，不再指向已删除的那段
        wx.showToast({ title: '已清空', icon: 'none' });
      },
    });
  },
});
