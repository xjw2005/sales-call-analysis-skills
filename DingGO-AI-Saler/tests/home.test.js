// 首页（助手）的交互逻辑：多轮对话、切换 / 新建 / 删除对话、陪练。用法：node --test tests/
const test = require('node:test');
const assert = require('node:assert');
const { mountPage, deferred, tick, clone } = require('./harness');

const SESSIONS = () => [
  { id: 1, title: '渝中区的店', updatedAt: 1700000000000 },
  { id: 2, title: '这家店最关心什么', updatedAt: 1700100000000 },
  { id: 3, title: '价格怎么回', updatedAt: 1700200000000 },
];

// 一个带假服务的首页。overrides 里可以替换任何一个假服务的方法，用来制造失败或延迟
function home(overrides = {}) {
  const calls = { ask: [], del: [], clear: 0, get: [], start: [], turn: [], finish: [], greet: 0, feedback: [] };
  const server = { sessions: SESSIONS() };
  const fake = {
    chat: {
      askStream: async (params) => { calls.ask.push(params); return { text: `答：${params.question}`, sessionId: 99, suggestions: [], logId: 7 }; },
      listSessions: async () => clone(server.sessions),
      getSession: async (id) => { calls.get.push(id); return [{ role: 'user', text: `问${id}` }, { role: 'ai', text: `答${id}`, logId: 5 }]; },
      deleteSession: async (id) => { calls.del.push(id); server.sessions = server.sessions.filter((s) => s.id !== Number(id)); return true; },
      clearSessions: async () => { calls.clear += 1; server.sessions = []; return { deleted: 3 }; },
      sendFeedback: async (...a) => { calls.feedback.push(a); return true; },
      ...(overrides.chat || {}),
    },
    practice: {
      isDemo: () => false,
      listScenarios: async () => [{ id: 'profit', title: '利润太薄' }],
      start: async (id) => { calls.start.push(id); return { sessionId: 11, title: '利润太薄', totalRounds: 8, reply: '这个一罐能赚多少？' }; },
      turn: async (sid, text) => { calls.turn.push([sid, text]); return { reply: `追问${calls.turn.length}`, finished: false, round: calls.turn.length + 1 }; },
      finish: async (sid) => { calls.finish.push(sid); return { total: 80, dims: [], reference: '参考', answers: calls.turn.length }; },
      scenarioByConcern: () => 'opening',
      ...(overrides.practice || {}),
    },
    plan: {
      greeting: async () => { calls.greet += 1; return { text: '早上好，今天去哪个区？', plan: [], options: [] }; },
      suggest: async () => ({ title: '候选', items: [] }),
      addPlan: async () => [], removePlan: async () => true, todayPlan: async () => [],
      ...(overrides.plan || {}),
    },
  };
  const env = mountPage('pages/home/index.js', {
    config: { useMock: false, mockAI: false, mockPractice: false },
    stubs: {
      'services/request.js': { refreshUser: async () => null },
      'services/store.js': { listStores: async () => [{ id: 's1', name: '甲店' }] },
      'services/todo.js': { listTodos: async () => [], setTodoDone: async () => true },
      'services/assistant.js': { getTodayPanel: async () => ({ hint: '', count: 0 }), markReviewSeen() {} },
      'services/chat.js': fake.chat,
      'services/plan.js': fake.plan,
      'services/memory.js': { confirmMemory: async () => true, deleteMemory: async () => true },
      'services/practice.js': fake.practice,
    },
    modals: overrides.modals || [],
  });
  return { ...env, calls, server, fake };
}

async function opened(overrides) {
  const h = home(overrides);
  h.page.onLoad();
  await h.page.onShow();
  await tick();
  return h;
}
const types = (page) => page.data.messages.map((m) => m.type);
const texts = (page) => page.data.messages.filter((m) => m.type === 'user' || m.type === 'ai').map((m) => m.text);
const tap = (id, extra = {}) => ({ currentTarget: { dataset: { id, ...extra } } });

// ---------- 基本对话 ----------
test('进入首页：只问候一次，重复 onShow 不会重复问候', async () => {
  const { page, calls } = await opened();
  await page.onShow();
  await page.onShow();
  await tick();
  assert.deepStrictEqual(types(page), ['greet']);
  assert.strictEqual(calls.greet, 1);
});

