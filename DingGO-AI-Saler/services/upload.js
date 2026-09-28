const config = require('../config/index');
const { ensureToken } = require('./request');

// 逐段上传录音；uploadUrl/formData 由后端 POST /visits 返回
function uploadSegments(segments, { uploadUrl, formData = {} } = {}, onProgress) {
  if (config.useMock) return Promise.resolve(segments.map((s, i) => `mock://segment-${i}`));
  const results = [];
  let token = '';
  return segments.reduce((p, filePath, index) => p.then(() => uploadOne(filePath, uploadUrl, { ...formData, index }, token, 2)
    .then((key) => {
      results.push(key);
      if (onProgress) onProgress(index + 1, segments.length);
    })), ensureToken().then((t) => { token = t; })).then(() => results);
}

function uploadOne(filePath, url, formData, token, retries) {
  return new Promise((resolve, reject) => {
    wx.uploadFile({
      url, filePath, name: 'file', formData,
      header: { Authorization: `Bearer ${token}` },
      success: (res) => (res.statusCode < 300 ? resolve(res.data) : reject(new Error(`上传失败 ${res.statusCode}`))),
      fail: (err) => reject(new Error(err.errMsg)),
    });
  }).catch((err) => (retries > 0 ? uploadOne(filePath, url, formData, token, retries - 1) : Promise.reject(err)));
}

module.exports = { uploadSegments };
