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
test('待办：标记完成失败时提示原因；成功后列表刷新', async () => {
  let fail = true;
  let items = [{ id: 't1', topic: '送物料', done: false, storeName: '甲店' }];
  const { page, log } = mountPage('pages/todo/index.js', {
    stubs: {
      'services/todo.js': {
        listTodos: async () => items,
        setTodoDone: async (id, done) => { if (fail) throw new Error('网络异常'); items = items.map((t) => (t.id === id ? { ...t, done } : t)); },
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
