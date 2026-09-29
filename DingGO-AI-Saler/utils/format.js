const pad = (n) => (n < 10 ? `0${n}` : `${n}`);

function duration(sec) {
  const s = Math.max(0, Math.round(sec || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h}:${pad(m)}:${pad(s % 60)}` : `${pad(m)}:${pad(s % 60)}`;
}

function timestamp(ms) {
  return duration((ms || 0) / 1000);
}

// 一律按北京时间（UTC+8）显示，不受手机或开发者工具所在电脑的时区影响
function beijing(input) {
  return new Date(new Date(input).getTime() + 8 * 3600 * 1000);
}

function date(input) {
  const d = beijing(input);
  return `${d.getUTCMonth() + 1}月${d.getUTCDate()}日 ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

function dayKey(input) {
  const d = beijing(input);
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
}

function grade(score) {
  if (score == null) return '';
  if (score >= 85) return 'A';
  if (score >= 70) return 'B';
  if (score >= 50) return 'C';
  return 'D';
}

module.exports = { duration, timestamp, date, dayKey, grade };
