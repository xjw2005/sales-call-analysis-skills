const config = require('../config/index');
const { request, delay } = require('./request');
const scenarios = require('../model/practice');

const sessions = {};

function listScenarios() {
  if (!config.useMock && !config.mockAI) return request({ url: '/practice/scenarios' });
  return delay(scenarios.map(({ id, title, concern, level, desc }) => ({ id, title, concern, level, desc })));
}

function scenarioByConcern(concern) {
  const s = scenarios.find((x) => x.concern === concern);
  return s ? s.id : 'opening';
}

// 开始陪练：返回客户第一句话
function start(scenarioId, storeName) {
  if (!config.useMock && !config.mockAI) return request({ url: '/practice/start', method: 'POST', data: { scenarioId, storeName } });
  const sc = scenarios.find((x) => x.id === scenarioId) || scenarios[0];
  const id = `p${Date.now()}`;
  sessions[id] = { sc, answers: [] };
  return delay({ sessionId: id, title: sc.title, desc: sc.desc, totalRounds: sc.lines.length, reply: sc.lines[0] });
}

// 销售回答一句，返回客户下一句；轮次用完后 finished=true
function turn(sessionId, text) {
  if (!config.useMock && !config.mockAI) return request({ url: '/practice/turn', method: 'POST', data: { sessionId, text } });
  const s = sessions[sessionId];
  s.answers.push(text);
  const next = s.sc.lines[s.answers.length];
  return delay({ reply: next || '', finished: !next }, 600);
}

// 结束点评：演示版按关键词命中和回答长度粗略打分
function finish(sessionId) {
  if (!config.useMock && !config.mockAI) return request({ url: '/practice/finish', method: 'POST', data: { sessionId } });
  const { sc, answers } = sessions[sessionId];
  const all = answers.join('');
  const dims = sc.dims.map(([name, words]) => {
    const hits = words.filter((w) => all.indexOf(w) >= 0);
    const score = Math.min(5, 1 + hits.length + (all.length > 60 ? 1 : 0));
    return {
      name, score, max: 5,
      comment: hits.length ? `做得好：用到了「${hits.join('、')}」` : `可以改进：试试提到「${words.slice(0, 2).join('」「')}」`,
    };
  });
  const total = Math.round((dims.reduce((sum, d) => sum + d.score, 0) / (dims.length * 5)) * 100);
  return delay({ total, dims, reference: sc.reference, answers: answers.length }, 800);
}

module.exports = { listScenarios, scenarioByConcern, start, turn, finish };
