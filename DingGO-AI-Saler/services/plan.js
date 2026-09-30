const config = require('../config/index');
const { request } = require('./request');
const { listStores } = require('./store');
const storage = require('../utils/storage');

const MOCK_KEY = 'mockPlan';

// 新对话的主动问候：有计划报计划，有到期约定提醒，都没有就问今天去哪
async function greeting() {
  if (!config.useMock) return request({ url: '/plans/greeting' });
  const plan = storage.get(MOCK_KEY, []);
  return {
    mode: plan.length ? 'plan' : 'ask',
    text: plan.length ? `今天计划去 ${plan.length} 家店，已完成 0 家。` : '今天准备去哪个区的门店？也可以从最近拜访过的店里挑。',
    plan,
    commitments: [],
    options: [{ label: '最近拜访过的店', kind: 'recent' }],
  };
}

// 候选门店：kind = district | recent | commitments
async function suggest({ kind, district }) {
  if (!config.useMock) return request({ url: '/plans/suggest', data: { kind, district } });
  const stores = (await listStores()).slice(0, 5);
  return {
    title: '演示数据：最近拜访过的门店',
    items: stores.map((s) => ({ storeId: s.id, name: s.name, district: '', address: s.address || '', reason: '演示数据', tags: [], checked: false })),
  };
}

async function addPlan(storeIds, source, reasons) {
  if (!config.useMock) return request({ url: '/plans', method: 'POST', data: { storeIds, source, reasons } });
  const plan = storage.get(MOCK_KEY, []);
  const stores = await listStores();
  storeIds.forEach((id) => {
    const s = stores.find((x) => x.id === id);
    if (s && !plan.find((p) => p.storeId === id)) plan.push({ id: `p${id}`, storeId: id, name: s.name, district: '', address: s.address || '', reason: (reasons || {})[id] || '', source, status: 'planned', visitId: '' });
  });
  storage.set(MOCK_KEY, plan);
  return plan;
}

async function todayPlan() {
  if (!config.useMock) return request({ url: '/plans/today' });
  return storage.get(MOCK_KEY, []);
}

async function removePlan(id) {
  if (!config.useMock) return request({ url: `/plans/${id}`, method: 'DELETE' });
  storage.set(MOCK_KEY, storage.get(MOCK_KEY, []).filter((p) => p.id !== id));
  return true;
}

module.exports = { greeting, suggest, addPlan, todayPlan, removePlan };
