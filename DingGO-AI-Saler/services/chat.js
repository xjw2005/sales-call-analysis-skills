const config = require('../config/index');
const { request, delay } = require('./request');
const { getStore, listStores } = require('./store');
const { OBJECTIONS } = require('../utils/playbook');

// AI 问答。真实模式：POST /chat，由后端调用大模型并结合门店数据作答（后期可改为流式输出）。
// 演示模式：按关键词从门店演示数据中拼出回答，只用于看界面效果。
async function ask({ question, storeId, history = [] }) {
  if (!config.useMock && !config.mockAI) return request({ url: '/chat', method: 'POST', data: { question, storeId, history } });
  const named = (await listStores()).find((s) => question.indexOf(s.name) >= 0);
  const id = named ? named.id : storeId;
  const store = id ? await getStore(id).catch(() => null) : null;
  return delay({ ...mockAnswer(question, store), demo: true }, 700);
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

module.exports = { ask };
