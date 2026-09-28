const fmt = require('../../utils/format');
const { STAGES, FIRST_STAGE } = require('../../utils/constants');
const { listStores } = require('../../services/store');
const { createVisit, completeUpload } = require('../../services/visit');
const { uploadSegments } = require('../../services/upload');

const app = getApp();
const SEGMENT_MS = 10 * 60 * 1000; // 微信单段录音上限 10 分钟，到点自动续录下一段

Page({
  data: {
    importMode: false,
    stores: [],
    storeIndex: -1,
    stages: STAGES,
    stageIndex: 0,
    isFirst: true,
    cooperated: '否',
    note: '',
    state: 'idle', // idle | recording | paused | stopped
    seconds: 0,
    timeText: '00:00',
    segments: [], // 本地音频文件路径
    importName: '',
    submitting: false,
    step: 0,
  },

  onLoad({ storeId, mode }) {
    this.initialStoreId = storeId;
    this.setData({ importMode: mode === 'import' });
    this.initRecorder();
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
  },

  // ---- 表单 ----
  onStore(e) {
    this.setData({ storeIndex: Number(e.detail.value) });
  },
  onStage(e) {
    const stageIndex = Number(e.detail.value);
    this.setData({ stageIndex, isFirst: STAGES[stageIndex] === FIRST_STAGE });
  },
  onCoop(e) {
    this.setData({ cooperated: e.currentTarget.dataset.v });
  },
  onNote(e) {
    this.setData({ note: e.detail.value });
  },
  addStore() {
    wx.navigateTo({ url: '/pages/store/edit/index' });
  },

  // ---- 录音 ----
  initRecorder() {
    const rm = wx.getRecorderManager();
    this.recorder = rm;
    rm.onStart(() => this.setState('recording'));
    rm.onResume(() => this.setState('recording'));
    rm.onPause(() => this.setState('paused'));
    rm.onInterruptionBegin(() => this.setState('paused'));
    rm.onInterruptionEnd(() => rm.resume());
    rm.onStop((res) => {
      this.keepSegment(res.tempFilePath);
      if (this.userStopped) {
        this.setState('stopped');
      } else {
        rm.start(this.recordOptions()); // 单段到时长上限，自动续录
      }
    });
    rm.onError((err) => {
      wx.showToast({ title: `录音出错：${err.errMsg}`, icon: 'none' });
      this.setState(this.data.segments.length ? 'stopped' : 'idle');
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
      success: (r) => r.confirm && this.setData({ state: 'idle', seconds: 0, timeText: '00:00', segments: [], importName: '' }),
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

  // ---- 提交 ----
  async submit() {
    if (this.data.state !== 'stopped' || this.data.submitting) return undefined;
    const { stores, storeIndex, stageIndex, isFirst, cooperated, note, seconds, segments } = this.data;
    if (storeIndex < 0) return wx.showToast({ title: '请先选择门店', icon: 'none' });
    if (!segments.length) return wx.showToast({ title: '还没有录音', icon: 'none' });
    this.setData({ submitting: true, step: 1 });
    wx.showLoading({ title: '上传中', mask: true });
    try {
      const visit = await createVisit({
        storeId: stores[storeIndex].id,
        stage: STAGES[stageIndex],
        cooperated: isFirst ? cooperated : null,
        note,
        durationSec: seconds,
        segments,
      });
      await uploadSegments(segments, visit.upload, (done, total) => wx.showLoading({ title: `上传 ${done}/${total}`, mask: true }));
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
