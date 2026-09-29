// 演示数据：门店与拜访记录（内存中可增改，重启开发者工具即恢复）
const DAY = 24 * 3600 * 1000;
const now = Date.now();

const stores = [
  { id: 's1', name: '宝贝乐母婴店', province: '重庆市', city: '渝北区', address: '龙溪街道新南路 88 号', cooperationStatus: '已合作', createdAt: now - 20 * DAY },
  { id: 's2', name: '张记副食', province: '重庆市', city: '江北区', address: '观音桥步行街 12 号', cooperationStatus: '已触达未合作', createdAt: now - 6 * DAY },
  { id: 's3', name: '童趣孕婴生活馆', province: '重庆市', city: '南岸区', address: '南坪万达广场 B1', cooperationStatus: '意向中', createdAt: now - 2 * DAY },
  { id: 's4', name: '惠民便利店', province: '重庆市', city: '沙坪坝区', address: '三峡广场北街 6 号', cooperationStatus: '未触达', createdAt: now - 30 * DAY },
];

const visits = [
  { id: 'v1', storeId: 's1', stage: '首访破冰', cooperated: '否', createdAt: now - 14 * DAY, durationSec: 612, estCost: 0.21, status: 'done', analysisKey: 'first' },
  { id: 'v2', storeId: 's1', stage: '日常维护', cooperated: null, createdAt: now - 1 * DAY, durationSec: 486, estCost: 0.17, status: 'done', analysisKey: 'daily' },
  { id: 'v3', storeId: 's2', stage: '首访破冰', cooperated: '否', createdAt: now - 3 * 3600 * 1000, durationSec: 935, estCost: 0.32, status: 'cost_pending' },
  { id: 'v5', storeId: 's4', stage: '首访破冰', cooperated: '否', createdAt: now - 16 * DAY, durationSec: 721, estCost: 0.25, status: 'done', analysisKey: 'first' },
  { id: 'v6', storeId: 's2', stage: '日常维护', cooperated: null, createdAt: now - 5 * DAY, durationSec: 0, estCost: 0, status: 'no_recording',
    recordingMode: 'none', noRecordingReason: '对方不同意录音', storeCondition: '正常运营', purposes: ['催动销', '陈列维护'], note: '老板在店，聊了近期动销，说周末客流大。' },
  // 从飞书导入的历史拜访：分析是原文，不是结构化数据
  { id: 'v7', storeId: 's3', stage: '首访破冰', cooperated: '否', createdAt: now - 40 * DAY, durationSec: 655, estCost: 0.23, status: 'done', analysisKey: 'legacy', legacy: true,
    storeCondition: '正常运营', purposes: ['破冰建联', '产品介绍'] },
  { id: 'v4', storeId: 's3', stage: '首访破冰', cooperated: '否', createdAt: now - 2 * DAY, durationSec: 58, estCost: 0.02, status: 'invalid_short' },
];

module.exports = { stores, visits };
