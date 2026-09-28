// 自定义 tabBar：每个 tab 页 onShow 时同步选中项
module.exports = function setTab(page, index) {
  if (typeof page.getTabBar === 'function' && page.getTabBar()) page.getTabBar().setData({ selected: index });
};
