Component({
  properties: {
    value: { type: null, value: null }, // 为空显示“--”
    max: { type: Number, value: 100 },
    unit: { type: String, value: '分' },
    label: String,
    color: { type: String, value: '#5b55e8' },
    size: { type: Number, value: 180 },
  },
  observers: {
    'value, max'(value, max) {
      const pct = value == null ? 0 : Math.max(0, Math.min(100, (value / max) * 100));
      this.setData({ pct, empty: value == null });
    },
  },
  data: { pct: 0, empty: true },
});
