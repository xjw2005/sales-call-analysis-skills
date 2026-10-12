// 页面滚动时决定要不要显示「回到顶部」按钮：滚过约一屏才显示。
// 只在跨过阈值时才 setData，滚动过程中不会每一帧都刷新页面。
const THRESHOLD = 600; // px

module.exports = function trackScroll(page, scrollTop) {
  const showTop = scrollTop > THRESHOLD;
  if (showTop !== page.data.showTop) page.setData({ showTop });
};
