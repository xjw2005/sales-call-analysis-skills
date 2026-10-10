// 全局开关
module.exports = {
  // true：全部用 model/ 里的演示数据，不请求后端；false：连接真实后端（DingGO-backend）
  useMock: true,
  // 首页问答：false 走后端大模型（需要服务器配置了 LLM 密钥）；true 用演示数据
  mockAI: false,
  // AI 陪练：false 走后端大模型扮演客户并点评；true 用演示数据
  mockPractice: false,
  // 连接方式：'http' 直连服务器地址；'cloud' 走微信云托管（上线免域名备案时用）
  transport: 'http',
  baseUrl: 'http://服务器公网IP:8000', // 例如 http://47.100.1.2:8000，末尾不要加 /
  cloudEnv: '', // 云托管环境 ID（transport 为 'cloud' 时填写）
  cloudService: 'dinggo-api', // 云托管服务名
  pollIntervalMs: 3000,
};
