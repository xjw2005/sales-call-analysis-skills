// 门店列表缓存：有效期、共用请求、什么时候失效、换人清空、门店页先显示缓存。用法：node --test "tests/*.test.js"
const test = require('node:test');
const assert = require('node:assert');
const path = require('path');
const { mountPage, deferred, tick } = require('./harness');

const ROOT = path.resolve(__dirname, '..');
const stores = (n, tag = '') => Array.from({ length: n }, (_, i) => ({ id: `s${i}`, name: `店${i}${tag}`, visitCount: 0 }));

// 真实的 store / visit / request 服务 + 假的网络。handlers: { '/stores': (opt) => 数据 | Promise }
function env({ handlers = {}, config = {}, user = { id: '1', name: '甲' }, page = 'pages/store/list/index.js' } = {}) {
  const calls = [];
  const m = mountPage(page, {
    config: { useMock: false, mockAI: false, transport: 'http', baseUrl: 'http://x', ...config },
    storage: { token: 'T', user },
    onWx: (wx) => {
      wx.request = (o) => {
        const key = o.url.replace('http://x', '');
        calls.push(`${o.method || 'GET'} ${key}`);
        const h = handlers[key] || (() => ({}));
        Promise.resolve().then(() => h(o)).then((data) => o.success({ statusCode: 200, data }), (err) => o.fail({ errMsg: err.message }));
      };
    },
  });
  const store = require(path.join(ROOT, 'services/store.js'));
  const visit = require(path.join(ROOT, 'services/visit.js'));
  const request = require(path.join(ROOT, 'services/request.js'));
  const count = (key) => calls.filter((c) => c === `GET ${key}`).length;
  return { ...m, calls, store, visit, request, count };
}

// 可以拨动的时钟：测试有效期
function clock() {
  const real = Date.now;
  let now = real();
  Date.now = () => now;
  return { advance: (ms) => { now += ms; }, restore: () => { Date.now = real; } };
}

test('一分钟内多个页面取门店：只请求一次，返回同一份数据', async () => {
  const { store, count } = env({ handlers: { '/stores': () => stores(1072) } });
  const a = await store.listStores();
  const b = await store.listStores();
  assert.strictEqual(a.length, 1072);
  assert.strictEqual(a, b); // 同一份，不再复制上千条
  assert.strictEqual(count('/stores'), 1);
});

test('同时发起的多个请求共用一次网络请求（首页、门店页、录音页一起要）', async () => {
  const gate = deferred();
  const { store, count } = env({ handlers: { '/stores': () => gate.promise } });
  const all = [store.listStores(), store.listStores(), store.listStores()];
  await tick();
  assert.strictEqual(count('/stores'), 1);
  gate.resolve(stores(3));
  const [a, b, c] = await Promise.all(all);
  assert.ok(a === b && b === c);
});

test('超过一分钟：重新请求，拿到最新数据', async () => {
  const t = clock();
  try {
    let n = 0;
    const { store, count } = env({ handlers: { '/stores': () => stores(2, `v${(n += 1)}`) } });
    assert.strictEqual((await store.listStores())[0].name, '店0v1');
    t.advance(59 * 1000);
    assert.strictEqual((await store.listStores())[0].name, '店0v1'); // 还在有效期
    t.advance(2 * 1000);
    assert.strictEqual((await store.listStores())[0].name, '店0v2');
    assert.strictEqual(count('/stores'), 2);
  } finally { t.restore(); }
});

test('强制刷新：有效期内也重新请求；失败要报错（下拉刷新要让人知道）', async () => {
  let fail = false;
  const { store, count } = env({ handlers: { '/stores': () => { if (fail) throw new Error('网络异常'); return stores(2); } } });
  await store.listStores();
  await store.listStores({ fresh: true });
  assert.strictEqual(count('/stores'), 2);
  fail = true;
  await assert.rejects(store.listStores({ fresh: true }), /网络异常/);
});

test('缓存过期后刷新失败：先用旧数据，不会弹出空白；从没成功过则照常报错', async () => {
  const t = clock();
  try {
    let fail = false;
    const { store } = env({ handlers: { '/stores': () => { if (fail) throw new Error('网络异常'); return stores(5); } } });
    await store.listStores();
    t.advance(120 * 1000);
    fail = true;
    assert.strictEqual((await store.listStores()).length, 5);
  } finally { t.restore(); }
  const cold = env({ handlers: { '/stores': () => { throw new Error('网络异常'); } } });
  await assert.rejects(cold.store.listStores(), /网络异常/);
});

test('新建门店后：列表马上失效，新门店出现在各处；旧列表还能先显示', async () => {
  let list = stores(3);
  const { store, count } = env({ handlers: { '/stores': (o) => (o.method === 'POST' ? { id: 'new' } : list) } });
  await store.listStores();
  list = [...list, { id: 'new', name: '新店' }]; // 服务器上多了一家
  await store.createStore({ name: '新店' });
  assert.strictEqual(store.peekStores().length, 3); // 旧值还在，可以先显示
  assert.strictEqual((await store.listStores()).length, 4); // 但下一次一定重新请求
  assert.strictEqual(count('/stores'), 2);
});

