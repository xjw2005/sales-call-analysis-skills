Component({
  data: {
    selected: 0,
    list: [
      { pagePath: '/pages/home/index', text: '助手', icon: 'home' },
      { pagePath: '/pages/store/list/index', text: '门店', icon: 'store' },
      { pagePath: '/pages/visit/list/index', text: '拜访', icon: 'visit' },
      { pagePath: '/pages/me/index', text: '我的', icon: 'me' },
    ],
  },
  methods: {
    switchTab(e) {
      const { index } = e.currentTarget.dataset;
      wx.switchTab({ url: this.data.list[index].pagePath });
    },
  },
});