test('连续多轮提问：每轮都能发、能收，sessionId 沿用，输入框清空', async () => {
  const { page, calls } = await opened();
  for (const q of ['第一问', '第二问', '第三问']) {
    await page.send(q);
    assert.strictEqual(page.data.thinking, false);
    assert.strictEqual(page.data.input, '');
  }
  assert.deepStrictEqual(texts(page), ['第一问', '答：第一问', '第二问', '答：第二问', '第三问', '答：第三问']);
  assert.strictEqual(calls.ask[0].sessionId, undefined); // 第一问：新对话
  assert.strictEqual(calls.ask[1].sessionId, 99); // 之后沿用服务端给的对话号
  assert.strictEqual(page.data.sessionId, 99);
});

test('空输入、全是空格、正在回答时发送：都不会发出请求', async () => {
  const gate = deferred();
  const { page, calls } = await opened({ chat: { askStream: async (p) => { calls0.push(p); return gate.promise; } } });
  const calls0 = [];
  await page.send('');
  await page.send('   ');
  assert.strictEqual(calls0.length, 0);
  const first = page.send('慢问题');
  await tick();
  await page.send('抢着再问'); // 还在回答，被忽略
  assert.strictEqual(calls0.length, 1);
  gate.resolve({ text: '好了', sessionId: 5 });
  await first;
  assert.strictEqual(page.data.thinking, false);
  assert.strictEqual(calls.ask.length, 0);
});

test('回答失败：气泡里显示原因，状态恢复，可以立刻再问', async () => {
  let n = 0;
  const { page } = await opened({ chat: { askStream: async (p) => { n += 1; if (n === 1) throw new Error('AI 现在请求比较多，请稍后再试'); return { text: '这次好了', sessionId: 8 }; } } });
  await page.send('问一下');
  assert.strictEqual(page.data.thinking, false);
  assert.ok(texts(page).includes('AI 现在请求比较多，请稍后再试'));
  await page.send('再问一次');
  assert.ok(texts(page).includes('这次好了'));
});

// ---------- 新建 / 切换 / 删除对话 ----------
test('新建对话：清空消息和对话号，并重新问候', async () => {
  const { page, calls } = await opened();
  await page.send('随便问');
  page.newChat();
  await tick();
  assert.deepStrictEqual(types(page), ['greet']);
  assert.strictEqual(page.data.sessionId, 0);
  assert.strictEqual(calls.greet, 2);
  await page.send('新对话里的第一问');
  assert.strictEqual(calls.ask[1].sessionId, undefined); // 不会带上旧对话的号
});

test('回答还没回来就新建对话：旧回答被丢弃，不会串进新对话，新对话马上能提问', async () => {
  const gate = deferred();
  let n = 0;
  const { page, calls } = await opened({ chat: { askStream: async (p) => { n += 1; return n === 1 ? gate.promise : { text: '新对话的回答', sessionId: 42 }; } } });
  const old = page.send('旧问题');
  await tick();
  page.newChat();
  await tick();
  assert.strictEqual(page.data.thinking, false); // 不会一直卡在「思考中」
  await page.send('新问题'); // 马上能问
  gate.resolve({ text: '旧回答', sessionId: 77, cands: { title: '候选', kind: 'gap', items: [{ storeId: 's1', name: '店' }] } });
  await old;
  await tick();
  assert.deepStrictEqual(texts(page), ['新问题', '新对话的回答']);
  assert.strictEqual(page.data.sessionId, 42); // 旧回答的 77 没有覆盖
  assert.ok(!types(page).includes('cands'));
  assert.strictEqual(page.data.thinking, false);
  assert.strictEqual(calls.greet, 2);
});

test('回答还没回来就打开历史对话：旧回答不会写进被打开的那段', async () => {
  const gate = deferred();
  const { page } = await opened({ chat: { askStream: async () => gate.promise } });
  const old = page.send('旧问题');
  await tick();
  await page.openHistory(tap(2));
  await tick();
  assert.strictEqual(page.data.sessionId, 2);
  gate.resolve({ text: '迟到的回答', sessionId: 77 });
  await old;
  await tick();
  assert.strictEqual(page.data.sessionId, 2);
  assert.deepStrictEqual(texts(page), ['问2', '答2']);
});

test('连点两条历史对话：以最后点的为准，即使先点的后返回', async () => {
  const slow = deferred();
  const { page } = await opened({ chat: { getSession: async (id) => (Number(id) === 1 ? slow.promise : [{ role: 'user', text: '问3' }]) } });
  const a = page.openHistory(tap(1));
  const b = page.openHistory(tap(3));
  await b;
  slow.resolve([{ role: 'user', text: '问1' }]);
  await a;
  await tick();
  assert.strictEqual(page.data.sessionId, 3);
  assert.deepStrictEqual(texts(page), ['问3']);
});

