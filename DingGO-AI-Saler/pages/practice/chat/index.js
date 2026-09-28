const { start, turn, finish } = require('../../../services/practice');

Page({
  data: {
    scenarioId: '',
    title: '',
    desc: '',
    totalRounds: 0,
    round: 0,
    messages: [], // { id, role: 'customer'|'sales', text }
    input: '',
    waiting: false,
    finished: false,
    result: null,
    scrollTo: '',
  },

  onLoad({ scenario }) {
    this.setData({ scenarioId: scenario || 'opening' });
    this.begin();
  },

  async begin() {
    const res = await start(this.data.scenarioId);
    this.sessionId = res.sessionId;
    wx.setNavigationBarTitle({ title: `陪练：${res.title}` });
    this.setData({
      title: res.title, desc: res.desc, totalRounds: res.totalRounds, round: 1,
      messages: [{ id: 'c0', role: 'customer', text: res.reply }],
      input: '', waiting: false, finished: false, result: null, scrollTo: 'bottom',
    });
  },

  onInput(e) {
    this.setData({ input: e.detail.value });
  },

  async submit() {
    const text = this.data.input.trim();
    if (!text || this.data.waiting || this.data.finished) return;
    const messages = [...this.data.messages, { id: `s${Date.now()}`, role: 'sales', text }];
    this.setData({ messages, input: '', waiting: true, scrollTo: 'bottom' });
    const res = await turn(this.sessionId, text);
    if (res.finished) {
      this.setData({ waiting: false, finished: true });
      const result = await finish(this.sessionId);
      this.setData({ result, scrollTo: 'bottom' });
      return;
    }
    this.setData({
      messages: [...messages, { id: `c${Date.now()}`, role: 'customer', text: res.reply }],
      round: this.data.round + 1,
      waiting: false,
      scrollTo: 'bottom',
    });
  },

  endEarly() {
    wx.showModal({
      title: '结束陪练',
      content: '现在结束会按已完成的轮次打分，确定吗？',
      success: async (r) => {
        if (!r.confirm || !this.data.messages.some((m) => m.role === 'sales')) return;
        this.setData({ finished: true });
        this.setData({ result: await finish(this.sessionId), scrollTo: 'bottom' });
      },
    });
  },

  again() {
    this.begin();
  },
  other() {
    wx.navigateBack();
  },
});