test('录完音 / 确认费用 / 重试后：门店列表（最近拜访、次数）马上失效', async () => {
  for (const action of [
    (v) => v.createVisit({ storeId: 's0', stage: '日常维护', durationSec: 60 }),
    (v) => v.completeUpload('v1', 60),
    (v) => v.confirmCost('v1'),
    (v) => v.retryVisit('v1'),
    (v) => v.rejudge('v1'),
  ]) {
    const { store, visit, count } = env({ handlers: { '/stores': () => stores(2) } });
    await store.listStores();
    await action(visit);
    await store.listStores();
    assert.strictEqual(count('/stores'), 2);
  }
});

test('请求在路上时数据又变了：这份旧结果不能被缓存', async () => {
  const slow = deferred();
  let n = 0;
  const { store, visit, count } = env({ handlers: { '/stores': () => (n += 1) === 1 ? slow.promise : stores(9) } });
  const inflight = store.listStores();
  await tick();
  await visit.confirmCost('v1'); // 此刻数据变了
  slow.resolve(stores(1)); // 旧请求这才回来
  await inflight;
  assert.strictEqual((await store.listStores()).length, 9); // 不会拿到那份旧的
  assert.strictEqual(count('/stores'), 2);
});

test('换了登录的人：上一个人的门店缓存立刻清掉，同一个人刷新资料不清', async () => {
  const { store, request } = env({ handlers: { '/stores': () => stores(4), '/auth/me': () => ({ id: '1', name: '甲' }) } });
  await store.listStores();
  await request.refreshUser(); // 同一个人
  assert.strictEqual(store.peekStores().length, 4);
  global.wx.request = (o) => o.success({ statusCode: 200, data: { id: '2', name: '乙' } });
  await request.refreshUser(); // 换成了另一个人
  assert.strictEqual(store.peekStores(), null);
});

test('演示模式：不缓存，也没有旧数据可显示', async () => {
  const { store } = env({ config: { useMock: true, mockAI: true } });
  assert.strictEqual(store.peekStores(), null);
  const a = await store.listStores();
  assert.ok(Array.isArray(a) && a.length > 0);
  assert.strictEqual(store.peekStores(), null);
});

// ---------- 门店列表页 ----------
test('门店页：切回来先显示缓存里的列表（不等网络），有效期内不再请求也不重新排版', async () => {
  const { page, store, count } = env({ handlers: { '/stores': () => stores(1072) } });
  await store.listStores(); // 别的页面已经取过
  const setDataCalls = [];
  const orig = page.setData;
  page.setData = function spy(p, cb) { setDataCalls.push(Object.keys(p)); return orig.call(this, p, cb); };
  const shown = page.onShow();
  assert.strictEqual(page.data.stores.length, 1072); // 同步就显示了，没有等网络
  assert.strictEqual(page.data.loaded, true);
  await shown;
  await tick();
  assert.strictEqual(count('/stores'), 1);
  const renders = setDataCalls.filter((k) => k.includes('stores')).length;
  await page.onShow(); // 再切一次
  await tick();
  assert.strictEqual(setDataCalls.filter((k) => k.includes('stores')).length, renders); // 同一份数据，不重新整理
  assert.strictEqual(count('/stores'), 1);
});

test('门店页：缓存过期后先显示旧的，后台更新完自动换成新的', async () => {
  const t = clock();
  try {
    let list = stores(2);
    const { page, store } = env({ handlers: { '/stores': () => list } });
    await store.listStores();
    t.advance(120 * 1000);
    list = stores(5);
    const shown = page.onShow();
    assert.strictEqual(page.data.stores.length, 2); // 先是旧的
    await shown;
    await tick();
    assert.strictEqual(page.data.stores.length, 5); // 更新后换成新的
  } finally { t.restore(); }
});

test('门店页：下拉刷新强制重新请求；失败时转圈也要收起并提示', async () => {
  let fail = false;
  const { page, store, count, log } = env({ handlers: { '/stores': () => { if (fail) throw new Error('网络异常'); return stores(3); } } });
  await store.listStores();
  page.onPullDownRefresh();
  await tick();
  assert.strictEqual(count('/stores'), 2); // 有效期内也重新请求了
  assert.strictEqual(log.pullStopped, 1);
  fail = true;
  page.onPullDownRefresh();
  await tick();
  assert.strictEqual(log.pullStopped, 2);
  assert.ok(log.toasts.includes('网络异常'));
});

test('门店页：筛选条件在后台更新数据后保持不变', async () => {
  const t = clock();
  try {
    let list = [{ id: 'a', name: '甲店', cooperationStatus: '已合作', visitCount: 1 }, { id: 'b', name: '乙店', cooperationStatus: '未触达', visitCount: 0 }];
    const { page, store } = env({ handlers: { '/stores': () => list } });
    page.onShow();
    await tick();
    page.pickCoop({ currentTarget: { dataset: { v: '已合作' } } });
    assert.deepStrictEqual(page.data.shown.map((s) => s.id), ['a']);
    t.advance(120 * 1000);
    list = [...list, { id: 'c', name: '丙店', cooperationStatus: '已合作', visitCount: 2 }];
    page.onShow();
    await tick();
    assert.deepStrictEqual(page.data.shown.map((s) => s.id), ['a', 'c']);
    assert.strictEqual(page.data.coop, '已合作');
  } finally { t.restore(); }
});
