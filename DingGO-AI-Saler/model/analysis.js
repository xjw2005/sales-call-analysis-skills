// dueDays：行动到期日 = 拜访日 + N 天（真实环境由后端把“周五前”等文字解析成日期）
// 演示用分析结果，字段结构对齐 sales-call-analysis-scripted/references/output-spec.md
const t = require('./transcript');
const ev = (...ids) => ids.map((id) => t.find((u) => u.uid === id));

const profileSections = [
  { key: 'one_line', label: '一句话画像', state: '当前状态', content: '社区母婴店，月销奶粉三四十罐，担心线上比价和压货，愿意小量试销。' },
  { key: 'basic', label: '门店基本信息', state: '稳定档案', content: '重庆市渝北区龙溪街道新南路 88 号；夫妻店，老板娘参与决策。' },
  { key: 'categories_brands', label: '主营品类与品牌', state: '当前状态', content: '奶粉为主，兼营纸尿裤；在售飞鹤、君乐宝，进口爱他美少量。' },
  { key: 'business_model', label: '经营模式', state: '未确认', content: '未确认' },
  { key: 'selection_motion', label: '选品偏好', state: '当前状态', content: '优先选择价格不透明、可退换的品牌；对进口高价品谨慎。' },
  { key: 'price_profit', label: '利润偏好', state: '当前状态', content: '看重单罐毛利，认为六十到七十元“还行”；最怕线上低价打穿利润。' },
  { key: 'cooperation_preferences', label: '合作偏好与排斥项', state: '稳定档案', content: '偏好小量试销、可退货；排斥无法兑现的控价承诺（曾因他牌乱价压货二十箱）。' },
];

const first = {
  explicitNeeds: [
    { point: '控价要真正落地', scene: '谈到进口奶粉与线上比价', explanation: '客户直接表达线上低价导致无利润，并举出曾被乱价压货的经历。', evidence: ev('U0004', 'U0006') },
    { point: '首单可退、控制压货风险', scene: '销售提出试销方案后', explanation: '客户表示“能退的话可以考虑”，退货是合作前提条件。', evidence: ev('U0010') },
    { point: '单罐利润与到货时效', scene: '讨论合作细节', explanation: '客户主动询问单罐利润和到货天数。', evidence: ev('U0010') },
  ],
  implicitNeeds: [
    { hypothesis: '对品牌方承诺的信任度低，需要可验证的控价证据', basis: '客户两次质疑控价（“都这么说”“去年有个牌子也说控价”）', logic: '过往损失形成防御心理，口头承诺难以打消顾虑。', confidence: '中', evidence: ev('U0006', 'U0004') },
  ],
  concerns: [
    { name: '价格敏感', state: '是', evidence: ev('U0004') },
    { name: '物流时效', state: '是', evidence: ev('U0010') },
    { name: '控价防窜', state: '是', evidence: ev('U0006') },
    { name: '培训支持', state: '否', evidence: [] },
    { name: '售后保障', state: '是', evidence: ev('U0010') },
    { name: '合作模式', state: '否', evidence: [] },
    { name: '利润空间', state: '是', evidence: ev('U0010', 'U0012') },
    { name: '系统工具', state: '否', evidence: [] },
    { name: '其他', state: '否', evidence: [] },
  ],
  effectiveness: {
    total: 71,
    dims: [
      { name: '开场目标与议程', max: 10, score: 8 },
      { name: '需求挖掘', max: 20, score: 14 },
      { name: '问题影响与经济意义', max: 20, score: 11 },
      { name: '倾听与异议承接', max: 15, score: 12 },
      { name: '方案与证据匹配', max: 10, score: 7 },
      { name: '价格与价值建立', max: 10, score: 7 },
      { name: '达成合作提问', max: 5, score: 3 },
      { name: '下一步承诺', max: 10, score: 9 },
    ],
    conclusion: '整体推进较顺，开场目标清晰，准确抓住了客户对线上比价和压货的顾虑，并用小量试销加退货政策承接异议。核心缺口在于控价只停留在口头承诺，未给出可验证的案例或机制，客户信任尚未建立。最终客户同意看价格表并与家人商量，约定下周二复访。',
    outcome: '客户待定，约定周五发资料、下周二复访。',
  },
  quotes: [
    { text: '您压货的顾虑我理解。我们首单可以只拿两箱试销，卖不动的一个月内原价退。', scene: '客户讲述被乱价压货的经历后', problem: '压货风险', method: '先共情，再用低门槛试销降低决策成本', value: '把“要不要合作”转成“要不要试两箱”', reaction: '客户回应“能退的话可以考虑”', evidence: ev('U0009') },
  ],
  nextAction: {
    judgement: '触发',
    reason: '客户明确索要价格表和退货政策，且约定了复访时间。',
    confirmed: ['周五上午把价格表、退货政策发客户微信（销售承诺）', '下周二再次到店拜访（销售承诺）'],
    actions: [
      { topic: '补齐控价证据', owner: '销售', timeframe: '周五前', dueDays: 2, action: '整理近三个月控价处理案例和红线价说明，与价格表一起发送', reason: '客户对控价承诺信任度低', acceptance: '客户收到并回复是否认可' },
      { topic: '复访促成试销', owner: '销售', timeframe: '下周二', dueDays: 6, action: '带两箱试销协议到店，现场说明退货流程', reason: '客户已表达“能退可以考虑”', acceptance: '签订试销单或明确拒绝原因' },
    ],
    revisitValue: '高：客户利润认可、条件明确，复访有望促成首单。',
  },
  profile: { sections: profileSections, sourceCount: 1 },
};

