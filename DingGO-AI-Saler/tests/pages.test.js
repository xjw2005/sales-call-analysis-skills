// 其他页面的交互逻辑 + 服务层请求 + 界面和代码的对应检查。用法：node --test "tests/*.test.js"
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const { mountPage, deferred, tick } = require('./harness');

const ROOT = path.resolve(__dirname, '..');

// ---------- 界面上绑定的每个事件，代码里都要有对应的函数 ----------
function walk(dir, out = []) {
  fs.readdirSync(dir, { withFileTypes: true }).forEach((e) => {
    if (e.name === 'tests' || e.name === 'node_modules') return;
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else if (p.endsWith('.wxml')) out.push(p);
  });
  return out;
}
test('每个页面：界面里绑定的事件处理函数在代码里都存在', () => {
  const problems = [];
  walk(ROOT).forEach((wxml) => {
    const js = wxml.replace(/\.wxml$/, '.js');
    if (!fs.existsSync(js)) return;
    const src = fs.readFileSync(js, 'utf8');
    const names = [...fs.readFileSync(wxml, 'utf8').matchAll(/\b(?:bind|catch):?[a-z]+="([A-Za-z_]\w*)"/g)].map((m) => m[1]);
    names.forEach((n) => { if (!new RegExp(`^\\s*(?:async\\s+)?${n}\\s*[(:]`, 'm').test(src)) problems.push(`${path.relative(ROOT, wxml)}: ${n}`); });
  });
  assert.deepStrictEqual(problems, []);
});

test('app.json 里登记的页面文件都齐全（js / wxml / json）', () => {
  const pages = JSON.parse(fs.readFileSync(path.join(ROOT, 'app.json'), 'utf8')).pages;
  const missing = [];
  pages.forEach((p) => ['.js', '.wxml', '.json'].forEach((ext) => { if (!fs.existsSync(path.join(ROOT, p + ext))) missing.push(p + ext); }));
  assert.deepStrictEqual(missing, []);
});

// ---------- 服务层：删除对话的请求对不对 ----------
test('服务层：删除一段对话 / 清空对话，请求的地址、方法、凭证都正确', async () => {
  const seen = [];
  mountPage('pages/me/index.js', {
    config: { useMock: false, mockAI: false, transport: 'http', baseUrl: 'http://x' },
    storage: { token: 'T0K' },
    stubs: { 'services/visit.js': { listVisits: async () => [] } },
    onWx: (wx) => { wx.request = (o) => { seen.push(o); o.success({ statusCode: 200, data: true }); }; },
  });
  const chat = require(path.join(ROOT, 'services/chat.js'));
  await chat.deleteSession(5);
  await chat.clearSessions();
  assert.deepStrictEqual(seen.map((o) => [o.method, o.url]), [['DELETE', 'http://x/chat/sessions/5'], ['DELETE', 'http://x/chat/sessions']]);
  assert.ok(seen.every((o) => o.header.Authorization === 'Bearer T0K'));
});

// ---------- 我的 ----------
function me(extra = {}) {
  const calls = { clear: 0 };
  const env = mountPage('pages/me/index.js', {
    config: { useMock: false, mockAI: false, ...(extra.config || {}) },
    storage: { user: { name: '王荣伟', role: 'manager' }, chatHistory: [{ id: 'c1' }], ...(extra.storage || {}) },
    modals: extra.modals,
    stubs: {
      'services/request.js': { refreshUser: async () => null },
      'services/visit.js': { listVisits: extra.listVisits || (async () => []) },
      'services/chat.js': { clearSessions: extra.clearSessions || (async () => { calls.clear += 1; }) },
      'services/todo.js': { loadTodoCenter: extra.loadTodoCenter || (async () => ({ openCount: 0 })) },
    },
  });
  env.app.globalData.userName = '王荣伟';
  return { ...env, calls };
}

test('我的：头像字和角色跟着登录的人走', async () => {
  const { page } = me();
  await page.onShow();
  assert.strictEqual(page.data.initial, '王');
  assert.strictEqual(page.data.roleText, '区域经理');
  const sales = me({ storage: { user: { name: '曹世茂', role: 'sales' } } });
  sales.app.globalData.userName = '曹世茂';
  await sales.page.onShow();
  assert.strictEqual(sales.page.data.roleText, '销售');
});

