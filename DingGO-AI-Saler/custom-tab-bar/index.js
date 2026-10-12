Component({
  data: {
    selected: 0,
    hidden: false,
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
      // 点的是当前已选中的标签：和微信的习惯一样，回到页面顶部
      if (Number(index) === this.data.selected) return wx.pageScrollTo({ scrollTop: 0, duration: 300 });
      return wx.switchTab({ url: this.data.list[index].pagePath });
    },
  },
});
