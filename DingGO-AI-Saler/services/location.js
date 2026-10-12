// 获取手机当前位置（拜访打卡用）。不会因为定位失败卡住任何流程：调用方拿到带 kind 的错误，自己决定怎么提示。
//   kind: denied      用户拒绝了小程序使用位置（可引导去设置）
//         system      手机或微信的定位权限 / 定位开关没开（可引导去设置）
//         unsupported 小程序没开通定位接口，或没在隐私指引里声明位置信息（只能改用地图选点）
//         cancel      用户在地图选点里点了返回
//         unknown     其他
function classify(err) {
  const msg = String((err && err.errMsg) || err || '');
  let kind = 'unknown';
  if (/cancel/i.test(msg)) kind = 'cancel';
  else if (/system permission|system.*denied|location service|gps|fail:?\s*ERROR_?10001/i.test(msg)) kind = 'system';
  else if (/auth deny|authorize no response|auth denied|user denied|permission denied/i.test(msg)) kind = 'denied';
  else if (/not declared|privacy|no permission|not authorized|api.*(unsupported|not support)|require.*(privacy|private)/i.test(msg)) kind = 'unsupported';
  const e = new Error(msg || '定位失败');
  e.kind = kind;
  e.errMsg = msg;
  return e;
}

// 当前位置（国测局坐标，和微信地图、腾讯地图一致）。返回 { lat, lng, accuracy }
function getLocation() {
  return new Promise((resolve, reject) => {
    wx.getLocation({
      type: 'gcj02',
      isHighAccuracy: true,
      highAccuracyExpireTime: 4000,
      success: (res) => resolve({ lat: res.latitude, lng: res.longitude, accuracy: res.accuracy }),
      fail: (err) => reject(classify(err)),
    });
  });
}

// 在地图上手动选一个位置（定位不可用时的备选；门店地址选点也在用这个接口）。返回 { lat, lng, address }
function chooseOnMap() {
  return new Promise((resolve, reject) => {
    wx.chooseLocation({
      success: (res) => resolve({ lat: res.latitude, lng: res.longitude, address: `${res.address || ''}${res.name ? ` ${res.name}` : ''}`.trim().slice(0, 200) }),
      fail: (err) => reject(classify(err)),
    });
  });
}

module.exports = { getLocation, chooseOnMap, classify };
