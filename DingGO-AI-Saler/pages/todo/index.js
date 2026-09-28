const { listTodos, setTodoDone } = require('../../services/todo');
const { wechatMessage } = require('../../utils/playbook');

const app = getApp();

Page({
  data: { tab: 0, open: [], done: [] },
  onShow() {
    this.load();
  },
  async load() {
    const todos = await listTodos();
    this.todos = todos;
    this.setData({ open: todos.filter((t) => !t.done), done: todos.filter((t) => t.done) });
  },
  switchTab(e) {
    this.setData({ tab: Number(e.currentTarget.dataset.i) });
  },
  async toggle(e) {
    const { id, done } = e.currentTarget.dataset;
    await setTodoDone(id, !done);
    if (!done) wx.showToast({ title: '已完成', icon: 'success' });
    this.load();
  },
  copy(e) {
    const todo = this.todos.find((t) => t.id === e.currentTarget.dataset.id);
    wx.setClipboardData({
      data: wechatMessage(todo, app.globalData.userName),
      success: () => wx.showToast({ title: '话术已复制，去微信粘贴', icon: 'none' }),
    });
  },
  openStore(e) {
    wx.navigateTo({ url: `/pages/store/detail/index?id=${e.currentTarget.dataset.id}` });
  },
});