test('打开历史对话后继续追问：带上这段对话的号', async () => {
  const { page, calls } = await opened();
  await page.openHistory(tap(2));
  assert.deepStrictEqual(texts(page), ['问2', '答2']);
  await page.send('接着问');
  assert.strictEqual(calls.ask[0].sessionId, 2);
});

test('打开历史对话失败（比如已被删）：提示原因，当前对话不变，列表刷新', async () => {
  const { page, server } = await opened({ chat: { getSession: async () => { throw new Error('对话不存在'); } } });
  await page.send('当前对话');
  server.sessions = [];
  await page.openHistory(tap(2));
  await tick();
  assert.deepStrictEqual(texts(page), ['当前对话', '答：当前对话']);
  assert.strictEqual(page.data.histories.length, 0);
});

test('抽屉：打开时加载对话记录并隐藏底部标签栏，关闭后恢复', async () => {
  const { page, tabBar } = await opened();
  page.toggleDrawer();
  await tick();
  assert.strictEqual(page.data.drawerOpen, true);
  assert.deepStrictEqual(page.data.histories.map((h) => h.id), [1, 2, 3]);
  assert.strictEqual(tabBar.data.hidden, true);
  page.toggleDrawer();
  assert.strictEqual(tabBar.data.hidden, false);
});

test('删除对话：取消不删；确定后从列表消失、服务器上也删掉', async () => {
  const { page, calls } = await opened({ modals: [{ confirm: false }, { confirm: true }] });
  page.toggleDrawer();
  await tick();
  await page.deleteHistory(tap(2, { title: '这家店最关心什么' }));
  assert.deepStrictEqual(calls.del, []); // 点了取消
  assert.strictEqual(page.data.histories.length, 3);
  await page.deleteHistory(tap(2, { title: '这家店最关心什么' }));
  await tick();
  assert.deepStrictEqual(calls.del, [2]);
  assert.deepStrictEqual(page.data.histories.map((h) => h.id), [1, 3]);
});

test('删除的是别的对话：当前对话不受影响', async () => {
  const { page } = await opened();
  await page.openHistory(tap(3));
  page.toggleDrawer();
  await tick();
  await page.deleteHistory(tap(1, { title: 'x' }));
  await tick();
  assert.strictEqual(page.data.sessionId, 3);
  assert.deepStrictEqual(texts(page), ['问3', '答3']);
});

test('删除的正是当前打开的对话：回到一段全新的对话，不会继续往已删除的对话里写', async () => {
  const { page, calls } = await opened();
  page.toggleDrawer(); // 真实操作：先打开抽屉，点一条对话（点击会关闭抽屉），再打开抽屉
  await tick();
  await page.openHistory(tap(3));
  page.toggleDrawer();
  await tick();
  assert.strictEqual(page.data.histories.find((h) => h.id === 3).current, true);
  await page.deleteHistory(tap(3, { title: '价格怎么回' }));
  await tick();
  assert.strictEqual(page.data.sessionId, 0);
  assert.deepStrictEqual(types(page), ['greet']);
  await page.send('删完后的第一问');
  assert.strictEqual(calls.ask[0].sessionId, undefined);
});

test('删除失败：提示原因，列表和当前对话都不变', async () => {
  const { page, toasts, log } = await opened({ chat: { deleteSession: async () => { throw new Error('网络异常'); } } });
  await page.openHistory(tap(2));
  page.toggleDrawer();
  await tick();
  await page.deleteHistory(tap(2, { title: 'x' }));
  await tick();
  assert.ok(log.toasts.includes('网络异常'));
  assert.strictEqual(page.data.sessionId, 2);
  assert.strictEqual(page.data.histories.length, 3);
});

test('清空全部对话：确认后全部删除，当前对话也回到新对话；空列表时点清空没有反应', async () => {
  const { page, calls, log } = await opened();
  await page.openHistory(tap(1));
  page.toggleDrawer();
  await tick();
  await page.clearHistories();
  await tick();
  assert.strictEqual(calls.clear, 1);
  assert.strictEqual(page.data.histories.length, 0);
  assert.strictEqual(page.data.sessionId, 0);
  await page.clearHistories(); // 已经空了
  assert.strictEqual(calls.clear, 1);
  assert.ok(log.toasts.includes('已清空'));
});

