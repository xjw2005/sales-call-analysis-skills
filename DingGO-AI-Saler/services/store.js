const config = require('../config/index');
const { request, delay } = require('./request');
const seed = require('../model/seed');
const { listVisits } = require('./visit');
const cache = require('../utils/cache');

const STORES_TTL = 60 * 1000; // 门店列表（上千家）一分钟内不重复拉
const storeCache = cache.create('stores', STORES_TTL, () => request({ url: '/stores' }));

// 由最近的拜访结果汇总门店仪表与档案
function summarize(store, visits) {
  const done = visits.filter((v) => v.status === 'done');
  const structured = done.filter((v) => !v.legacy); // 历史（飞书导入）拜访没有结构化分析，不参与仪表
  const latest = structured[0];
  const latestFirst = structured.find((v) => v.analysis.effectiveness);
  const latestLoop = structured.find((v) => v.analysis.loop);
  const cooperationStatus = store.cooperationStatus || (store.cooperated ? '已合作' : '未触达');
  const loopItems = latestLoop ? latestLoop.analysis.loop.items : [];
  return {
    ...store,
    cooperationStatus,
    cooperated: cooperationStatus === '已合作',
    visitCount: visits.length,
    lastVisitAt: visits[0] ? visits[0].createdAt : null,
    profile: latest ? { ...latest.analysis.profile, sourceCount: done.length } : null,
    oneLine: latest ? latest.analysis.profile.sections[0].content : '',
    metrics: {
      score: latestFirst ? latestFirst.analysis.effectiveness.total : null,
      concernHits: latest ? latest.analysis.concerns.filter((c) => c.state === '是').length : null,
      loopRate: loopItems.length ? Math.round((loopItems.filter((i) => i.status === '已完成').length / loopItems.length) * 100) : null,
    },
    actions: latest ? latest.analysis.nextAction.actions : [],
    loops: structured.filter((v) => v.analysis.loop).map((v) => ({ visitId: v.id, createdAt: v.createdAt, items: v.analysis.loop.items })),
  };
}

// 门店列表：真实模式有缓存，多个页面共用。{ fresh: true } 强制重新请求（下拉刷新）
async function listStores({ fresh = false } = {}) {
  if (!config.useMock) return storeCache.get({ fresh });
  const all = await listVisits();
  return seed.stores
    .filter((s) => !s.deleted)
    .map((s) => summarize(s, all.filter((v) => v.storeId === s.id)))
    .sort((a, b) => (b.lastVisitAt || b.createdAt) - (a.lastVisitAt || a.createdAt));
}

async function getStore(id) {
  if (!config.useMock) return request({ url: `/stores/${id}` });
  const store = seed.stores.find((s) => s.id === id && !s.deleted);
  if (!store) throw new Error('门店不存在');
  const visits = await listVisits({ storeId: id });
  const out = { ...summarize(store, visits), visits, canManage: true };
  // 演示模式：手改过的档案维度覆盖分析结果
  if (out.profile && store.profileEdits) {
    out.profile = { ...out.profile, sections: out.profile.sections.map((s) => (store.profileEdits[s.key] ? { ...s, ...store.profileEdits[s.key] } : s)) };
  }
  return out;
}

// 修改门店资料（名称、地址、坐标、合作状态、联系人）：门店负责人或其上级才能改
async function updateStore(id, data) {
  if (!config.useMock) {
    const store = await request({ url: `/stores/${id}`, method: 'PATCH', data });
    cache.invalidate('stores');
    return store;
  }
  const store = seed.stores.find((s) => s.id === id);
  if (!store) throw new Error('门店不存在');
  Object.assign(store, data);
  return delay(store);
}

// 删除门店：只是隐藏，管理员能恢复；已有拜访的店后端会拒绝（提示改合作状态）
async function deleteStore(id) {
  if (!config.useMock) {
    const res = await request({ url: `/stores/${id}`, method: 'DELETE' });
    cache.invalidate('stores');
    return res;
  }
  const store = seed.stores.find((s) => s.id === id);
  const visits = seed.visits.filter((v) => v.storeId === id && !v.deleted).length;
  if (visits) return delay(null).then(() => { throw new Error(`这家店已有 ${visits} 次拜访记录，不能删除。不再跟进的话，可以把合作状态改掉`); });
  if (store) store.deleted = true;
  return delay({ ok: true });
}

// 直接修改门店档案的某个维度（留纠正记录）
async function editProfile(id, key, content, state) {
  if (!config.useMock) {
    const store = await request({ url: `/stores/${id}/profile/${key}`, method: 'PATCH', data: { content, state } });
    cache.invalidate('stores');
    return store;
  }
  const store = seed.stores.find((s) => s.id === id);
  store.profileEdits = { ...(store.profileEdits || {}), [key]: { content, ...(state ? { state } : {}) } };
  return getStore(id);
}

// 缓存里已有的门店列表（可能已过期），用来切回页面时先显示、再后台更新；没有或演示模式返回 null
function peekStores() {
  return config.useMock ? null : storeCache.peek();
}

async function createStore(data) {
  if (!config.useMock) {
    const store = await request({ url: '/stores', method: 'POST', data });
    cache.invalidate('stores'); // 新建的门店要马上出现在各处的列表里
    return store;
  }
  const store = { id: `s${Date.now()}`, cooperationStatus: '未触达', createdAt: Date.now(), ...data };
  seed.stores.push(store);
  return delay(store);
}

// 门店档案纠正（对应 correction_field，DSR 人工填写，权威性最高）
async function submitCorrection(id, text) {
  if (!config.useMock) {
    const res = await request({ url: `/stores/${id}/corrections`, method: 'POST', data: { text } });
    cache.invalidate('stores');
    return res;
  }
  const store = seed.stores.find((s) => s.id === id);
  store.correction = text;
  return delay(true);
}

module.exports = { listStores, peekStores, getStore, createStore, updateStore, deleteStore, editProfile, submitCorrection };
