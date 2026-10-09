const config = require('../config/index');
const { request, delay, ensureToken } = require('./request');
const { getStore, listStores } = require('./store');
const { OBJECTIONS } = require('../utils/playbook');
const { lineSplitter, decode } = require('../utils/utf8');

// AI 问答。真实模式：POST /chat，由后端调用大模型并结合门店数据作答（后期可改为流式输出）。
// 演示模式：按关键词从门店演示数据中拼出回答，只用于看界面效果。
async function ask({ question, storeId, history = [], context }) {
  if (!config.useMock && !config.mockAI) return request({ url: '/chat', method: 'POST', data: { question, storeId, history, context } });
  const named = (await listStores()).find((s) => question.indexOf(s.name) >= 0);
  const id = named ? named.id : storeId;
  const store = id ? await getStore(id).catch(() => null) : null;
  return delay({ ...mockAnswer(question, store), demo: true }, 700);
}

// 流式问答：边生成边显示。事件：onStatus(进度文字) / onDelta(回答文字的一小段)；返回完整结果。
// 演示模式、云托管（不支持分片）时退回一次性返回。
async function askStream(params, { onStatus, onDelta } = {}) {
  if (config.useMock || config.mockAI || config.transport === 'cloud') return ask(params);
  const token = await ensureToken();
  const run = (tk) => new Promise((resolve, reject) => {
    let final = null;
    let failure = null;
    const split = lineSplitter((line) => {
      let ev;
      try { ev = JSON.parse(line); } catch (e) { return; }
      if (ev.type === 'status' && onStatus) onStatus(ev.text);
      else if (ev.type === 'delta' && onDelta) onDelta(ev.text);
      else if (ev.type === 'final') final = ev;
      else if (ev.type === 'error') failure = ev.message;
    });
    const raw = [];
    const task = wx.request({
      url: `${config.baseUrl}/chat/stream`,
      method: 'POST',
      data: params,
      header: { Authorization: `Bearer ${tk}` },
      enableChunked: true,
      responseType: 'arraybuffer',
      success: (res) => {
        if (res.statusCode === 401) return resolve({ unauthorized: true });
        if (res.statusCode !== 200) {
          // 出错时后端返回的是普通 JSON：{"message": "..."}
          let msg = `请求失败 ${res.statusCode}`;
          try { msg = JSON.parse(decode(new Uint8Array(res.data || raw[0] || new ArrayBuffer(0)))).message || msg; } catch (e) { /* 保持默认提示 */ }
          return reject(new Error(msg));
        }
        if (failure) return reject(new Error(failure));
        if (!final) return reject(new Error('回答中断了，请再试一次'));
        return resolve(final);
      },
      fail: (err) => reject(new Error(err.errMsg || '网络异常')),
    });
    task.onChunkReceived((r) => {
      raw[0] = r.data;
      split(r.data);
    });
  });
  let res = await run(token);
  if (res.unauthorized) {
    wx.removeStorageSync('token');
    res = await run(await ensureToken());
    if (res.unauthorized) throw new Error('登录已失效，请重新打开小程序');
  }
  return res;
}

// 对一次回答点「有用 / 没用」
async function sendFeedback(logId, rating, note = '') {
  if (config.useMock || config.mockAI || !logId) return true;
  return request({ url: '/chat/feedback', method: 'POST', data: { logId, rating, note } });
}

function mockAnswer(q, store) {
  const objection = Object.keys(OBJECTIONS).find((k) => q.indexOf(k) >= 0)
    || (/乱价|控价|比价|网上.*便宜/.test(q) && '控价防窜') || (/贵|价格/.test(q) && '价格敏感') || (/利润|赚/.test(q) && '利润空间');
  if (objection && /怎么|回|应对|说/.test(q)) {
    const o = OBJECTIONS[objection];
    return { text: `客户说“${o.says}”时，可以这样回应：\n${o.points.map((p, i) => `${i + 1}. ${p}`).join('\n')}\n\n参考话术：“${o.script}”`, suggestions: ['下一步该做什么？'] };
  }
  if (!store) return { text: '请先在上方选择一个门店，我会结合这家门店的拜访记录来回答。', suggestions: [] };
  const name = store.name;
  const latest = (store.visits || []).find((v) => v.status === 'done');
  if (!latest) {
    return { text: `${name} 还没有分析完成的拜访录音。录一次拜访后，我就能告诉你这家店的需求、顾虑和下一步该做什么。`, suggestions: ['怎么开始拜访录音？'] };
  }
  const a = latest.analysis;
  if (/档案|画像|门店情况|介绍/.test(q)) {
    const lines = a.profile.sections.map((s) => `· ${s.label}（${s.state}）：${s.content}`);
    return { text: `${name} 的门店档案（累计 ${store.profile.sourceCount} 次拜访）：\n${lines.join('\n')}`, suggestions: ['这家店最关心什么？', '下一步该做什么？'] };
  }
  if (/关心|顾虑|担心|需求/.test(q)) {
    const hits = a.concerns.filter((c) => c.state === '是').map((c) => c.name);
    const needs = a.explicitNeeds.map((n) => `· ${n.point}：${n.explanation}`);
    return { text: `${name} 最近一次拜访中，客户关心：${hits.join('、') || '暂无'}。\n\n客户直接提出的需求：\n${needs.join('\n')}`, suggestions: ['下一步该做什么？', '上次拜访评分多少？'] };
  }
  if (/下一步|行动|待办|跟进|卡在|上次/.test(q)) {
    const acts = a.nextAction.actions.map((x, i) => `${i + 1}. 【${x.timeframe}】${x.topic}：${x.action}（验收：${x.acceptance}）`);
    return { text: `${name} 上次拜访（${latest.stage}）后的行动判断：${a.nextAction.judgement}。${a.nextAction.reason}\n\n建议行动：\n${acts.join('\n')}`, suggestions: ['这家店最关心什么？'] };
  }
  if (/评分|打分|进展|话术/.test(q)) {
    const first = (store.visits || []).find((v) => v.status === 'done' && v.analysis.effectiveness);
    if (!first) return { text: '这家店还没有首访评分（日常拜访不打分）。', suggestions: [] };
    const e = first.analysis.effectiveness;
    return { text: `首访合作进展 ${e.total} 分。\n${e.conclusion}`, suggestions: ['下一步该做什么？'] };
  }
  return {
    text: `（演示模式）大模型还没有接入，目前我只能回答「${name}」的档案、关心点、评分和下一步行动。后端接入大模型后，这里可以回答更多门店与产品知识。`,
    suggestions: ['介绍一下这家门店', '这家店最关心什么？', '下一步该做什么？'],
  };
}

module.exports = { ask, askStream, sendFeedback };
