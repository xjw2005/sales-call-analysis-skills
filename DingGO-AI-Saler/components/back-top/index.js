// 「回到顶部」悬浮按钮：长列表页往下滚过一屏后出现，点一下平滑回到顶部。
// 用法：页面里 <back-top show="{{showTop}}" />，页面的 onPageScroll 里调 utils/backtop 的 trackScroll
Component({
  // offset：按钮离屏幕底部多远（rpx）。默认放在悬浮标签栏上方；页面底部另有固定按钮或没有标签栏时自己调
  properties: { show: Boolean, offset: { type: Number, value: 176 } },
  methods: {
    top() {
      wx.pageScrollTo({ scrollTop: 0, duration: 300 });
    },
  },
});
