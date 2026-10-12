const config = require('../config/index');
const { request, delay } = require('./request');
const { listVisits } = require('./visit');
const plans = require('./plan');
const storage = require('../utils/storage');
const fmt = require('../utils/format');
const seed = require('../model/seed');

const DAY = 24 * 3600 * 1000;
const DONE_KEY = 'todoDone';
const CANCEL_KEY = 'todoCancelled'; // 演示模式：删除的待办记在本机

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
  const cancelled = storage.get(CANCEL_KEY, []);
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
  return delay(todos.filter((t) => cancelled.indexOf(t.id) < 0).sort((a, b) => a.dueAt - b.dueAt));
}

function setTodoDone(id, isDone) {
  if (!config.useMock) return request({ url: `/todos/${id}/${isDone ? 'done' : 'undo'}`, method: 'POST' });
  const done = storage.get(DONE_KEY, []).filter((x) => x !== id);
  if (isDone) done.push(id);
  storage.set(DONE_KEY, done);
  return delay(true, 100);
}

// 删除一条待办（生成错了、不需要做了）：后端是「取消」，不再出现在列表里，重新分析也不会再生成同一条
function cancelTodo(id) {
  if (!config.useMock) return request({ url: `/todos/${id}`, method: 'PATCH', data: { status: 'cancelled' } });
  storage.set(CANCEL_KEY, [...storage.get(CANCEL_KEY, []).filter((x) => x !== id), id]);
  return delay(true, 100);
}

// 「全部待办」= 今日计划要去的店 + 跟进事项（拜访分析生成的行动 / 约定）+ 等你确认费用的录音。
// 首页「今日待办」卡片、我的待办页、「我的」页的数字都用这一份，不会各说各的。
async function loadTodoCenter() {
  const [plan, todos, visits] = await Promise.all([plans.todayPlan(), listTodos(), listVisits()]);
  const planOpen = plan.filter((p) => p.status !== 'visited');
  const planDone = plan.filter((p) => p.status === 'visited');
  const followOpen = todos.filter((t) => !t.done);
  const followDone = todos.filter((t) => t.done);
  // 飞书导入的历史录音文件还没迁移，不能确认识别，不算待处理
  const recordings = visits.filter((v) => v.status === 'cost_pending' && !v.legacy);
  return {
    planOpen, planDone, followOpen, followDone, recordings,
    openCount: planOpen.length + followOpen.length + recordings.length,
    doneCount: planDone.length + followDone.length,
  };
}

module.exports = { listTodos, setTodoDone, cancelTodo, loadTodoCenter };