test('我的：清空对话记录（真实模式）连服务器上的一起清，并通知助手页换新对话', async () => {
  const { page, calls, app, log } = me();
  page.clearChats();
  await tick();
  assert.strictEqual(calls.clear, 1);
  assert.strictEqual('chatHistory' in log.storage, false);
  assert.strictEqual(app.globalData.chatsCleared, true);
  assert.ok(log.toasts.includes('已清空'));
});

test('我的：清空失败时不能说「已清空」，也不通知助手页', async () => {
  const { page, app, log } = me({ clearSessions: async () => { throw new Error('网络异常'); } });
  page.clearChats();
  await tick();
  assert.ok(log.toasts.includes('网络异常') && !log.toasts.includes('已清空'));
  assert.notStrictEqual(app.globalData.chatsCleared, true);
});

test('我的：点了「取消」什么都不会删', async () => {
  const { page, calls } = me({ modals: [{ confirm: false }] });
  page.clearChats();
  await tick();
  assert.strictEqual(calls.clear, 0);
});

test('我的：演示模式只清本机，不请求服务器', async () => {
  const { page, calls, log } = me({ config: { useMock: true, mockAI: true } });
  page.clearChats();
  await tick();
  assert.strictEqual(calls.clear, 0);
  assert.strictEqual('chatHistory' in log.storage, false);
});

test('我的：拜访数据加载失败时提示原因，不报错', async () => {
  const { page, log } = me({ listVisits: async () => { throw new Error('网络异常'); } });
  await page.onShow();
  await tick();
  assert.ok(log.toasts.includes('网络异常'));
});

// ---------- 拜访列表 ----------
test('拜访列表：下拉刷新加载失败，转圈也要收起来', async () => {
  const { page, log } = mountPage('pages/visit/list/index.js', {
    config: { useMock: false },
    stubs: { 'services/visit.js': { listVisits: async () => { throw new Error('网络异常'); } } },
  });
  page.onPullDownRefresh();
  await tick();
  assert.strictEqual(log.pullStopped, 1);
  assert.ok(log.toasts.includes('网络异常'));
});

test('拜访列表：按状态筛选，「待确认」不含历史导入的', async () => {
  const visits = [
    { id: '1', status: 'cost_pending', legacy: false }, { id: '2', status: 'cost_pending', legacy: true },
    { id: '3', status: 'done' }, { id: '4', status: 'analyzing' }, { id: '5', status: 'failed' },
  ];
  const { page } = mountPage('pages/visit/list/index.js', { stubs: { 'services/visit.js': { listVisits: async () => visits } } });
  await page.onShow();
  await tick();
  const pick = async (i) => { page.pick({ currentTarget: { dataset: { i } } }); return page.data.shown.map((v) => v.id); };
  assert.deepStrictEqual(await pick(0), ['1', '2', '3', '4', '5']);
  assert.deepStrictEqual(await pick(1), ['1']);
  assert.deepStrictEqual(await pick(2), ['4']);
  assert.deepStrictEqual(await pick(3), ['3']);
  assert.deepStrictEqual(await pick(4), ['5']);
});

// ---------- 待办 ----------
const center = (over = {}) => ({
  planOpen: [], planDone: [], followOpen: [], followDone: [], recordings: [], openCount: 0, doneCount: 0, ...over,
});

test('待办：标记完成失败时提示原因；成功后列表刷新', async () => {
  let fail = true;
  let open = [{ id: 't1', topic: '送物料', done: false, storeName: '甲店' }];
  let done = [];
  const { page, log } = mountPage('pages/todo/index.js', {
    stubs: {
      'services/todo.js': {
        loadTodoCenter: async () => center({ followOpen: open, followDone: done, openCount: open.length, doneCount: done.length }),
        setTodoDone: async (id, d) => { if (fail) throw new Error('网络异常'); done = d ? [...open.map((t) => ({ ...t, done: true }))] : []; open = d ? [] : open; },
      },
    },
  });
  await page.onShow();
  await tick();
  await page.toggle({ currentTarget: { dataset: { id: 't1', done: false } } });
  assert.ok(log.toasts.includes('网络异常'));
  assert.strictEqual(page.data.open.length, 1);
  fail = false;
  await page.toggle({ currentTarget: { dataset: { id: 't1', done: false } } });
  await tick();
  assert.strictEqual(page.data.open.length, 0);
  assert.strictEqual(page.data.done.length, 1);
});

