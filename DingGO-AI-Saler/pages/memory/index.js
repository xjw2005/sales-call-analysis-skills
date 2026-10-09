const { listMemories, addMemory, confirmMemory, updateMemory, deleteMemory, shareMemory } = require('../../services/memory');

Page({
  data: { pending: [], active: [], team: [], loaded: false, isManager: false },
  onShow() {
    this.load();
  },
  async load() {
    try {
      const { items } = await listMemories();
      this.setData({
        pending: items.filter((m) => m.mine && m.status === 'pending'),
        active: items.filter((m) => m.mine && m.status === 'active'),
        team: items.filter((m) => !m.mine),
        loaded: true,
        isManager: (wx.getStorageSync('user') || {}).role === 'manager',
      });
    } catch (err) {
      this.setData({ loaded: true });
      wx.showToast({ title: err.message, icon: 'none' });
    }
  },
  async run(fn, ok) {
    try {
      await fn();
      if (ok) wx.showToast({ title: ok, icon: 'none' });
      this.load();
    } catch (err) {
      wx.showToast({ title: err.message, icon: 'none' });
    }
  },
  confirm(e) { this.run(() => confirmMemory(e.currentTarget.dataset.id), '已记住'); },
  remove(e) {
    const { id } = e.currentTarget.dataset;
    wx.showModal({ title: '删除这条记忆', content: '删除后助手不会再用它。', success: (r) => { if (r.confirm) this.run(() => deleteMemory(id)); } });
  },
  edit(e) {
    const { id, kind, key, value } = e.currentTarget.dataset;
    wx.showModal({
      title: kind === 'alias' ? `「${key}」指哪些地区？` : '修改内容',
      editable: true,
      placeholderText: kind === 'alias' ? '多个用顿号隔开，如：官渡区、呈贡区' : '',
      content: Array.isArray(value) ? value.join('、') : String(value),
      success: (r) => { if (r.confirm && r.content.trim()) this.run(() => updateMemory(id, { value: r.content.trim() }), '已保存'); },
    });
  },
  share(e) {
    const { id, scope } = e.currentTarget.dataset;
    this.run(() => shareMemory(id, scope !== 'team'), scope === 'team' ? '已取消团队通用' : '已设为团队通用，下属也能用');
  },
  add() {
    wx.showModal({
      title: '教助手一个说法', editable: true, placeholderText: '先输入说法，如：城东', confirmText: '下一步',
      success: (r1) => {
        if (!r1.confirm || !r1.content.trim()) return;
        const key = r1.content.trim();
        wx.showModal({
          title: `「${key}」指哪些地区？`, editable: true, placeholderText: '多个用顿号隔开，如：官渡区、呈贡区',
          success: (r2) => { if (r2.confirm && r2.content.trim()) this.run(() => addMemory({ kind: 'alias', key, value: r2.content.trim() }), '已记住'); },
        });
      },
    });
  },
});
