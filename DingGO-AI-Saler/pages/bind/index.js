const { bindWithCode } = require('../../services/request');

Page({
  data: { code: '', loading: false, error: '' },
  onInput(e) {
    this.setData({ code: e.detail.value.replace(/\s/g, ''), error: '' });
  },
  async submit() {
    const { code, loading } = this.data;
    if (loading) return;
    if (code.length < 8) return this.setData({ error: '请输入 8 位绑定码' });
    this.setData({ loading: true, error: '' });
    try {
      await bindWithCode(code);
      wx.showToast({ title: '绑定成功', icon: 'success' });
      setTimeout(() => wx.reLaunch({ url: '/pages/home/index' }), 600);
    } catch (err) {
      this.setData({ loading: false, error: err.message });
    }
    return undefined;
  },
});