test('待办页：今日计划、跟进事项、待确认录音都在「待办」里，数字是三类之和', async () => {
  const data = center({
    planOpen: [{ id: 'p1', storeId: 's1', name: '阳光苗苗学府一号店', reason: '9月销量 0' }, { id: 'p2', storeId: 's2', name: '建辉乳业', reason: '' }],
    planDone: [{ id: 'p3', storeId: 's3', name: '已去的店', visitId: 'v9' }],
    followOpen: [{ id: 't1', topic: '送物料', storeName: '甲店', storeId: 's1', due: { text: '明天到期', tone: 'info' } }],
    followDone: [{ id: 't2', topic: '发资料', storeName: '乙店', storeId: 's2', done: true }],
    recordings: [{ id: 'v7', storeId: 's4', storeName: '丁店', createdAt: Date.now(), durationSec: 300, estCost: 0.5 }],
    openCount: 4, doneCount: 2,
  });
  const { page } = mountPage('pages/todo/index.js', { stubs: { 'services/todo.js': { loadTodoCenter: async () => data, setTodoDone: async () => true } } });
  await page.onShow();
  await tick();
  assert.deepStrictEqual([page.data.planOpen.length, page.data.open.length, page.data.recs.length], [2, 1, 1]);
  assert.strictEqual(page.data.openCount, 4);
  assert.strictEqual(page.data.doneCount, 2);
  assert.strictEqual(page.data.recs[0].durText, '05:00');
  assert.strictEqual(page.data.loaded, true);
});

test('待办页：今日计划里的店可以直接看简报、到店录音、看拜访', async () => {
  const { page, log, app } = mountPage('pages/todo/index.js', { stubs: { 'services/todo.js': { loadTodoCenter: async () => center(), setTodoDone: async () => true } } });
  page.openBrief({ currentTarget: { dataset: { store: 's1' } } });
  page.recordStore({ currentTarget: { dataset: { store: 's2' } } });
  page.openVisit({ currentTarget: { dataset: { id: 'v9' } } });
  assert.deepStrictEqual(log.navigations, ['navigateTo:/pages/brief/index?storeId=s1', 'navigateTo:/pages/record/index?storeId=s2', 'navigateTo:/pages/visit/detail/index?id=v9']);
  assert.strictEqual(app.globalData.currentStoreId, 's2');
});

test('待办页：加载失败时提示原因，页面不会一直空白转圈', async () => {
  const { page, log } = mountPage('pages/todo/index.js', { stubs: { 'services/todo.js': { loadTodoCenter: async () => { throw new Error('网络异常'); }, setTodoDone: async () => true } } });
  await page.onShow();
  await tick();
  assert.ok(log.toasts.includes('网络异常'));
  assert.strictEqual(page.data.loaded, true);
});

test('待办页：没有任何待办才显示「待办都完成了」', () => {
  const wxml = fs.readFileSync(path.join(ROOT, 'pages/todo/index.wxml'), 'utf8');
  assert.ok(/wx:if="\{\{loaded && !openCount\}\}"/.test(wxml)); // 以三类之和为准，不是只看跟进事项
});

