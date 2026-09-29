const config = require('../config/index');
const { request, delay } = require('./request');
const seed = require('../model/seed');
const mockAnalysis = require('../model/analysis');
const transcript = require('../model/transcript');
const { FIRST_STAGE, FIRST_MODULES, DAILY_MODULES, VALID_MIN_SECONDS } = require('../utils/constants');

const clone = (v) => JSON.parse(JSON.stringify(v));

// 演示模式：确认费用后按经过秒数模拟流水线推进（识别→整理→分析→核对→完成）
function simulate(v) {
  const modules = v.stage === FIRST_STAGE ? FIRST_MODULES : DAILY_MODULES;
  const out = { ...v, mode: v.stage === FIRST_STAGE ? 'first' : 'daily', moduleKeys: modules, moduleDone: 0 };
  if (v.confirmedAt && !['done', 'invalid_short', 'invalid_content'].includes(v.status)) {
    const s = (Date.now() - v.confirmedAt) / 1000;
    if (s < 3) out.status = 'asr_running';
    else if (s < 6) out.status = 'role_classifying';
    else if (v.durationSec < VALID_MIN_SECONDS) out.status = 'invalid_short';
    else if (s < 14) {
      out.status = 'analyzing';
      out.moduleDone = Math.min(modules.length - 1, Math.floor(((s - 6) / 8) * modules.length));
    } else if (s < 17) {
      out.status = 'reviewing';
      out.moduleDone = modules.length;
    } else {
      out.status = 'done';
      out.analysisKey = out.mode;
    }
    if (['done', 'invalid_short'].includes(out.status)) Object.assign(v, { status: out.status, analysisKey: out.analysisKey });
  }
  if (out.status === 'done') {
    out.moduleDone = modules.length;
    out.analysis = clone(mockAnalysis[out.analysisKey || out.mode]);
    out.legacy = !!v.legacy;
    // 历史拜访：转写只有原文，录音还没迁移
    out.transcript = v.legacy ? [] : clone(transcript);
    out.transcriptRaw = v.legacy ? '[00:11.450–00:12.290] 销售：老板您好，我是 A2 的业务。\n[00:34.490–00:37.940] 客户：你先把价格表发我看看。' : '';
    out.audioMissing = v.legacy ? 1 : 0;
  }
  out.purposes = v.purposes || [];
  out.checkin = v.checkin || { address: '' };
  out.storeCondition = v.storeCondition || '';
  out.recordingMode = v.recordingMode || 'uploaded';
  out.noRecordingReason = v.noRecordingReason || '';
  const store = seed.stores.find((st) => st.id === v.storeId);
  out.storeName = store ? store.name : '';
  return out;
}

function listVisits({ storeId } = {}) {
  if (!config.useMock) return request({ url: '/visits', data: { storeId } });
  const list = seed.visits
    .filter((v) => !storeId || v.storeId === storeId)
    .map(simulate)
    .sort((a, b) => b.createdAt - a.createdAt);
  return delay(list);
}

function getVisit(id) {
  if (!config.useMock) return request({ url: `/visits/${id}` });
  const v = seed.visits.find((x) => x.id === id);
  return v ? delay(simulate(v)) : Promise.reject(new Error('拜访记录不存在'));
}

// 新建拜访：真实模式下先拿上传凭证，再逐段上传录音（见 services/upload.js）。
// recordingMode 为 'none' 时是「登记无录音拜访」，不需要上传
function createVisit({ storeId, stage, cooperated, note, durationSec, segments, enteredAt, storeCondition, recordingMode = 'uploaded', noRecordingReason = '' }) {
  if (!config.useMock) {
    return request({ url: '/visits', method: 'POST', data: { storeId, stage, cooperated, note, durationSec, enteredAt, storeCondition, recordingMode, noRecordingReason } });
  }
  const none = recordingMode === 'none';
  const v = {
    id: `v${Date.now()}`,
    storeId, stage, cooperated, note, segments, storeCondition, recordingMode, noRecordingReason,
    createdAt: enteredAt || Date.now(),
    durationSec: durationSec || 0,
    estCost: Math.round((durationSec || 0) * 0.00035 * 100) / 100,
    status: none ? 'no_recording' : 'cost_pending',
  };
  seed.visits.push(v);
  return delay(simulate(v), 600);
}

// 所有分段上传完成：后端计算时长与预估费用，进入「待确认费用」
function completeUpload(id, durationSec) {
  if (!config.useMock) return request({ url: `/visits/${id}/segments/complete`, method: 'POST', data: { durationSec } });
  return delay(null);
}

function confirmCost(id) {
  if (!config.useMock) return request({ url: `/visits/${id}/confirm-cost`, method: 'POST' });
  const v = seed.visits.find((x) => x.id === id);
  v.confirmedAt = Date.now();
  v.status = 'asr_running';
  return delay(simulate(v));
}

function rejudge(id) {
  if (!config.useMock) return request({ url: `/visits/${id}/rejudge`, method: 'POST' });
  return delay(null).then(() => { throw new Error('演示数据：录音不足 120 秒，仍判定为过短'); });
}

function retryVisit(id) {
  if (!config.useMock) return request({ url: `/visits/${id}/retry`, method: 'POST' });
  return delay(null).then(() => { throw new Error('演示数据没有失败的处理任务'); });
}

module.exports = { listVisits, getVisit, createVisit, completeUpload, confirmCost, rejudge, retryVisit };