const daily = {
  explicitNeeds: [
    { point: '希望增加试吃装', scene: '试销两周复盘', explanation: '客户表示有顾客想先试再买。', evidence: ev('U0010') },
  ],
  implicitNeeds: [],
  concerns: first.concerns.map((c) => ({ ...c, state: ['利润空间', '售后保障'].includes(c.name) ? '是' : '否' })),
  effectiveness: null,
  quotes: [],
  nextAction: {
    judgement: '触发',
    reason: '客户提出试吃装需求，需确认政策后答复。',
    confirmed: ['本周内确认试吃装政策并回复客户'],
    actions: [
      { topic: '试吃装支持', owner: '销售', timeframe: '本周内', dueDays: 1, action: '向区域经理确认试吃装配额，确认并发送给客户', reason: '客户反馈顾客想先试', acceptance: '客户收到明确答复' },
    ],
    revisitValue: '',
  },
  loop: {
    items: [
      { prev: '周五发送价格表与退货政策', status: '已完成', now: '客户已收到并认可退货政策' },
      { prev: '下周二复访促成试销', status: '已完成', now: '客户试销两箱，已售出 5 罐' },
    ],
  },
  profile: { sections: profileSections.map((s) => (s.key === 'one_line' ? { ...s, content: '社区母婴店，已试销两箱 A2，动销尚可，关注试吃装支持。' } : s)), sourceCount: 2 },
};

// 从飞书导入的历史拜访：分析是排好版的原文（按模块分段展示），结构化字段留空
const legacy = {
  explicitNeeds: [], implicitNeeds: [], concerns: [], effectiveness: null, quotes: [], profile: { sections: [] },
  nextAction: { judgement: '', reason: '', confirmed: [], actions: [], revisitValue: '' },
  legacy: true,
  legacySections: [
    { module: 'ai-summary', title: 'AI 拜访摘要', text: '首次拜访，老板对跨境奶粉不熟，担心合规和压货，希望先看价格表和红线价。', evidence: '' },
    { module: 'explicit-needs', title: '显性需求', text: '1. 需求点：先看价格表和红线价\n   场景：介绍跨境奶粉时\n   解释：客户直接提出要价格表。', evidence: '[00:34.490–00:37.940] 客户：你先把价格表发我看看。' },
    { module: 'store-profile', title: '门店档案', text: '一句话画像：新开母婴店，老板无母婴经验。\n\n门店基本信息（当前状态）：夫妻店，位置好。', evidence: '' },
    { module: 'next-action', title: '下一步行动策略', text: '行动判断：触发。\n建议行动：周五前发送价格表和红线价说明。', evidence: '' },
  ],
};

module.exports = { first, daily, legacy };