// 统一口径：首页卡片、待办页、「我的」页用同一份数据
function centerEnv({ plan = [], todos = [], visits = [], fail = '' } = {}) {
  return mountPage('pages/me/index.js', {
    config: { useMock: false, transport: 'http', baseUrl: 'http://x' },
    storage: { token: 'T', user: { id: '1', name: '甲' } },
    onWx: (wx) => {
      wx.request = (o) => {
        const data = { '/plans/today': plan, '/todos': todos, '/visits': visits }[o.url.replace('http://x', '').split('?')[0]];
        if (fail && o.url.endsWith(fail)) return o.fail({ errMsg: '网络异常' });
        return o.success({ statusCode: 200, data: data || [] });
      };
    },
  });
}
test('全部待办的统计：今日计划要去的 + 未完成的跟进事项 + 待确认录音（历史导入的不算）', async () => {
  centerEnv({
    plan: [{ id: 'p1', status: 'planned', storeId: 's1' }, { id: 'p2', status: 'planned', storeId: 's2' }, { id: 'p3', status: 'visited', storeId: 's3', visitId: 'v1' }],
    todos: [{ id: 't1', done: false }, { id: 't2', done: true }, { id: 't3', done: true }],
    visits: [{ id: 'v1', status: 'cost_pending', legacy: false }, { id: 'v2', status: 'cost_pending', legacy: true }, { id: 'v3', status: 'done' }],
  });
  const { loadTodoCenter } = require(path.join(ROOT, 'services/todo.js'));
  const c = await loadTodoCenter();
  assert.deepStrictEqual([c.planOpen.length, c.planDone.length, c.followOpen.length, c.followDone.length, c.recordings.length], [2, 1, 1, 2, 1]);
  assert.strictEqual(c.openCount, 4); // 2 + 1 + 1
  assert.strictEqual(c.doneCount, 3); // 1 已去 + 2 已完成
});

test('全部待办：有计划但没有跟进事项时，也不是 0（就是你截图里的情况）', async () => {
  centerEnv({ plan: [{ id: 'p1', status: 'planned' }, { id: 'p2', status: 'planned' }], todos: [{ id: 't1', done: true }, { id: 't2', done: true }, { id: 't3', done: true }] });
  const { loadTodoCenter } = require(path.join(ROOT, 'services/todo.js'));
  const c = await loadTodoCenter();
  assert.strictEqual(c.openCount, 2);
  assert.strictEqual(c.followOpen.length, 0);
});

test('全部待办：任何一个请求失败就整体报错，不会悄悄少算', async () => {
  centerEnv({ fail: '/todos' });
  const { loadTodoCenter } = require(path.join(ROOT, 'services/todo.js'));
  await assert.rejects(loadTodoCenter(), /网络异常/);
});

test('我的：有「我的待办」入口，带未完成数量，点进待办页；取不到数量不影响别的', async () => {
  const { page, log } = me({ loadTodoCenter: async () => ({ openCount: 5 }) });
  await page.onShow();
  await tick();
  assert.strictEqual(page.data.todoCount, 5);
  page.openTodos();
  assert.deepStrictEqual(log.navigations, ['navigateTo:/pages/todo/index']);
  const wxml = fs.readFileSync(path.join(ROOT, 'pages/me/index.wxml'), 'utf8');
  assert.ok(wxml.includes('bindtap="openTodos"') && wxml.includes('{{todoCount}}'));
  const broken = me({ loadTodoCenter: async () => { throw new Error('网络异常'); } });
  await broken.page.onShow();
  await tick();
  assert.strictEqual(broken.page.data.todoCount, 0);
});

// ---------- 录音：上传失败后重试 ----------
function recorder({ uploadFails = 1, completeFails = 0 } = {}) {
  const calls = { create: 0, upload: 0, complete: 0 };
  const env = mountPage('pages/record/index.js', {
    config: { useMock: false },
    stubs: {
      'services/store.js': { listStores: async () => [{ id: 's1', name: '甲店' }, { id: 's2', name: '乙店' }] },
      'services/visit.js': {
        createVisit: async () => { calls.create += 1; return { id: `v${calls.create}`, upload: {} }; },
        completeUpload: async () => { calls.complete += 1; if (calls.complete <= completeFails) throw new Error('完成失败'); },
      },
      'services/upload.js': { uploadSegments: async () => { calls.upload += 1; if (calls.upload <= uploadFails) throw new Error('上传失败 500'); return ['k']; } },
    },
  });
  env.page.onLoad({ storeId: 's1' });
  env.page.setData({ stores: [{ id: 's1', name: '甲店' }, { id: 's2', name: '乙店' }], storeIndex: 0, state: 'stopped', segments: ['a.mp3'], seconds: 300 });
  return { ...env, calls };
}