test('对话在别处被删后继续提问：自动另开一段，不会一直报「对话不存在」', async () => {
  let n = 0;
  const { page, calls } = await opened({
    chat: { askStream: async (p) => { calls.ask.push(p); n += 1; if (p.sessionId) throw new Error('对话不存在'); return { text: '另开一段后的回答', sessionId: 50 }; } },
  });
  await page.openHistory(tap(2));
  await page.send('在已删除的对话里问');
  assert.ok(texts(page).includes('另开一段后的回答'));
  assert.strictEqual(page.data.sessionId, 50);
  assert.strictEqual(n, 2);
});

test('在「我的」页清空对话后回到首页：当前对话换成新的', async () => {
  const { page, app } = await opened();
  await page.openHistory(tap(2));
  app.globalData.chatsCleared = true;
  await page.onShow();
  await tick();
  assert.strictEqual(page.data.sessionId, 0);
  assert.deepStrictEqual(types(page), ['greet']);
  assert.strictEqual(app.globalData.chatsCleared, false);
});

test('演示模式：删除、清空对话只动本机缓存，不请求服务器', async () => {
  const storage = { chatHistory: [{ id: 'c1', title: '甲', updatedAt: 1, messages: [{ id: 'a', type: 'user', text: '甲' }] }, { id: 'c2', title: '乙', updatedAt: 2, messages: [] }] };
  const calls = { del: 0, clear: 0 };
  const env = mountPage('pages/home/index.js', {
    storage,
    config: { useMock: true, mockAI: true, mockPractice: true },
    stubs: {
      'services/request.js': { refreshUser: async () => null },
      'services/store.js': { listStores: async () => [] },
      'services/todo.js': { listTodos: async () => [], setTodoDone: async () => true },
      'services/assistant.js': { getTodayPanel: async () => ({ hint: '', count: 0 }), markReviewSeen() {} },
      'services/chat.js': { askStream: async () => ({ text: '演示', demo: true }), listSessions: async () => [], getSession: async () => [], deleteSession: async () => { calls.del += 1; }, clearSessions: async () => { calls.clear += 1; }, sendFeedback: async () => true },
      'services/plan.js': { greeting: async () => ({ text: '你好', plan: [], options: [] }) },
      'services/memory.js': {},
      'services/practice.js': { isDemo: () => true },
    },
  });
  env.page.onLoad();
  await env.page.onShow();
  env.page.toggleDrawer();
  await tick();
  assert.strictEqual(env.page.data.histories.length, 2);
  await env.page.deleteHistory(tap('c1', { title: '甲' }));
  await tick();
  assert.deepStrictEqual(storage.chatHistory.map((h) => h.id), ['c2']);
  await env.page.clearHistories();
  assert.deepStrictEqual(storage.chatHistory, []);
  assert.deepStrictEqual([calls.del, calls.clear], [0, 0]);
});

// ---------- 反馈 ----------
test('对回答点「有用」：只能点一次；提交失败要撤回', async () => {
  const { page, calls } = await opened();
  await page.send('问');
  const ai = page.data.messages.find((m) => m.type === 'ai');
  await page.rate(tap(ai.id, { rating: 1 }));
  await page.rate(tap(ai.id, { rating: -1 })); // 已经评过，忽略
  assert.strictEqual(calls.feedback.length, 1);
  assert.strictEqual(page.data.messages.find((m) => m.id === ai.id).rating, 1);
});

// ---------- 陪练 ----------
async function practicing(overrides) {
  const h = await opened(overrides);
  h.page.pickScenario(tap('profit', { title: '利润太薄' }));
  await tick();
  return h;
}

test('陪练：开始 → 多轮回答 → 结束并点评 → 回到普通对话', async () => {
  const { page, calls } = await practicing();
  assert.strictEqual(page.data.practice.round, 1);
  assert.ok(page.data.messages.some((m) => m.type === 'customer' && m.text === '这个一罐能赚多少？'));
  for (const t of ['回答一', '回答二', '回答三']) {
    await page.send(t);
  }
  assert.strictEqual(page.data.practice.round, 4);
  assert.deepStrictEqual(calls.turn.map((c) => c[1]), ['回答一', '回答二', '回答三']);
  page.endPractice();
  await tick();
  assert.strictEqual(calls.finish.length, 1);
  assert.strictEqual(page.data.practice, null);
  assert.ok(types(page).includes('result'));
  await page.send('陪练结束后问普通问题');
  assert.strictEqual(calls.ask.length, 1); // 走的是 AI 问答，不是陪练
});

