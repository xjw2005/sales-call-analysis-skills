// 全局开关：useMock=true 时所有数据来自 model/ 的演示数据，不请求后端
module.exports = {
  useMock: true,
  baseUrl: 'https://api.example.com', // 后端就绪后替换为真实地址
  pollIntervalMs: 3000,
};