test('录音：上传中途失败后点重试，不会再建一条重复的拜访', async () => {
  const { page, calls, log } = recorder({ uploadFails: 1 });
  await page.submit();
  assert.strictEqual(calls.create, 1);
  assert.strictEqual(page.data.submitting, false); // 失败后能再点
  await page.submit();
  assert.strictEqual(calls.create, 1); // 沿用刚才那条
  assert.strictEqual(calls.upload, 2);
  assert.deepStrictEqual(log.navigations, ['redirectTo:/pages/visit/detail/index?id=v1']);
});

test('录音：上传已完成、只是「完成上传」那一步失败，重试时不重传录音', async () => {
  const { page, calls } = recorder({ uploadFails: 0, completeFails: 1 });
  await page.submit();
  await page.submit();
  assert.deepStrictEqual([calls.create, calls.upload, calls.complete], [1, 1, 2]);
});

test('录音：失败后换了门店再提交，是另一条拜访', async () => {
  const { page, calls } = recorder({ uploadFails: 1 });
  await page.submit();
  page.setData({ storeIndex: 1 });
  await page.submit();
  assert.strictEqual(calls.create, 2);
});

test('录音：没选门店 / 没录音时不能提交；重复点提交只提交一次', async () => {
  const { page, calls, log } = recorder({ uploadFails: 0 });
  page.setData({ storeIndex: -1 });
  await page.submit();
  assert.ok(log.toasts.includes('请先选择门店'));
  page.setData({ storeIndex: 0, segments: [] });
  await page.submit();
  assert.ok(log.toasts.includes('还没有录音'));
  assert.strictEqual(calls.create, 0);
  page.setData({ segments: ['a.mp3'] });
  await Promise.all([page.submit(), page.submit()]);
  assert.strictEqual(calls.create, 1);
});

test('录音：重录会清掉旧的进店时间和上传进度', async () => {
  const { page, calls } = recorder({ uploadFails: 1 });
  page.startedAt = 12345;
  await page.submit(); // 失败，留下上传进度
  page.reset();
  assert.strictEqual(page.startedAt, 0);
  assert.strictEqual(page.data.state, 'idle');
  page.setData({ state: 'stopped', segments: ['b.mp3'] });
  await page.submit();
  assert.strictEqual(calls.create, 2); // 新录音是新拜访
});

test('录音：录完还没提交时离开要提醒；提交成功后不再提醒', async () => {
  const { page, log } = recorder({ uploadFails: 0 });
  page.setState('stopped');
  assert.strictEqual(log.alertOn, true);
  await page.submit();
  assert.strictEqual(log.alertOn, false);
});

// ---------- 绑定账号（同事第一次打开小程序） ----------
function bindEnv(responses, storage = { bindToken: 'BT' }) {
  const sent = [];
  const env = mountPage('pages/bind/index.js', {
    config: { useMock: false, transport: 'http', baseUrl: 'http://x' },
    storage,
    onWx: (wx) => {
      wx.request = (o) => { sent.push(o); const r = responses.shift(); o.success({ statusCode: r.status, data: r.data }); };
      wx.login = ({ success }) => success({ code: 'c' });
    },
  });
  return { ...env, sent };
}

test('绑定：码正确 → 保存登录凭证、清掉绑定令牌、进入首页', async () => {
  const { page, log, sent } = bindEnv([{ status: 200, data: { token: 'LOGIN', user: { name: '曹世茂', role: 'sales' } } }]);
  page.onInput({ detail: { value: '1234 5678' } }); // 中间的空格会被去掉
  assert.strictEqual(page.data.code, '12345678');
  await page.submit();
  assert.deepStrictEqual(sent[0].data, { bindToken: 'BT', code: '12345678' });
  assert.strictEqual(log.storage.token, 'LOGIN');
  assert.ok(!('bindToken' in log.storage));
  assert.strictEqual(log.storage.user.name, '曹世茂');
  await new Promise((r) => setTimeout(r, 700));
  assert.deepStrictEqual(log.navigations, ['reLaunch:/pages/home/index']);
});

