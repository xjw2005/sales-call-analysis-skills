const config = require('../config/index');
const { request } = require('./request');
const { listStores, getStore } = require('./store');
const { listVisits } = require('./visit');
const { listTodos } = require('./todo');
const storage = require('../utils/storage');
const fmt = require('../utils/format');
const { FIRST_STAGE } = require('../utils/constants');
const { PROFILE_QUESTIONS, GENERAL_QUESTIONS, OBJECTIONS, DIM_TIPS, weakestDim } = require('../utils/playbook');

const DAY = 24 * 3600 * 1000;
const STALE_DAYS = 14;
const REVIEW_KEY = 'reviewSeen';
const OBJECTION_FOCUS = ['控价防窜', '价格敏感', '利润空间'];
const PROCESSING = ['uploading', 'asr_running', 'role_classifying', 'analyzing', 'reviewing'];

function markReviewSeen(visitId) {
  const seen = storage.get(REVIEW_KEY, []);
  if (seen.indexOf(visitId) < 0) storage.set(REVIEW_KEY, [...seen, visitId]);
}

// 首页主动卡片 + 拜访四步进度
async function getTodayFeed(currentStoreId) {
  if (!config.useMock) return request({ url: '/assistant/feed', data: { storeId: currentStoreId } });
  const [stores, visits, todos] = await Promise.all([listStores(), listVisits(), listTodos()]);
  const cards = [];
  const store = stores.find((s) => s.id === currentStoreId) || stores[0];

  // 1. 到期跟进：只展示最紧急的一件，其余提示数量（避免首条消息过长）
  const due = todos.filter((t) => !t.done && t.due.days <= 0);
  if (due.length) {
    const t = due[0];
    cards.push({
      type: 'todo', key: `todo-${t.id}`, badge: t.due.text, tone: t.due.tone,
      title: `${t.storeName}：${t.topic}`,
      desc: t.action + (due.length > 1 ? `（另有 ${due.length - 1} 件到期）` : ''),
      buttons: [
        due.length > 1 ? { text: '全部待办', action: 'openTodos' } : { text: '标记完成', action: 'todoDone', id: t.id },
        { text: '复制微信话术', action: 'copyMsg', id: t.id, primary: true },
      ],
    });
  }

  // 2. 待处理录音
  const pending = visits.filter((v) => v.status === 'cost_pending');
  const short = visits.filter((v) => v.status === 'invalid_short' && Date.now() - v.createdAt < 7 * DAY);
  if (pending.length) {
    cards.push({
      type: 'pending', key: 'pending', badge: '待确认', tone: 'warn',
      title: `${pending.length} 条录音待确认识别费用`,
      desc: `${pending[0].storeName} · 录音 ${fmt.duration(pending[0].durationSec)}，确认后自动开始分析`,
      buttons: [{ text: '去确认', action: 'openVisit', id: pending[0].id, primary: true }],
    });
  }
  // 3. 拜访复盘（3 天内新完成、未看过）
  const seen = storage.get(REVIEW_KEY, []);
  const fresh = visits.find((v) => v.status === 'done' && Date.now() - v.createdAt < 3 * DAY && seen.indexOf(v.id) < 0);
  if (fresh) {
    const weak = weakestDim(fresh.analysis.effectiveness);
    const tip = weak && DIM_TIPS[weak.name];
    const eff = fresh.analysis.effectiveness;
    cards.push({
      type: 'review', key: `review-${fresh.id}`, badge: eff ? `${eff.total} 分 · ${fmt.grade(eff.total)}` : '复盘', tone: 'primary',
      title: `${fresh.storeName} 的拜访分析好了`,
      desc: tip
        ? `最该改进：${weak.name}（${weak.score}/${weak.max}）。${tip.tip}，比如：“${tip.say}”`
        : `下一步：${fresh.analysis.nextAction.actions.map((a) => a.topic).join('、') || '无需新增行动'}`,
      buttons: [
        { text: '知道了', action: 'dismissReview', id: fresh.id },
        { text: '看详情', action: 'openVisit', id: fresh.id, primary: true },
      ],
    });
  }

  // 4. 进店前简报（当前门店）
  if (store) {
    const unconfirmed = store.profile ? store.profile.sections.filter((s) => s.state === '未确认').length : 0;
    cards.push({
      type: 'brief', key: `brief-${store.id}`, badge: '进店前', tone: 'info',
      title: `去 ${store.name} 前，先看 30 秒简报`,
      desc: store.profile
        ? `${store.oneLine}${unconfirmed ? `｜还有 ${unconfirmed} 项情况没摸清，已帮你准备好要问的问题。` : ''}`
        : '第一次拜访：已准备好开场白和要问的问题。',
      buttons: [{ text: '开始录音', action: 'record', id: store.id }, { text: '看简报', action: 'openBrief', id: store.id, primary: true }],
    });
  }

  // 5. 异议提醒（当前门店命中的重点关心点）
  const latest = store && visits.find((v) => v.storeId === store.id && v.status === 'done');
  if (latest) {
    const hit = latest.analysis.concerns.find((c) => c.state === '是' && OBJECTION_FOCUS.indexOf(c.name) >= 0);
    if (hit && OBJECTIONS[hit.name]) {
      cards.push({
        type: 'objection', key: `obj-${store.id}`, badge: hit.name, tone: 'warn',
        title: `${store.name} 在意「${hit.name}」，这样回应`,
        desc: OBJECTIONS[hit.name].script,
        buttons: [{ text: '看应对要点', action: 'openObjection', id: hit.name, primary: true }],
      });
    }
  }

  // 6. 久未拜访
  stores.filter((s) => s.lastVisitAt && Date.now() - s.lastVisitAt > STALE_DAYS * DAY).slice(0, 1).forEach((s) => {
    const days = Math.floor((Date.now() - s.lastVisitAt) / DAY);
    cards.push({
      type: 'stale', key: `stale-${s.id}`, badge: `${days} 天未访`, tone: 'muted',
      title: `${s.name} 已经 ${days} 天没去了`,
      desc: s.actions.length ? `上次卡在：${s.actions[0].topic}` : '去看看最近情况，别让门店凉了。',
      buttons: [{ text: '看简报', action: 'openBrief', id: s.id, primary: true }],
    });
  });

  // 7. 录音过短提醒（优先级最低）
  if (short.length) {
    cards.push({
      type: 'short', key: 'short', badge: '提醒', tone: 'muted',
      title: `${short[0].storeName} 的录音太短，没法分析`,
      desc: '有效对话不足 2 分钟。下次进店多问几个开放式问题，让老板多说一会儿。',
      buttons: [{ text: '看看怎么问', action: 'openBrief', id: short[0].storeId, primary: true }],
    });
  }

  return { cards: cards.slice(0, 5), steps: computeSteps(store, visits, todos) };
}

