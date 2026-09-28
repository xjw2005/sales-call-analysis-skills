const config = require('../config/index');

// 真实接口请求封装：自动带 token，非 2xx 统一抛错
function request({ url, method = 'GET', data }) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${config.baseUrl}${url}`,
      method,
      data,
      header: { Authorization: `Bearer ${wx.getStorageSync('token') || ''}` },
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) resolve(res.data);
        else reject(new Error((res.data && res.data.message) || `请求失败 ${res.statusCode}`));
      },
      fail: (err) => reject(new Error(err.errMsg || '网络异常')),
    });
  });
}

const delay = (value, ms = 200) => new Promise((resolve) => setTimeout(() => resolve(value), ms));

module.exports = { request, delay };
