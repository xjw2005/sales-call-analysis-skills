const { createStore, getStore, updateStore, deleteStore } = require('../../../services/store');
const { chooseOnMap } = require('../../../services/location');
const { COOPERATION_STATUSES } = require('../../../utils/constants');

const app = getApp();

// 添加门店；带 id 时是修改门店资料（名称、地址、坐标、合作状态、联系人），负责人还可以删除门店
Page({
  data: {
    id: '', loaded: true, canManage: true, saving: false,
    name: '', province: '', city: '', address: '', lat: null, lng: null, hasPoint: false,
    coopOpts: COOPERATION_STATUSES, coopIndex: COOPERATION_STATUSES.length - 1,
    contactName: '', contactPhone: '',
  },
  async onLoad({ id }) {
    if (!id) return;
    wx.setNavigationBarTitle({ title: '编辑门店' });
    this.setData({ id, loaded: false });
    try {
      const s = await getStore(id);
      const coopIndex = COOPERATION_STATUSES.indexOf(s.cooperationStatus);
      this.setData({
        loaded: true, canManage: s.canManage !== false,
        name: s.name, province: s.province || '', city: s.city || '', address: s.address || '',
        lat: s.lat == null ? null : s.lat, lng: s.lng == null ? null : s.lng, hasPoint: s.lat != null && s.lng != null,
        coopIndex: coopIndex < 0 ? COOPERATION_STATUSES.length - 1 : coopIndex,
        contactName: s.contactName || '', contactPhone: s.contactPhone || '',
      });
    } catch (err) {
      wx.showToast({ title: err.message, icon: 'none' });
    }
  },
  onField(e) {
    this.setData({ [e.currentTarget.dataset.key]: e.detail.value });
  },
  onRegion(e) {
    const [province, city] = e.detail.value;
    this.setData({ province, city });
  },
  onCoop(e) {
    this.setData({ coopIndex: Number(e.detail.value) });
  },
  // 地图选点：地址和坐标一起记下（以后可以算「离门店多远」、规划路线）
  async chooseLocation() {
    try {
      const p = await chooseOnMap();
      this.setData({ address: p.address, lat: p.lat, lng: p.lng, hasPoint: true });
    } catch (err) {
      if (err.kind !== 'cancel') wx.showToast({ title: '没能打开地图，可以直接填写地址', icon: 'none' });
    }
  },
  payload() {
    const { name, province, city, address, lat, lng, coopIndex, contactName, contactPhone } = this.data;
    const data = { name: name.trim(), province, city, address: address.trim(), cooperationStatus: COOPERATION_STATUSES[coopIndex], contactName: contactName.trim(), contactPhone: contactPhone.trim() };
    if (lat != null && lng != null) Object.assign(data, { lat, lng });
    return data;
  },
  async save() {
    if (this.data.saving) return undefined;
    const data = this.payload();
    if (!data.name) return wx.showToast({ title: '请填写门店名称', icon: 'none' });
    if (data.contactPhone && !/^[\d\-+ ]{5,20}$/.test(data.contactPhone)) return wx.showToast({ title: '电话格式不对', icon: 'none' });
    this.setData({ saving: true });
    try {
      if (this.data.id) {
        await updateStore(this.data.id, data);
        wx.showToast({ title: '已保存', icon: 'success' });
      } else {
        const store = await createStore(data);
        app.setCurrentStore(store.id);
        wx.showToast({ title: '已添加', icon: 'success' });
      }
      setTimeout(() => wx.navigateBack(), 600);
    } catch (err) {
      wx.showToast({ title: err.message, icon: 'none' });
      this.setData({ saving: false });
    }
    return undefined;
  },
  // 删除门店：先确认；已有拜访的店后端会拒绝，并提示改合作状态
  remove() {
    wx.showModal({
      title: '删除这家门店',
      content: `「${this.data.name}」删除后从门店列表、计划里消失，未完成的待办一并取消。删错了可以找管理员恢复。`,
      confirmText: '删除',
      confirmColor: '#e5484d',
      success: async (r) => {
        if (!r.confirm) return;
        try {
          await deleteStore(this.data.id);
        } catch (err) {
          wx.showModal({ title: '不能删除', content: err.message, showCancel: false });
          return;
        }
        if (app.globalData.currentStoreId === this.data.id) app.setCurrentStore('');
        wx.showToast({ title: '已删除', icon: 'none' });
        // 门店详情页已经没有意义了：回到门店列表
        setTimeout(() => wx.switchTab({ url: '/pages/store/list/index' }), 600);
      },
    });
  },
});
