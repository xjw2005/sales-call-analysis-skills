const { TIMELINE, MODULE_LABELS } = require('../../utils/constants');

// 状态 → 当前所在节点下标
const CURRENT = {
  uploading: 0, cost_pending: 1, asr_running: 2, role_classifying: 3,
  invalid_short: 3, invalid_content: 3, analyzing: 4, reviewing: 5,
  done: 7, partial_manual: 5, failed: 4,
};

Component({
  properties: { visit: Object },
  observers: {
    visit(v) {
      if (!v) return;
      const cur = CURRENT[v.status] != null ? CURRENT[v.status] : 0;
      const bad = ['invalid_short', 'invalid_content', 'failed', 'partial_manual'].includes(v.status);
      const nodes = TIMELINE.map((n, i) => {
        let state = 'todo';
        if (i < cur) state = 'done';
        else if (i === cur) state = bad ? 'error' : 'active';
        let sub = '';
        if (n.key === 'analyze' && v.moduleKeys) {
          sub = `${v.moduleDone || 0}/${v.moduleKeys.length} 个模块`;
          if (state === 'active' && v.moduleKeys[v.moduleDone]) sub += ` · 正在分析${MODULE_LABELS[v.moduleKeys[v.moduleDone]]}`;
        }
        if (n.key === 'role' && v.status === 'invalid_short') sub = '录音过短（不足 120 秒）';
        if (n.key === 'role' && v.status === 'invalid_content') sub = '内容无效';
        return { ...n, state, sub };
      });
      this.setData({ nodes });
    },
  },
  data: { nodes: [] },
});