// 拜访四步：0 进店前 → 1 进店中 → 2 离店后 → 3 跟进；全部完成为 4
function computeSteps(store, visits, todos) {
  const labels = ['进店前看简报', '进店中录音', '离店后看复盘', '按待办跟进'];
  let current = 0;
  let hint = store ? `今天去 ${store.name}？先看一眼简报` : '先添加一家门店';
  if (store) {
    const today = fmt.dayKey(Date.now());
    const v = visits.find((x) => x.storeId === store.id && fmt.dayKey(x.createdAt) === today);
    if (v && (v.status === 'cost_pending' || PROCESSING.indexOf(v.status) >= 0)) {
      current = 2;
      hint = v.status === 'cost_pending' ? '录音已上传，确认费用后开始分析' : '录音分析中，好了会出现在下方';
    } else if (v && v.status === 'done') {
      const open = todos.filter((t) => t.storeId === store.id && !t.done);
      current = open.length ? 3 : 4;
      hint = open.length ? `还有 ${open.length} 件跟进事项` : '今天这家店的工作都完成了';
    }
  }
  return { labels, current, hint };
}

// 进店前简报
async function getBrief(storeId) {
  if (!config.useMock) return request({ url: `/assistant/brief/${storeId}` });
  const [store, todos] = await Promise.all([getStore(storeId), listTodos()]);
  const last = store.visits.find((v) => v.status === 'done');
  const brief = {
    store,
    isFirst: !last,
    lastText: last ? `${fmt.date(last.createdAt)} · ${last.stage}` : '',
    oneLine: store.oneLine,
    concerns: last ? last.analysis.concerns.filter((c) => c.state === '是').map((c) => c.name) : [],
    openTodos: todos.filter((t) => t.storeId === storeId && !t.done),
    goals: [],
    questions: [],
    objections: [],
  };
  if (!last) {
    brief.goals = ['了解门店基本情况和主营品牌', '找到老板最在意的 1–2 个问题', '约好下一次见面的时间'];
    brief.questions = GENERAL_QUESTIONS.slice();
    brief.opening = `老板您好，我是 A2 奶粉的业务，今天想花十分钟了解下店里奶粉卖得怎么样，看看有没有能帮上忙的。`;
  } else {
    brief.goals = last.analysis.nextAction.actions.map((a) => `${a.topic}：${a.acceptance}`);
    brief.questions = store.profile.sections
      .filter((s) => s.state === '未确认' && PROFILE_QUESTIONS[s.key])
      .map((s) => `${PROFILE_QUESTIONS[s.key]}（补全「${s.label}」）`);
    if (brief.questions.length < 2) brief.questions.push(...GENERAL_QUESTIONS.slice(0, 2 - brief.questions.length));
    // 最多列 2 个异议，重点关心点优先
    const ordered = OBJECTION_FOCUS.concat(Object.keys(OBJECTIONS).filter((k) => OBJECTION_FOCUS.indexOf(k) < 0));
    brief.objections = ordered.filter((c) => brief.concerns.indexOf(c) >= 0).slice(0, 2).map((c) => ({ name: c, ...OBJECTIONS[c] }));
  }
  return brief;
}

module.exports = { getTodayFeed, getBrief, markReviewSeen };
