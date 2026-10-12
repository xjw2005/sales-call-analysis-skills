const { listTodos, createTodo, updateTodo } = require('../../../services/todo');
const { listStores } = require('../../../services/store');
const fmt = require('../../../utils/format');

// 新建一条待办（可挂在某家店下、定到期日）；带 id 时修改已有待办的内容和到期日
Page({
  data: { id: '', topic: '', action: '', storeId: '', storeName: '', dueDate: '', today: '', saving: false, storeLocked: false },
  async onLoad({ id, storeId }) {
    this.setData({ today: fmt.dayKey(Date.now()) });
    if (id) {
      wx.setNavigationBarTitle({ title: '修改待办' });
      try {
        const t = (await listTodos()).find((x) => x.id === id);
        if (!t) throw new Error('这条待办已不存在');
        // 修改时不换门店：门店关系由后端决定（AI 生成的待办和拜访绑在一起）
        this.setData({ id, topic: t.topic, action: t.action || '', storeId: t.storeId || '', storeName: t.storeName || '', dueDate: t.dueAt ? fmt.dayKey(t.dueAt) : '', storeLocked: true });
      } catch (err) {
        wx.showToast({ title: err.message, icon: 'none' });
      }
      return;
    }
    wx.setNavigationBarTitle({ title: '新建待办' });
    if (storeId) {
      try {
        const s = (await listStores()).find((x) => x.id === storeId);
        if (s) this.setData({ storeId, storeName: s.name });
      } catch (err) {
        // 门店名取不到不影响新建，销售可以自己再选
      }
    }
  },
  onField(e) {
    this.setData({ [e.currentTarget.dataset.key]: e.detail.value });
  },
  onDate(e) {
    this.setData({ dueDate: e.detail.value });
  },
  clearDate() {
    this.setData({ dueDate: '' });
  },
  pickStore() {
    if (this.data.storeLocked) return;
    wx.navigateTo({
      url: '/pages/store/pick/index',
      events: { picked: (s) => this.setData({ storeId: s.id, storeName: s.name }) },
    });
  },
  clearStore() {
    if (!this.data.storeLocked) this.setData({ storeId: '', storeName: '' });
  },
  async save() {
    const { id, topic, action, storeId, dueDate, saving } = this.data;
    if (saving) return undefined;
    if (!topic.trim()) return wx.showToast({ title: '先写下要做什么', icon: 'none' });
    this.setData({ saving: true });
    try {
      if (id) await updateTodo(id, { topic: topic.trim(), action: action.trim(), dueDate });
      else await createTodo({ topic: topic.trim(), action: action.trim(), storeId, dueDate });
      wx.showToast({ title: id ? '已保存' : '已添加', icon: 'success' });
      setTimeout(() => wx.navigateBack(), 600);
    } catch (err) {
      wx.showToast({ title: err.message, icon: 'none' });
      this.setData({ saving: false });
    }
    return undefined;
  },
});
