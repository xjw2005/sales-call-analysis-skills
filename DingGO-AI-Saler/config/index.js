// 全局开关
module.exports = {
  // true：全部用 model/ 里的演示数据，不请求后端；false：连接真实后端（DingGO-backend）
  useMock: true,
  // AI 功能（问答、陪练）后端尚未接入：为 true 时即使连了后端也继续用演示数据
  mockAI: true,
  // 连接方式：'http' 直连服务器地址；'cloud' 走微信云托管（上线免域名备案时用）
  transport: 'http',
  baseUrl: 'http://服务器公网IP:8000', // 例如 http://47.100.1.2:8000，末尾不要加 /
  cloudEnv: '', // 云托管环境 ID（transport 为 'cloud' 时填写）
  cloudService: 'dinggo-api', // 云托管服务名
  pollIntervalMs: 3000,
};
