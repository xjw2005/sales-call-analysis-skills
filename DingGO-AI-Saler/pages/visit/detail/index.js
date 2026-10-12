const config = require('../../../config/index');
const fmt = require('../../../utils/format');
const { STATUS, STORE_CONDITIONS } = require('../../../utils/constants');
const { getVisit, confirmCost, rejudge, retryVisit, updateVisit, deleteVisit } = require('../../../services/visit');

const TERMINAL = ['done', 'partial_manual', 'invalid_short', 'invalid_content', 'failed', 'cost_pending'];
const CONCERN_TONE = { 是: 'on', 否: 'off', 证据不足: 'unknown' };
const CONF_TONE = { 高: 'success', 中: 'info', 低: 'muted' };

const STAGE_TEXT = { asr_submit: '提交语音识别', asr_poll: '语音识别', roles: '整理对话角色', validity: '判断录音是否有效', analysis: 'AI 分析', queued: '排队' };

// 给证据补上显示用的时间戳文本
const withTs = (list) => (list || []).map((e) => ({ ...e, ts: fmt.timestamp(e.startMs) }));

Page({
  data: {
    id: '',
    visit: null,
    status: {},
    tabs: [],
    tab: 0,
    showTimeline: true,
    a: null,
    grade: '',
    failDesc: '',
    invalidDesc: '',
    transcript: [],
    playingUid: '',
  },

  onLoad({ id }) {
    this.setData({ id });
    this.audio = wx.createInnerAudioContext();
  },
  onShow() {
    this.load();
  },
  onHide() {
    this.stopPolling();
  },
  onUnload() {
    this.stopPolling();
    if (this.audio) this.audio.destroy();
  },

  async load() {
    try {
      const visit = await getVisit(this.data.id);
      this.render(visit);
      if (TERMINAL.includes(visit.status)) this.stopPolling();
      else this.startPolling();
    } catch (err) {
      wx.showToast({ title: err.message, icon: 'none' });
    }
  },

  render(visit) {
    const isFirst = visit.mode === 'first';
    const pipe = visit.pipeline || {};
    this.setData({
      failDesc: pipe.uncertain
        ? '上次提交语音识别时无法确认是否成功，为避免重复计费，请联系管理员核对后再处理。'
        : `${STAGE_TEXT[pipe.errorStage] || '处理'}出错：${pipe.error || '未知原因'}`,
      invalidDesc: pipe.validity && pipe.validity.reason ? `判定理由：${pipe.validity.reason}` : '',
    });
    const survey = visit.survey || {};
    const patch = {
      visit: {
        ...visit,
        // 确认费用、重试等接口的返回里没有「能不能改」：沿用详情接口给的
        canEdit: visit.canEdit !== undefined ? visit.canEdit : !!(this.data.visit && this.data.visit.canEdit),
        time: fmt.date(visit.createdAt),
        dur: fmt.duration(visit.durationSec),
        purposes: visit.purposes || [],
        surveyText: [survey.area && `面积 ${survey.area}`, survey.storeType && survey.storeType, survey.crossBorder && `跨境：${survey.crossBorder}`,
          survey.monthlyTotal != null && `全品类月均 ${survey.monthlyTotal} 罐`, survey.a2MonthlyEst != null && `A2 月均预估 ${survey.a2MonthlyEst} 罐`]
          .filter(Boolean).join(' · '),
      },
      status: STATUS[visit.status] || {},
    };
    // 拜访信息卡：有目的、门店状况、速记、打卡地址或调研信息时才显示
    const checkin = visit.checkin || {};
    const hasPoint = checkin.lat != null && checkin.lng != null;
    patch.hasCheckin = !!(checkin.address || hasPoint);
    patch.checkinText = checkin.address || (hasPoint ? `${Number(checkin.lat).toFixed(5)}，${Number(checkin.lng).toFixed(5)}` : '');
    patch.checkinOnMap = hasPoint;
    patch.hasInfo = !!((visit.purposes || []).length || visit.storeCondition || visit.note || patch.hasCheckin || patch.visit.surveyText);
    if (visit.status === 'done' && visit.analysis && visit.analysis.legacy) {
      // 从飞书导入的历史拜访：分析是原文，按模块分段展示
      patch.a = visit.analysis;
      patch.tabs = ['历史记录', '对话'];
      patch.grade = '';
      patch.transcript = (visit.transcript || []).map((u) => ({ ...u, ts: fmt.timestamp(u.startMs) }));
      if (!this.data.a) patch.showTimeline = false;
    } else if (visit.status === 'done' && visit.analysis) {
      const a = visit.analysis;
      patch.a = {
        ...a,
        explicitNeeds: a.explicitNeeds.map((n) => ({ ...n, evidence: withTs(n.evidence) })),
        implicitNeeds: a.implicitNeeds.map((n) => ({ ...n, tone: CONF_TONE[n.confidence], evidence: withTs(n.evidence) })),
        concerns: a.concerns.map((c) => ({ ...c, tone: CONCERN_TONE[c.state] })),
        quotes: a.quotes.map((q) => ({ ...q, evidence: withTs(q.evidence) })),
      };
      patch.grade = a.effectiveness ? fmt.grade(a.effectiveness.total) : '';
      patch.tabs = isFirst ? ['洞察', '话术', '行动', '对话'] : ['洞察', '行动', '对话'];
      patch.transcript = (visit.transcript || []).map((u) => ({ ...u, ts: fmt.timestamp(u.startMs) }));
      if (!this.data.a) patch.showTimeline = false; // 已完成时默认收起时间线
    }
    this.setData(patch);
  },

  startPolling() {
    if (this.timer) return;
    this.timer = setInterval(() => this.load(), config.pollIntervalMs);
  },
  stopPolling() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  },

  // 打卡位置：有坐标就在微信地图上打开
  openCheckin() {
    const c = (this.data.visit && this.data.visit.checkin) || {};
    if (c.lat == null || c.lng == null) return;
    wx.openLocation({ latitude: Number(c.lat), longitude: Number(c.lng), name: '拜访打卡位置', address: c.address || '', scale: 17 });
  },
  toggleTimeline() {
    this.setData({ showTimeline: !this.data.showTimeline });
  },
  switchTab(e) {
    this.setData({ tab: Number(e.currentTarget.dataset.i) });
  },

  async confirm() {
    try {
      const visit = await confirmCost(this.data.id);
      this.render(visit);
      this.startPolling();
    } catch (err) {
      wx.showToast({ title: err.message, icon: 'none' });
    }
  },
  later() {
    wx.navigateBack();
  },
  async rejudge() {
    try {
      await rejudge(this.data.id);
      this.load();
    } catch (err) {
      wx.showModal({ title: '重新判定', content: err.message, showCancel: false });
    }
  },

  async retry() {
    try {
      const visit = await retryVisit(this.data.id);
      this.render(visit);
      this.startPolling();
    } catch (err) {
      wx.showModal({ title: '重新处理', content: err.message, showCancel: false });
    }
  },

  openStore() {
    const v = this.data.visit;
    if (v && v.storeId) wx.navigateTo({ url: `/pages/store/detail/index?id=${v.storeId}` });
  },

  // ---- 「···」：修改速记 / 门店状况，删除这次拜访 ----
  sheet(itemList) {
    return new Promise((resolve) => wx.showActionSheet({ itemList, success: (r) => resolve(itemList[r.tapIndex]), fail: () => resolve('') }));
  },
  async more() {
    const pick = await this.sheet(['修改现场速记', '修改门店状况', '删除这次拜访']);
    if (pick === '修改现场速记') return this.editNote();
    if (pick === '修改门店状况') return this.editCondition();
    if (pick === '删除这次拜访') return this.remove();
    return undefined;
  },
  editNote() {
    if (!this.data.visit.canEdit) return;
    wx.showModal({
      title: '现场速记',
      editable: true,
      content: this.data.visit.note || '',
      placeholderText: '记下这次拜访的要点',
      success: async (r) => {
        if (!r.confirm || (r.content || '') === (this.data.visit.note || '')) return;
        await this.save({ note: (r.content || '').trim() });
      },
    });
  },
  async editCondition() {
    const pick = await this.sheet([...STORE_CONDITIONS, '清除']);
    if (!pick) return undefined;
    return this.save({ storeCondition: pick === '清除' ? '' : pick });
  },
  async save(data) {
    try {
      this.render(await updateVisit(this.data.id, data));
      wx.showToast({ title: '已保存', icon: 'none' });
    } catch (err) {
      wx.showToast({ title: err.message, icon: 'none' });
    }
  },
  // 删除：先确认，说清楚会发生什么、能不能找回
  remove() {
    wx.showModal({
      title: '删除这次拜访',
      content: '删除后拜访列表、门店记录里都不再显示，这次分析生成的未完成待办也会取消。删错了可以找管理员恢复。',
      confirmText: '删除',
      confirmColor: '#e5484d',
      success: async (r) => {
        if (!r.confirm) return;
        try {
          await deleteVisit(this.data.id);
        } catch (err) {
          wx.showModal({ title: '没有删除', content: err.message, showCancel: false });
          return;
        }
        this.stopPolling();
        wx.showToast({ title: '已删除', icon: 'none' });
        setTimeout(() => wx.navigateBack(), 600);
      },
    });
  },

  // 点击时间戳：跳到录音对应位置播放
  seek(e) {
    const { ms, uid } = e.currentTarget.dataset;
    const src = this.data.visit.audioUrl;
    if (!src) {
      wx.showToast({ title: `演示数据无录音，定位到 ${fmt.timestamp(ms)}`, icon: 'none' });
      this.setData({ playingUid: uid || '' });
      return;
    }
    if (this.audio.src !== src) this.audio.src = src;
    this.audio.seek(ms / 1000);
    this.audio.play();
    this.setData({ playingUid: uid || '' });
  },
});
