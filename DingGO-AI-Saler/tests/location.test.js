// 定位（拜访打卡）：定位服务的错误分类、录音页的行为、拜访详情的显示。用法：node --test "tests/*.test.js"
const test = require('node:test');
const assert = require('node:assert');
const path = require('path');
const { mountPage, tick } = require('./harness');

const ROOT = path.resolve(__dirname, '..');

// ---------- 定位服务 ----------
test('定位服务：把各种失败原因分成能引导用户的几类', () => {
  const { classify } = require(path.join(ROOT, 'services/location.js'));
  const kinds = {
    'getLocation:fail auth deny': 'denied',
    'getLocation:fail authorize no response': 'denied',
    'getLocation:fail system permission denied': 'system',
    'getLocation:fail:ERROR_10001': 'system',
    'getLocation:fail the api scope is not declared in the privacy agreement': 'unsupported',
    'getLocation:fail privacy permission is not authorized': 'unsupported',
    'chooseLocation:fail cancel': 'cancel',
    'getLocation:fail something odd': 'unknown',
  };
  Object.entries(kinds).forEach(([msg, kind]) => assert.strictEqual(classify({ errMsg: msg }).kind, kind, msg));
});

test('定位服务：成功时返回国测局坐标和精度；用的是高精度定位', async () => {
  const seen = [];
  mountPage('pages/me/index.js', {
    stubs: { 'services/visit.js': { listVisits: async () => [] } },
    onWx: (wx) => { wx.getLocation = (o) => { seen.push(o); o.success({ latitude: 29.5630, longitude: 106.5515, accuracy: 18 }); }; },
  });
  const { getLocation } = require(path.join(ROOT, 'services/location.js'));
  assert.deepStrictEqual(await getLocation(), { lat: 29.563, lng: 106.5515, accuracy: 18 });
  assert.strictEqual(seen[0].type, 'gcj02');
});

// ---------- 录音页 ----------
// loc：{ ok: {...} | err: 'errMsg' | delay: ms } 控制 wx.getLocation 的结果；map：地图选点的结果
function recordEnv({ gps = { latitude: 29.5630, longitude: 106.5515, accuracy: 20 }, gpsError = '', map = null, sheet = 0 } = {}) {
  const calls = { create: [], getLocation: 0, openSetting: 0 };
  const env = mountPage('pages/record/index.js', {
    config: { useMock: false },
    stubs: {
      'services/store.js': { listStores: async () => [{ id: 's1', name: '甲店' }] },
      'services/visit.js': { createVisit: async (p) => { calls.create.push(p); return { id: 'v1', upload: {} }; }, completeUpload: async () => {} },
      'services/upload.js': { uploadSegments: async () => ['k'] },
    },
    onWx: (wx) => {
      wx.authorize = ({ success }) => success();
      wx.openSetting = () => { calls.openSetting += 1; };
      wx.getLocation = (o) => { calls.getLocation += 1; if (gpsError) o.fail({ errMsg: gpsError }); else o.success(gps); };
      wx.chooseLocation = (o) => (map ? o.success(map) : o.fail({ errMsg: 'chooseLocation:fail cancel' }));
      wx.showActionSheet = (o) => o.success({ tapIndex: sheet });
    },
  });
  env.page.onLoad({ storeId: 's1' });
  env.page.setData({ stores: [{ id: 's1', name: '甲店' }], storeIndex: 0 });
  return { ...env, calls };
}
const readyToSubmit = (page) => page.setData({ state: 'stopped', segments: ['a.mp3'], seconds: 120 });

test('录音：开始录音时顺便定位；提交时把位置带给后端', async () => {
  const { page, calls } = recordEnv();
  page.begin(); // 已告知客户 → 开始录音 + 定位
  await tick();
  assert.strictEqual(calls.getLocation, 1);
  assert.strictEqual(page.data.locState, 'ok');
  assert.ok(page.data.locText.includes('精度约 20 米'));
  readyToSubmit(page);
  await page.submit();
  assert.deepStrictEqual(calls.create[0].checkin, { lat: 29.563, lng: 106.5515, address: '' });
});

test('录音：定位被拒绝或失败，录音和提交照常进行，只是没有位置；自动定位不弹窗打断', async () => {
  const { page, calls, log } = recordEnv({ gpsError: 'getLocation:fail auth deny' });
  const modalsBefore = log.modals.length;
  page.begin();
  await tick();
  assert.strictEqual(page.data.locState, 'fail');
  assert.ok(page.data.locText.includes('重试'));
  assert.strictEqual(log.modals.length - modalsBefore, 1); // 只有「请先告知客户」那一个弹窗，没有定位的弹窗
  readyToSubmit(page);
  await page.submit();
  assert.strictEqual(calls.create.length, 1);
  assert.strictEqual(calls.create[0].checkin, undefined);
});

test('录音：自己点「获取当前位置」被拒绝时，引导去设置', async () => {
  const { page, calls, log } = recordEnv({ gpsError: 'getLocation:fail auth deny', sheet: 0 });
  page.tapLocation();
  await tick();
  const modal = log.modals[log.modals.length - 1];
  assert.strictEqual(modal.title, '没有获取到位置');
  assert.strictEqual(modal.confirmText, '去设置');
  assert.strictEqual(calls.openSetting, 1);
});

test('录音：手机定位开关没开时，说明是系统设置问题', async () => {
  const { page, log } = recordEnv({ gpsError: 'getLocation:fail system permission denied', sheet: 0 });
  page.tapLocation();
  await tick();
  assert.ok(log.modals[log.modals.length - 1].content.includes('手机'));
});

