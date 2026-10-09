// 小程序基础库没有统一的 TextDecoder：手写 UTF-8 解码（流式分片时用，调用方保证传入的是完整的一行字节）
function decode(bytes) {
  let out = '';
  let i = 0;
  while (i < bytes.length) {
    const b = bytes[i];
    let cp;
    if (b < 0x80) { cp = b; i += 1; }
    else if (b >= 0xf0 && i + 3 < bytes.length) { cp = ((b & 0x07) << 18) | ((bytes[i + 1] & 0x3f) << 12) | ((bytes[i + 2] & 0x3f) << 6) | (bytes[i + 3] & 0x3f); i += 4; }
    else if (b >= 0xe0 && i + 2 < bytes.length) { cp = ((b & 0x0f) << 12) | ((bytes[i + 1] & 0x3f) << 6) | (bytes[i + 2] & 0x3f); i += 3; }
    else if (b >= 0xc0 && i + 1 < bytes.length) { cp = ((b & 0x1f) << 6) | (bytes[i + 1] & 0x3f); i += 2; }
    else { cp = 0xfffd; i += 1; }
    out += String.fromCodePoint(cp);
  }
  return out;
}

// 把流式收到的字节拆成一行一行（按换行字节切，换行不会出现在多字节字符中间，所以切分是安全的）
function lineSplitter(onLine) {
  let pending = [];
  return (arrayBuffer) => {
    const chunk = new Uint8Array(arrayBuffer);
    for (let i = 0; i < chunk.length; i += 1) {
      if (chunk[i] === 0x0a) {
        const line = decode(pending);
        pending = [];
        if (line.trim()) onLine(line);
      } else {
        pending.push(chunk[i]);
      }
    }
  };
}

module.exports = { decode, lineSplitter };
