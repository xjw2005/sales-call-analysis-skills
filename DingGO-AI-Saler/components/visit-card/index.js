const { STATUS, FIRST_STAGE } = require('../../utils/constants');
const fmt = require('../../utils/format');

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
        summary: v.analysis ? v.analysis.profile.sections[0].content : '',
      });
    },
  },
  methods: {
    open() {
      wx.navigateTo({ url: `/pages/visit/detail/index?id=${this.data.visit.id}` });
    },
  },
});
