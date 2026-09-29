const config = require('../config/index');
const { request, delay } = require('./request');
const seed = require('../model/seed');
const { listVisits } = require('./visit');

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

async function listStores() {
  if (!config.useMock) return request({ url: '/stores' });
  const all = await listVisits();
  return seed.stores
    .map((s) => summarize(s, all.filter((v) => v.storeId === s.id)))
    .sort((a, b) => (b.lastVisitAt || b.createdAt) - (a.lastVisitAt || a.createdAt));
}

async function getStore(id) {
  if (!config.useMock) return request({ url: `/stores/${id}` });
  const store = seed.stores.find((s) => s.id === id);
  if (!store) throw new Error('门店不存在');
  const visits = await listVisits({ storeId: id });
  return { ...summarize(store, visits), visits };
}

function createStore(data) {
  if (!config.useMock) return request({ url: '/stores', method: 'POST', data });
  const store = { id: `s${Date.now()}`, cooperationStatus: '未触达', createdAt: Date.now(), ...data };
  seed.stores.push(store);
  return delay(store);
}

// 门店档案纠正（对应 correction_field，DSR 人工填写，权威性最高）
function submitCorrection(id, text) {
  if (!config.useMock) return request({ url: `/stores/${id}/corrections`, method: 'POST', data: { text } });
  const store = seed.stores.find((s) => s.id === id);
  store.correction = text;
  return delay(true);
}

module.exports = { listStores, getStore, createStore, submitCorrection };