test('陪练：老板收尾（finished）时，收尾的话先显示，再自动点评', async () => {
  const { page, calls } = await practicing({ practice: { turn: async () => ({ reply: '行，那就先拿两箱试试', finished: true, round: 4 }) } });
  await page.send('我先给您发资料');
  await tick();
  const customers = page.data.messages.filter((m) => m.type === 'customer').map((m) => m.text);
  assert.ok(customers.includes('行，那就先拿两箱试试'));
  assert.ok(types(page).includes('result'));
  assert.strictEqual(page.data.practice, null);
  assert.strictEqual(calls.finish.length, 1);
});

test('陪练：连点同一个场景两次，只开一场', async () => {
  const gate = deferred();
  const h = await opened({ practice: { start: async () => gate.promise } });
  h.page.pickScenario(tap('profit', { title: '利润太薄' }));
  h.page.pickScenario(tap('profit', { title: '利润太薄' }));
  gate.resolve({ sessionId: 11, title: '利润太薄', totalRounds: 8, reply: '开场' });
  await tick();
  assert.strictEqual(h.page.data.messages.filter((m) => m.type === 'user' && m.text.startsWith('练')).length, 1);
  assert.strictEqual(h.page.data.messages.filter((m) => m.type === 'customer').length, 1);
});

test('陪练：这一句失败了，气泡撤掉、文字放回输入框、轮数不变，重发成功后没有重复', async () => {
  let n = 0;
  const { page, calls } = await practicing({ practice: { turn: async (sid, text) => { n += 1; if (n === 1) throw new Error('AI 现在请求比较多，请稍后再试'); return { reply: '追问', finished: false, round: 2 }; } } });
  await page.send('我的回答');
  assert.strictEqual(page.data.input, '我的回答');
  assert.strictEqual(page.data.practice.round, 1);
  assert.ok(!texts(page).includes('我的回答'));
  assert.strictEqual(page.data.thinking, false);
  await page.send(page.data.input);
  assert.strictEqual(texts(page).filter((t) => t === '我的回答').length, 1);
  assert.strictEqual(page.data.practice.round, 2);
});

test('陪练：点评生成失败后，再发任意一句只是重试点评，不算新的回答', async () => {
  let f = 0;
  const { page, calls } = await practicing({ practice: { finish: async () => { f += 1; if (f === 1) throw new Error('AI 暂时没有响应，请稍后再试'); return { total: 70, dims: [], reference: '', answers: 1 }; } } });
  await page.send('一句回答');
  page.endPractice();
  await tick();
  assert.strictEqual(page.data.practice.pendingFinish, true);
  await page.send('随便发一句');
  await tick();
  assert.strictEqual(calls.turn.length, 1); // 没有把「随便发一句」当成回答
  assert.ok(types(page).includes('result'));
  assert.strictEqual(page.data.practice, null);
});

test('陪练：开场还没回来就新建对话，旧陪练不会冒出来', async () => {
  const gate = deferred();
  const h = await opened({ practice: { start: async () => gate.promise } });
  h.page.pickScenario(tap('profit', { title: '利润太薄' }));
  await tick();
  h.page.newChat();
  gate.resolve({ sessionId: 11, title: '利润太薄', totalRounds: 8, reply: '迟到的开场' });
  await tick();
  assert.strictEqual(h.page.data.practice, null);
  assert.ok(!h.page.data.messages.some((m) => m.type === 'customer'));
  assert.strictEqual(h.page.data.thinking, false);
});

test('陪练中点「新对话」：退出陪练，输入提示恢复，之后正常问答', async () => {
  const { page, calls } = await practicing();
  assert.notStrictEqual(page.data.placeholder, page.data.defaultPlaceholder);
  page.newChat();
  await tick();
  assert.strictEqual(page.data.practice, null);
  assert.strictEqual(page.data.placeholder, page.data.defaultPlaceholder);
  await page.send('普通问题');
  assert.strictEqual(calls.ask.length, 1);
  assert.strictEqual(calls.turn.length, 0);
});

test('陪练中打开历史对话：陪练条消失', async () => {
  const { page } = await practicing();
  await page.openHistory(tap(1));
  assert.strictEqual(page.data.practice, null);
  assert.deepStrictEqual(texts(page), ['问1', '答1']);
});
