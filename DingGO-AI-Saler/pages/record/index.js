const fmt = require('../../utils/format');
const { STAGES, FIRST_STAGE, STORE_CONDITIONS, NO_RECORDING_REASONS } = require('../../utils/constants');
const { listStores } = require('../../services/store');
const { createVisit, completeUpload } = require('../../services/visit');
const { uploadSegments } = require('../../services/upload');
const { getLocation, chooseOnMap } = require('../../services/location');

const app = getApp();
const SEGMENT_MS = 10 * 60 * 1000; // 微信单段录音上限 10 分钟，到点自动续录下一段

// 录音管理器是全局唯一的，监听函数一旦注册就删不掉：每次进页面都注册，旧页面的监听会越积越多，
// 在新页面录音时被重复触发（甚至旧页面自己又发起一次录音，引发 operateRecorder:fail）。
// 所以全局只注册一次，由它把事件转给「当前打开的这个页面」。
let activePage = null;
let wired = false;
function wireRecorder(rm) {
  if (wired) return;
  wired = true;
  const to = (name) => (...args) => { if (activePage) activePage[name](...args); };
  rm.onStart(to('onRecStart'));
  rm.onResume(to('onRecResume'));
  rm.onPause(to('onRecPause'));
  rm.onInterruptionBegin(to('onRecPause'));
  rm.onInterruptionEnd(to('onRecInterruptEnd'));
  rm.onStop(to('onRecStop'));
  rm.onError(to('onRecError'));
}