test('绑定：码不足 8 位、码错误：给出提示且留在本页，可以再试；不会重复提交', async () => {
  const { page, log, sent } = bindEnv([{ status: 400, data: { message: '绑定码不对，还可以再试 3 次' } }]);
  page.onInput({ detail: { value: '123' } });
  await page.submit();
  assert.strictEqual(page.data.error, '请输入 8 位绑定码');
  assert.strictEqual(sent.length, 0);
  page.onInput({ detail: { value: '12345678' } });
  await page.submit();
  assert.strictEqual(page.data.error, '绑定码不对，还可以再试 3 次');
  assert.strictEqual(page.data.loading, false);
  assert.strictEqual(log.storage.bindToken, 'BT'); // 绑定令牌还在，能继续输入
  assert.deepStrictEqual(log.navigations, []);
});

// ---------- 自定义组件：用到的都要声明，声明的文件都要存在 ----------
test('wxml 里用到的自定义组件都在 json 里声明过，声明的组件文件都存在', () => {
  const BUILTIN = new Set(['scroll-view', 'swiper-item', 'movable-view', 'movable-area', 'cover-view', 'cover-image', 'match-media', 'page-container', 'share-element', 'page-meta', 'navigation-bar', 'open-data', 'web-view', 'grid-view', 'list-view', 'sticky-header', 'sticky-section']);
  const appComps = (JSON.parse(fs.readFileSync(path.join(ROOT, 'app.json'), 'utf8')).usingComponents) || {};
  const problems = [];
  walk(ROOT).forEach((wxml) => {
    const jsonFile = wxml.replace(/\.wxml$/, '.json');
    const declared = fs.existsSync(jsonFile) ? (JSON.parse(fs.readFileSync(jsonFile, 'utf8')).usingComponents || {}) : {};
    Object.entries({ ...appComps, ...declared }).forEach(([, ref]) => {
      if (!fs.existsSync(path.join(ROOT, `${ref}.js`)) || !fs.existsSync(path.join(ROOT, `${ref}.wxml`))) problems.push(`${path.relative(ROOT, jsonFile)}: 组件文件不存在 ${ref}`);
    });
    const used = new Set([...fs.readFileSync(wxml, 'utf8').matchAll(/<([a-z]+(?:-[a-z]+)+)[\s/>]/g)].map((m) => m[1]));
    used.forEach((tag) => { if (!BUILTIN.has(tag) && !(tag in declared) && !(tag in appComps)) problems.push(`${path.relative(ROOT, wxml)}: 用了 <${tag}> 但没有在 json 里声明`); });
  });
  assert.deepStrictEqual(problems, []);
});

// ---------- 回到顶部 ----------
function loadComponent(rel, wx) {
  let def;
  global.Component = (d) => { def = d; };
  global.wx = wx;
  const file = path.join(ROOT, rel);
  delete require.cache[require.resolve(file)];
  require(file);
  return def;
}

test('回到顶部：滚过一屏才显示；只在跨过阈值时刷新页面，滚动过程中不会每帧 setData', () => {
  const { page } = mountPage('pages/visit/list/index.js', { stubs: { 'services/visit.js': { listVisits: async () => [] } } });
  const sets = [];
  const orig = page.setData;
  page.setData = function spy(p, cb) { sets.push(p); return orig.call(this, p, cb); };
  [0, 50, 300, 600].forEach((y) => page.onPageScroll({ scrollTop: y }));
  assert.strictEqual(sets.length, 0); // 没到一屏：不动
  page.onPageScroll({ scrollTop: 601 });
  assert.deepStrictEqual(sets, [{ showTop: true }]);
  [700, 900, 2000, 5000].forEach((y) => page.onPageScroll({ scrollTop: y }));
  assert.strictEqual(sets.length, 1); // 一直在往下滚：不重复刷新
  page.onPageScroll({ scrollTop: 100 }); // 往回滚到顶部附近
  assert.deepStrictEqual(sets[1], { showTop: false });
  assert.strictEqual(page.data.showTop, false);
});