test('录音：定位接口没开通时，提示改用地图选点；地图选点的位置和地址一起提交', async () => {
  const { page, calls, log } = recordEnv({ gpsError: 'getLocation:fail the api scope is not declared in the privacy agreement', map: { latitude: 29.6, longitude: 106.5, address: '重庆市渝北区新南路88号', name: '宝贝乐' }, sheet: 0 });
  page.tapLocation();
  await tick();
  assert.ok(log.toasts.some((t) => t.includes('地图选点')));
  assert.ok(page.data.locText.includes('地图选点'));
  const picker = recordEnv({ map: { latitude: 29.6, longitude: 106.5, address: '重庆市渝北区新南路88号', name: '宝贝乐' }, sheet: 1 });
  picker.page.tapLocation();
  await tick();
  assert.ok(picker.page.data.locText.includes('重庆市渝北区新南路88号 宝贝乐'));
  readyToSubmit(picker.page);
  await picker.page.submit();
  assert.deepStrictEqual(picker.calls.create[0].checkin, { lat: 29.6, lng: 106.5, address: '重庆市渝北区新南路88号 宝贝乐' });
  assert.ok(calls.getLocation >= 1);
});

test('录音：已经在地图上手动选了位置，后面开始录音的自动定位不会覆盖它', async () => {
  const { page, calls } = recordEnv({ map: { latitude: 30, longitude: 107, address: '手选的位置', name: '' }, sheet: 1 });
  page.tapLocation();
  await tick();
  page.begin(); // 已有位置，不再自动定位
  await tick();
  assert.strictEqual(calls.getLocation, 0);
  readyToSubmit(page);
  await page.submit();
  assert.strictEqual(calls.create[0].checkin.address, '手选的位置');
});

test('录音：在地图选点里点返回，什么都不改；重录会清掉位置', async () => {
  const { page } = recordEnv({ map: null, sheet: 1 });
  page.tapLocation();
  await tick();
  assert.strictEqual(page.data.locState, 'idle');
  const again = recordEnv();
  again.page.begin();
  await tick();
  assert.strictEqual(again.page.data.locState, 'ok');
  again.page.reset();
  assert.strictEqual(again.page.loc, null);
  assert.strictEqual(again.page.data.locState, 'idle');
});

test('录音：登记无录音的拜访时也会尝试定位并带上位置；定位失败照常登记', async () => {
  const a = recordEnv();
  a.page.setData({ stageIndex: 1 });
  a.page.registerNoRecording();
  await tick();
  assert.deepStrictEqual(a.calls.create[0].checkin, { lat: 29.563, lng: 106.5515, address: '' });
  assert.strictEqual(a.calls.create[0].recordingMode, 'none');
  const b = recordEnv({ gpsError: 'getLocation:fail auth deny' });
  b.page.registerNoRecording();
  await tick();
  assert.strictEqual(b.calls.create.length, 1);
  assert.strictEqual(b.calls.create[0].checkin, undefined);
});

test('录音：清除位置后不再带位置提交', async () => {
  const { page, calls } = recordEnv({ sheet: 2 });
  page.begin();
  await tick();
  page.tapLocation(); // 已有位置：菜单第 3 项「清除位置」
  assert.strictEqual(page.loc, null);
  readyToSubmit(page);
  await page.submit();
  assert.strictEqual(calls.create[0].checkin, undefined);
});

// ---------- 拜访详情 ----------
test('拜访详情：有坐标显示坐标并可在地图上打开；有地址显示地址；都没有就不显示这一行', () => {
  const opened = [];
  const { page } = mountPage('pages/visit/detail/index.js', {
    stubs: { 'services/visit.js': { getVisit: async () => ({}), confirmCost: async () => ({}), rejudge: async () => ({}), retryVisit: async () => ({}) } },
    onWx: (wx) => { wx.openLocation = (o) => opened.push(o); },
  });
  page.onLoad({ id: 'v1' });
  const base = { id: 'v1', status: 'no_recording', stage: '日常维护', createdAt: Date.now(), mode: 'daily', purposes: [], survey: {} };
  page.render({ ...base, checkin: { address: '', lat: 29.56301, lng: 106.55156 } });
  assert.strictEqual(page.data.hasCheckin, true);
  assert.strictEqual(page.data.checkinText, '29.56301，106.55156');
  assert.strictEqual(page.data.hasInfo, true);
  page.openCheckin();
  assert.deepStrictEqual([opened[0].latitude, opened[0].longitude], [29.56301, 106.55156]);
  page.render({ ...base, checkin: { address: '重庆市渝北区', lat: null, lng: null } });
  assert.strictEqual(page.data.checkinText, '重庆市渝北区');
  assert.strictEqual(page.data.checkinOnMap, false);
  const n = opened.length;
  page.openCheckin(); // 没有坐标：点了也不会乱开地图
  assert.strictEqual(opened.length, n);
  page.render({ ...base, checkin: { address: '', lat: null, lng: null } });
  assert.strictEqual(page.data.hasCheckin, false);
});

test('app.json：声明了定位接口，并写了用途；隐私说明里提到了位置', () => {
  const app = require(path.join(ROOT, 'app.json'));
  assert.ok(app.requiredPrivateInfos.includes('getLocation') && app.requiredPrivateInfos.includes('chooseLocation'));
  assert.ok(app.permission['scope.userLocation'].desc.length > 0 && app.permission['scope.userLocation'].desc.length <= 30);
  const me = mountPage('pages/me/index.js', { stubs: { 'services/visit.js': { listVisits: async () => [] } } });
  me.page.showPrivacy();
  assert.ok(me.log.modals[0].content.includes('位置'));
});
