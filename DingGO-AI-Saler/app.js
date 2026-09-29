const config = require('./config/index');

App({
  globalData: {
    userName: '小熊老师',
    currentStoreId: 's1',
  },
  onLaunch() {
    const saved = wx.getStorageSync('currentStoreId');
    if (saved) this.globalData.currentStoreId = saved;
    if (!config.useMock) {
      // 真实模式：显示上次登录的人的名字，还没登录过就显示「新用户」
      const user = wx.getStorageSync('user');
      this.globalData.userName = (user && user.name) || '新用户';
    }
  },
  setCurrentStore(id) {
    this.globalData.currentStoreId = id;
    wx.setStorageSync('currentStoreId', id);
  },
});