test('回到顶部：长列表页（拜访、门店、选择门店）都接上了滚动监听和按钮', () => {
  [['pages/visit/list', null], ['pages/store/list', 300], ['pages/store/pick', 48]].forEach(([dir, offset]) => {
    const js = fs.readFileSync(path.join(ROOT, dir, 'index.js'), 'utf8');
    const wxml = fs.readFileSync(path.join(ROOT, dir, 'index.wxml'), 'utf8');
    assert.ok(js.includes('onPageScroll') && js.includes('showTop'), `${dir} 缺少滚动监听`);
    assert.ok(wxml.includes('<back-top show="{{showTop}}"'), `${dir} 缺少按钮`);
    if (offset) assert.ok(wxml.includes(`offset="{{${offset}}}"`), `${dir} 按钮位置`);
  });
});

test('回到顶部：点按钮平滑滚回页面顶部', () => {
  const calls = [];
  const def = loadComponent('components/back-top/index.js', { pageScrollTo: (o) => calls.push(o) });
  assert.strictEqual(def.properties.offset.value, 176); // 默认在标签栏上方
  def.methods.top();
  assert.deepStrictEqual(calls, [{ scrollTop: 0, duration: 300 }]);
});

test('底部标签栏：点已选中的标签回到顶部，点别的标签才切换页面', () => {
  const calls = { scroll: [], tab: [] };
  const def = loadComponent('custom-tab-bar/index.js', { pageScrollTo: (o) => calls.scroll.push(o), switchTab: (o) => calls.tab.push(o.url) });
  const self = { data: { ...def.data, selected: 2 } };
  def.methods.switchTab.call(self, { currentTarget: { dataset: { index: 2 } } });
  assert.deepStrictEqual([calls.scroll.length, calls.tab.length], [1, 0]);
  def.methods.switchTab.call(self, { currentTarget: { dataset: { index: 0 } } });
  assert.deepStrictEqual(calls.tab, ['/pages/home/index']);
  assert.strictEqual(calls.scroll.length, 1);
});

// ---------- 录音管理器：全局只有一个，监听只注册一次 ----------
function fakeRecorderEnv() {
  const handlers = {};
  const rm = { starts: [], stops: 0 };
  ['onStart', 'onResume', 'onPause', 'onInterruptionBegin', 'onInterruptionEnd', 'onStop', 'onError'].forEach((n) => { rm[n] = (fn) => { (handlers[n] = handlers[n] || []).push(fn); }; });
  rm.start = (o) => rm.starts.push(o);
  rm.stop = () => { rm.stops += 1; };
  rm.pause = () => {};
  rm.resume = () => { rm.resumed = (rm.resumed || 0) + 1; };
  rm.emit = (name, ...args) => (handlers[name] || []).forEach((f) => f(...args));
  rm.count = (name) => (handlers[name] || []).length;
  const flags = {};
  const env = mountPage('pages/record/index.js', {
    config: { useMock: false },
    stubs: {
      'services/store.js': { listStores: async () => [{ id: 's1', name: '甲店' }] },
      'services/visit.js': { createVisit: async () => ({ id: 'v1', upload: {} }), completeUpload: async () => {} },
      'services/upload.js': { uploadSegments: async () => ['k'] },
    },
    onWx: (wx) => {
      wx.getRecorderManager = () => rm;
      wx.authorize = ({ success }) => success();
      wx.openSetting = () => { flags.settingOpened = true; };
    },
  });
  const another = () => ({ ...env.page, data: JSON.parse(JSON.stringify(env.page.data)) }); // 再打开一次录音页
  return { ...env, rm, flags, another };
}

test('录音：多次进出录音页，录音管理器的监听始终只有一份', () => {
  const { page, rm, another } = fakeRecorderEnv();
  page.onLoad({ storeId: 's1' });
  const p2 = another(); p2.onLoad({ storeId: 's1' });
  const p3 = another(); p3.onLoad({ storeId: 's1' });
  ['onStart', 'onResume', 'onPause', 'onInterruptionBegin', 'onInterruptionEnd', 'onStop', 'onError'].forEach((n) => assert.strictEqual(rm.count(n), 1, n));
});