Page({
  data: {
    importMode: false,
    stores: [],
    storeIndex: -1,
    stages: STAGES,
    stageIndex: 0,
    isFirst: true,
    cooperated: '否',
    conditions: ['未选择', ...STORE_CONDITIONS],
    conditionIndex: 0,
    note: '',
    state: 'idle', // idle | recording | paused | stopped
    seconds: 0,
    timeText: '00:00',
    segments: [], // 本地音频文件路径
    importName: '',
    submitting: false,
    step: 0,
    locState: 'idle', // idle | locating | ok | fail
    locText: '点击获取（选填，不影响提交）',
  },

  onLoad({ storeId, mode }) {
    this.initialStoreId = storeId;
    this.setData({ importMode: mode === 'import' });
    activePage = this;
    this.recorder = wx.getRecorderManager();
    wireRecorder(this.recorder);
    if (mode === 'import') this.chooseFile();
  },

  // 每次回到本页都刷新门店（可能刚新建了门店）
  async onShow() {
    const stores = await listStores();
    const cur = this.data.storeIndex >= 0 ? this.data.stores[this.data.storeIndex].id : null;
    const id = cur || this.initialStoreId || app.globalData.currentStoreId;
    this.setData({ stores, storeIndex: stores.findIndex((s) => s.id === id) });
  },

  onUnload() {
    clearInterval(this.timer);
    if (this.data.state === 'recording' || this.data.state === 'paused') {
      this.userStopped = true;
      this.recorder.stop();
    }
    if (activePage === this) activePage = null;
  },

  // ---- 表单 ----
  onStore() {
    wx.navigateTo({
      url: '/pages/store/pick/index',
      events: {
        picked: (store) => {
          let stores = this.data.stores;
          let idx = stores.findIndex((s) => s.id === store.id);
          if (idx < 0) {
            stores = [store, ...stores];
            idx = 0;
          }
          this.setData({ stores, storeIndex: idx });
        },
      },
    });
  },
  onStage(e) {
    const stageIndex = Number(e.detail.value);
    this.setData({ stageIndex, isFirst: STAGES[stageIndex] === FIRST_STAGE });
  },
  onCoop(e) {
    this.setData({ cooperated: e.currentTarget.dataset.v });
  },
  onCondition(e) {
    this.setData({ conditionIndex: Number(e.detail.value) });
  },
  onNote(e) {
    this.setData({ note: e.detail.value });
  },
  addStore() {
    wx.navigateTo({ url: '/pages/store/edit/index' });
  },

  // ---- 录音 ----
  // 以下是录音管理器的事件（由 wireRecorder 转发到当前页面）
  onRecStart() {
    if (!this.startedAt) this.startedAt = Date.now(); // 进店时间：真正开始录的那一刻，开始失败就不记
    this.setState('recording');
  },
  onRecResume() { this.setState('recording'); },
  onRecPause() { this.setState('paused'); },
  onRecInterruptEnd() { this.recorder.resume(); },
  onRecStop(res) {
    this.keepSegment(res.tempFilePath);
    if (this.userStopped) {
      this.setState('stopped');
    } else {
      this.recorder.start(this.recordOptions()); // 单段到时长上限，自动续录
    }
  },
  onRecError(err) {
    const msg = String((err && err.errMsg) || err || '未知错误');
    this.setState(this.data.segments.length ? 'stopped' : 'idle');
    // 提示条（toast）会把长句子截断，看不到原因；用对话框完整显示，并按原因给出能做的事
    const noPermission = /auth|permission|deny/i.test(msg);
    wx.showModal({
      title: '录音出错',
      content: noPermission
        ? '没有麦克风权限。请在手机的微信设置里允许使用麦克风，然后重试。'
        : `录音没能开始或中断了：${msg}\n\n可能是麦克风正被通话、语音或其他应用占用，请先关闭它们再试。仍然不行的话，把这段提示截图发给管理员。`,
      confirmText: noPermission ? '去设置' : '知道了',
      showCancel: noPermission,
      success: (r) => { if (noPermission && r.confirm) wx.openSetting(); },
    });
  },

  recordOptions() {
    return { duration: SEGMENT_MS, sampleRate: 16000, numberOfChannels: 1, encodeBitRate: 48000, format: 'mp3' };
  },

  setState(state) {
    this.setData({ state });
    clearInterval(this.timer);
    if (state === 'recording') {
      this.timer = setInterval(() => {
        const seconds = this.data.seconds + 1;
        this.setData({ seconds, timeText: fmt.duration(seconds) });
      }, 1000);
      wx.enableAlertBeforeUnload({ message: '正在录音，离开将结束本次录音，确定离开吗？' });
    } else if (state === 'stopped' && this.data.segments.length) {
      // 录完还没提交就离开会丢掉这次录音：继续提醒，直到提交成功或重录
      wx.enableAlertBeforeUnload({ message: '录音还没有提交，离开将丢失这次录音，确定离开吗？' });
    } else if (state === 'stopped' || state === 'idle') {
      wx.disableAlertBeforeUnload();
    }
  },

  // 先把临时文件存到本地，防止切后台或异常时丢失
  keepSegment(tempFilePath) {
    const push = (path) => this.setData({ segments: [...this.data.segments, path] });
    wx.getFileSystemManager().saveFile({
      tempFilePath,
      success: (res) => push(res.savedFilePath),
      fail: () => push(tempFilePath),
    });
  },

  tapRecord() {
    const { state } = this.data;
    if (state === 'idle') return this.begin();
    if (state === 'recording') return this.recorder.pause();
    if (state === 'paused') return this.recorder.resume();
    return undefined;
  },

  begin() {
    if (this.data.storeIndex < 0) return wx.showToast({ title: '请先选择门店', icon: 'none' });
    wx.authorize({
      scope: 'scope.record',
      success: () => {
        wx.showModal({
          title: '录音提示',
          content: '请先告知客户本次拜访会录音，录音仅用于销售分析。',
          confirmText: '已告知',
          success: (r) => {
            if (!r.confirm) return;
            this.userStopped = false;
            this.recorder.start(this.recordOptions());
            if (!this.loc) this.locate(); // 顺便记下到店位置：不等结果，失败也不影响录音
          },
        });
      },
      fail: () => {
        wx.showModal({
          title: '需要麦克风权限',
          content: '请在设置中允许使用麦克风后再录音',
          confirmText: '去设置',
          success: (r) => r.confirm && wx.openSetting(),
        });
      },
    });
  },

  finish() {
    this.userStopped = true;
    this.recorder.stop();
  },

  reset() {
    wx.showModal({
      title: '重新录音',
      content: '当前录音将被丢弃，确定吗？',
      success: (r) => {
        if (!r.confirm) return;
        this.startedAt = 0; // 进店时间重新算：以重录后第一次开始录音为准
        this.submitted = null; // 旧的上传进度作废
        this.loc = null;
        this.setData({ locState: 'idle', locText: '点击获取（选填，不影响提交）' });
        wx.disableAlertBeforeUnload();
        this.setData({ state: 'idle', seconds: 0, timeText: '00:00', segments: [], importName: '' });
      },
    });
  },

  // ---- 导入 ----
  chooseFile() {
    wx.chooseMessageFile({
      count: 1,
      type: 'file',
      extension: ['mp3', 'm4a', 'wav', 'aac', 'amr'],
      success: ({ tempFiles }) => {
        const f = tempFiles[0];
        this.setData({ importName: f.name, segments: [f.path], state: 'stopped' });
        this.readDuration(f.path);
      },
    });
  },

  readDuration(src) {
    const audio = wx.createInnerAudioContext();
    audio.src = src;
    audio.onCanplay(() => {
      setTimeout(() => {
        const seconds = Math.round(audio.duration || 0);
        this.setData({ seconds, timeText: fmt.duration(seconds) });
        audio.destroy();
      }, 300);
    });
    audio.onError(() => audio.destroy());
  },

  // ---- 没有录音：直接登记这次拜访 ----
  registerNoRecording() {
    const { stores, storeIndex, stageIndex, isFirst, cooperated, note, conditions, conditionIndex } = this.data;
    if (storeIndex < 0) return wx.showToast({ title: '请先选择门店', icon: 'none' });
    return wx.showActionSheet({
      itemList: NO_RECORDING_REASONS,
      success: async ({ tapIndex }) => {
        wx.showLoading({ title: '登记中', mask: true });
        try {
          // 没录音更需要位置来证明去过：试一次（最多等 6 秒，不能卡在系统弹窗上），失败也照常登记
          if (!this.loc) await Promise.race([this.locate(), new Promise((r) => setTimeout(r, 6000))]);
          const visit = await createVisit({
            storeId: stores[storeIndex].id,
            stage: STAGES[stageIndex],
            cooperated: isFirst ? cooperated : null,
            note,
            durationSec: 0,
            storeCondition: conditionIndex > 0 ? conditions[conditionIndex] : undefined,
            recordingMode: 'none',
            noRecordingReason: NO_RECORDING_REASONS[tapIndex],
            checkin: this.checkin(),
          });
          wx.hideLoading();
          wx.redirectTo({ url: `/pages/visit/detail/index?id=${visit.id}` });
        } catch (err) {
          wx.hideLoading();
          wx.showToast({ title: err.message, icon: 'none' });
        }
      },
    });
  },

  // ---- 打卡位置（选填：定位失败、没授权都不影响录音和提交） ----
  checkin() {
    const l = this.loc;
    return l ? { lat: l.lat, lng: l.lng, address: l.address || '' } : undefined;
  },
  async locate({ manual = false } = {}) {
    if (this.data.locState === 'locating') return;
    this.setData({ locState: 'locating', locText: '定位中…' });
    try {
      const p = await getLocation();
      if (this.loc && this.loc.source === 'map') { // 期间用户已经在地图上选了位置：以手选的为准
        this.setData({ locState: 'ok', locText: this.locText(this.loc) });
        return;
      }
      this.loc = { lat: p.lat, lng: p.lng, accuracy: p.accuracy, address: '', source: 'gps' };
      this.setData({ locState: 'ok', locText: this.locText(this.loc) });
    } catch (err) {
      this.setData({ locState: 'fail', locText: err.kind === 'unsupported' ? '定位不可用，点此地图选点' : '未获取到位置，点此重试' });
      if (manual) this.explainLocationError(err);
    }
  },
  locText(l) {
    if (l.source === 'map') return `已选：${l.address || '地图上的位置'}`;
    return `已定位（精度约 ${Math.round(l.accuracy || 0)} 米）`;
  },
  // 只在用户自己点「获取位置」时才弹说明；开始录音时的自动定位失败只在行里提示，不打断
  explainLocationError(err) {
    if (err.kind === 'denied' || err.kind === 'system') {
      wx.showModal({
        title: '没有获取到位置',
        content: err.kind === 'system' ? '手机或微信的定位没有开启。请打开手机的定位开关，并在系统设置里允许微信使用位置。' : '你没有允许小程序使用位置。可以在设置里打开，也可以点「地图选点」手动选。',
        confirmText: '去设置',
        success: (r) => { if (r.confirm) wx.openSetting(); },
      });
    } else if (err.kind === 'unsupported') {
      wx.showToast({ title: '定位接口暂未开通，请用地图选点', icon: 'none' });
    } else if (err.kind !== 'cancel') {
      wx.showToast({ title: '定位失败，可稍后重试或地图选点', icon: 'none' });
    }
  },
  tapLocation() {
    const items = ['获取当前位置', '在地图上选择位置'];
    if (this.loc) items.push('清除位置');
    wx.showActionSheet({
      itemList: items,
      success: ({ tapIndex }) => {
        if (tapIndex === 0) { this.loc = null; this.locate({ manual: true }); }
        if (tapIndex === 1) this.pickOnMap();
        if (tapIndex === 2) {
          this.loc = null;
          this.setData({ locState: 'idle', locText: '点击获取（选填，不影响提交）' });
        }
      },
    });
  },
  async pickOnMap() {
    try {
      const p = await chooseOnMap();
      this.loc = { lat: p.lat, lng: p.lng, address: p.address, source: 'map' };
      this.setData({ locState: 'ok', locText: this.locText(this.loc) });
    } catch (err) {
      if (err.kind === 'denied' || err.kind === 'system') this.explainLocationError(err);
    }
  },

  // ---- 提交 ----
  async submit() {
    if (this.data.state !== 'stopped' || this.data.submitting) return undefined;
    const { stores, storeIndex, stageIndex, isFirst, cooperated, note, seconds, segments, conditions, conditionIndex } = this.data;
    if (storeIndex < 0) return wx.showToast({ title: '请先选择门店', icon: 'none' });
    if (!segments.length) return wx.showToast({ title: '还没有录音', icon: 'none' });
    this.setData({ submitting: true, step: 1 });
    wx.showLoading({ title: '上传中', mask: true });
    try {
      // 上传中途失败后重试：沿用已经创建好的拜访、跳过已完成的步骤，不能再建一条重复的拜访
      const key = `${stores[storeIndex].id}|${STAGES[stageIndex]}|${segments.join(',')}`;
      if (!this.submitted || this.submitted.key !== key) this.submitted = { key, visit: null, uploaded: false };
      const sub = this.submitted;
      if (!sub.visit) {
        sub.visit = await createVisit({
          storeId: stores[storeIndex].id,
          stage: STAGES[stageIndex],
          cooperated: isFirst ? cooperated : null,
          note,
          durationSec: seconds,
          segments,
          enteredAt: this.startedAt,
          storeCondition: conditionIndex > 0 ? conditions[conditionIndex] : undefined,
          checkin: this.checkin(),
        });
      }
      const visit = sub.visit;
      if (!sub.uploaded) {
        await uploadSegments(segments, visit.upload, (done, total) => wx.showLoading({ title: `上传 ${done}/${total}`, mask: true }));
        sub.uploaded = true;
      }
      await completeUpload(visit.id, seconds);
      wx.hideLoading();
      wx.disableAlertBeforeUnload();
      wx.redirectTo({ url: `/pages/visit/detail/index?id=${visit.id}` });
    } catch (err) {
      wx.hideLoading();
      this.setData({ submitting: false, step: 0 });
      wx.showModal({ title: '上传失败', content: `${err.message}。录音已保存在手机上，可稍后重试。`, showCancel: false });
    }
  },
});
