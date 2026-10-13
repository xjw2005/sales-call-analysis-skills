const { STATUS, FIRST_STAGE } = require('../../utils/constants');
const fmt = require('../../utils/format');

// 需要销售动手的状态：卡片上直接写一句「要你做什么」，不用点进去才知道
function todoHint(v) {
  if (v.status === 'cost_pending' && !v.legacy) return `待你确认识别费 ¥${v.estCost || 0}，确认后自动分析 ›`;
  if (v.status === 'failed') return '处理失败，点进去可以重新处理 ›';
  if (v.status === 'invalid_short' || v.status === 'invalid_content') return '判定为无效录音，觉得不对可以点进去重新判定 ›';
  return '';
}

Component({
  options: { addGlobalClass: true },
  properties: { visit: Object, showStore: { type: Boolean, value: true } },
  observers: {
    visit(v) {
      if (!v) return;
      const eff = v.analysis && v.analysis.effectiveness;
      this.setData({
        status: STATUS[v.status] || { text: v.status, tone: 'muted' },
        isFirst: v.stage === FIRST_STAGE,
        time: fmt.date(v.createdAt),
        dur: fmt.duration(v.durationSec),
        grade: eff ? fmt.grade(eff.total) : '',
        score: eff ? eff.total : null,
        summary: v.analysis && v.analysis.profile && v.analysis.profile.sections.length ? v.analysis.profile.sections[0].content : '',
        legacy: !!v.legacy,
        noRecording: v.status === 'no_recording',
        noteBrief: v.note ? (v.note.length > 40 ? `${v.note.slice(0, 40)}…` : v.note) : '',
        todo: todoHint(v),
      });
    },
  },
  methods: {
    open() {
      wx.navigateTo({ url: `/pages/visit/detail/index?id=${this.data.visit.id}` });
    },
  },
});