test('录音：事件只交给当前打开的页面，旧页面不会再动（也不会自己续录）', () => {
  const { page, rm, another } = fakeRecorderEnv();
  page.onLoad({ storeId: 's1' });
  const p2 = another(); p2.onLoad({ storeId: 's1' });
  p2.userStopped = true;
  rm.emit('onStop', { tempFilePath: 'a.mp3' });
  assert.strictEqual(p2.data.segments.length, 1);
  assert.strictEqual(page.data.segments.length, 0);
  assert.strictEqual(rm.starts.length, 0); // 旧页面没有「时长到了自动续录」
  p2.onUnload(); // 离开页面后再来事件：不报错，也没有谁处理
  rm.emit('onStop', { tempFilePath: 'late.mp3' });
  rm.emit('onError', { errMsg: 'x' });
  assert.strictEqual(p2.data.segments.length, 1);
});

test('录音：单段到点自动续录；暂停 / 恢复 / 来电打断后能继续', () => {
  const { page, rm } = fakeRecorderEnv();
  page.onLoad({ storeId: 's1' });
  rm.emit('onStart');
  assert.strictEqual(page.data.state, 'recording');
  rm.emit('onStop', { tempFilePath: 'seg1.mp3' }); // 不是用户停的：到时长上限
  assert.strictEqual(rm.starts.length, 1);
  assert.strictEqual(page.data.segments.length, 1);
  rm.emit('onInterruptionBegin');
  assert.strictEqual(page.data.state, 'paused');
  rm.emit('onInterruptionEnd');
  assert.strictEqual(rm.resumed, 1);
  page.userStopped = true;
  rm.emit('onStop', { tempFilePath: 'seg2.mp3' });
  assert.strictEqual(page.data.state, 'stopped');
  assert.strictEqual(page.data.segments.length, 2);
});

test('录音出错：完整显示原因（不被截断），页面回到可重试状态，进店时间不记', () => {
  const { page, rm, log } = fakeRecorderEnv();
  page.onLoad({ storeId: 's1' });
  page.setData({ stores: [{ id: 's1', name: '甲店' }], storeIndex: 0 });
  page.begin();
  assert.strictEqual(rm.starts.length, 1);
  assert.strictEqual(page.startedAt, undefined); // 还没真正开始
  rm.emit('onError', { errMsg: 'operateRecorder:fail internal error' });
  assert.strictEqual(page.data.state, 'idle');
  assert.strictEqual(page.startedAt, undefined);
  const modal = log.modals[log.modals.length - 1];
  assert.strictEqual(modal.title, '录音出错');
  assert.ok(modal.content.includes('operateRecorder:fail internal error'));
  assert.ok(!log.toasts.some((t) => t.includes('录音出错'))); // 不再用会截断的提示条
});

test('录音出错：没有麦克风权限时，引导去设置；已录了一部分时保留已录的内容', () => {
  const { page, rm, log, flags } = fakeRecorderEnv();
  page.onLoad({ storeId: 's1' });
  rm.emit('onError', { errMsg: 'operateRecorder:fail auth deny' });
  const modal = log.modals[log.modals.length - 1];
  assert.strictEqual(modal.confirmText, '去设置');
  assert.strictEqual(flags.settingOpened, true);
  page.setData({ segments: ['part.mp3'] });
  rm.emit('onError', { errMsg: 'operateRecorder:fail interrupted' });
  assert.strictEqual(page.data.state, 'stopped'); // 已有录音，不丢
});

test('录音：真正开始（onStart）才记录进店时间，且只记第一次', () => {
  const { page, rm } = fakeRecorderEnv();
  page.onLoad({ storeId: 's1' });
  rm.emit('onStart');
  const first = page.startedAt;
  assert.ok(first > 0);
  page.startedAt = first - 5000;
  rm.emit('onStart'); // 续录的下一段
  assert.strictEqual(page.startedAt, first - 5000);
});
