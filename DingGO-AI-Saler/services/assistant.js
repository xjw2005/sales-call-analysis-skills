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

// 「今日待办」大卡片：把跟进、待处理录音、复盘、进店前、久未拜访合并成一张卡的分区列表
async function getTodayPanel(currentStoreId) {
  if (!config.useMock) return request({ url: '/assistant/today', data: { storeId: currentStoreId } });
  const [stores, visits, todos] = await Promise.all([listStores(), listVisits(), listTodos()]);
  const store = stores.find((s) => s.id === currentStoreId) || stores[0];
  const sections = [];

  // 跟进事项：逾期 + 今天到期；都没有时展示最近一件
  const open = todos.filter((t) => !t.done);
  const due = open.filter((t) => t.due.days <= 0);
  const followRows = (due.length ? due : open.slice(0, 1)).map((t) => ({
    key: `todo-${t.id}`, badge: t.due.text, tone: t.due.tone,
    text: `${t.storeName} · ${t.topic}`, sub: t.action,
    btn: '完成', action: 'todoDone', id: t.id,
    subBtn: '复制微信话术', subAction: 'copyMsg',
  }));
  if (followRows.length) sections.push({ title: '跟进事项', rows: followRows });

  // 待处理录音
  const recRows = [];
  visits.filter((v) => v.status === 'cost_pending').forEach((v) => recRows.push({
    key: `cost-${v.id}`, badge: '待确认', tone: 'warn',
    text: `${v.storeName} · 录音 ${fmt.duration(v.durationSec)}`, sub: `预估识别费 ¥${v.estCost}，确认后自动分析`,
    btn: '去确认', action: 'openVisit', id: v.id,
  }));
  visits.filter((v) => v.status === 'invalid_short' && Date.now() - v.createdAt < 7 * DAY).forEach((v) => recRows.push({
    key: `short-${v.id}`, badge: '过短', tone: 'muted',
    text: `${v.storeName} · 录音不足 2 分钟`, sub: '下次多问开放式问题，让老板多说',
    btn: '看怎么问', action: 'openBrief', id: v.storeId,
  }));
  if (recRows.length) sections.push({ title: '待处理录音', rows: recRows });

  // 拜访复盘：3 天内新完成、未看过
  const seen = storage.get(REVIEW_KEY, []);
  const reviewRows = visits
    .filter((v) => v.status === 'done' && Date.now() - v.createdAt < 3 * DAY && seen.indexOf(v.id) < 0)
    .map((v) => {
      const eff = v.analysis.effectiveness;
      const weak = weakestDim(eff);
      const tip = weak && DIM_TIPS[weak.name];
      return {
        key: `review-${v.id}`, badge: eff ? `${eff.total}分 ${fmt.grade(eff.total)}` : '已分析', tone: 'primary',
        text: `${v.storeName} · ${v.stage}`,
        sub: tip ? `最该改：${weak.name}。${tip.tip}` : `下一步：${v.analysis.nextAction.actions.map((a) => a.topic).join('、') || '无需新增行动'}`,
        btn: '看详情', action: 'openVisit', id: v.id,
      };
    });
  if (reviewRows.length) sections.push({ title: '拜访复盘', rows: reviewRows });

  // 进店前：当前门店
  if (store) {
    const unconfirmed = store.profile ? store.profile.sections.filter((x) => x.state === '未确认').length : 0;
    sections.push({
      title: '进店前',
      rows: [{
        key: `brief-${store.id}`, badge: '简报', tone: 'info',
        text: store.name,
        sub: store.profile ? `${store.oneLine}${unconfirmed ? `（${unconfirmed} 项情况待摸清）` : ''}` : '第一次拜访，已备好开场白和要问的问题',
        btn: '看简报', action: 'openBrief', id: store.id,
      }],
    });
  }

  // 久未拜访
  const staleRows = stores
    .filter((s) => s.lastVisitAt && Date.now() - s.lastVisitAt > STALE_DAYS * DAY)
    .map((s) => ({
      key: `stale-${s.id}`, badge: `${Math.floor((Date.now() - s.lastVisitAt) / DAY)}天未访`, tone: 'muted',
      text: s.name, sub: s.actions.length ? `上次卡在：${s.actions[0].topic}` : '去看看最近情况',
      btn: '看简报', action: 'openBrief', id: s.id,
    }));
  if (staleRows.length) sections.push({ title: '久未拜访', rows: staleRows });

  const overdue = due.filter((t) => t.due.days < 0).length;
  const stuck = due[0] || staleRows[0];
  return {
    count: due.length,
    hint: due.length ? `今天有 ${due.length} 件事要跟进${overdue ? `，${overdue} 件已逾期` : ''}` : '今天暂无到期的跟进事项',
    stat: `${due.length} 件跟进${overdue ? ` · ${overdue} 件逾期` : ''}`,
    steps: computeSteps(store, visits, todos),
    sections,
    suggestions: [
      '客户说网上更便宜怎么回？',
      stuck ? `${stuck.storeName || stuck.text}上次卡在哪？` : '下一步该做什么？',
      '这家店最关心什么？',
    ],
  };
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

module.exports = { getTodayPanel, getBrief, markReviewSeen };
