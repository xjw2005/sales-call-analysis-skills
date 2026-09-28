App({
  globalData: {
    userName: '小熊老师',
    currentStoreId: 's1',
  },
  onLaunch() {
    const saved = wx.getStorageSync('currentStoreId');
    if (saved) this.globalData.currentStoreId = saved;
  },
  setCurrentStore(id) {
    this.globalData.currentStoreId = id;
    wx.setStorageSync('currentStoreId', id);
  },
});
