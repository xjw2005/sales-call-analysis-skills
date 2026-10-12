const { loadTodoCenter, setTodoDone, cancelTodo } = require('../../services/todo');
const plans = require('../../services/plan');
const { wechatMessage } = require('../../utils/playbook');
const fmt = require('../../utils/format');

const app = getApp();

const decorate = (t) => {
  const hasTarget = t.targetQty !== null && t.targetQty !== undefined;
  return { ...t, hasTarget, pct: hasTarget && t.targetQty ? Math.min(100, Math.round(((t.achievedQty || 0) / t.targetQty) * 100)) : 0 };
};

// 我的待办 = 今日计划要去的店 + 跟进事项 + 待确认的录音（和首页「今日待办」卡片、「我的」页的数字是同一个口径）
Page({
  data: { tab: 0, loaded: false, planOpen: [], planDone: [], open: [], done: [], recs: [], openCount: 0, doneCount: 0 },
  onShow() {
    this.load();
  },
  async load() {
    let center;
    try {
      center = await loadTodoCenter();
    } catch (err) {
      this.setData({ loaded: true });
      return wx.showToast({ title: err.message, icon: 'none' });
    }
    this.todos = [...center.followOpen, ...center.followDone].map(decorate);
    return this.setData({
      loaded: true,
      planOpen: center.planOpen,
      planDone: center.planDone,
      open: center.followOpen.map(decorate),
      done: center.followDone.map(decorate),
      recs: center.recordings.map((v) => ({ ...v, timeText: fmt.date(v.createdAt), durText: fmt.duration(v.durationSec) })),
      openCount: center.openCount,
      doneCount: center.doneCount,
    });
  },
  switchTab(e) {
    this.setData({ tab: Number(e.currentTarget.dataset.i) });
  },
  async toggle(e) {
    const { id, done } = e.currentTarget.dataset;
    try {
      await setTodoDone(id, !done);
    } catch (err) {
      return wx.showToast({ title: err.message, icon: 'none' });
    }
    if (!done) wx.showToast({ title: '已完成', icon: 'success' });
    return this.load();
  },
  copy(e) {
    const todo = this.todos.find((t) => t.id === e.currentTarget.dataset.id);
    if (!todo) return;
    wx.setClipboardData({
      data: wechatMessage(todo, app.globalData.userName),
      success: () => wx.showToast({ title: '话术已复制，去微信粘贴', icon: 'none' }),
    });
  },
  openStore(e) {
    wx.navigateTo({ url: `/pages/store/detail/index?id=${e.currentTarget.dataset.id}` });
  },
  // 今日计划里的店：看简报 / 到店录音 / 看拜访
  openBrief(e) {
    app.setCurrentStore(e.currentTarget.dataset.store);
    wx.navigateTo({ url: `/pages/brief/index?storeId=${e.currentTarget.dataset.store}` });
  },
  recordStore(e) {
    app.setCurrentStore(e.currentTarget.dataset.store);
    wx.navigateTo({ url: `/pages/record/index?storeId=${e.currentTarget.dataset.store}` });
  },
  openVisit(e) {
    wx.navigateTo({ url: `/pages/visit/detail/index?id=${e.currentTarget.dataset.id}` });
  },

  // ---- 生成错了、不需要做了：可以删除 ----
  confirmModal(options) {
    return new Promise((resolve) => wx.showModal({ ...options, confirmColor: '#e5484d', success: (r) => resolve(!!r.confirm), fail: () => resolve(false) }));
  },
  // 今日计划里的店：移出计划（不影响门店本身）
  async removePlan(e) {
    const { id, name } = e.currentTarget.dataset;
    if (!(await this.confirmModal({ title: '移出今日计划', content: `不去「${name}」了吗？门店本身不受影响。`, confirmText: '移出' }))) return undefined;
    try {
      await plans.removePlan(id);
    } catch (err) {
      return wx.showToast({ title: err.message, icon: 'none' });
    }
    wx.showToast({ title: '已移出今日计划', icon: 'none' });
    return this.load();
  },
  // 跟进事项：删除后不再出现、不再提醒，重新分析这次拜访也不会再生成同一条
  async removeTodo(e) {
    const { id, topic } = e.currentTarget.dataset;
    if (!(await this.confirmModal({ title: '删除这条待办', content: `「${topic}」删除后不再提醒。是做完了的话，请点「标记完成」。`, confirmText: '删除' }))) return undefined;
    try {
      await cancelTodo(id);
    } catch (err) {
      return wx.showToast({ title: err.message, icon: 'none' });
    }
    wx.showToast({ title: '已删除', icon: 'none' });
    return this.load();
  },
});
