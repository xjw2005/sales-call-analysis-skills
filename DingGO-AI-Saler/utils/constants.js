// 与 sales-call-analysis-scripted 的定义保持一致（config.example.json / scripts/pipeline.py）
const FIRST_STAGE = '首访破冰';
const DAILY_STAGES = ['需求确认', '方案讲解', '异议处理', '促成签约', '交付培训', '日常维护', '复活挽回', '确定联合推广'];
const STAGES = [FIRST_STAGE, ...DAILY_STAGES];

const CONCERN_NAMES = ['价格敏感', '物流时效', '控价防窜', '培训支持', '售后保障', '合作模式', '利润空间', '系统工具', '其他'];

const DIMENSIONS = [
  ['开场目标与议程', 10],
  ['需求挖掘', 20],
  ['问题影响与经济意义', 20],
  ['倾听与异议承接', 15],
  ['方案与证据匹配', 10],
  ['价格与价值建立', 10],
  ['达成合作提问', 5],
  ['下一步承诺', 10],
];

// 门店档案七维度（一句话画像 + PROFILE_SECTIONS）
const PROFILE_SECTIONS = [
  ['one_line', '一句话画像'],
  ['basic', '门店基本信息'],
  ['categories_brands', '主营品类与品牌'],
  ['business_model', '经营模式'],
  ['selection_motion', '选品偏好'],
  ['price_profit', '利润偏好'],
  ['cooperation_preferences', '合作偏好与排斥项'],
];

const FIRST_MODULES = ['explicit-needs', 'implicit-needs', 'concerns', 'effectiveness', 'quotes', 'store-profile', 'next-action'];
const DAILY_MODULES = ['explicit-needs', 'concerns', 'store-profile', 'next-action'];
const MODULE_LABELS = {
  'explicit-needs': '显性需求',
  'implicit-needs': '隐性需求',
  concerns: '关心类目',
  effectiveness: '合作进展评分',
  quotes: '销售金句',
  'store-profile': '门店档案',
  'next-action': '下一步行动',
};

// 拜访处理状态（对应流程图各节点）
const STATUS = {
  uploading: { text: '上传中', tone: 'info' },
  cost_pending: { text: '待确认费用', tone: 'warn' },
  asr_running: { text: '语音识别中', tone: 'info' },
  role_classifying: { text: '整理对话中', tone: 'info' },
  analyzing: { text: 'AI 分析中', tone: 'info' },
  reviewing: { text: '核对中', tone: 'info' },
  done: { text: '已完成', tone: 'success' },
  partial_manual: { text: '部分转人工', tone: 'warn' },
  invalid_short: { text: '录音过短', tone: 'muted' },
  invalid_content: { text: '内容无效', tone: 'muted' },
  failed: { text: '处理失败', tone: 'error' },
  no_recording: { text: '无录音', tone: 'muted' },
};

// 时间线节点：key 为节点，statuses 为处于该节点时的状态
const TIMELINE = [
  { key: 'upload', label: '录音已上传' },
  { key: 'cost', label: '确认识别费用' },
  { key: 'asr', label: '语音识别' },
  { key: 'role', label: '整理对话 · 判定有效性' },
  { key: 'analyze', label: 'AI 分析' },
  { key: 'review', label: '原话核对 · 风险复核' },
  { key: 'profile', label: '同步门店档案' },
];

const VALID_MIN_SECONDS = 120;

// 门店合作状态（4 种）和对应标签颜色；与拜访里的「是否达成合作」（是/否）不是一回事
const COOPERATION_STATUSES = ['已合作', '已触达未合作', '意向中', '未触达'];
const COOP_TONE = { 已合作: 'success', 已触达未合作: 'warn', 意向中: 'info', 未触达: 'muted' };
// 拜访当时门店的状况
const STORE_CONDITIONS = ['正常运营', '未找到对接人', '已闭店', '门店休息', '拒绝跨境产品'];
const NO_RECORDING_REASONS = ['对方不同意录音', '环境太吵不便录音', '忘记录音', '手机或网络问题', '其他原因'];

module.exports = {
  FIRST_STAGE, DAILY_STAGES, STAGES, CONCERN_NAMES, DIMENSIONS, PROFILE_SECTIONS,
  FIRST_MODULES, DAILY_MODULES, MODULE_LABELS, STATUS, TIMELINE, VALID_MIN_SECONDS,
  COOPERATION_STATUSES, COOP_TONE, STORE_CONDITIONS, NO_RECORDING_REASONS,
};
