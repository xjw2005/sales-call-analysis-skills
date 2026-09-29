const config = require('../config/index');
const { request, delay } = require('./request');
const { listVisits } = require('./visit');
const storage = require('../utils/storage');
const fmt = require('../utils/format');
const seed = require('../model/seed');

const DAY = 24 * 3600 * 1000;
const DONE_KEY = 'todoDone';

function endOfDay(ts) {
  const d = new Date(ts);
  d.setHours(23, 59, 59, 999);
  return d.getTime();
}

// 到期描述：逾期 N 天 / 今天到期 / 明天 / X月X日
function dueText(dueAt) {
  const days = Math.round((endOfDay(dueAt) - endOfDay(Date.now())) / DAY);
  if (days < 0) return { text: `逾期 ${-days} 天`, tone: 'error', days };
  if (days === 0) return { text: '今天到期', tone: 'warn', days };
  if (days === 1) return { text: '明天到期', tone: 'info', days };
  const d = new Date(dueAt);
  return { text: `${d.getMonth() + 1}月${d.getDate()}日`, tone: 'muted', days };
}

// 待办 = 每家门店最近一次已分析拜访的「建议行动」
async function listTodos() {
  if (!config.useMock) return request({ url: '/todos' });
  const visits = await listVisits();
  const done = storage.get(DONE_KEY, []);
  const seen = {};
  const todos = [];
  visits.forEach((v) => {
    if (v.status !== 'done' || seen[v.storeId]) return;
    seen[v.storeId] = true;
    v.analysis.nextAction.actions.forEach((a, i) => {
      const dueAt = endOfDay(v.createdAt + (a.dueDays || 3) * DAY);
      const id = `${v.id}-${i}`;
      todos.push({
        ...a, id, source: 'ai', visitId: v.id, storeId: v.storeId, storeName: v.storeName,
        dueAt, due: dueText(dueAt), dueDate: fmt.date(dueAt).split(' ')[0],
        done: done.indexOf(id) >= 0, targetQty: null, achievedQty: null, gapQty: null, unit: '', progressNote: '',
      });
    });
  });
  // 月度目标类待办（旧「后续行动」表）：有目标数量和达成数量，差额 = 目标 - 达成
  const monthEnd = new Date();
  const goalDue = endOfDay(new Date(monthEnd.getFullYear(), monthEnd.getMonth() + 1, 0).getTime());
  const goalStore = seed.stores.find((s) => s.id === 's1');
  todos.push({
    id: 't-goal-s1', source: 'import', visitId: '', storeId: goalStore.id, storeName: goalStore.name,
    topic: `${monthEnd.getMonth() + 1}月任务拆解`, action: '', owner: '销售', timeframe: '', reason: '', acceptance: '',
    dueAt: goalDue, due: dueText(goalDue), dueDate: fmt.date(goalDue).split(' ')[0], done: done.indexOf('t-goal-s1') >= 0,
    targetQty: 60, achievedQty: 36, gapQty: 24, unit: '罐', progressNote: '月中下单，本月指标已和老板确认。',
  });
  return delay(todos.sort((a, b) => a.dueAt - b.dueAt));
}

function setTodoDone(id, isDone) {
  if (!config.useMock) return request({ url: `/todos/${id}/${isDone ? 'done' : 'undo'}`, method: 'POST' });
  const done = storage.get(DONE_KEY, []).filter((x) => x !== id);
  if (isDone) done.push(id);
  storage.set(DONE_KEY, done);
  return delay(true, 100);
}

module.exports = { listTodos, setTodoDone };
