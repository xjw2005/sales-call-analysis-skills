const { listMemories, addMemory, confirmMemory, updateMemory, deleteMemory, shareMemory } = require('../../services/memory');

// 记忆分组显示：你教我的知识 / 说法 / 习惯和纠正。顺序按「最容易出错、最需要你检查」排
const GROUPS = [
  { kind: 'fact', title: '你教我的知识', note: '只对你自己生效，没有经过公司核实。和公司资料不一致时，助手以公司资料为准并提醒你。' },
  { kind: 'alias', title: '说法（地区别名）', note: '' },
  { kind: 'habit', title: '习惯和纠正', note: '' },
];
const groupOf = (m) => (m.kind === 'fact' || m.kind === 'alias' ? m.kind : 'habit');

Page({
  data: { pending: [], groups: [], team: [], loaded: false, isManager: false, empty: false },
  onShow() {
    this.load();
  },
  async load() {
    try {
      const { items } = await listMemories();
      const active = items.filter((m) => m.mine && m.status === 'active');
      const groups = GROUPS.map((g) => ({ ...g, items: active.filter((m) => groupOf(m) === g.kind) })).filter((g) => g.items.length);
      const pending = items.filter((m) => m.mine && m.status === 'pending');
      this.setData({
        pending,
        groups,
        team: items.filter((m) => !m.mine),
        loaded: true,
        empty: !active.length && !pending.length,
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
    wx.showModal({ title: '删除这条记忆', content: '删除后助手不会再用它。', confirmColor: '#e5484d', success: (r) => { if (r.confirm) this.run(() => deleteMemory(id)); } });
  },
  edit(e) {
    const { id, kind, key, value } = e.currentTarget.dataset;
    const titles = { alias: `「${key}」指哪些地区？`, fact: `关于「${key}」，正确的说法是？` };
    wx.showModal({
      title: titles[kind] || '修改内容',
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
  ask(options) {
    return new Promise((resolve) => wx.showModal({ editable: true, ...options, success: (r) => resolve(r.confirm ? (r.content || '').trim() : ''), fail: () => resolve('') }));
  },
  // 手动教助手：先选教什么
  add() {
    const items = ['教一条知识（产品、品牌、政策）', '教一个说法（地区别名）', '记一个习惯'];
    wx.showActionSheet({
      itemList: items,
      success: async ({ tapIndex }) => {
        if (tapIndex === 0) {
          const key = await this.ask({ title: '这条知识是关于什么的？', placeholderText: '主题词，如：a2、紫白金、补税', confirmText: '下一步' });
          if (!key) return;
          const value = await this.ask({ title: `关于「${key}」`, placeholderText: '写一句完整的话，如：紫白金下月起换新包装' });
          if (value) this.run(() => addMemory({ kind: 'fact', key, value }), '已记住');
        } else if (tapIndex === 1) {
          const key = await this.ask({ title: '教助手一个说法', placeholderText: '先输入说法，如：城东', confirmText: '下一步' });
          if (!key) return;
          const value = await this.ask({ title: `「${key}」指哪些地区？`, placeholderText: '多个用顿号隔开，如：官渡区、呈贡区' });
          if (value) this.run(() => addMemory({ kind: 'alias', key, value }), '已记住');
        } else if (tapIndex === 2) {
          const value = await this.ask({ title: '记一个习惯', placeholderText: '如：回答尽量简短，先给结论' });
          if (value) this.run(() => addMemory({ kind: 'preference', key: '', value }), '已记住');
        }
      },
    });
  },
});
